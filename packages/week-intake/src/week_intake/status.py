"""Roll up captain state across this week's items.

For each item, we look at:
  - the local WeekItem state (parsed / needs_clarification / ready / routed / ...)
  - if the item is routed, the captain's view of the admiral_note via
    GET /apps/{app_id} — specifically whether our note_id appears in
    ``admiral_notes_queued`` or in ``admiral_notes_consumed``.

Cycle 2 enrichment: when reachable, also surface paused circuit breaker,
in-flight slice, last meaningful captain action, and escalation state.

Captain API failures degrade gracefully: rollups still show local state,
with safe defaults for the enriched fields.

Captain HTTP JSON contract:
  - paused_until: ISO string or null (captain pre-filters expired pauses)
  - pause_reason: str or null
  - current_slice: dict or null. Has slice_id, objective, title.
  - captain_log_tail: list[dict]. Captain returns oldest-first (per
    captain's read_captain_log impl), so we sort by ts desc client-side.
    Entries have: ts, kind, verdict, rationale, slice_id, references.
  - admiral_notes_queued / admiral_notes_consumed: list[dict].

Captain log kinds we know about (verified by reading captain protocol.py):
  validate, replan, dispatch, stall_detected, note_received, note_response,
  escalation_raised, escalation_resolved, roadmap_complete,
  pull_request_opened, pull_request_merged, post_merge_cycle.

Escalation rule: walk the log newest-first; the first encountered
``escalation_raised`` OR ``validate``-with-``verdict=escalate`` activates
escalation, UNLESS a newer ``escalation_resolved`` was already seen.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from week_intake.captain_client import CaptainError, get_app_status_http
from week_intake.types import WeekItem

CaptainNoteStatus = str
# "queued" | "consumed" | "no_note" | "unreachable" | "unknown_app"
# | "not_routed" | "done" | "abandoned"

# Cycle 5: states for which it is meaningful to ask the captain. Items in
# pre-route states (parsed, needs_clarification, ready) and terminal states
# (done, abandoned) do NOT round-trip to captain. Cycle 4 active view uses
# the broader ACTIVE_STATES; this is captain-link-specific.
CAPTAIN_LINKED_STATES: frozenset[str] = frozenset({"routed", "in_progress", "blocked"})

# ---------------------------------------------------------------------------
# Sentinels for the per-rollup bundle cache
# ---------------------------------------------------------------------------

_CACHE_UNREACHABLE = "_UNREACHABLE_SENTINEL"
_CACHE_NOT_FOUND = "_NOT_FOUND_SENTINEL"

# Log kinds we filter out when computing the "meaningful" action — we
# already know we filed the note; capturing note_received as the latest
# action would just be noise. Local best-effort constant; documented as
# subject to drift if captain renames.
_NOISY_KINDS = frozenset({"note_received"})

# Captain timeout for status enrichment GETs. Lower than register's 5.0s
# so a 30-app week has a worst-case ceiling around 90s rather than 150s.
_CAPTAIN_GET_TIMEOUT = 3.0

# Display truncation in the CLI table.
_TABLE_SLICE_MAX = 24
_TABLE_ACTION_MAX = 32
_TABLE_RATIONALE_MAX = 32


@dataclass
class CaptainItemStatus:
    """Enriched per-item captain view. Always populated to safe defaults
    so JSON consumers can rely on field presence."""

    note_status: CaptainNoteStatus
    slice_in_flight: str | None = None
    pause_active: bool = False
    pause_reason: str | None = None
    pause_parse_error: bool = False
    last_captain_action: str | None = None
    last_meaningful_action: str | None = None
    last_action_ts: str | None = None
    last_action_rationale: str | None = None  # FULL text; CLI truncates display
    latest_meaningful_is_escalate: bool = False
    needs_attention: bool = False
    attention_reason: str | None = None  # "escalation" | "pause" | "pause_parse_error" | None


# ---------------------------------------------------------------------------
# Public surface — back-compat preserved
# ---------------------------------------------------------------------------


def per_item_captain_status(
    item: WeekItem,
) -> tuple[CaptainNoteStatus, dict[str, Any] | None]:
    """Return (captain_note_status, raw_app_bundle) for one routed item.

    Back-compat shape from cycle 1; ``rollup`` now uses
    ``per_item_captain_detail`` internally.
    """
    if item.state == "done":
        return ("done", None)
    if item.state == "abandoned":
        return ("abandoned", None)
    if item.state not in CAPTAIN_LINKED_STATES:
        return ("not_routed", None)
    app_id = item.target.app_id
    if not app_id:
        return ("unknown_app", None)
    try:
        bundle = get_app_status_http(app_id, timeout=_CAPTAIN_GET_TIMEOUT)
    except CaptainError:
        return ("unreachable", None)
    if bundle is None:
        return ("unknown_app", None)

    note_id = item.captain_note_id
    if not note_id:
        return ("no_note", bundle)

    note_status = _resolve_note_status(bundle, note_id)
    return (note_status, bundle)


def per_item_captain_detail(
    item: WeekItem,
    *,
    bundle_cache: dict[str, dict[str, Any] | str] | None = None,
    timeout: float = _CAPTAIN_GET_TIMEOUT,
) -> CaptainItemStatus:
    """Full enriched status. Tolerates missing/malformed bundle fields.

    ``bundle_cache`` is a per-rollup map of ``app_id`` → bundle dict OR a
    sentinel string (``_CACHE_UNREACHABLE`` / ``_CACHE_NOT_FOUND``). If
    provided, this helper will reuse cached results instead of refetching.
    """
    if item.state == "done":
        return CaptainItemStatus(note_status="done")
    if item.state == "abandoned":
        return CaptainItemStatus(note_status="abandoned")
    if item.state not in CAPTAIN_LINKED_STATES:
        return CaptainItemStatus(note_status="not_routed")
    app_id = item.target.app_id
    if not app_id:
        return CaptainItemStatus(note_status="unknown_app")

    bundle = _fetch_or_cached(app_id, bundle_cache, timeout)
    if bundle is _CACHE_UNREACHABLE:
        return CaptainItemStatus(note_status="unreachable")
    if bundle is _CACHE_NOT_FOUND:
        return CaptainItemStatus(note_status="unknown_app")
    assert isinstance(bundle, dict)

    note_id = item.captain_note_id
    note_status = (
        _resolve_note_status(bundle, note_id) if note_id else "no_note"
    )

    detail = CaptainItemStatus(note_status=note_status)
    _populate_slice(detail, bundle.get("current_slice"))
    _populate_pause(detail, bundle.get("paused_until"), bundle.get("pause_reason"))
    _populate_log(detail, bundle.get("captain_log_tail"))
    _populate_attention(detail)
    return detail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_or_cached(
    app_id: str,
    cache: dict[str, dict[str, Any] | str] | None,
    timeout: float,
) -> dict[str, Any] | str:
    if cache is not None and app_id in cache:
        return cache[app_id]
    try:
        bundle = get_app_status_http(app_id, timeout=timeout)
    except CaptainError:
        result: dict[str, Any] | str = _CACHE_UNREACHABLE
    else:
        result = bundle if bundle is not None else _CACHE_NOT_FOUND
    if cache is not None:
        cache[app_id] = result
    return result


def _resolve_note_status(bundle: dict[str, Any], note_id: str) -> CaptainNoteStatus:
    queued = bundle.get("admiral_notes_queued")
    consumed = bundle.get("admiral_notes_consumed")
    queued_ids = _note_ids(queued)
    consumed_ids = _note_ids(consumed)
    if note_id in consumed_ids:
        return "consumed"
    if note_id in queued_ids:
        return "queued"
    return "no_note"


def _note_ids(records: Any) -> set[str]:
    """Extract note_ids tolerantly. Bad container shapes return empty set."""
    if not isinstance(records, list):
        return set()
    ids: set[str] = set()
    for r in records:
        if isinstance(r, str):
            ids.add(r)
        elif isinstance(r, dict):
            nid = r.get("note_id")
            if isinstance(nid, str):
                ids.add(nid)
    return ids


def _populate_slice(detail: CaptainItemStatus, current_slice: Any) -> None:
    if not isinstance(current_slice, dict):
        return
    # Fallback chain: title → objective → slice_id.
    for field in ("title", "objective", "slice_id"):
        v = current_slice.get(field)
        if isinstance(v, str) and v.strip():
            detail.slice_in_flight = v.strip()
            return


def _populate_pause(
    detail: CaptainItemStatus,
    paused_until: Any,
    pause_reason: Any,
) -> None:
    if paused_until is None:
        return
    if not isinstance(paused_until, str):
        # Unparseable shape — treat as parse-error attention signal.
        detail.pause_parse_error = True
        return
    detail.pause_reason = pause_reason if isinstance(pause_reason, str) else None
    parsed = _parse_iso(paused_until)
    if parsed is None:
        detail.pause_parse_error = True
        return
    if parsed > datetime.now(timezone.utc):
        detail.pause_active = True


def _parse_iso(value: str) -> datetime | None:
    """Parse ISO-8601 strings, normalize naive timestamps to UTC.

    Returns None on parse failure (caller treats as parse error).
    """
    s = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _populate_log(detail: CaptainItemStatus, log_tail: Any) -> None:
    """Sort log tail newest-first by ts; compute last action + escalation."""
    sorted_entries = _sort_log_tail(log_tail)
    if not sorted_entries:
        return
    # last_captain_action is the newest entry's kind, regardless of meaningfulness.
    newest = sorted_entries[0]
    detail.last_captain_action = _safe_kind(newest)

    # last_meaningful_action: newest entry whose kind is not in _NOISY_KINDS.
    for e in sorted_entries:
        kind = _safe_kind(e)
        if kind is None or kind in _NOISY_KINDS:
            continue
        detail.last_meaningful_action = kind
        ts = e.get("ts")
        if isinstance(ts, str):
            detail.last_action_ts = ts
        rationale = e.get("rationale")
        if isinstance(rationale, str):
            detail.last_action_rationale = rationale
        break

    # Escalation: walk newest-first. First escalation event activates UNLESS
    # a newer escalation_resolved came first.
    for e in sorted_entries:
        if _is_escalation_resolution(e):
            detail.latest_meaningful_is_escalate = False
            return
        if _is_escalation_event(e):
            detail.latest_meaningful_is_escalate = True
            return


def _sort_log_tail(entries: Any) -> list[dict[str, Any]]:
    """Best-effort sort. Parseable-ts entries sorted newest-first; entries
    with missing/unparseable ts kept in original order, appended after.
    """
    if not isinstance(entries, list):
        return []
    valid: list[tuple[datetime, dict[str, Any]]] = []
    invalid: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue  # silently drop non-dict rows
        ts = e.get("ts")
        if isinstance(ts, str):
            dt = _parse_iso(ts)
            if dt is not None:
                valid.append((dt, e))
                continue
        invalid.append(e)
    valid.sort(key=lambda t: t[0], reverse=True)
    return [e for _, e in valid] + invalid


def _safe_kind(entry: dict[str, Any]) -> str | None:
    k = entry.get("kind")
    return k if isinstance(k, str) else None


def _is_escalation_event(entry: dict[str, Any]) -> bool:
    """An escalation is either kind=escalation_raised OR kind=validate
    with verdict=escalate."""
    kind = _safe_kind(entry)
    if kind == "escalation_raised":
        return True
    if kind == "validate" and entry.get("verdict") == "escalate":
        return True
    return False


def _is_escalation_resolution(entry: dict[str, Any]) -> bool:
    return _safe_kind(entry) == "escalation_resolved"


def _populate_attention(detail: CaptainItemStatus) -> None:
    """Compute needs_attention + attention_reason with precedence:
    escalation > pause > pause_parse_error."""
    if detail.latest_meaningful_is_escalate:
        detail.needs_attention = True
        detail.attention_reason = "escalation"
        return
    if detail.pause_active:
        detail.needs_attention = True
        detail.attention_reason = "pause"
        return
    if detail.pause_parse_error:
        detail.needs_attention = True
        detail.attention_reason = "pause_parse_error"
        return
    detail.needs_attention = False
    detail.attention_reason = None


# ---------------------------------------------------------------------------
# Rollup
# ---------------------------------------------------------------------------


def rollup(items: list[WeekItem]) -> dict[str, Any]:
    """Aggregate counts + per-item enriched captain status.

    Returns a dict suitable for JSON display. Per-rollup bundle cache means
    multiple week-items pointing to the same app_id share one HTTP GET.
    """
    by_state = Counter(it.state for it in items)
    by_kind = Counter(it.kind for it in items)
    by_app = Counter((it.target.app_id or "(unrouted)") for it in items)

    bundle_cache: dict[str, dict[str, Any] | str] = {}
    rows: list[dict[str, Any]] = []
    unreachable_apps: set[str] = set()
    routed_n = 0
    needs_attention_n = 0
    per_app_log_window: dict[str, dict[str, Any]] = {}

    for it in items:
        detail = per_item_captain_detail(it, bundle_cache=bundle_cache)
        if it.state == "routed":
            routed_n += 1
        if detail.note_status == "unreachable" and it.target.app_id:
            unreachable_apps.add(it.target.app_id)
        if detail.needs_attention:
            needs_attention_n += 1
        # After per_item_captain_detail runs, the bundle_cache holds the
        # bundle (or sentinel) for this app. Capture log_tail once per app.
        app_id = it.target.app_id
        if (
            app_id
            and app_id not in per_app_log_window
            and isinstance(bundle_cache.get(app_id), dict)
        ):
            cached = bundle_cache[app_id]
            assert isinstance(cached, dict)
            tail = cached.get("captain_log_tail")
            entries = tail if isinstance(tail, list) else []
            tail_oldest_ts: str | None = None
            for e in entries:
                if isinstance(e, dict):
                    ts = e.get("ts")
                    if isinstance(ts, str):
                        tail_oldest_ts = ts
                        break  # captain returns oldest-first
            per_app_log_window[app_id] = {
                "entries": entries,
                "tail_oldest_ts": tail_oldest_ts,
            }
        rows.append(
            {
                "item_id": it.item_id,
                "state": it.state,
                "kind": it.kind,
                "app_id": it.target.app_id,
                "captain_note_status": detail.note_status,
                "captain_note_id": it.captain_note_id,
                "title": it.title or it.raw_text[:80],
                # Cycle-2 additions (always present, with explicit defaults):
                "slice_in_flight": detail.slice_in_flight,
                "pause_active": detail.pause_active,
                "pause_reason": detail.pause_reason,
                "pause_parse_error": detail.pause_parse_error,
                "last_captain_action": detail.last_captain_action,
                "last_meaningful_action": detail.last_meaningful_action,
                "last_action_ts": detail.last_action_ts,
                "last_action_rationale": detail.last_action_rationale,
                "latest_meaningful_is_escalate": detail.latest_meaningful_is_escalate,
                "needs_attention": detail.needs_attention,
                "attention_reason": detail.attention_reason,
            }
        )

    # captain_unreachable is a count of UNIQUE unreachable apps the rollup
    # touched. Matches existing semantics; cycle 1's count was per-item (a
    # 5-item-1-app dip would count 5 unreachables). With caching, we now
    # naturally count unique apps. Document behavioral change in tests.
    return {
        "by_state": dict(by_state),
        "by_kind": dict(by_kind),
        "by_app": dict(by_app),
        "items": rows,
        "totals": {
            "items": len(items),
            "routed": routed_n,
            "captain_unreachable": _count_items_for_unreachable_apps(items, unreachable_apps),
            "needs_attention": needs_attention_n,
        },
        # Cycle-3 hook: brief.py reads this to do windowed log aggregation.
        # Same bundle as cycle-2 — no extra HTTP. Apps that were unreachable
        # or 404'd do not appear here.
        "per_app_log_window": per_app_log_window,
    }


def _count_items_for_unreachable_apps(
    items: list[WeekItem], unreachable_apps: set[str]
) -> int:
    """Count routed items whose app fell into the unreachable bucket.

    Preserves cycle-1 semantics: total = routed-items pointing at an
    unreachable app, NOT the count of unique apps.
    """
    if not unreachable_apps:
        return 0
    return sum(
        1
        for it in items
        if it.state in CAPTAIN_LINKED_STATES
        and it.target.app_id
        and it.target.app_id in unreachable_apps
    )


__all__ = [
    "CaptainItemStatus",
    "per_item_captain_detail",
    "per_item_captain_status",
    "rollup",
]

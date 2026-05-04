"""Cycle 3 — `chad-week brief`: narrative weekly digest.

Inputs: this week's WeekItems + the per-app captain log tail that
``status.rollup()`` already collects (no extra HTTP).

Outputs: a ``WeekBrief`` with deterministic facts (per-app windowed
event counts, app-scoped attention surface) and an LLM-generated
narrative paragraph. Narrative is cached at ``<week_root>/brief.cache.json``,
keyed by a hash of the exact facts payload sent to the LLM. The cache is
defensive: any malformed/unreadable cache file is treated as a miss.

Design constraints (review-locked):
- Cache path lives under WeekFolder.root → CHAD_WEEK_DIR is honored.
- ``escalations_raised`` counts ONLY ``kind=escalation_raised``;
  ``validate+verdict=escalate`` is a state signal (cycle-2), not a
  weekly event counter.
- Attention surface is APP-SCOPED. Two routed items on one paused app
  produce ONE attention row (with both item_ids), not two.
- Log-window truncation is signaled when we cannot prove we saw every
  in-window event (oldest parseable tail ts is not strictly older than
  ``week_start_utc``).
- ``--no-llm`` skips both LLM call AND cached prose; ``narrative=""``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tracked_app_registry.storage import atomic_write

from week_intake.llm import LLMError, claude_complete
from week_intake.protocol import WeekFolder, iso_week_for
from week_intake.status import CAPTAIN_LINKED_STATES, _parse_iso, rollup
from week_intake.types import WeekItem

logger = logging.getLogger(__name__)

# Bump to invalidate every cached narrative globally.
_BRIEF_PROMPT_VERSION = 1

_NARRATIVE_SYSTEM = (
    "You write a one-paragraph weekly engineering digest for Chad. Be terse. "
    "No bullet points, no markdown headers. Lead with what shipped concretely "
    "(name apps, name counts), then what needs his attention. 4 to 6 sentences "
    "max. Do not invent facts that are not in the JSON. If everything is quiet, "
    "say so in one sentence."
)
_NARRATIVE_TIMEOUT = 60
_NARRATIVE_MODEL = "haiku"

_CACHE_FILE = "brief.cache.json"


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class AppActivity:
    app_id: str
    prs_opened: int = 0
    prs_merged: int = 0
    roadmap_completes: int = 0
    escalations_raised: int = 0  # only kind=escalation_raised
    escalations_resolved: int = 0
    last_dispatch_ts: str | None = None
    pause_active: bool = False
    slice_in_flight: str | None = None
    log_window_truncated: bool = False
    item_ids: list[str] = field(default_factory=list)


@dataclass
class AttentionRow:
    app_id: str
    attention_reason: str  # "escalation" | "pause" | "pause_parse_error"
    pause_reason: str | None
    last_action_rationale: str | None
    item_ids: list[str]


@dataclass
class WeekBrief:
    week: str
    week_start_utc: str
    week_end_utc: str
    totals: dict[str, int]
    apps: list[AppActivity]
    attention_items: list[AttentionRow]
    narrative: str
    prompt_version: int
    used_cache: bool


# ---------------------------------------------------------------------------
# Week-window helpers
# ---------------------------------------------------------------------------


def iso_week_bounds(week: str) -> tuple[datetime, datetime]:
    """Return [start, end) UTC datetimes for an ISO week tag like '2026-W19'."""
    year_s, week_s = week.split("-W")
    start = datetime.fromisocalendar(int(year_s), int(week_s), 1).replace(
        tzinfo=timezone.utc
    )
    return start, start + timedelta(days=7)


def _is_window_truncated(tail: Any, week_start: datetime) -> bool:
    """True if we cannot prove we saw every event in [week_start, +7d)."""
    if not isinstance(tail, list):
        return True
    oldest: datetime | None = None
    for e in tail:
        if not isinstance(e, dict):
            continue
        ts_raw = e.get("ts")
        if not isinstance(ts_raw, str):
            continue
        ts = _parse_iso(ts_raw)
        if ts is None:
            continue
        if oldest is None or ts < oldest:
            oldest = ts
    if oldest is None:
        return True
    return oldest >= week_start


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate_app(
    app_id: str,
    log_window: dict[str, Any],
    week_start: datetime,
    week_end: datetime,
    *,
    pause_active: bool,
    slice_in_flight: str | None,
    item_ids: list[str],
) -> AppActivity:
    activity = AppActivity(
        app_id=app_id,
        pause_active=pause_active,
        slice_in_flight=slice_in_flight,
        item_ids=item_ids,
    )
    entries = log_window.get("entries") if isinstance(log_window, dict) else None
    activity.log_window_truncated = _is_window_truncated(entries, week_start)
    if not isinstance(entries, list):
        return activity
    newest_dispatch_ts: datetime | None = None
    for e in entries:
        if not isinstance(e, dict):
            continue
        ts_raw = e.get("ts")
        if not isinstance(ts_raw, str):
            continue
        ts = _parse_iso(ts_raw)
        if ts is None or ts < week_start or ts >= week_end:
            continue
        kind = e.get("kind")
        if kind == "pull_request_opened":
            activity.prs_opened += 1
        elif kind == "pull_request_merged":
            activity.prs_merged += 1
        elif kind == "roadmap_complete":
            activity.roadmap_completes += 1
        elif kind == "escalation_raised":
            activity.escalations_raised += 1
        elif kind == "escalation_resolved":
            activity.escalations_resolved += 1
        elif kind == "dispatch":
            if newest_dispatch_ts is None or ts > newest_dispatch_ts:
                newest_dispatch_ts = ts
                activity.last_dispatch_ts = ts_raw
    return activity


def _build_apps(
    rollup_data: dict[str, Any],
    items: list[WeekItem],
    week_start: datetime,
    week_end: datetime,
) -> list[AppActivity]:
    """One AppActivity per unique app_id with at least one captain-linked WeekItem."""
    items_by_app: dict[str, list[WeekItem]] = {}
    for it in items:
        if it.state not in CAPTAIN_LINKED_STATES:
            continue
        app_id = it.target.app_id
        if not app_id:
            continue
        items_by_app.setdefault(app_id, []).append(it)

    # Filter rollup rows to linked states FIRST so a terminal row for app-a
    # does not shadow live captain detail for that same app.
    rows = rollup_data.get("items", [])
    detail_by_app: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("state") not in CAPTAIN_LINKED_STATES:
            continue
        app_id = r.get("app_id")
        if app_id and app_id not in detail_by_app:
            detail_by_app[app_id] = r

    log_window = rollup_data.get("per_app_log_window") or {}
    apps: list[AppActivity] = []
    for app_id in sorted(items_by_app.keys()):
        item_ids = sorted(it.item_id for it in items_by_app[app_id])
        d = detail_by_app.get(app_id) or {}
        apps.append(
            _aggregate_app(
                app_id,
                log_window.get(app_id) or {},
                week_start,
                week_end,
                pause_active=bool(d.get("pause_active")),
                slice_in_flight=d.get("slice_in_flight"),
                item_ids=item_ids,
            )
        )
    return apps


# ---------------------------------------------------------------------------
# Attention surface (app-scoped)
# ---------------------------------------------------------------------------

# Precedence cycle-2 already encodes; we re-rank here to dedupe app-scoped.
_REASON_RANK = {"escalation": 3, "pause": 2, "pause_parse_error": 1}


def _build_attention(rollup_data: dict[str, Any]) -> list[AttentionRow]:
    """Group routed-with-attention rows by app_id; pick strongest reason."""
    rows = rollup_data.get("items", [])
    by_app: dict[str, dict[str, Any]] = {}
    items_by_app: dict[str, list[str]] = {}
    for r in rows:
        if r.get("state") not in CAPTAIN_LINKED_STATES:
            continue
        reason = r.get("attention_reason")
        app_id = r.get("app_id")
        if not app_id:
            continue
        item_id = r.get("item_id")
        if isinstance(item_id, str):
            items_by_app.setdefault(app_id, []).append(item_id)
        if not reason:
            continue
        existing = by_app.get(app_id)
        rank = _REASON_RANK.get(reason, 0)
        if existing is None or rank > _REASON_RANK.get(existing.get("attention_reason"), 0):
            by_app[app_id] = r

    attention: list[AttentionRow] = []
    for app_id in sorted(by_app.keys()):
        r = by_app[app_id]
        item_ids = sorted(set(items_by_app.get(app_id, [])))
        attention.append(
            AttentionRow(
                app_id=app_id,
                attention_reason=r["attention_reason"],
                pause_reason=r.get("pause_reason"),
                last_action_rationale=r.get("last_action_rationale"),
                item_ids=item_ids,
            )
        )
    return attention


# ---------------------------------------------------------------------------
# Facts payload + cache
# ---------------------------------------------------------------------------


def _facts_payload(brief: WeekBrief) -> dict[str, Any]:
    """Canonical dict sent to the LLM AND used as cache hash input."""
    return {
        "prompt_version": brief.prompt_version,
        "week": brief.week,
        "week_start_utc": brief.week_start_utc,
        "week_end_utc": brief.week_end_utc,
        "totals": brief.totals,
        "apps": [asdict(a) for a in brief.apps],
        "attention_items": [asdict(a) for a in brief.attention_items],
    }


def _hash_facts(facts: dict[str, Any]) -> str:
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_path(week: str) -> Path:
    return WeekFolder(week=week).root / _CACHE_FILE


def _read_cache(path: Path, expected_hash: str, expected_version: int) -> str | None:
    """Return cached narrative on hit, None on any miss/error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        if data.get("prompt_version") != expected_version:
            return None
        if data.get("input_hash") != expected_hash:
            return None
        narrative = data.get("narrative")
    except (KeyError, TypeError):
        return None
    if not isinstance(narrative, str):
        return None
    return narrative


def _write_cache(
    path: Path, *, input_hash: str, prompt_version: int, narrative: str
) -> None:
    payload = {
        "input_hash": input_hash,
        "prompt_version": prompt_version,
        "narrative": narrative,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def build_brief(
    week: str | None = None,
    *,
    use_llm: bool = True,
    refresh: bool = False,
) -> WeekBrief:
    week = week or iso_week_for()
    folder = WeekFolder(week=week)
    items = folder.list_items()
    rollup_data = rollup(items)
    week_start, week_end = iso_week_bounds(week)

    apps = _build_apps(rollup_data, items, week_start, week_end)
    attention = _build_attention(rollup_data)

    rollup_totals = rollup_data.get("totals") or {}
    events_total = sum(
        a.prs_opened
        + a.prs_merged
        + a.roadmap_completes
        + a.escalations_raised
        + a.escalations_resolved
        for a in apps
    )
    totals = {
        "items": int(rollup_totals.get("items", 0)),
        "routed": int(rollup_totals.get("routed", 0)),
        "captain_unreachable": int(rollup_totals.get("captain_unreachable", 0)),
        "needs_attention": int(rollup_totals.get("needs_attention", 0)),
        "events_total": events_total,
    }

    brief = WeekBrief(
        week=week,
        week_start_utc=week_start.isoformat(),
        week_end_utc=week_end.isoformat(),
        totals=totals,
        apps=apps,
        attention_items=attention,
        narrative="",
        prompt_version=_BRIEF_PROMPT_VERSION,
        used_cache=False,
    )

    if not use_llm:
        return brief

    facts = _facts_payload(brief)
    input_hash = _hash_facts(facts)
    cache_path = _cache_path(week)

    if not refresh:
        cached = _read_cache(cache_path, input_hash, _BRIEF_PROMPT_VERSION)
        if cached is not None:
            brief.narrative = cached
            brief.used_cache = True
            return brief

    try:
        narrative = claude_complete(
            json.dumps(facts, sort_keys=True),
            model=_NARRATIVE_MODEL,
            system=_NARRATIVE_SYSTEM,
            timeout=_NARRATIVE_TIMEOUT,
        )
    except LLMError as exc:
        logger.warning("brief: narrative LLM failed: %s", exc)
        return brief  # narrative stays ""; do NOT write cache

    brief.narrative = narrative.strip()
    try:
        _write_cache(
            cache_path,
            input_hash=input_hash,
            prompt_version=_BRIEF_PROMPT_VERSION,
            narrative=brief.narrative,
        )
    except OSError as exc:
        logger.warning("brief: cache write failed: %s", exc)
    return brief


# ---------------------------------------------------------------------------
# Markdown render
# ---------------------------------------------------------------------------


def render_markdown(brief: WeekBrief) -> str:
    lines: list[str] = []
    lines.append(f"# Week {brief.week}")
    lines.append("")
    needs = brief.totals.get("needs_attention", 0)
    summary = (
        f"{brief.totals.get('items', 0)} items, "
        f"{brief.totals.get('routed', 0)} routed across {len(brief.apps)} apps, "
        f"{brief.totals.get('events_total', 0)} captain events this week"
    )
    if needs:
        summary += f", {needs} need attention"
    lines.append(summary + ".")
    lines.append("")
    if brief.narrative:
        lines.append(brief.narrative)
    else:
        lines.append("(narrative unavailable)")
    lines.append("")
    if brief.apps:
        lines.append("## Apps")
        for a in brief.apps:
            bits: list[str] = []
            bits.append(f"{a.prs_opened} PR opened")
            bits.append(f"{a.prs_merged} merged")
            if a.roadmap_completes:
                bits.append(f"{a.roadmap_completes} roadmap done")
            if a.escalations_raised:
                bits.append(f"{a.escalations_raised} escalation raised")
            if a.escalations_resolved:
                bits.append(f"{a.escalations_resolved} resolved")
            if a.pause_active:
                bits.append("paused")
            if a.slice_in_flight:
                bits.append(f"slice: {a.slice_in_flight}")
            if a.log_window_truncated:
                bits.append("(log window truncated)")
            lines.append(f"- {a.app_id}: " + ", ".join(bits))
        lines.append("")
    if brief.attention_items:
        lines.append("## Attention")
        for a in brief.attention_items:
            detail = a.attention_reason
            if a.attention_reason == "pause" and a.pause_reason:
                detail = f"pause: {a.pause_reason}"
            elif a.last_action_rationale:
                detail = f"{a.attention_reason}: {a.last_action_rationale}"
            items_str = ", ".join(a.item_ids)
            lines.append(f"- {a.app_id} — {detail} (items: {items_str})")
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "AppActivity",
    "AttentionRow",
    "WeekBrief",
    "build_brief",
    "iso_week_bounds",
    "render_markdown",
]

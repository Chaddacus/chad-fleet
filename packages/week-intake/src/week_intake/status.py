"""Roll up captain state across this week's items.

For each item, we look at:
  - the local WeekItem state (parsed / needs_clarification / ready / routed / ...)
  - if the item is routed, the captain's view of the admiral_note via
    GET /apps/{app_id} — specifically whether our note_id appears in
    ``admiral_notes_queued`` (captain hasn't ticked yet) or in
    ``admiral_notes_consumed`` (captain has acknowledged it).

Captain API failures degrade gracefully: rollups still show local state,
with ``captain_status="unreachable"`` per item.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from week_intake.captain_client import CaptainError, get_app_status_http
from week_intake.types import WeekItem

CaptainNoteStatus = str  # "queued" | "consumed" | "no_note" | "unreachable" | "unknown_app"


def per_item_captain_status(item: WeekItem) -> tuple[CaptainNoteStatus, dict[str, Any] | None]:
    """Return (captain_note_status, raw_app_bundle) for one routed item.

    For non-routed items, returns ``("not_routed", None)`` without hitting
    the API.
    """
    if item.state != "routed":
        return ("not_routed", None)
    app_id = item.target.app_id
    if not app_id:
        return ("unknown_app", None)
    try:
        bundle = get_app_status_http(app_id)
    except CaptainError:
        return ("unreachable", None)
    if bundle is None:
        return ("unknown_app", None)

    note_id = item.captain_note_id
    if not note_id:
        return ("no_note", bundle)

    queued = bundle.get("admiral_notes_queued") or []
    consumed = bundle.get("admiral_notes_consumed") or []
    queued_ids = _note_ids(queued)
    consumed_ids = _note_ids(consumed)
    if note_id in consumed_ids:
        return ("consumed", bundle)
    if note_id in queued_ids:
        return ("queued", bundle)
    return ("no_note", bundle)


def _note_ids(records: list[Any]) -> set[str]:
    ids: set[str] = set()
    for r in records:
        if isinstance(r, str):
            ids.add(r)
        elif isinstance(r, dict):
            nid = r.get("note_id")
            if isinstance(nid, str):
                ids.add(nid)
    return ids


def rollup(items: list[WeekItem]) -> dict[str, Any]:
    """Aggregate counts + per-item captain status.

    Returns a dict suitable for JSON display:

        {
          "by_state":  {state: count, ...},
          "by_kind":   {kind: count, ...},
          "by_app":    {app_id: count, ...},
          "items": [
            {item_id, state, kind, app_id, captain_note_status},
            ...
          ],
          "totals": {"items": N, "routed": M, "captain_unreachable": K},
        }
    """
    by_state = Counter(it.state for it in items)
    by_kind = Counter(it.kind for it in items)
    by_app = Counter((it.target.app_id or "(unrouted)") for it in items)

    rows: list[dict[str, Any]] = []
    unreachable = 0
    routed_n = 0
    for it in items:
        status, _bundle = per_item_captain_status(it)
        if status == "unreachable":
            unreachable += 1
        if it.state == "routed":
            routed_n += 1
        rows.append(
            {
                "item_id": it.item_id,
                "state": it.state,
                "kind": it.kind,
                "app_id": it.target.app_id,
                "captain_note_status": status,
                "captain_note_id": it.captain_note_id,
                "title": it.title or it.raw_text[:80],
            }
        )

    return {
        "by_state": dict(by_state),
        "by_kind": dict(by_kind),
        "by_app": dict(by_app),
        "items": rows,
        "totals": {
            "items": len(items),
            "routed": routed_n,
            "captain_unreachable": unreachable,
        },
    }


__all__ = ["per_item_captain_status", "rollup"]

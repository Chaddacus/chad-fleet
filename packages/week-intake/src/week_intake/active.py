"""Cycle 4 — `chad-week active`: cross-week non-terminal item view.

When a new ISO week begins, `chad-week list` only shows items in the new
week's folder. Last week's `routed`, `blocked`, `needs_clarification`, etc.
items vanish from view. `chad-week active` solves that by reading recent
prior week folders and surfacing items that are still in flight.

Design boundaries (review-locked):
- READ-ONLY. No data movement; this is a view, not a copy.
- Allowlist of `ACTIVE_STATES` (derived from the canonical Literal in
  types.py, minus terminal {done, abandoned}). Unknown/corrupt persisted
  states are excluded.
- Date-bounded lookback: `current_monday - 7*lookback <= week_monday <=
  current_monday`. Future weeks dropped. Sparse old weeks beyond bound
  dropped.
- WeekFolder.list_items() already collapses JSONL to latest record per
  item_id (verified protocol.py:_list_items_impl), so no extra dedup
  needed here.
- Within-week ordering: created_at descending (newest first), item_id
  ascending as stable tie-break.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import get_args

from week_intake.protocol import WeekFolder, iso_week_for, parse_iso_week, week_base
from week_intake.types import WeekItem, WeekItemState

# Canonical state sets — single source of truth.
_TERMINAL_STATES: frozenset[str] = frozenset({"done", "abandoned"})
ACTIVE_STATES: frozenset[str] = frozenset(get_args(WeekItemState)) - _TERMINAL_STATES
# evaluates to {parsed, needs_clarification, ready, routed, in_progress, blocked}


@dataclass
class ActiveRow:
    week: str
    item: WeekItem


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_weeks(base: Path) -> list[str]:
    """Return parseable ISO-week tags found under base. Order undefined."""
    if not base.exists():
        return []
    weeks: list[str] = []
    for p in base.iterdir():
        if not p.is_dir():
            continue
        try:
            parse_iso_week(p.name)
        except ValueError:
            continue
        weeks.append(p.name)
    return weeks


def _select_weeks(
    discovered: list[str],
    now_week: str,
    lookback: int,
) -> list[str]:
    """Return week tags in [current_monday - 7*lookback, current_monday],
    newest-first, with current always included."""
    if lookback < 0:
        raise ValueError(f"lookback must be >= 0, got {lookback}")
    current_mon = parse_iso_week(now_week)
    earliest = current_mon - timedelta(days=7 * lookback)
    keep: set[str] = {now_week}
    for tag in discovered:
        try:
            mon = parse_iso_week(tag)
        except ValueError:
            continue
        if earliest <= mon <= current_mon:
            keep.add(tag)
    return sorted(keep, key=parse_iso_week, reverse=True)


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


def _is_active(item: WeekItem, state_filter: str | None) -> bool:
    if item.state not in ACTIVE_STATES:
        return False
    if state_filter is None:
        return True
    return item.state == state_filter


def _sort_within_week(items: list[WeekItem]) -> list[WeekItem]:
    """created_at desc; item_id asc as stable tie-break."""
    items = sorted(items, key=lambda it: it.item_id)            # asc
    items.sort(key=lambda it: it.created_at, reverse=True)      # desc, stable
    return items


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def list_active(
    *,
    lookback: int = 4,
    state: str | None = None,
    base: Path | None = None,
    now_week: str | None = None,
) -> list[ActiveRow]:
    """List non-terminal items across the current week and N prior weeks.

    Args:
        lookback: number of prior weeks to include in addition to current.
        state: optional state filter; must be in ACTIVE_STATES if not None.
        base: filesystem root for week dirs (defaults to week_base()).
        now_week: anchor week for lookback (defaults to current ISO week).
    """
    if state is not None and state not in ACTIVE_STATES:
        raise ValueError(
            f"state must be None or one of {sorted(ACTIVE_STATES)!r}, got {state!r}"
        )
    if lookback < 0:
        raise ValueError(f"lookback must be >= 0, got {lookback}")

    base = base if base is not None else week_base()
    now = now_week or iso_week_for()

    discovered = _discover_weeks(base)
    selected = _select_weeks(discovered, now, lookback)

    rows: list[ActiveRow] = []
    for week in selected:
        folder = WeekFolder(week=week, base=base)
        items = [it for it in folder.list_items() if _is_active(it, state)]
        for it in _sort_within_week(items):
            rows.append(ActiveRow(week=week, item=it))
    return rows


def list_active_enriched(
    *,
    lookback: int = 4,
    state: str | None = None,
    base: Path | None = None,
    now_week: str | None = None,
) -> tuple[list[ActiveRow], dict[tuple[str, str], dict]]:
    """Return active rows + per-row captain enrichment.

    The enrichment dict is keyed by (week, item_id) so cross-week duplicate
    item ids round-trip correctly. Each value is the rollup-emitted row dict
    (cycle-2 enriched fields: captain_note_status, slice_in_flight,
    pause_active, last_meaningful_action, attention_reason, ...). Pre-route
    active items still appear with note_status="not_routed".

    Captain GETs are deduped across weeks by rollup's local bundle_cache —
    one HTTP call per unique app_id even if items span multiple weeks.
    """
    from week_intake.status import rollup

    rows = list_active(lookback=lookback, state=state, base=base, now_week=now_week)
    if not rows:
        return rows, {}
    items = [r.item for r in rows]
    report = rollup(items)
    report_items = report.get("items") or []
    enrichment: dict[tuple[str, str], dict] = {}
    for row, rep in zip(rows, report_items):
        enrichment[(row.week, row.item.item_id)] = rep
    return rows, enrichment


__all__ = [
    "ACTIVE_STATES",
    "ActiveRow",
    "list_active",
    "list_active_enriched",
]

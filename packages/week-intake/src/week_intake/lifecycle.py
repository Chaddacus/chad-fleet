"""Cycle 5 — `chad-week complete | abandon | reopen` lifecycle transitions.

Closes the loop on the WeekItem state machine. Before cycle 5 the lifecycle
ended at `routed` and items piled up forever. Now Chad can mark them
`done` or `abandoned`, and undo a mistake with `reopen`.

Boundaries (review-locked through 3 codex rounds):
- Each helper is atomic under WeekFolder.lock().
- Each helper bumps `revision` by exactly +1 (lifecycle owns it; upsert
  does not).
- `complete` is allowed only from {routed, in_progress, blocked} (the
  CAPTAIN_LINKED_STATES). Pre-route states should be `abandon`ed instead.
- `abandon` is allowed from any non-terminal state.
- `reopen` is allowed from {done, abandoned}. Target state is restored
  from the most recent complete/abandon LifecycleEvent. Legacy items
  with no log fall back to `needs_clarification` + a refresh_warnings
  entry — never `ready` (would let Chad route an item with stale target).
- captain_note_id is preserved across all three. Re-triggering captain
  is out of scope (captain dedups deterministic note ids; that's a
  future cycle).
"""

from __future__ import annotations

from week_intake.protocol import WeekFolder
from week_intake.types import LifecycleEvent, Note, WeekItem, WeekItemState

# Allowed source states per transition.
COMPLETE_FROM: frozenset[str] = frozenset({"routed", "in_progress", "blocked"})
ABANDON_FROM: frozenset[str] = frozenset({
    "parsed",
    "needs_clarification",
    "ready",
    "routed",
    "in_progress",
    "blocked",
})
REOPEN_FROM: frozenset[str] = frozenset({"done", "abandoned"})


class TransitionError(ValueError):
    """Raised when a requested transition is not allowed from the current state."""


# ---------------------------------------------------------------------------
# Restoration rule
# ---------------------------------------------------------------------------


def _reopen_target_state(item: WeekItem) -> tuple[WeekItemState, list[str]]:
    """Restore from_state of the most recent complete/abandon event.

    Returns (target_state, warnings). When no terminal-transition history
    exists (legacy item with empty log), falls back to
    ``needs_clarification`` so the item lands in clarification queue
    instead of being silently promoted to routable.
    """
    for ev in reversed(item.lifecycle_log):
        if ev.transition in ("complete", "abandon"):
            return ev.from_state, []
    return "needs_clarification", [
        "no terminal-transition history; falling back to needs_clarification"
    ]


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _transition(
    week: str,
    item_id: str,
    transition: str,
    *,
    allowed_from: frozenset[str],
    target_state_fn,  # (item) -> (state, warnings)
    reason: str | None = None,
) -> WeekItem:
    folder = WeekFolder(week=week)
    with folder.lock():
        item = folder.get_item(item_id)
        if item is None:
            raise TransitionError(
                f"item {item_id!r} not found in week {week!r}"
            )
        if item.state not in allowed_from:
            raise TransitionError(
                f"cannot {transition} {item_id!r}: state is {item.state!r}; "
                f"allowed sources: {sorted(allowed_from)!r}"
            )
        from_state = item.state
        to_state, warnings = target_state_fn(item)
        item.lifecycle_log.append(
            LifecycleEvent(
                transition=transition,  # type: ignore[arg-type]
                from_state=from_state,
                to_state=to_state,
                reason=reason,
            )
        )
        item.state = to_state
        if warnings:
            # Record one-shot fallback warnings; do not accumulate stale ones.
            item.refresh_warnings = warnings
        item.revision += 1
        item.touch()
        folder.upsert_item(item)
        return item


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def complete_item(week: str, item_id: str) -> WeekItem:
    return _transition(
        week,
        item_id,
        "complete",
        allowed_from=COMPLETE_FROM,
        target_state_fn=lambda _it: ("done", []),
    )


def abandon_item(week: str, item_id: str, reason: str | None = None) -> WeekItem:
    return _transition(
        week,
        item_id,
        "abandon",
        allowed_from=ABANDON_FROM,
        target_state_fn=lambda _it: ("abandoned", []),
        reason=reason,
    )


def reopen_item(week: str, item_id: str) -> WeekItem:
    return _transition(
        week,
        item_id,
        "reopen",
        allowed_from=REOPEN_FROM,
        target_state_fn=_reopen_target_state,
    )


def record_note(week: str, item_id: str, text: str) -> WeekItem:
    """Append an ad-hoc note to an item without mutating state or revision.

    Cycle 7. Useful when execution surfaces information you want attached
    to the item but no open clarify question exists and no lifecycle
    transition is warranted (e.g. a discovery correction during dogfood).

    Allowed from any state, including terminal — notes are observations,
    not state changes. Held under WeekFolder.lock() so concurrent notes
    don't race; revision is intentionally NOT bumped (notes don't change
    semantic state).
    """
    if not text or not text.strip():
        raise ValueError("note text must be non-empty")
    folder = WeekFolder(week=week)
    with folder.lock():
        item = folder.get_item(item_id)
        if item is None:
            raise TransitionError(
                f"item {item_id!r} not found in week {week!r}"
            )
        item.notes.append(Note(text=text.strip()))
        item.touch()  # advance updated_at; revision intentionally unchanged
        folder.upsert_item(item)
        return item


__all__ = [
    "ABANDON_FROM",
    "COMPLETE_FROM",
    "REOPEN_FROM",
    "TransitionError",
    "abandon_item",
    "complete_item",
    "record_note",
    "reopen_item",
]

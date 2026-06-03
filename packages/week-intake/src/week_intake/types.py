"""Core data types for week-intake.

All Pydantic v2; round-trips cleanly through JSONL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# Lifecycle of a WeekItem.
#
#     parsed
#       │  intake CLI parsed this from a brain-dump bullet
#       ▼
#   needs_clarification ─────┐
#       │                    │  driver answers in chat,
#       ▼                    │  re-runs intake-resolve
#     ready                  │
#       │  route CLI invoked │
#       ▼                    │
#     routed ◀────────────────┘
#       │  captain consumes the admiral_note
#       ▼
#     in_progress | blocked | done
WeekItemState = Literal[
    "parsed",
    "needs_clarification",
    "ready",
    "routed",
    "in_progress",
    "blocked",
    "done",
    "abandoned",
]


# What kind of work this is. Resolved during clarification.
WeekItemKind = Literal[
    "unknown",        # not yet classified
    "wip",            # ongoing work in an existing tracked app
    "github_repo",    # specific public/private repo not yet tracked
    "greenfield",     # new project — needs scaffold
    "decision",       # not code work; a decision Chad needs to make
    "meeting_prep",   # prep for a specific meeting/call
    "research",       # exploratory, no shippable artifact
]


class ClarificationQuestion(BaseModel):
    """One targeted question generated for a low-confidence item.

    Keep it ONE question per round. The driver asks Chad in chat,
    Chad answers, the answer is recorded, the item is re-evaluated.
    """

    question_id: str = Field(..., description="Stable id within the item, e.g. 'kind' or 'app_id'")
    prompt: str = Field(..., description="Plain-English question to show Chad")
    answer: str | None = None
    answered_at: str | None = None


class RouteTarget(BaseModel):
    """Resolved routing destination for one item.

    Populated incrementally during clarification. ``app_id`` is the
    captain workspace id under ``~/.chad/fleet/apps/<app_id>/``.
    """

    app_id: str | None = None
    repo_path: str | None = None  # local checkout path (for wip/github_repo/greenfield)
    is_new_app: bool = False      # if True, route CLI must register before noting
    greenfield_name: str | None = None  # human label for net-new projects


class Note(BaseModel):
    """One ad-hoc observation recorded against a WeekItem (cycle 7).

    Notes are append-only and do not mutate state or revision. Use them
    when execution surfaces information you want to attach to the item
    without going through clarify (which requires an open question) or
    triggering a lifecycle transition.
    """

    text: str
    at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    by: str = "chad"


class LifecycleEvent(BaseModel):
    """One lifecycle transition (complete/abandon/reopen) recorded on a WeekItem.

    Append-only. Reopen reads the most recent complete/abandon entry to
    restore the prior state.
    """

    transition: Literal["complete", "abandon", "reopen"]
    from_state: WeekItemState
    to_state: WeekItemState
    reason: str | None = None
    at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    by: str = "chad"


class WeekItem(BaseModel):
    """One unit of weekly work — from raw brain-dump bullet to routed admiral-note."""

    item_id: str = Field(..., description="Short slug, unique within the week, e.g. 'wk-001'")
    week: str = Field(..., description="ISO-week tag: '2026-W18' (Monday-anchored)")
    raw_text: str = Field(..., description="Original bullet from the brain dump")
    title: str = Field(default="", description="≤80-char headline; falls back to raw_text[:80]")
    kind: WeekItemKind = "unknown"
    state: WeekItemState = "parsed"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    target: RouteTarget = Field(default_factory=RouteTarget)
    clarifications: list[ClarificationQuestion] = Field(default_factory=list)
    captain_note_id: str | None = None  # set after route CLI files admiral_note

    # Two-phase commit / optimistic locking (added v1 / cycle 1).
    # Increments on every state-mutating upsert. Pre-v1 items load with default 0.
    revision: int = 0
    # Set in clarify phase 1 when an answer is recorded; cleared in phase 3
    # after reclassify applies. Non-None means "answer persisted but reclassify
    # has not run yet" — `clarify --continue` picks up from here.
    pending_refresh_question_id: str | None = None
    # Replaced (not appended) on every successful clarify refresh; populated
    # when the LLM proposed values we couldn't apply (invalid slug, missing
    # workspace, invalid repo hint, etc.). Cleared when the next refresh
    # produces clean output.
    refresh_warnings: list[str] = Field(default_factory=list)
    # Cycle 5: append-only audit log of complete/abandon/reopen transitions.
    # Pre-cycle-5 items load with empty list (default factory).
    lifecycle_log: list[LifecycleEvent] = Field(default_factory=list)
    # Cycle 7: append-only ad-hoc observations recorded via `chad-week note`.
    # Does not mutate state or revision. Pre-cycle-7 items load empty.
    notes: list[Note] = Field(default_factory=list)

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

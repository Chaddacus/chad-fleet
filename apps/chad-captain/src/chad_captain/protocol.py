"""Captain ↔ goose-runner ↔ dashboard filesystem protocol.

Per-app workspace layout (under `~/.chad/fleet/apps/<app_id>/`):

    current_slice.json       captain writes, goose-runner reads (next slice instruction)
    progress.jsonl           goose-runner appends, captain tails (per-tool-call events)
    slice_complete.json      goose-runner writes once, captain reads + deletes (completion)
    roadmap.json             captain writes (after replan), dashboard reads
    admiral_notes/<ts>.json  dashboard writes, captain reads on tick + moves to consumed/
    captain_log.jsonl        captain appends, dashboard tails (decisions for this app)
    research/app-profile.json    research pipeline writes (weekly cache)
    scorecard-history.jsonl  rubric suite appends, captain reads

All atomic writes via tracked_app_registry.storage.atomic_write (tempfile + rename).
One writer per file -> no locking needed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from tracked_app_registry.storage import append_jsonl, atomic_write

# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

DEFAULT_FLEET_BASE = Path.home() / ".chad" / "fleet" / "apps"


def fleet_base() -> Path:
    """Root of all per-app workspaces. Override with CHAD_FLEET_APPS_DIR."""
    raw = os.environ.get("CHAD_FLEET_APPS_DIR")
    return Path(raw).expanduser() if raw else DEFAULT_FLEET_BASE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AppWorkspace:
    """Filesystem paths + helpers for one tracked app's captain workspace."""

    def __init__(self, app_id: str, base: Path | None = None) -> None:
        self.app_id = app_id
        self.root = (base or fleet_base()) / app_id

    # ---- paths ----
    @property
    def current_slice_path(self) -> Path:
        return self.root / "current_slice.json"

    @property
    def progress_path(self) -> Path:
        return self.root / "progress.jsonl"

    @property
    def slice_complete_path(self) -> Path:
        return self.root / "slice_complete.json"

    @property
    def roadmap_path(self) -> Path:
        return self.root / "roadmap.json"

    @property
    def admiral_notes_dir(self) -> Path:
        return self.root / "admiral_notes"

    @property
    def admiral_notes_consumed_dir(self) -> Path:
        return self.root / "admiral_notes" / "consumed"

    @property
    def captain_log_path(self) -> Path:
        return self.root / "captain_log.jsonl"

    @property
    def research_path(self) -> Path:
        return self.root / "research" / "app-profile.json"

    @property
    def feature_backlog_path(self) -> Path:
        """Persistent product backlog. Captain reads at replan to anchor
        feature slices on a known roadmap of work; writes at merge to mark
        items shipped. Phase A is human-seeded via `chad-captain backlog add`;
        Phase B will auto-populate via deep research.
        """
        return self.root / "research" / "feature_backlog.json"

    @property
    def scorecard_history_path(self) -> Path:
        return self.root / "scorecard-history.jsonl"

    @property
    def slice_baseline_path(self) -> Path:
        """Pre-slice scorecard snapshot. Captain writes at dispatch,
        reads at validate to compute the rubric delta."""
        return self.root / "slice_baseline.json"

    @property
    def branch_baseline_path(self) -> Path:
        """Pre-branch (PR-wide) scorecard snapshot. Captain writes once at
        captain-branch creation, reads at roadmap_complete to embed a
        before/after delta in the PR body. Cleared after PR open."""
        return self.root / "branch_baseline.json"

    @property
    def last_dispatched_slice_path(self) -> Path:
        """Captain-owned snapshot of the most recently dispatched CurrentSlice.

        Cycle C: goose-runner clears `current_slice.json` after writing
        slice_complete, but the validator (especially custom validators) needs
        the actual dispatched system_prompt + user_prompt to make prompt-aware
        decisions. Captain writes this snapshot BEFORE current_slice.json at
        dispatch time, reads it at validation, clears it after the validate
        block completes.
        """
        return self.root / "last_dispatched_slice.json"

    @property
    def retry_context_path(self) -> Path:
        """Per-slice retry context. Cycle C: captain writes after a
        reject_retry / kill_replan verdict, dispatch path reads + clears
        before issuing the retried slice. Threaded into goose's user_prompt
        as 'PRIOR ATTEMPT FAILED: ...' so the retry isn't a blind redo.
        """
        return self.root / "retry_context.json"

    @property
    def pause_until_path(self) -> Path:
        """Wall-clock pause marker. Written by C8 circuit breaker when
        consecutive failures exceed the threshold, read by captain_tick
        to gate dispatch. Plain ISO-8601 string in a tiny JSON file.
        Operator can `chad-captain unpause --app <id>` to clear early."""
        return self.root / "pause_until.json"

    def ensure(self) -> None:
        """Create the workspace directory tree if missing."""
        for d in (
            self.root,
            self.admiral_notes_dir,
            self.admiral_notes_consumed_dir,
            self.research_path.parent,
        ):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Protocol models
# ---------------------------------------------------------------------------


class CurrentSlice(BaseModel):
    """Captain → goose-runner: the next slice to execute.

    Single source of truth for `goose run`'s system + user prompts. The runner
    treats this file as immutable for the lifetime of the slice — captain
    writes a new version only after slice_complete.json is consumed.
    """

    slice_id: str
    app_id: str
    objective: str = Field(..., description="One-line goal for this slice (used in logs)")
    title: str = Field(default="", description="≤80-char human-readable headline for dashboard display; falls back to objective")
    system_prompt: str = Field(..., description="Full system prompt passed to goose --system")
    user_prompt: str = Field(..., description="Full user prompt passed to goose --text")
    repo_path: str = Field(..., description="Working directory for goose run")
    max_turns: int = 80
    max_tool_repetitions: int = 5
    timeout_seconds: int = 1800
    started_at: str | None = None  # set by goose-runner when it picks up the slice
    deadline: str | None = None    # captain's hard deadline; runner kills if exceeded
    issued_at: str = Field(default_factory=_now_iso)

    # Captain context (not passed to goose, used by validator):
    expected_rubric_categories: list[str] = Field(default_factory=list)
    parent_slice_id: str | None = None  # set on retry/replan derivatives


class ProgressEvent(BaseModel):
    """One event in progress.jsonl. Append-only stream from goose-runner."""

    ts: str = Field(default_factory=_now_iso)
    slice_id: str
    kind: Literal[
        "slice_started",
        "tool_call",
        "tool_result",
        "stdout_chunk",
        "heartbeat",
        "slice_completing",
        "slice_aborted",
    ]
    detail: dict = Field(default_factory=dict)


class SliceComplete(BaseModel):
    """Goose-runner → captain: slice finished (success or otherwise).

    Captain's validator reads this, runs the rubric suite, decides accept /
    reject-retry / hard-reject / escalate, writes a CaptainLogEntry, and then
    deletes this file.
    """

    slice_id: str
    app_id: str
    finished_at: str = Field(default_factory=_now_iso)
    duration_seconds: float
    goose_exit_code: int
    summary: str = Field(..., description="goose's own summary of what it did")
    files_changed: list[str] = Field(default_factory=list)
    diff_path: str | None = None
    log_path: str | None = None
    failure_tail: str | None = None  # last 2KB of stderr if exit != 0
    cheat_flags: list[str] = Field(default_factory=list)


CaptainVerdict = Literal[
    "accept",
    "soft_accept",
    "reject_retry",
    "reject_hard",
    "revert",
    "kill_replan",
    "escalate",
]


class CaptainLogEntry(BaseModel):
    """One captain decision for an app. Appended to captain_log.jsonl."""

    ts: str = Field(default_factory=_now_iso)
    app_id: str
    slice_id: str | None = None
    kind: Literal[
        "validate",
        "replan",
        "dispatch",
        "stall_detected",
        "note_received",
        "note_response",
        "escalation_raised",
        "escalation_resolved",
        # Captain → main integration (C2): emitted when all roadmap slices
        # reach terminal state and when the captain auto-opens a PR.
        "roadmap_complete",
        "pull_request_opened",
        # Captain → main integration (C4): emitted when a captain-opened PR
        # is detected as merged on origin and when the captain refreshes
        # local main + clears roadmap to begin a new cycle.
        "pull_request_merged",
        "post_merge_cycle",
        # Cycle B: captain detected its open PR is in a non-mergeable state
        # (DIRTY/BLOCKED — typically merge conflicts or failing required
        # checks). Emitted ONCE per pending PR; admiral resolves manually.
        # Suppresses re-firing of roadmap_complete + pull_request_opened.
        "pr_conflict",
        # Cycle D: roadmap had no dispatchable queued slice but was not yet
        # in a terminal state (some slices in_flight or blocked). Emitted
        # before an "exhausted" replan so the daemon's churn is visible
        # in the captain log.
        "roadmap_drained",
    ]
    verdict: CaptainVerdict | None = None
    rubric_delta_pp: float | None = None
    rationale: str = ""
    references: dict = Field(default_factory=dict)  # admiral_note_id, slice_id, etc.


class RoadmapSlice(BaseModel):
    """One slice in an app's roadmap."""

    slice_id: str
    objective: str
    title: str = ""  # ≤80-char human-readable headline; falls back to objective
    phase: str = ""  # e.g. "T-32", "fundations", "compliance"
    estimated_minutes: int = 30
    blocked_by: list[str] = Field(default_factory=list)
    status: Literal["queued", "in_flight", "done", "skipped", "blocked"] = "queued"
    notes: str = ""


class Roadmap(BaseModel):
    """An ordered roadmap for one app. Captain manages this; dashboard reads."""

    app_id: str
    generated_at: str = Field(default_factory=_now_iso)
    generated_by: Literal["initial", "replanner", "manual"] = "initial"
    objective_summary: str = ""
    slices: list[RoadmapSlice] = Field(default_factory=list)


FeatureStatus = Literal["queued", "shipped", "deferred", "obsolete"]


class FeatureBacklogItem(BaseModel):
    """One product-level feature on the captain's backlog.

    Bigger than a slice — usually decomposes into 2-4 slices. Anchors the
    replanner so it isn't re-inventing features each cycle. Status flips
    to 'shipped' when the captain merges a roadmap whose slice titles
    overlap this item's title (fuzzy match).
    """

    id: str  # short stable id, e.g. "fb-001"
    title: str
    rationale: str = ""  # why this feature matters; surfaces in replan prompt
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    estimated_slice_count: int = 2
    status: FeatureStatus = "queued"
    source: str = ""  # "admiral", "research", "manual", "auto-ideation"
    competitive_evidence: list[str] = Field(default_factory=list)
    shipped_in: str | None = None  # e.g. "PR#147"
    shipped_at: str | None = None
    created_at: str = Field(default_factory=_now_iso)


class FeatureBacklog(BaseModel):
    """Persistent product backlog for one app."""

    app_id: str
    generated_at: str = Field(default_factory=_now_iso)
    items: list[FeatureBacklogItem] = Field(default_factory=list)

    def queued(self, *, top: int | None = None) -> list[FeatureBacklogItem]:
        ranked = sorted(
            (i for i in self.items if i.status == "queued"),
            key=lambda i: i.priority,
            reverse=True,
        )
        return ranked[:top] if top else ranked

    def shipped(self, *, last: int | None = None) -> list[FeatureBacklogItem]:
        items = [i for i in self.items if i.status == "shipped"]
        items.sort(key=lambda i: i.shipped_at or i.created_at, reverse=True)
        return items[:last] if last else items

    def by_id(self, item_id: str) -> FeatureBacklogItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def next_id(self) -> str:
        max_n = 0
        for it in self.items:
            if it.id.startswith("fb-"):
                try:
                    max_n = max(max_n, int(it.id.split("-", 1)[1]))
                except ValueError:
                    continue
        return f"fb-{max_n + 1:03d}"


class RetryContext(BaseModel):
    """Cycle C: failure context threaded into a retried slice's prompt.

    Captain writes this when the validator returns reject_retry/kill_replan,
    dispatch reads + clears it right before issuing the retry. Lets the
    next attempt see why the previous one failed instead of rerunning blind.
    """

    slice_id: str  # original slice_id (no -retry suffix)
    failed_at: str = Field(default_factory=_now_iso)
    rationale: str
    retry_hint: str = ""


class AdmiralNote(BaseModel):
    """One note from the admiral (Chad) targeted at one app's captain context."""

    note_id: str
    app_id: str
    received_at: str = Field(default_factory=_now_iso)
    body: str
    expects_response: bool = True
    captain_response: str | None = None
    responded_at: str | None = None


# ---------------------------------------------------------------------------
# Read / write helpers (atomic writes, append-only logs)
# ---------------------------------------------------------------------------


def write_current_slice(ws: AppWorkspace, slice_: CurrentSlice) -> None:
    ws.ensure()
    atomic_write(ws.current_slice_path, slice_.model_dump_json(indent=2))


def read_current_slice(ws: AppWorkspace) -> CurrentSlice | None:
    if not ws.current_slice_path.exists():
        return None
    return CurrentSlice.model_validate_json(ws.current_slice_path.read_text())


def clear_current_slice(ws: AppWorkspace) -> None:
    if ws.current_slice_path.exists():
        ws.current_slice_path.unlink()


def append_progress(ws: AppWorkspace, event: ProgressEvent) -> None:
    ws.ensure()
    append_jsonl(ws.progress_path, event.model_dump(mode="json"))


def write_slice_complete(ws: AppWorkspace, complete: SliceComplete) -> None:
    ws.ensure()
    atomic_write(ws.slice_complete_path, complete.model_dump_json(indent=2))


def read_slice_complete(ws: AppWorkspace) -> SliceComplete | None:
    if not ws.slice_complete_path.exists():
        return None
    return SliceComplete.model_validate_json(ws.slice_complete_path.read_text())


def clear_slice_complete(ws: AppWorkspace) -> None:
    if ws.slice_complete_path.exists():
        ws.slice_complete_path.unlink()


def append_captain_log(ws: AppWorkspace, entry: CaptainLogEntry) -> None:
    ws.ensure()
    append_jsonl(ws.captain_log_path, entry.model_dump(mode="json"))


def read_captain_log(ws: AppWorkspace, limit: int | None = None) -> list[CaptainLogEntry]:
    if not ws.captain_log_path.exists():
        return []
    lines = ws.captain_log_path.read_text().splitlines()
    if limit is not None:
        lines = lines[-limit:]
    out: list[CaptainLogEntry] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(CaptainLogEntry.model_validate_json(line))
        except Exception:
            continue
    return out


def write_roadmap(ws: AppWorkspace, roadmap: Roadmap) -> None:
    ws.ensure()
    atomic_write(ws.roadmap_path, roadmap.model_dump_json(indent=2))


def read_roadmap(ws: AppWorkspace) -> Roadmap | None:
    if not ws.roadmap_path.exists():
        return None
    return Roadmap.model_validate_json(ws.roadmap_path.read_text())


def read_feature_backlog(ws: AppWorkspace) -> FeatureBacklog:
    """Return the persisted backlog, or an empty one if none exists yet."""
    if not ws.feature_backlog_path.exists():
        return FeatureBacklog(app_id=ws.app_id)
    try:
        return FeatureBacklog.model_validate_json(
            ws.feature_backlog_path.read_text()
        )
    except (ValueError, OSError):
        # Corrupt file — return empty rather than crashing the daemon.
        return FeatureBacklog(app_id=ws.app_id)


def write_feature_backlog(ws: AppWorkspace, backlog: FeatureBacklog) -> None:
    ws.ensure()
    ws.feature_backlog_path.parent.mkdir(parents=True, exist_ok=True)
    backlog.generated_at = _now_iso()
    atomic_write(ws.feature_backlog_path, backlog.model_dump_json(indent=2))


def write_last_dispatched_slice(ws: AppWorkspace, slice_: CurrentSlice) -> None:
    """Cycle C: captain-owned snapshot of dispatched slice. Written BEFORE
    current_slice.json so the validator can always see the real prompts."""
    ws.ensure()
    atomic_write(ws.last_dispatched_slice_path, slice_.model_dump_json(indent=2))


def read_last_dispatched_slice(ws: AppWorkspace) -> CurrentSlice | None:
    if not ws.last_dispatched_slice_path.exists():
        return None
    try:
        return CurrentSlice.model_validate_json(
            ws.last_dispatched_slice_path.read_text()
        )
    except Exception:  # noqa: BLE001 — corrupt snapshot → treat as missing
        return None


def clear_last_dispatched_slice(ws: AppWorkspace) -> None:
    if ws.last_dispatched_slice_path.exists():
        ws.last_dispatched_slice_path.unlink()


def write_retry_context(ws: AppWorkspace, ctx: RetryContext) -> None:
    """Cycle C: persist retry context for the next dispatch of this slice."""
    ws.ensure()
    atomic_write(ws.retry_context_path, ctx.model_dump_json(indent=2))


def read_retry_context(ws: AppWorkspace) -> RetryContext | None:
    if not ws.retry_context_path.exists():
        return None
    try:
        return RetryContext.model_validate_json(ws.retry_context_path.read_text())
    except Exception:  # noqa: BLE001 — corrupt sidecar → treat as missing
        return None


def clear_retry_context(ws: AppWorkspace) -> None:
    if ws.retry_context_path.exists():
        ws.retry_context_path.unlink()


def write_admiral_note(ws: AppWorkspace, note: AdmiralNote) -> Path:
    ws.ensure()
    path = ws.admiral_notes_dir / f"{note.note_id}.json"
    atomic_write(path, note.model_dump_json(indent=2))
    return path


def list_unread_admiral_notes(ws: AppWorkspace) -> list[Path]:
    if not ws.admiral_notes_dir.exists():
        return []
    return sorted(p for p in ws.admiral_notes_dir.glob("*.json") if p.is_file())


def consume_admiral_note(ws: AppWorkspace, note_path: Path) -> None:
    """Move a processed note from admiral_notes/ to admiral_notes/consumed/.

    Captain calls this after writing its response to captain_log + updating
    the note's captain_response field.
    """
    ws.admiral_notes_consumed_dir.mkdir(parents=True, exist_ok=True)
    target = ws.admiral_notes_consumed_dir / note_path.name
    note_path.replace(target)


__all__ = [
    "AppWorkspace",
    "fleet_base",
    "CurrentSlice",
    "ProgressEvent",
    "SliceComplete",
    "CaptainLogEntry",
    "CaptainVerdict",
    "RoadmapSlice",
    "Roadmap",
    "FeatureBacklogItem",
    "FeatureBacklog",
    "FeatureStatus",
    "RetryContext",
    "AdmiralNote",
    "write_current_slice",
    "read_current_slice",
    "clear_current_slice",
    "append_progress",
    "write_slice_complete",
    "read_slice_complete",
    "clear_slice_complete",
    "append_captain_log",
    "read_captain_log",
    "write_roadmap",
    "read_roadmap",
    "read_feature_backlog",
    "write_feature_backlog",
    "write_last_dispatched_slice",
    "read_last_dispatched_slice",
    "clear_last_dispatched_slice",
    "write_retry_context",
    "read_retry_context",
    "clear_retry_context",
    "write_admiral_note",
    "list_unread_admiral_notes",
    "consume_admiral_note",
]

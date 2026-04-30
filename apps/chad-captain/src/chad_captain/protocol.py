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
    def scorecard_history_path(self) -> Path:
        return self.root / "scorecard-history.jsonl"

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
    ]
    verdict: CaptainVerdict | None = None
    rubric_delta_pp: float | None = None
    rationale: str = ""
    references: dict = Field(default_factory=dict)  # admiral_note_id, slice_id, etc.


class RoadmapSlice(BaseModel):
    """One slice in an app's roadmap."""

    slice_id: str
    objective: str
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
    "write_admiral_note",
    "list_unread_admiral_notes",
    "consume_admiral_note",
]

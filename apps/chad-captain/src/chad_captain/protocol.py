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
    def task_manifest_path(self) -> Path:
        """PR15 v6 §6.4: per-app declaration of what this captain
        produces/consumes on the cross-task artifact bus. Captain checks
        this at roadmap_complete to refuse opening a PR if a declared
        produces entry hasn't been written to the bus by this captain
        yet (split_task safety: producer captain shouldn't ship before
        consumer can observe its outputs).
        """
        return self.root / "task_manifest.json"

    @property
    def pending_replan_reasons_path(self) -> Path:
        """PR9 v6 §6.1 trigger queue: JSONL of pending replan reasons
        (Twin enqueues 'scope change', 'admiral note batch', 'cost
        breach', etc.). Replanner drains on next tick before deciding
        what to generate. Atomic append via append_jsonl; consumer
        rotates by truncating the file under exclusive flock.
        """
        return self.root / "pending_replan_reasons.jsonl"

    @property
    def goose_pid_path(self) -> Path:
        """PR9 v6 §6.3.1 scope-change abort: PID of the running goose
        subprocess for the current slice. goose-runner writes at spawn,
        clears at exit. Twin reads to send SIGTERM when scope changes
        invalidate an in-flight slice.
        """
        return self.root / "goose.pid"

    @property
    def twin_holds_dir(self) -> Path:
        """PR8 v6 §validation L4: per-task hold markers written by Twin.

        Each file is one hold (typically `<task_id>-<reason>.json`) with
        a payload of {reason, created_at, expires_at, raised_by}. Captain
        close handler refuses to mark a task complete while ANY unexpired
        hold is present in this directory. Cleared by Twin via
        `twin captain release-hold --app <id> --hold <name>`.
        """
        return self.root / "twin_holds"

    @property
    def replan_history_path(self) -> Path:
        """JSONL of recent replan attempts. Each line: {ts, trigger,
        slice_count, shape_signature}. Replanner reads to enforce the
        per-captain rate limit (5/hour by default; FLEET_PROCESS spec
        line 17). Append-only; rotated by ops if it grows large.
        """
        return self.root / "replan_history.jsonl"

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
            self.twin_holds_dir,
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

    # PR6/v8: task_id propagation. None for legacy slices that pre-date
    # the Twin daemon's task-scoped scaffolding. Twin's close handler
    # filters by task_id; legacy None slices are excluded from any
    # task close check (never block).
    task_id: str | None = None


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

    # PR6/v8: task_id copied from CurrentSlice so close-handler queries can
    # filter by task. None for legacy completes.
    task_id: str | None = None
    # PR6/v8 R4#10: when goose deletes test files/functions, the runner
    # MUST set this field with a human-readable rationale. Captain
    # validator inspects diff for deletions and rejects (reject_retry/
    # reject_hard) if deletions present without this rationale.
    removed_tests_reason: str | None = None


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
        # PR15 v6 §6.4: roadmap is structurally complete but the task
        # manifest declares produces[] artifacts that haven't been
        # published to the cross-task artifact bus yet. Captain holds
        # the PR until those artifacts land — a downstream consumer
        # captain may be waiting on them.
        "roadmap_complete_pending_producer",
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
    # PR6/v8: copied from the dispatched CurrentSlice so Twin's close-handler
    # can filter captain_log by task_id. None for legacy entries.
    task_id: str | None = None


class RoadmapSlice(BaseModel):
    """One slice in an app's roadmap."""

    slice_id: str
    objective: str
    title: str = ""  # ≤80-char human-readable headline; falls back to objective
    phase: str = ""  # e.g. "T-32", "fundations", "compliance"
    estimated_minutes: int = 30
    blocked_by: list[str] = Field(default_factory=list)
    status: Literal[
        "queued", "in_flight", "done", "skipped", "blocked",
        # PR9 v6 §6.3.1: slice was superseded by a mid-flight scope change
        # (Twin received a clarification or new task that invalidates the
        # current slice). goose-runner is SIGTERM'd; the slice is marked
        # superseded_by_scope_change instead of done/blocked so the
        # replanner can decide whether to regenerate or move on.
        "superseded_by_scope_change",
    ] = "queued"
    notes: str = ""

    # Cycle E: per-slice prompt overrides. When set, build_current_slice uses
    # these in place of the default coding-agent system/user prompts. The
    # replanner uses this to seed slices with task-specific instructions
    # (manuscript voice, ES query template, deploy-script invocation, etc.)
    # that the default prompts don't capture. Either field may be set
    # independently — None = use default for that field.
    custom_system_prompt: str | None = None
    custom_user_prompt: str | None = None

    # PR6/v8: task_id copied from the FeatureBacklogItem this slice was
    # generated from. Threaded into CurrentSlice when build_current_slice
    # constructs the dispatched slice. None for legacy slices.
    task_id: str | None = None


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
    # PR6/v8: task_id this backlog item belongs to. Set by SCAFFOLD when
    # the backlog is seeded from a Twin task. None for hand-seeded
    # backlog items predating the Twin daemon.
    task_id: str | None = None


class FeatureBacklog(BaseModel):
    """Persistent product backlog for one app."""

    app_id: str
    generated_at: str = Field(default_factory=_now_iso)
    items: list[FeatureBacklogItem] = Field(default_factory=list)
    # PR8 R3#7 §6.3: monotonic generation counter. Incremented on every
    # write so concurrent writers (replanner draining + scaffold seeding
    # a new task) can detect lost-update via compare-and-swap.
    # update_feature_backlog() handles the increment + flock atomically.
    generation: int = 0

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


class TaskManifest(BaseModel):
    """PR15 v6 §6.4: declaration of cross-task artifact contracts for
    one captain. Lives at AppWorkspace.task_manifest_path.

    ``task_id`` ties to the Twin task this captain belongs to.
    ``produces`` lists artifact names this captain MUST publish to the
    bus before its roadmap_complete handler is allowed to open a PR.
    ``consumes`` lists artifact names this captain reads at slice
    start (informational; future slices will gate dispatch on
    presence/freshness).
    """

    task_id: str
    produces: list[str] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list)


def read_task_manifest(ws: AppWorkspace) -> TaskManifest | None:
    """Return the TaskManifest for ``ws`` or None if no manifest is
    declared (back-compat for captains that pre-date the artifact bus).
    """
    if not ws.task_manifest_path.exists():
        return None
    try:
        return TaskManifest.model_validate_json(
            ws.task_manifest_path.read_text()
        )
    except (ValueError, OSError):
        return None


def write_task_manifest(ws: AppWorkspace, manifest: TaskManifest) -> None:
    """Persist a TaskManifest to disk (atomic write)."""
    ws.ensure()
    atomic_write(ws.task_manifest_path, manifest.model_dump_json(indent=2))


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
    """Direct write — does NOT take a lock. Prefer update_feature_backlog
    for read-modify-write semantics; this is the legacy path for callers
    that have full ownership of the file (initial seed, tests).
    """
    ws.ensure()
    ws.feature_backlog_path.parent.mkdir(parents=True, exist_ok=True)
    backlog.generated_at = _now_iso()
    atomic_write(ws.feature_backlog_path, backlog.model_dump_json(indent=2))


class BacklogGenerationConflict(RuntimeError):
    """PR8 R3#7 §6.3: raised when a compare-and-swap update detects that
    the backlog was modified by another writer between read and write.
    Caller should re-read and retry the mutation."""


def _backlog_lock_path(ws: AppWorkspace) -> Path:
    """Sibling lock file for the feature backlog. Lives next to the JSON
    so locking semantics never interact with the data file's existence
    or parsing. Created on first lock acquisition.
    """
    p = ws.feature_backlog_path
    return p.parent / f".{p.name}.lock"


def update_feature_backlog(
    ws: AppWorkspace,
    mutate: "callable[[FeatureBacklog], None]",
    *,
    expected_generation: int | None = None,
) -> FeatureBacklog:
    """Read-modify-write the backlog under an exclusive flock with
    monotonic generation increment.

    Pattern:
        update_feature_backlog(ws, lambda b: b.items.append(item))

    If ``expected_generation`` is given AND does not match the on-disk
    generation, raises ``BacklogGenerationConflict`` so the caller can
    re-read and retry instead of silently overwriting another writer's
    update. When None, the call always wins (last-writer-wins semantics
    plus an incremented generation so subsequent CAS callers can detect
    the change).
    """
    import fcntl
    ws.ensure()
    ws.feature_backlog_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _backlog_lock_path(ws)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            backlog = read_feature_backlog(ws)
            if (
                expected_generation is not None
                and backlog.generation != expected_generation
            ):
                raise BacklogGenerationConflict(
                    f"app={ws.app_id}: backlog generation mismatch "
                    f"(on-disk={backlog.generation}, "
                    f"expected={expected_generation}); re-read and retry"
                )
            mutate(backlog)
            backlog.generation += 1
            backlog.generated_at = _now_iso()
            atomic_write(
                ws.feature_backlog_path,
                backlog.model_dump_json(indent=2),
            )
            return backlog
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# PR9 v6 §6.1 — pending replan reasons queue
# ---------------------------------------------------------------------------


_REPLAN_REASON_PRIORITIES = {
    "scope_change": 0,        # highest — Twin saw a clarification mid-flight
    "admiral_note_batch": 1,
    "cost_breach": 2,
    "stalled": 3,
    "manual": 4,
    "exhausted": 5,           # lowest — natural drain
}


def enqueue_replan_reason(
    ws: AppWorkspace,
    *,
    reason: str,
    detail: str = "",
    source: str = "twin",
) -> None:
    """Append one pending-replan reason to the queue. Caller passes a
    ``reason`` from the well-known set above (unknown reasons are
    accepted but get the lowest priority). Replanner drains via
    ``drain_replan_reasons`` on the next tick.
    """
    ws.ensure()
    append_jsonl(
        ws.pending_replan_reasons_path,
        {
            "ts": _now_iso(),
            "reason": reason,
            "detail": detail,
            "source": source,
            "priority": _REPLAN_REASON_PRIORITIES.get(reason, 99),
        },
    )


def drain_replan_reasons(ws: AppWorkspace) -> list[dict]:
    """Atomically read+truncate the queue. Returns entries sorted by
    priority (lowest int = most urgent), then by ts.

    Uses an exclusive flock on a sibling .lock file so concurrent
    enqueues during a drain never lose entries (the truncate-after-read
    pattern is safe because enqueue_replan_reason takes the same lock
    via append_jsonl's PIPE_BUF guarantee + the file's append mode).
    """
    import fcntl
    if not ws.pending_replan_reasons_path.exists():
        return []
    lock_path = ws.pending_replan_reasons_path.parent / (
        f".{ws.pending_replan_reasons_path.name}.lock"
    )
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            entries: list[dict] = []
            with open(ws.pending_replan_reasons_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            # Truncate the queue. Done under the lock so a parallel
            # enqueue cannot land an entry that we then drop.
            ws.pending_replan_reasons_path.write_text("")
            entries.sort(key=lambda e: (e.get("priority", 99), e.get("ts", "")))
            return entries
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# PR9 v6 §6.3.1 — goose PID tracking + scope-change abort
# ---------------------------------------------------------------------------


def write_goose_pid(ws: AppWorkspace, pid: int) -> None:
    """Record the running goose subprocess PID so Twin can SIGTERM it
    when a scope change invalidates the in-flight slice. Called by
    goose-runner immediately after Popen succeeds."""
    ws.ensure()
    atomic_write(ws.goose_pid_path, str(pid))


def read_goose_pid(ws: AppWorkspace) -> int | None:
    """Return the recorded goose PID, or None if no slice is in flight."""
    if not ws.goose_pid_path.exists():
        return None
    try:
        return int(ws.goose_pid_path.read_text().strip())
    except (ValueError, OSError):
        return None


def clear_goose_pid(ws: AppWorkspace) -> None:
    """Remove the PID marker. Called by goose-runner on normal exit."""
    try:
        ws.goose_pid_path.unlink()
    except FileNotFoundError:
        pass


def send_goose_abort_signal(ws: AppWorkspace) -> bool:
    """Send SIGTERM to the recorded goose PID. Returns True if a signal
    was delivered, False if no PID was tracked or the process was gone.

    Caller (Twin scope-change handler) should follow up by marking the
    in-flight slice as ``superseded_by_scope_change`` in the roadmap.
    Does NOT clear the PID file — goose-runner will do that on its own
    exit path so we don't race the cleanup.
    """
    import errno
    import signal
    pid = read_goose_pid(ws)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        # Already exited — nothing to abort.
        return False
    except OSError as exc:
        if exc.errno == errno.EPERM:
            # Different user owns the PID; treat as not-aborted, log upstream.
            return False
        raise


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
    "update_feature_backlog",
    "BacklogGenerationConflict",
    "enqueue_replan_reason",
    "drain_replan_reasons",
    "TaskManifest",
    "read_task_manifest",
    "write_task_manifest",
    "write_goose_pid",
    "read_goose_pid",
    "clear_goose_pid",
    "send_goose_abort_signal",
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

"""Captain validator + next-slice dispatcher.

Runs on the captain's async tick. For each tracked app:
  1. If slice_complete.json exists → validate it, log a verdict, advance roadmap.
  2. If no current_slice and roadmap has a queued slice → dispatch it.
  3. Drain admiral_notes (separate concern, see notes.py — added in S13).

Validation decision rubric (no rubric-suite invocation here — that's
plug-replaceable via the `score_delta` callback so tests + the per-tick
loop can both run cheaply):

  | Signal                                          | Verdict      |
  | cheat_flags present                             | escalate     |
  | goose exit -9 (timeout)                         | kill_replan  |
  | goose exit != 0, never retried                  | reject_retry |
  | goose exit != 0, already a retry                | reject_hard  |
  | goose exit 0 + zero files_changed               | reject_retry |
  | goose exit 0 + delta >= +0.5pp                  | accept       |
  | goose exit 0 + delta in [0, 0.5)                | soft_accept  |
  | goose exit 0 + delta < 0 (regression)           | revert       |

Replan triggers (caller — the daemon — handles these):
  - Two soft_accepts in a row for the same app
  - Roadmap exhausted (no queued slices left)
  - Admiral note received with "replan" intent
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from chad_captain.protocol import (
    AppWorkspace,
    CaptainLogEntry,
    CaptainVerdict,
    CurrentSlice,
    Roadmap,
    RoadmapSlice,
    SliceComplete,
    append_captain_log,
    clear_slice_complete,
    read_current_slice,
    read_roadmap,
    read_slice_complete,
    write_current_slice,
    write_roadmap,
)

logger = logging.getLogger(__name__)


# Default "no-op" score delta. Caller wires the real rubric-suite runner.
def _no_score_delta(*_args, **_kwargs) -> float | None:
    return None


@dataclass
class ValidationResult:
    verdict: CaptainVerdict
    rationale: str
    rubric_delta_pp: float | None = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_slice(
    complete: SliceComplete,
    slice_: CurrentSlice,
    *,
    score_delta: Callable[[CurrentSlice, SliceComplete], float | None] = _no_score_delta,
) -> ValidationResult:
    """Decide what to do about a finished slice."""
    if complete.cheat_flags:
        return ValidationResult(
            verdict="escalate",
            rationale=f"cheat patterns detected: {', '.join(complete.cheat_flags)}",
        )

    if complete.goose_exit_code == -9:
        return ValidationResult(verdict="kill_replan", rationale="goose timeout")

    is_retry = slice_.parent_slice_id is not None

    if complete.goose_exit_code != 0:
        # Edge case from S3 dogfood: goose's own commit attempt was blocked by
        # sandbox/permissions but its file edits were already on the working
        # tree, and the captain runner's _git_autocommit step staged + committed
        # them. So exit!=0 with files_changed populated means "the work landed
        # despite goose's own exit." Treat as soft_accept (low confidence)
        # rather than reject_retry (which would redo or break finished work).
        if complete.files_changed:
            return ValidationResult(
                verdict="soft_accept",
                rationale=(
                    f"goose exit {complete.goose_exit_code} but {len(complete.files_changed)} "
                    f"file(s) committed by captain-runner — work landed, low-confidence accept"
                ),
            )
        if is_retry:
            return ValidationResult(
                verdict="reject_hard",
                rationale=f"non-zero exit ({complete.goose_exit_code}) after retry",
            )
        return ValidationResult(
            verdict="reject_retry",
            rationale=f"non-zero exit ({complete.goose_exit_code}), retrying once",
        )

    if not complete.files_changed:
        if is_retry:
            return ValidationResult(
                verdict="reject_hard",
                rationale="no files changed despite success exit (after retry)",
            )
        return ValidationResult(
            verdict="reject_retry",
            rationale="no files changed despite success exit",
        )

    # Persistence-required gate (C13): if the objective claims to "log",
    # "track", "audit", or "store" and the diff introduces module-level
    # mutable state at module scope (e.g. `_DECISIONS: list = []`), the
    # work is in-memory only — passes tests via clear_X() between cases
    # but loses everything on restart and races under multi-worker.
    # Live failure: PR #144 shipped author_toolkit/agent/decision_log.py
    # with `_DECISIONS: list[AgentDecision] = []`. Looked like a
    # feature, isn't production-grade.
    persist_violation = _detect_persistence_violation(slice_, complete)
    if persist_violation:
        if is_retry:
            return ValidationResult(
                verdict="reject_hard",
                rationale=(
                    f"persistence required but in-memory only "
                    f"({persist_violation}) after retry"
                ),
            )
        return ValidationResult(
            verdict="reject_retry",
            rationale=(
                f"persistence required by objective but implementation "
                f"is in-memory only: {persist_violation}; retry with "
                f"DB / file / queue persistence"
            ),
        )

    delta = score_delta(slice_, complete)

    if delta is None:
        # No rubric run available — accept on clean exit + files modified.
        return ValidationResult(
            verdict="accept",
            rationale="clean exit, files modified, no rubric delta available",
        )

    # Noise floor for the continuous rubric — anything in
    # [-NOISE_FLOOR, +ACCEPT] is soft_accept. Without this, the new
    # continuous file_size_health/test_density dims produce tiny
    # floating-point negative deltas (-0.0001pp from same-LOC files) that
    # fired reject_retry → revert and threw out legit slices.
    NOISE_FLOOR_PP = 0.5
    ACCEPT_THRESHOLD_PP = 0.5

    if delta <= -NOISE_FLOOR_PP:
        if is_retry:
            return ValidationResult(
                verdict="revert",
                rationale=f"rubric regression {delta:+.2f}pp after retry, revert",
                rubric_delta_pp=delta,
            )
        return ValidationResult(
            verdict="reject_retry",
            rationale=f"rubric regression {delta:+.2f}pp, retrying once",
            rubric_delta_pp=delta,
        )

    if delta >= ACCEPT_THRESHOLD_PP:
        return ValidationResult(
            verdict="accept",
            rationale=f"rubric delta {delta:+.2f}pp",
            rubric_delta_pp=delta,
        )

    return ValidationResult(
        verdict="soft_accept",
        rationale=f"low-yield rubric delta {delta:+.2f}pp",
        rubric_delta_pp=delta,
    )


# ---------------------------------------------------------------------------
# Per-app verify gate (C1)
# ---------------------------------------------------------------------------


def run_verify_gate(
    *,
    repo_path: str,
    verify_cmd: str | None,
    timeout_seconds: int = 300,
) -> tuple[bool, str]:
    """Run the per-app verify command (e.g. 'make check', 'npm test').

    Returns ``(passed, summary)`` — when ``verify_cmd`` is None/empty the gate
    is skipped (passed=True). Tails stderr/stdout on failure so the captain
    log captures *why* CI failed without dumping the full build log.
    """
    if not verify_cmd or not verify_cmd.strip():
        return True, "no verify_cmd configured"
    try:
        proc = subprocess.run(  # noqa: S602 — operator-configured local cmd
            verify_cmd,
            shell=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"verify_cmd timed out after {timeout_seconds}s"
    except OSError as e:
        return False, f"verify_cmd failed to launch: {e}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1024:].strip()
        return False, f"verify_cmd exit {proc.returncode}: {tail[:500]}"
    return True, f"verify_cmd passed ({verify_cmd!r})"


def apply_verify_gate(
    result: ValidationResult,
    *,
    is_retry: bool,
    repo_path: str,
    verify_cmd: str | None,
    timeout_seconds: int = 300,
) -> ValidationResult:
    """Run the verify gate against an accepted slice and downgrade if CI fails.

    Only runs for verdicts where goose claims success (``accept``, ``soft_accept``).
    For already-rejecting verdicts the gate is a no-op — the slice is going to
    be retried/escalated regardless.
    """
    if result.verdict not in ("accept", "soft_accept"):
        return result
    passed, summary = run_verify_gate(
        repo_path=repo_path,
        verify_cmd=verify_cmd,
        timeout_seconds=timeout_seconds,
    )
    if passed:
        return result
    new_verdict: CaptainVerdict = "reject_hard" if is_retry else "reject_retry"
    return ValidationResult(
        verdict=new_verdict,
        rationale=f"goose {result.verdict} but {summary}",
        rubric_delta_pp=result.rubric_delta_pp,
    )


# ---------------------------------------------------------------------------
# Roadmap advancement
# ---------------------------------------------------------------------------


def advance_roadmap(roadmap: Roadmap, slice_id: str, verdict: CaptainVerdict) -> None:
    """Mutate the roadmap in-place to reflect a slice's outcome."""
    for rs in roadmap.slices:
        if rs.slice_id != slice_id:
            continue
        if verdict in ("accept", "soft_accept"):
            rs.status = "done"
        elif verdict in ("reject_retry", "kill_replan"):
            rs.status = "queued"  # will be re-dispatched
        elif verdict in ("reject_hard", "revert"):
            rs.status = "skipped"
            rs.notes = (rs.notes + "\n" if rs.notes else "") + f"captain verdict: {verdict}"
        elif verdict == "escalate":
            rs.status = "blocked"
            rs.notes = (rs.notes + "\n" if rs.notes else "") + "escalated to admiral"
        return


def next_queued_slice(roadmap: Roadmap) -> RoadmapSlice | None:
    """Return the first queued slice whose blockers are all done."""
    done_ids = {rs.slice_id for rs in roadmap.slices if rs.status == "done"}
    for rs in roadmap.slices:
        if rs.status != "queued":
            continue
        if rs.blocked_by and not set(rs.blocked_by).issubset(done_ids):
            continue
        return rs
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def build_current_slice(
    rs: RoadmapSlice,
    *,
    app_id: str,
    repo_path: str,
    parent_slice_id: str | None = None,
    extra_context: str = "",
) -> CurrentSlice:
    """Materialize a CurrentSlice from a Roadmap entry.

    System + user prompts are kept terse here — the replanner (S8) is the
    component that builds rich, research-grounded prompts from playbook +
    scorecard + research artifacts. For S3 we just route the objective.
    """
    system = (
        "You are a careful coding agent working under a captain's direction. "
        "Make the smallest correct change that satisfies the objective. "
        "Run tests for the code you changed before declaring done. "
        "Do not introduce abstractions that weren't asked for. "
        "If the objective is ambiguous, do the obvious interpretation and "
        "describe your reasoning briefly at the end."
    )

    user = f"OBJECTIVE: {rs.objective}\n"
    if rs.phase:
        user += f"PHASE: {rs.phase}\n"
    if extra_context:
        user += f"\nCONTEXT:\n{extra_context}\n"
    user += "\nWhen done, summarize what you changed and why.\n"

    return CurrentSlice(
        slice_id=rs.slice_id if not parent_slice_id else f"{rs.slice_id}-retry",
        app_id=app_id,
        objective=rs.objective,
        system_prompt=system,
        user_prompt=user,
        repo_path=repo_path,
        parent_slice_id=parent_slice_id,
    )


# ---------------------------------------------------------------------------
# Per-app tick (the captain's main loop calls this for each app)
# ---------------------------------------------------------------------------


def captain_tick(
    ws: AppWorkspace,
    *,
    repo_path: str,
    score_delta: Callable[[CurrentSlice, SliceComplete], float | None] | None = None,
    use_baseline_scorecard: bool = True,
    auto_replan: bool = False,
) -> str | None:
    """One captain tick for one app. Returns a one-line status for logs.

    If ``score_delta`` is None and ``use_baseline_scorecard`` is True (default),
    we use a baseline-scorecard adapter: pre-slice snapshot is written at
    dispatch, post-slice score is computed at validate, delta is in pp.
    """
    if score_delta is None:
        if use_baseline_scorecard:
            from chad_captain.extras import get_extras
            from chad_captain.scorecard import make_baseline_score_delta
            score_delta = make_baseline_score_delta(
                ws.slice_baseline_path,
                repo_path,
                extras=get_extras(ws.app_id),
            )
        else:
            score_delta = _no_score_delta

    # Look up the registered app once — used by C1 (verify gate) and C2
    # (auto-push, auto-PR on roadmap_complete). Tolerated when missing so
    # tests that don't install a fake registry still drive the basic path.
    from chad_captain.apps_registry import load_registry
    reg_app = load_registry().by_id(ws.app_id)

    # C7 — stall watchdog. Before any other work, check if a slice has
    # been "in flight" (current_slice on disk, no slice_complete) past
    # its timeout + grace window. If so, synthesize a SliceComplete
    # with goose_exit_code=-9 so the validate path routes it through
    # kill_replan. This recovers the loop when goose-runner hangs.
    _maybe_watchdog_stalled_slice(ws)

    # 1. Validate any pending completion.
    completion = read_slice_complete(ws)
    if completion is not None:
        roadmap = read_roadmap(ws)
        if roadmap is None:
            # Captain has no roadmap yet — log and clear.
            append_captain_log(
                ws,
                CaptainLogEntry(
                    app_id=ws.app_id,
                    slice_id=completion.slice_id,
                    kind="validate",
                    verdict="escalate",
                    rationale="slice completed but no roadmap on file",
                ),
            )
            clear_slice_complete(ws)
            return f"completion {completion.slice_id} consumed; no roadmap → escalate"

        # We need the original slice context to know if it was a retry. The
        # protocol doesn't currently persist the issued CurrentSlice once
        # consumed; we reconstruct minimal context from the roadmap.
        rs = next((s for s in roadmap.slices if s.slice_id == completion.slice_id or s.slice_id == completion.slice_id.removesuffix("-retry")), None)
        was_retry = completion.slice_id.endswith("-retry")
        proxy_slice = CurrentSlice(
            slice_id=completion.slice_id,
            app_id=ws.app_id,
            objective=rs.objective if rs else "",
            system_prompt="",
            user_prompt="",
            repo_path=repo_path,
            parent_slice_id="parent" if was_retry else None,
        )
        result = validate_slice(complete=completion, slice_=proxy_slice, score_delta=score_delta)

        # C14 — reuse-regression guard. If the slice introduced a NEW
        # parallel package (reuse_consistency dropped from before to
        # after), override the verdict / rationale with a specific
        # parallel-package message. Live failure: PR #145 shipped two
        # parallel `entitlements.py` modules — one in top-level
        # `billing/` with TIER_RANK and one in `apps/billing/services/`
        # with TIER_FEATURE_MATRIX. reuse_consistency dropped, but the
        # generic "rubric regression" message gave the captain no
        # actionable signal. The override turns it into an explicit
        # "EXTEND existing package instead" instruction.
        if use_baseline_scorecard:
            reuse_drop = _detect_reuse_regression(ws, repo_path)
            if reuse_drop is not None:
                was_retry = completion.slice_id.endswith("-retry")
                # Always reject when a parallel package is introduced.
                # On retry, reject_hard so we don't loop on the same
                # mistake. First time, reject_retry with explicit
                # guidance.
                verdict = "reject_hard" if was_retry else "reject_retry"
                suffix = "after retry; reject hard" if was_retry else (
                    "retry — EXTEND existing package instead of parallel"
                )
                result = ValidationResult(
                    verdict=verdict,
                    rationale=(
                        f"reuse_consistency regressed {reuse_drop} — "
                        f"slice introduced parallel package; {suffix}"
                    ),
                    rubric_delta_pp=result.rubric_delta_pp,
                )

        # C1 verify gate: if the registered app has a verify_cmd, run it
        # against the repo. Goose's exit-code is local to the slice; the
        # verify gate is global ("does the project still build/test?").
        # Failure downgrades accept/soft_accept → reject_retry/reject_hard.
        if reg_app is not None:
            result = apply_verify_gate(
                result,
                is_retry=was_retry,
                repo_path=repo_path,
                verify_cmd=reg_app.verify_cmd,
                timeout_seconds=reg_app.verify_timeout_seconds,
            )

        rs_id_in_roadmap = completion.slice_id.removesuffix("-retry")
        advance_roadmap(roadmap, rs_id_in_roadmap, result.verdict)
        write_roadmap(ws, roadmap)

        append_captain_log(
            ws,
            CaptainLogEntry(
                app_id=ws.app_id,
                slice_id=completion.slice_id,
                kind="validate",
                verdict=result.verdict,
                rubric_delta_pp=result.rubric_delta_pp,
                rationale=result.rationale,
                references={"diff_path": completion.diff_path or "", "log_path": completion.log_path or ""},
            ),
        )
        clear_slice_complete(ws)
        # Baseline snapshot was specific to this completed slice — drop it.
        from chad_captain.scorecard import clear_baseline
        clear_baseline(ws.slice_baseline_path)

        # C2 — auto-push captain branch on accept/soft_accept. Idempotent;
        # subsequent pushes are fast-forward. Silent on success (per-slice
        # pushes are side-effect, not reportable events). Only log on failure
        # so admiral knows the branch is out of sync.
        if reg_app is not None and reg_app.auto_push and reg_app.captain_branch \
                and result.verdict in ("accept", "soft_accept"):
            from chad_captain.merge_facilitator import push_captain_branch
            pres = push_captain_branch(
                repo_path=repo_path, branch=reg_app.captain_branch,
            )
            if not pres.ok:
                append_captain_log(
                    ws,
                    CaptainLogEntry(
                        app_id=ws.app_id,
                        slice_id=completion.slice_id,
                        kind="escalation_raised",
                        rationale=f"auto-push failed: {pres.summary}",
                        references={
                            "branch": reg_app.captain_branch,
                            "event": "auto_push_failed",
                        },
                    ),
                )

        # Retry path — we re-queue the slice in advance_roadmap, so the
        # next dispatch step picks it up automatically.
        status = f"validate {completion.slice_id} → {result.verdict}: {result.rationale}"
    else:
        status = None

    # C8 — circuit breaker: after writing a validate entry, count consecutive
    # bad verdicts. If ≥ threshold, write pause_until and skip dispatch this
    # tick (and subsequent ticks until pause expires).
    if completion is not None and reg_app is not None:
        _maybe_trip_circuit_breaker(ws, reg_app)
        # C12 — low-yield streak: detect rubric saturation / no-op slice
        # spinning by counting consecutive soft_accepts with rubric_delta_pp
        # below the noise floor. Pauses the same way the circuit breaker
        # does, but signals "the rubric isn't measuring this work" rather
        # than "the work is breaking things."
        _maybe_trip_low_yield_streak(ws, reg_app)

    # C8 pause gate — applies only to dispatch (not validation, which
    # we always want to process).
    if _is_paused(ws):
        return (status + "; " if status else "") + "paused (circuit breaker)"

    # 2. Dispatch next slice if no current_slice in flight.
    if not ws.current_slice_path.exists():
        roadmap = read_roadmap(ws)
        if roadmap is None:
            if auto_replan:
                from chad_captain.replanner import replan
                roadmap = replan(ws, repo_path, trigger="initial")
            else:
                return status or "no roadmap"
        rs = next_queued_slice(roadmap)
        if rs is None:
            # C2 — roadmap_complete: emit event + auto-PR if configured.
            # When auto_open_pr is set, captain does NOT auto-replan here —
            # admiral merges first, then NEXT tick replans against new main.
            from chad_captain.merge_facilitator import is_roadmap_complete
            if is_roadmap_complete(roadmap):
                # C4: if a captain-opened PR is already merged on origin,
                # run the post-merge cycle (refresh main, drop stale branch,
                # clear roadmap so next tick replans). This precedes the
                # roadmap_complete handler so we don't try to re-open a PR
                # for a branch we're about to delete.
                merged = _maybe_handle_pr_merge(ws, repo_path, reg_app)
                if merged:
                    return (status + "; " if status else "") + "post_merge_cycle"
                _handle_roadmap_complete(ws, repo_path, roadmap, reg_app)
                if reg_app is not None and reg_app.auto_open_pr:
                    return (status + "; " if status else "") + "roadmap_complete (PR opened)"

            if auto_replan:
                from chad_captain.replanner import replan
                roadmap = replan(ws, repo_path, trigger="exhausted")
                rs = next_queued_slice(roadmap)
            if rs is None:
                return (status + "; " if status else "") + "roadmap exhausted (replan needed)"

        # C2 branch auto-create: before dispatching, ensure the captain
        # branch is checked out. Skipped when no captain_branch configured
        # (back-compat: dispatch on whatever branch admiral set up).
        if reg_app is not None and reg_app.captain_branch:
            from chad_captain.merge_facilitator import ensure_captain_branch
            br = ensure_captain_branch(
                repo_path=repo_path,
                branch=reg_app.captain_branch,
                base_branch=reg_app.pr_base_branch,
            )
            if not br.ok:
                # Don't dispatch onto the wrong branch — log + return without
                # writing current_slice. Admiral can fix the worktree and tick again.
                append_captain_log(
                    ws,
                    CaptainLogEntry(
                        app_id=ws.app_id, slice_id=rs.slice_id,
                        kind="escalation_raised",
                        rationale=f"branch setup failed: {br.summary}",
                        references={
                            "event": "branch_setup_failed",
                            "branch": reg_app.captain_branch,
                            "base": reg_app.pr_base_branch,
                        },
                    ),
                )
                return (status + "; " if status else "") + f"branch setup failed: {br.summary}"

            # C3 branch baseline: snapshot the scorecard once per
            # branch lifetime (created or resumed-with-no-baseline).
            # Roadmap_complete reads it back to embed before/after
            # delta in the PR body. Idempotent — never overwrites
            # an existing baseline. Cleared on PR open.
            if use_baseline_scorecard and not ws.branch_baseline_path.exists():
                try:
                    from chad_captain.extras import get_extras
                    from chad_captain.scorecard import score_repo, write_baseline
                    write_baseline(
                        ws.branch_baseline_path,
                        score_repo(repo_path, extras=get_extras(ws.app_id)),
                    )
                except Exception as e:
                    logger.warning(
                        "branch baseline write failed for %s: %s", ws.app_id, e,
                    )

        # Detect retry: is this a re-queue of a slice we just rejected?
        log_tail = list(_recent_validate_for(ws, rs.slice_id, limit=5))
        is_retry = any(e.verdict in ("reject_retry", "kill_replan") for e in log_tail)
        parent_id = rs.slice_id if is_retry else None

        new_slice = build_current_slice(
            rs,
            app_id=ws.app_id,
            repo_path=repo_path,
            parent_slice_id=parent_id,
        )
        write_current_slice(ws, new_slice)

        # Snapshot pre-slice scorecard so we can compute a real delta when
        # the slice completes. Best-effort — failures shouldn't block dispatch.
        if use_baseline_scorecard:
            try:
                from chad_captain.extras import get_extras
                from chad_captain.scorecard import score_repo, write_baseline
                write_baseline(
                    ws.slice_baseline_path,
                    score_repo(repo_path, extras=get_extras(ws.app_id)),
                )
            except Exception as e:
                logger.warning("baseline scorecard write failed for %s: %s", ws.app_id, e)

        # Mark in-flight in roadmap.
        rs.status = "in_flight"
        write_roadmap(ws, roadmap)

        append_captain_log(
            ws,
            CaptainLogEntry(
                app_id=ws.app_id,
                slice_id=new_slice.slice_id,
                kind="dispatch",
                rationale=f"dispatched: {rs.objective}",
                references={"is_retry": str(is_retry)},
            ),
        )
        return (status + "; " if status else "") + f"dispatched {new_slice.slice_id}"

    return status or "in flight"


def _recent_validate_for(ws: AppWorkspace, slice_id: str, limit: int = 5):
    from chad_captain.protocol import read_captain_log

    entries = read_captain_log(ws, limit=50)
    matches = [e for e in entries if e.kind == "validate" and (e.slice_id == slice_id or (e.slice_id or "").startswith(slice_id))]
    return matches[-limit:]


# --- C9 pending-merge detection ---


# Patterns gh emits when CI / required checks haven't completed yet. These
# are NOT real failures — captain just retries on the next tick. Conservative
# match (broad lower-case substring) so we err on "treat as pending" rather
# than spam escalations.
_PENDING_MERGE_PATTERNS = (
    "unstable status",
    "is not in a state to allow merging",
    "is not in a state to allow checks",
    "expected — waiting",
    "required status check",
    "checks pending",
    "checks_pending",
    "status_pending",
)


def _is_pending_merge_failure(summary: str) -> bool:
    s = (summary or "").lower()
    return any(p in s for p in _PENDING_MERGE_PATTERNS)


def _recent_auto_merge_failure(ws: AppWorkspace, *, minutes: int) -> bool:
    """True iff the captain log already has an auto_merge_failed escalation
    within the last ``minutes`` (used to dedup tick-by-tick re-emissions
    when admiral hasn't resolved the underlying issue yet)."""
    from chad_captain.protocol import read_captain_log
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    log = read_captain_log(ws, limit=50)
    for e in reversed(log):
        if e.kind != "escalation_raised":
            continue
        if (e.references or {}).get("event") != "auto_merge_failed":
            continue
        try:
            when = datetime.fromisoformat(e.ts)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when >= cutoff:
            return True
        return False  # older than window — no recent dup
    return False


# --- C8 circuit breaker helpers ---


_BAD_VERDICTS = {"reject_hard", "revert", "escalate"}


def _is_paused(ws: AppWorkspace) -> bool:
    """True iff pause_until.json exists AND wall-clock now < its timestamp."""
    p = ws.pause_until_path
    if not p.exists():
        return False
    try:
        import json as _json
        data = _json.loads(p.read_text())
        until = datetime.fromisoformat(data["until"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except (ValueError, KeyError, OSError):
        # Malformed pause file — clear it so the loop can resume rather
        # than wedging forever on bad on-disk state.
        try:
            p.unlink()
        except OSError:
            pass
        return False
    if datetime.now(timezone.utc) >= until:
        try:
            p.unlink()  # auto-clear expired pause
        except OSError:
            pass
        return False
    return True


def _write_pause_until(ws: AppWorkspace, until_iso: str) -> None:
    import json as _json
    ws.pause_until_path.parent.mkdir(parents=True, exist_ok=True)
    ws.pause_until_path.write_text(_json.dumps({"until": until_iso}))


def _maybe_trip_circuit_breaker(ws: AppWorkspace, reg_app) -> None:
    """If the most recent N validate entries (N = threshold) are all bad
    verdicts, write pause_until and log circuit_breaker_tripped. Idempotent —
    re-running on an already-paused app is a no-op (pause file just gets
    rewritten with the new deadline)."""
    threshold = max(1, int(reg_app.circuit_breaker_threshold))
    pause_min = max(1, int(reg_app.circuit_breaker_pause_minutes))

    from chad_captain.protocol import read_captain_log
    log = read_captain_log(ws, limit=threshold * 4)
    validates = [e for e in log if e.kind == "validate"]
    recent = validates[-threshold:]
    if len(recent) < threshold:
        return
    if not all(e.verdict in _BAD_VERDICTS for e in recent):
        return

    until = datetime.now(timezone.utc) + timedelta(minutes=pause_min)
    _write_pause_until(ws, until.isoformat())
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id, slice_id=None,
            kind="escalation_raised",
            rationale=(
                f"circuit breaker tripped: {threshold} consecutive bad "
                f"verdicts ({', '.join(e.verdict or '?' for e in recent)}); "
                f"dispatch paused for {pause_min}m"
            ),
            references={
                "event": "circuit_breaker_tripped",
                "threshold": str(threshold),
                "pause_minutes": str(pause_min),
                "pause_until": until.isoformat(),
            },
        ),
    )


_LOW_YIELD_PP_FLOOR = 0.5


# Objective phrases that require real persistence. Conservative — false
# positives are OK (worker just makes it persistent), false negatives
# silently ship in-memory features.
_PERSISTENCE_OBJECTIVE_RE = re.compile(
    r"\b(log\s+(?:every|each|all|the|to)|"
    r"track(?:ing|ed)?|audit\s+(?:trail|log)|"
    r"persist(?:ed|s)?|store\s+(?:a|the|all|each)|"
    r"record\s+(?:every|each|all|the))\b",
    re.IGNORECASE,
)
# Detect module-level mutable state at file scope (start of line, no
# indent). Matches lines added in the diff (`+` prefix).
_MODULE_LEVEL_STATE_RE = re.compile(
    r"^\+(_[A-Z][A-Z0-9_]*)\s*(?::\s*[^=]+)?\s*=\s*(\[\s*\]|\{\s*\}|set\(\s*\))\s*$",
    re.MULTILINE,
)


def _detect_reuse_regression(ws: AppWorkspace, repo_path: str) -> str | None:
    """Compare the cached slice baseline scorecard's reuse_consistency
    score to the live repo's. If live is materially lower (≥0.05 drop,
    one new duplicate package), return a short string describing it.

    Returns None if no baseline is available, scorecard has no
    reuse_consistency dim, or no regression."""
    try:
        from chad_captain.scorecard import (
            read_baseline, score_repo,
        )
        from chad_captain.extras import get_extras
    except ImportError:
        return None
    before = read_baseline(ws.slice_baseline_path)
    if before is None:
        return None
    after = score_repo(repo_path, extras=get_extras(ws.app_id))
    before_dim = before.by_name("reuse_consistency")
    after_dim = after.by_name("reuse_consistency")
    if before_dim is None or after_dim is None:
        return None
    drop = before_dim.score - after_dim.score
    if drop < 0.05:
        return None
    # Identify which name is new (in after but not before).
    before_names = {d["name"] for d in (before_dim.detail or {}).get("duplicates", [])}
    after_names = {d["name"] for d in (after_dim.detail or {}).get("duplicates", [])}
    new_dups = sorted(after_names - before_names)
    if new_dups:
        return f"-{drop:.2f} (new parallel package: {', '.join(new_dups)})"
    return f"-{drop:.2f}"


def _detect_persistence_violation(
    slice_: CurrentSlice, complete: SliceComplete,
) -> str | None:
    """Return a short string describing the violation if the slice's
    objective implies persistence and the diff introduces module-level
    mutable state. Else None.

    See C13 comment in validate_slice for context."""
    objective = (slice_.objective or "") + " " + (slice_.user_prompt or "")
    if not _PERSISTENCE_OBJECTIVE_RE.search(objective):
        return None

    diff_path_str = complete.diff_path
    if not diff_path_str:
        return None
    try:
        diff_text = Path(diff_path_str).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    matches = _MODULE_LEVEL_STATE_RE.findall(diff_text)
    if not matches:
        return None
    # First match's variable name is the most actionable signal.
    first_var = matches[0][0]
    return f"module-level mutable state introduced: {first_var}"


def _maybe_trip_low_yield_streak(ws: AppWorkspace, reg_app) -> None:
    """If the most recent N validate entries are all `soft_accept` with
    abs(rubric_delta_pp) below the noise floor, pause dispatch and log a
    `low_yield_streak` escalation. This catches two failure modes:
      1. Rubric saturated — every dim is pinned at 1.0, no slice can move it.
      2. Replanner spinning on cosmetic work that doesn't affect any dim.

    Re-trips are guarded: if the most recent log entry is already a
    low_yield_streak escalation, this is a no-op until the streak breaks
    (i.e. an `accept` or non-soft `validate` shifts the trailing window)."""
    threshold = max(1, int(getattr(reg_app, "low_yield_streak_threshold", 5)))
    pause_min = max(1, int(getattr(reg_app, "low_yield_pause_minutes", 30)))

    from chad_captain.protocol import read_captain_log
    # Pull a generous window so we can find the most recent escalation
    # AND threshold validates after it.
    log = read_captain_log(ws, limit=threshold * 8)

    # Reset window: only consider validates AFTER the most recent
    # low_yield_streak escalation. Without this reset, every time the
    # pause expires the trailing N validates still includes the
    # already-counted ones → guard re-trips on the same streak forever.
    # Live failure: author-toolkit tripped 4 times in a row at 30min
    # intervals (03:54, 04:22, 04:54, 05:00) — pause expires, one
    # validate fires, immediately re-trips, captain stuck.
    last_escalation_idx = -1
    for i in range(len(log) - 1, -1, -1):
        e = log[i]
        if (e.kind == "escalation_raised"
                and (e.references or {}).get("event") == "low_yield_streak"):
            last_escalation_idx = i
            break
    fresh_log = log[last_escalation_idx + 1 :] if last_escalation_idx >= 0 else log

    validates = [e for e in fresh_log if e.kind == "validate"]
    recent = validates[-threshold:]
    if len(recent) < threshold:
        return
    if not all(
        e.verdict == "soft_accept"
        and e.rubric_delta_pp is not None
        and abs(e.rubric_delta_pp) < _LOW_YIELD_PP_FLOOR
        for e in recent
    ):
        return

    until = datetime.now(timezone.utc) + timedelta(minutes=pause_min)
    _write_pause_until(ws, until.isoformat())
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id, slice_id=None,
            kind="escalation_raised",
            rationale=(
                f"low-yield streak: {threshold} consecutive soft_accepts "
                f"with abs(delta) < {_LOW_YIELD_PP_FLOOR}pp — rubric is "
                f"saturated or dispatch is generating no-op slices; "
                f"dispatch paused for {pause_min}m"
            ),
            references={
                "event": "low_yield_streak",
                "threshold": str(threshold),
                "pause_minutes": str(pause_min),
                "pause_until": until.isoformat(),
            },
        ),
    )


def clear_pause(ws: AppWorkspace) -> bool:
    """Manually clear the pause marker. Returns True iff a pause existed."""
    if ws.pause_until_path.exists():
        try:
            ws.pause_until_path.unlink()
            return True
        except OSError:
            return False
    return False


# Grace window past CurrentSlice.timeout_seconds before declaring a stall.
# goose-runner's own watchdog should kill at timeout_seconds; if it didn't,
# the runner itself has likely hung (tool-call loop, network deadlock, etc).
# 5min grace is enough to cover slow shutdowns without blocking the loop.
_STALL_GRACE_SECONDS = 300


def _maybe_watchdog_stalled_slice(ws: AppWorkspace) -> None:
    """If current_slice has been in flight past timeout + grace, synthesize
    a SliceComplete(goose_exit_code=-9) so the validator's kill_replan path
    fires and the slice gets re-queued. Idempotent: if a slice_complete
    already exists, do nothing (validator handles it normally).
    """
    if read_slice_complete(ws) is not None:
        return  # validator will handle the real completion this tick
    cs = read_current_slice(ws)
    if cs is None or not cs.started_at:
        return  # no in-flight slice (or runner hasn't picked up yet)

    try:
        started = datetime.fromisoformat(cs.started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("watchdog: bad started_at %r on %s", cs.started_at, ws.app_id)
        return

    age_s = (datetime.now(timezone.utc) - started).total_seconds()
    deadline_s = cs.timeout_seconds + _STALL_GRACE_SECONDS
    if age_s < deadline_s:
        return  # still within timeout + grace window

    logger.warning(
        "stall watchdog: %s slice %s in flight %.0fs (limit %ds); killing.",
        ws.app_id, cs.slice_id, age_s, deadline_s,
    )

    from chad_captain.protocol import (
        clear_current_slice,
        write_slice_complete,
    )

    write_slice_complete(
        ws,
        SliceComplete(
            slice_id=cs.slice_id,
            app_id=ws.app_id,
            duration_seconds=age_s,
            goose_exit_code=-9,
            summary=(
                f"watchdog: slice in flight {age_s:.0f}s, exceeded "
                f"timeout {cs.timeout_seconds}s + grace {_STALL_GRACE_SECONDS}s"
            ),
            failure_tail="captain stall watchdog killed the slice",
        ),
    )
    clear_current_slice(ws)
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id,
            slice_id=cs.slice_id,
            kind="stall_detected",
            rationale=(
                f"slice in flight {age_s:.0f}s past limit "
                f"({cs.timeout_seconds}s + {_STALL_GRACE_SECONDS}s grace); "
                "synthesized SliceComplete(-9) for kill_replan"
            ),
            references={
                "started_at": cs.started_at,
                "age_seconds": f"{age_s:.0f}",
                "limit_seconds": str(deadline_s),
            },
        ),
    )


def _maybe_self_merge_pr(
    ws: AppWorkspace,
    repo_path: str,
    reg_app,  # RegisteredApp
    sc_before: dict | None,
    sc_after: dict | None,
    pr_url: str,
) -> None:
    """Captain self-merge guard. Called after a successful auto-PR open.

    Safety gates (any failure → log escalation, leave PR open):
      1. Aggregate scorecard delta from branch baseline must clear
         ``reg_app.auto_merge_min_delta`` (default 0.0 — no regression).
      2. ``gh pr merge`` must succeed (handles branch protection,
         conflicts, required CI). Non-zero exit → admiral resolves.

    On success: do NOT log pull_request_merged here — the next captain
    tick's _maybe_handle_pr_merge will detect the MERGED state via
    ``gh pr view`` and run the post-merge cycle (refresh main, drop
    local branch, clear roadmap). One canonical detection path.
    """
    # Gate 1: scorecard delta non-regression.
    if sc_before and sc_after:
        delta = float(sc_after.get("aggregate", 0.0)) - float(sc_before.get("aggregate", 0.0))
        if delta < reg_app.auto_merge_min_delta:
            append_captain_log(
                ws,
                CaptainLogEntry(
                    app_id=ws.app_id, slice_id=None, kind="escalation_raised",
                    rationale=(
                        f"auto_merge blocked: scorecard delta {delta:+.4f} below "
                        f"min_delta {reg_app.auto_merge_min_delta:+.4f}; "
                        "admiral review required"
                    ),
                    references={
                        "event": "auto_merge_blocked",
                        "pr_url": pr_url,
                        "branch": reg_app.captain_branch or "",
                        "delta": f"{delta:+.4f}",
                    },
                ),
            )
            return

    # Gate 2: actually merge. gh enforces branch protection / required
    # checks / conflicts — non-zero exit means admiral handles it.
    from chad_captain.merge_facilitator import auto_merge_pr
    res = auto_merge_pr(
        repo_path=repo_path,
        head=reg_app.captain_branch or "",
        method=reg_app.auto_merge_method,
        delete_branch=True,
    )
    if not res.ok:
        # C9: distinguish CI-pending failures from hard failures.
        # Pending = silent (next tick retries; pending IS not a failure).
        # Hard = escalate, but dedup within 30min so we don't spam the log
        # every 5min as the captain re-enters _handle_roadmap_complete.
        if _is_pending_merge_failure(res.summary):
            return
        if _recent_auto_merge_failure(ws, minutes=30):
            return  # already escalated within window
        append_captain_log(
            ws,
            CaptainLogEntry(
                app_id=ws.app_id, slice_id=None, kind="escalation_raised",
                rationale=f"auto_merge failed: {res.summary}",
                references={
                    "event": "auto_merge_failed",
                    "pr_url": pr_url,
                    "branch": reg_app.captain_branch or "",
                },
            ),
        )
        return
    # Success — log a hint event so the dashboard knows captain initiated
    # the merge (vs admiral). Final pull_request_merged + post_merge_cycle
    # come from _maybe_handle_pr_merge on the next tick (single source of
    # truth for "the PR is now merged on origin").
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id, slice_id=None, kind="pull_request_opened",
            rationale=(
                f"captain self-merged PR ({reg_app.auto_merge_method}); "
                "post-merge cycle on next tick"
            ),
            references={
                "event": "auto_merge_initiated",
                "pr_url": pr_url,
                "branch": reg_app.captain_branch or "",
                "merged_by": "captain",
                "method": reg_app.auto_merge_method,
            },
        ),
    )


def _maybe_handle_pr_merge(
    ws: AppWorkspace,
    repo_path: str,
    reg_app,  # RegisteredApp | None
) -> bool:
    """If the captain has a pull_request_opened event since its last
    pull_request_merged event, poll the PR's state. On MERGED:
      - emit pull_request_merged log
      - refresh local base branch (fetch + checkout + ff-pull)
      - delete the stale captain branch (it's now in main)
      - clear current_slice / slice_complete / roadmap so the next tick
        replans against the freshly-merged main
      - emit post_merge_cycle log

    Returns True iff the post-merge cycle ran. False = nothing to do
    (no PR, PR not merged, gh lookup failed). All errors tolerated.
    """
    if reg_app is None or not reg_app.captain_branch:
        return False

    # Find the most recent pull_request_opened with no later merged event.
    from chad_captain.protocol import read_captain_log
    log = read_captain_log(ws, limit=200)
    pending_pr_url: str | None = None
    for entry in reversed(log):
        if entry.kind == "pull_request_merged":
            break  # we already handled the latest PR
        if entry.kind == "pull_request_opened":
            pending_pr_url = (entry.references or {}).get("pr_url") or None
            break
    if not pending_pr_url:
        return False

    from chad_captain.merge_facilitator import (
        delete_local_branch,
        get_pr_state,
        refresh_base_branch,
    )

    state, raw = get_pr_state(
        repo_path=repo_path, head=reg_app.captain_branch,
    )
    if state != "MERGED":
        return False

    merge_commit = (raw or {}).get("mergeCommit") or {}
    merged_at = (raw or {}).get("mergedAt") or ""
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id, slice_id=None,
            kind="pull_request_merged",
            rationale=f"PR merged at {merged_at or 'unknown'}",
            references={
                "pr_url": pending_pr_url,
                "branch": reg_app.captain_branch,
                "merge_sha": (merge_commit or {}).get("oid", ""),
            },
        ),
    )

    # Refresh main, then drop the stale captain branch. Both are
    # best-effort — failures escalate but don't block the next tick.
    rb = refresh_base_branch(
        repo_path=repo_path, base_branch=reg_app.pr_base_branch,
    )
    if not rb.ok:
        append_captain_log(
            ws,
            CaptainLogEntry(
                app_id=ws.app_id, slice_id=None, kind="escalation_raised",
                rationale=f"post-merge base refresh failed: {rb.summary}",
                references={"event": "post_merge_refresh_failed"},
            ),
        )
    else:
        # We're now on base; safe to delete the stale captain branch.
        db = delete_local_branch(
            repo_path=repo_path, branch=reg_app.captain_branch,
        )
        if not db.ok:
            append_captain_log(
                ws,
                CaptainLogEntry(
                    app_id=ws.app_id, slice_id=None, kind="escalation_raised",
                    rationale=f"local branch delete failed: {db.summary}",
                    references={
                        "event": "post_merge_branch_delete_failed",
                        "branch": reg_app.captain_branch,
                    },
                ),
            )

    # Clear roadmap + slice state so next tick starts a fresh cycle.
    # Branch baseline already cleared on PR open; clear again defensively.
    try:
        if ws.roadmap_path.exists():
            ws.roadmap_path.unlink()
        if ws.current_slice_path.exists():
            ws.current_slice_path.unlink()
        if ws.slice_complete_path.exists():
            ws.slice_complete_path.unlink()
        if ws.branch_baseline_path.exists():
            ws.branch_baseline_path.unlink()
    except OSError as e:
        logger.warning("post_merge state clear failed for %s: %s", ws.app_id, e)

    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id, slice_id=None,
            kind="post_merge_cycle",
            rationale="local main refreshed, captain branch cleaned, roadmap cleared",
            references={"event": "post_merge_cycle"},
        ),
    )

    # C10 — post-merge verify gate. After captain self-merges and refreshes
    # main, run reg_app.verify_cmd against the freshly-merged main. If it
    # fails (with flake retries), the merge broke main → log critical
    # escalation + trip circuit breaker so captain stops dispatching new
    # work until admiral fixes main.
    _post_merge_verify(ws, repo_path, reg_app, pending_pr_url, merge_commit)
    return True


def _post_merge_verify(
    ws: AppWorkspace,
    repo_path: str,
    reg_app,  # RegisteredApp
    pr_url: str,
    merge_commit: dict,
) -> None:
    """Run verify_cmd against fresh main; if it fails after flake retries,
    mark main as broken and trip the circuit breaker.

    Why no auto-revert in v1: reverting requires either pushing directly
    to main (destructive) or opening a revert PR (non-trivial). The high-
    severity escalation + circuit-breaker pause stops captain from making
    things worse. Auto-revert is a v2 enhancement.
    """
    if not reg_app.verify_cmd:
        return

    last_summary = ""
    for attempt in range(1, 4):  # 3 attempts to absorb flakes
        passed, summary = run_verify_gate(
            repo_path=repo_path,
            verify_cmd=reg_app.verify_cmd,
            timeout_seconds=reg_app.verify_timeout_seconds,
        )
        if passed:
            return  # main is healthy
        last_summary = summary
        logger.warning(
            "post-merge verify attempt %d/3 failed for %s: %s",
            attempt, ws.app_id, summary,
        )

    # All 3 attempts failed — main is broken.
    until = datetime.now(timezone.utc) + timedelta(
        minutes=reg_app.circuit_breaker_pause_minutes,
    )
    _write_pause_until(ws, until.isoformat())
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id, slice_id=None, kind="escalation_raised",
            rationale=(
                f"POST-MERGE VERIFY FAILED — main is broken. verify_cmd "
                f"failed 3 attempts: {last_summary}; dispatch paused for "
                f"{reg_app.circuit_breaker_pause_minutes}m"
            ),
            references={
                "event": "post_merge_verify_failed",
                "pr_url": pr_url,
                "merge_sha": (merge_commit or {}).get("oid", ""),
                "verify_cmd": reg_app.verify_cmd,
                "pause_until": until.isoformat(),
                "severity": "critical",
            },
        ),
    )


def _handle_roadmap_complete(
    ws: AppWorkspace,
    repo_path: str,
    roadmap: Roadmap,
    reg_app,  # RegisteredApp | None
) -> None:
    """Emit roadmap_complete event; push branch + open PR if configured.

    Idempotent — re-running on an already-complete roadmap may try to push
    again (no-op fast-forward) and re-open a PR (gh detects existing PR
    and the merge_facilitator surfaces it as success). Safe.
    """
    # Always emit the roadmap_complete log entry, even when auto_open_pr is
    # off — admiral observes via dashboard / log tail and can take manual action.
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id,
            slice_id=None,
            kind="roadmap_complete",
            rationale=(
                f"{len(roadmap.slices)} slices reached terminal state "
                f"({sum(1 for s in roadmap.slices if s.status == 'done')} done, "
                f"{sum(1 for s in roadmap.slices if s.status == 'skipped')} skipped)"
            ),
            references={"event": "roadmap_complete"},
        ),
    )

    if reg_app is None or not reg_app.auto_open_pr:
        return
    if not reg_app.captain_branch:
        append_captain_log(
            ws,
            CaptainLogEntry(
                app_id=ws.app_id, slice_id=None, kind="escalation_raised",
                rationale="auto_open_pr=true but captain_branch is unset; cannot open PR",
                references={"event": "roadmap_complete"},
            ),
        )
        return

    from chad_captain.merge_facilitator import (
        format_pr_body,
        format_pr_title,
        open_pull_request,
        push_captain_branch,
    )

    # Push first — PR creation requires the branch on origin.
    push_res = push_captain_branch(
        repo_path=repo_path, branch=reg_app.captain_branch,
    )
    if not push_res.ok:
        append_captain_log(
            ws,
            CaptainLogEntry(
                app_id=ws.app_id, slice_id=None, kind="escalation_raised",
                rationale=f"roadmap_complete push failed: {push_res.summary}",
                references={"event": "roadmap_complete", "branch": reg_app.captain_branch},
            ),
        )
        return

    # C3: load branch baseline + score current repo so the PR body
    # embeds an aggregate scorecard delta. Best-effort — failures
    # mean we ship the PR without the section, not block on it.
    sc_before: dict | None = None
    sc_after: dict | None = None
    try:
        from chad_captain.extras import get_extras
        from chad_captain.scorecard import read_baseline, score_repo
        before = read_baseline(ws.branch_baseline_path)
        if before is not None:
            after = score_repo(repo_path, extras=get_extras(ws.app_id))
            sc_before = before.model_dump()
            sc_after = after.model_dump()
    except Exception as e:
        logger.warning(
            "branch scorecard delta failed for %s: %s", ws.app_id, e,
        )

    body = format_pr_body(
        app_id=ws.app_id,
        roadmap=roadmap,
        scorecard_before=sc_before,
        scorecard_after=sc_after,
        verify_cmd=reg_app.verify_cmd,
    )
    title = format_pr_title(app_id=ws.app_id, roadmap=roadmap)
    pr_res = open_pull_request(
        repo_path=repo_path,
        base=reg_app.pr_base_branch,
        head=reg_app.captain_branch,
        title=title,
        body=body,
        # Auto-merge requires a non-draft PR (`gh pr merge` fails on drafts
        # with "Pull Request is still a draft"). Open ready-for-review when
        # the registry has auto_merge=True so the immediate self-merge
        # attempt is actually mergeable.
        draft=not reg_app.auto_merge,
    )
    # Clear the branch baseline only on successful PR open so a
    # crash mid-handler still has the snapshot for the next attempt.
    if pr_res.ok:
        try:
            from chad_captain.scorecard import clear_baseline
            clear_baseline(ws.branch_baseline_path)
        except Exception as e:
            logger.warning(
                "branch baseline clear failed for %s: %s", ws.app_id, e,
            )

        # C5: captain self-merge gate. Autonomous fleet → captain merges
        # its own PRs as long as safety gates hold. Gate fails → leave PR
        # open and escalate; admiral resolves manually.
        if reg_app.auto_merge:
            _maybe_self_merge_pr(
                ws, repo_path, reg_app, sc_before, sc_after, pr_res.stdout.strip(),
            )
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id,
            slice_id=None,
            kind="pull_request_opened" if pr_res.ok else "escalation_raised",
            rationale=(
                f"PR opened: {pr_res.stdout.strip()}" if pr_res.ok
                else f"PR open failed: {pr_res.summary}"
            ),
            references={
                "event": "roadmap_complete_pr",
                "branch": reg_app.captain_branch,
                "base": reg_app.pr_base_branch,
                "pr_url": pr_res.stdout.strip() if pr_res.ok else "",
            },
        ),
    )


__all__ = [
    "ValidationResult",
    "validate_slice",
    "advance_roadmap",
    "next_queued_slice",
    "build_current_slice",
    "captain_tick",
]

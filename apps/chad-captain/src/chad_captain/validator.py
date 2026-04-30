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
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
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

    delta = score_delta(slice_, complete)

    if delta is None:
        # No rubric run available — accept on clean exit + files modified.
        return ValidationResult(
            verdict="accept",
            rationale="clean exit, files modified, no rubric delta available",
        )

    if delta < 0:
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

    if delta >= 0.5:
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
                _handle_roadmap_complete(ws, repo_path, roadmap, reg_app)
                if reg_app is not None and reg_app.auto_open_pr:
                    return (status + "; " if status else "") + "roadmap_complete (PR opened)"

            if auto_replan:
                from chad_captain.replanner import replan
                roadmap = replan(ws, repo_path, trigger="exhausted")
                rs = next_queued_slice(roadmap)
            if rs is None:
                return (status + "; " if status else "") + "roadmap exhausted (replan needed)"

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

    body = format_pr_body(
        app_id=ws.app_id,
        roadmap=roadmap,
        verify_cmd=reg_app.verify_cmd,
    )
    title = format_pr_title(app_id=ws.app_id, roadmap=roadmap)
    pr_res = open_pull_request(
        repo_path=repo_path,
        base=reg_app.pr_base_branch,
        head=reg_app.captain_branch,
        title=title,
        body=body,
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

"""Captain validator + dispatcher tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from chad_captain.protocol import (
    AppWorkspace,
    CurrentSlice,
    Roadmap,
    RoadmapSlice,
    SliceComplete,
    read_captain_log,
    read_current_slice,
    read_roadmap,
    write_roadmap,
    write_slice_complete,
)
from chad_captain.validator import (
    ValidationResult,
    advance_roadmap,
    apply_verify_gate,
    build_current_slice,
    captain_tick,
    next_queued_slice,
    run_verify_gate,
    validate_slice,
)


# ---------------------------------------------------------------------------
# validate_slice — decision rubric
# ---------------------------------------------------------------------------


def _slice(slice_id: str = "s1", parent: str | None = None) -> CurrentSlice:
    return CurrentSlice(
        slice_id=slice_id,
        app_id="test-app",
        objective="o",
        system_prompt="s",
        user_prompt="u",
        repo_path="/tmp/r",
        parent_slice_id=parent,
    )


def _complete(
    *,
    slice_id: str = "s1",
    exit_code: int = 0,
    files: list[str] | None = None,
    cheats: list[str] | None = None,
    summary: str = "ok",
) -> SliceComplete:
    return SliceComplete(
        slice_id=slice_id,
        app_id="test-app",
        duration_seconds=10.0,
        goose_exit_code=exit_code,
        summary=summary,
        files_changed=files if files is not None else ["README.md"],
        cheat_flags=cheats or [],
    )


def test_persistence_required_rejects_in_memory_log(tmp_path: Path) -> None:
    """Live failure: PR #144 shipped author_toolkit/agent/decision_log.py
    with `_DECISIONS: list[AgentDecision] = []` — module-level mutable
    state, lost on restart, claimed to 'log every cover-variant decision'.
    Validator now catches the mismatch."""
    diff_path = tmp_path / "slice.diff"
    diff_path.write_text(
        "diff --git a/x/decision_log.py b/x/decision_log.py\n"
        "+++ b/x/decision_log.py\n"
        "@@ +0,0 @@\n"
        "+from dataclasses import dataclass\n"
        "+\n"
        "+_DECISIONS: list = []\n"
        "+\n"
        "+def log_decision(t, k, p):\n"
        "+    _DECISIONS.append((t, k, p))\n"
    )
    sl = CurrentSlice(
        slice_id="s1", app_id="t", system_prompt="s", user_prompt="u",
        objective="Log every cover-variant approval decision with rationale and tenant",
        repo_path="/tmp/r",
    )
    cm = SliceComplete(
        slice_id="s1", app_id="t", duration_seconds=5,
        goose_exit_code=0, summary="ok", files_changed=["x/decision_log.py"],
        diff_path=str(diff_path),
    )
    r = validate_slice(complete=cm, slice_=sl)
    assert r.verdict == "reject_retry"
    assert "in-memory" in r.rationale
    assert "_DECISIONS" in r.rationale


def test_persistence_required_passes_when_using_db(tmp_path: Path) -> None:
    """Same objective phrasing, but the diff uses a Django model — no
    module-level state, persistence is real. Don't reject."""
    diff_path = tmp_path / "slice.diff"
    diff_path.write_text(
        "diff --git a/x/models.py b/x/models.py\n"
        "+++ b/x/models.py\n"
        "+from django.db import models\n"
        "+\n"
        "+class AgentDecision(models.Model):\n"
        "+    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE)\n"
        "+    rationale = models.TextField()\n"
    )
    sl = CurrentSlice(
        slice_id="s1", app_id="t", system_prompt="s", user_prompt="u",
        objective="Log every cover-variant approval decision with rationale and tenant",
        repo_path="/tmp/r",
    )
    cm = SliceComplete(
        slice_id="s1", app_id="t", duration_seconds=5,
        goose_exit_code=0, summary="ok", files_changed=["x/models.py"],
        diff_path=str(diff_path),
    )
    r = validate_slice(complete=cm, slice_=sl)
    # Without rubric scorer this falls into the "no delta" path → accept.
    assert r.verdict == "accept"


def test_persistence_required_inert_for_unrelated_objectives(tmp_path: Path) -> None:
    """Objective not about logging/tracking — even if module-level
    state is added, don't reject. The dim is a targeted gate, not a
    blanket ban."""
    diff_path = tmp_path / "slice.diff"
    diff_path.write_text(
        "+++ b/x/cache.py\n"
        "+_CACHE: dict = {}\n"  # legit cache pattern
    )
    sl = CurrentSlice(
        slice_id="s1", app_id="t", system_prompt="s", user_prompt="u",
        objective="Add a fast in-memory cache for parsed API responses",
        repo_path="/tmp/r",
    )
    cm = SliceComplete(
        slice_id="s1", app_id="t", duration_seconds=5,
        goose_exit_code=0, summary="ok", files_changed=["x/cache.py"],
        diff_path=str(diff_path),
    )
    r = validate_slice(complete=cm, slice_=sl)
    # Falls through to no-delta accept path
    assert r.verdict == "accept"


def test_persistence_required_retries_then_hard_rejects(tmp_path: Path) -> None:
    diff_path = tmp_path / "slice.diff"
    diff_path.write_text(
        "+++ b/x/log.py\n+_AUDIT: list = []\n"
    )
    sl = CurrentSlice(
        slice_id="s1-retry", app_id="t", system_prompt="s", user_prompt="u",
        objective="Persist audit trail of every login",
        repo_path="/tmp/r",
        parent_slice_id="s1",  # retry
    )
    cm = SliceComplete(
        slice_id="s1-retry", app_id="t", duration_seconds=5,
        goose_exit_code=0, summary="ok", files_changed=["x/log.py"],
        diff_path=str(diff_path),
    )
    r = validate_slice(complete=cm, slice_=sl)
    assert r.verdict == "reject_hard"


def test_cheat_flags_escalate() -> None:
    r = validate_slice(
        complete=_complete(cheats=["assert-true-only:tests/test_x.py"]),
        slice_=_slice(),
    )
    assert r.verdict == "escalate"
    assert "cheat" in r.rationale


def test_timeout_kill_replan() -> None:
    r = validate_slice(complete=_complete(exit_code=-9), slice_=_slice())
    assert r.verdict == "kill_replan"


def test_nonzero_exit_no_files_first_attempt_retries() -> None:
    """No work landed AND non-zero exit → retry once."""
    r = validate_slice(complete=_complete(exit_code=7, files=[]), slice_=_slice())
    assert r.verdict == "reject_retry"


def test_nonzero_exit_no_files_after_retry_hard_rejects() -> None:
    """Retry already happened and still no work → hard reject."""
    r = validate_slice(complete=_complete(exit_code=7, files=[]),
                       slice_=_slice(parent="s1"))
    assert r.verdict == "reject_hard"


def test_nonzero_exit_with_files_soft_accepts() -> None:
    """Regression from S3 dogfood: goose's own commit was blocked by sandbox
    (exit≠0) but the captain-runner _git_autocommit step landed the file edits.
    Reject_retry would redo or break already-good work — soft_accept instead."""
    r = validate_slice(
        complete=_complete(exit_code=1, files=["scripts/x.py", "tests/test_x.py"]),
        slice_=_slice(),
    )
    assert r.verdict == "soft_accept"
    assert "2 file" in r.rationale
    assert "exit 1" in r.rationale


def test_nonzero_exit_with_files_soft_accepts_even_on_retry() -> None:
    """Same logic on retry — work landed, take it. Don't escalate to
    reject_hard just because exit was nonzero."""
    r = validate_slice(
        complete=_complete(exit_code=1, files=["a.py"]),
        slice_=_slice(parent="s1"),
    )
    assert r.verdict == "soft_accept"


def test_no_files_changed_first_attempt_retries() -> None:
    r = validate_slice(complete=_complete(files=[]), slice_=_slice())
    assert r.verdict == "reject_retry"


def test_no_files_changed_after_retry_hard_rejects() -> None:
    r = validate_slice(complete=_complete(files=[]), slice_=_slice(parent="s1"))
    assert r.verdict == "reject_hard"


def test_clean_exit_no_rubric_accepts() -> None:
    r = validate_slice(complete=_complete(), slice_=_slice())
    assert r.verdict == "accept"
    assert r.rubric_delta_pp is None


def test_clean_exit_positive_delta_accepts() -> None:
    r = validate_slice(
        complete=_complete(),
        slice_=_slice(),
        score_delta=lambda *_: 1.5,
    )
    assert r.verdict == "accept"
    assert r.rubric_delta_pp == 1.5


def test_clean_exit_low_delta_soft_accepts() -> None:
    r = validate_slice(
        complete=_complete(),
        slice_=_slice(),
        score_delta=lambda *_: 0.2,
    )
    assert r.verdict == "soft_accept"


def test_regression_first_attempt_retries() -> None:
    r = validate_slice(
        complete=_complete(),
        slice_=_slice(),
        score_delta=lambda *_: -0.8,
    )
    assert r.verdict == "reject_retry"
    assert r.rubric_delta_pp == -0.8


def test_regression_after_retry_reverts() -> None:
    r = validate_slice(
        complete=_complete(),
        slice_=_slice(parent="s1"),
        score_delta=lambda *_: -0.5,
    )
    assert r.verdict == "revert"


# ---------------------------------------------------------------------------
# C1 — verify gate (per-app CI command)
# ---------------------------------------------------------------------------


def test_run_verify_gate_no_cmd_passes(tmp_path: Path) -> None:
    passed, summary = run_verify_gate(
        repo_path=str(tmp_path), verify_cmd=None, timeout_seconds=10,
    )
    assert passed is True
    assert "no verify_cmd" in summary


def test_run_verify_gate_empty_cmd_passes(tmp_path: Path) -> None:
    passed, _ = run_verify_gate(
        repo_path=str(tmp_path), verify_cmd="   ", timeout_seconds=10,
    )
    assert passed is True


def test_run_verify_gate_zero_exit_passes(tmp_path: Path) -> None:
    passed, summary = run_verify_gate(
        repo_path=str(tmp_path), verify_cmd="true", timeout_seconds=10,
    )
    assert passed is True
    assert "passed" in summary


def test_run_verify_gate_nonzero_exit_fails(tmp_path: Path) -> None:
    passed, summary = run_verify_gate(
        repo_path=str(tmp_path),
        verify_cmd="echo 'failure tail' >&2; exit 7",
        timeout_seconds=10,
    )
    assert passed is False
    assert "exit 7" in summary
    assert "failure tail" in summary


def test_run_verify_gate_timeout_fails(tmp_path: Path) -> None:
    passed, summary = run_verify_gate(
        repo_path=str(tmp_path), verify_cmd="sleep 5", timeout_seconds=1,
    )
    assert passed is False
    assert "timed out" in summary


def test_apply_verify_gate_passes_through_rejecting_verdicts(tmp_path: Path) -> None:
    """Already-rejecting verdicts shouldn't trigger CI — the slice is going
    to retry/escalate regardless."""
    for v in ("reject_retry", "reject_hard", "escalate", "kill_replan", "revert"):
        result = ValidationResult(verdict=v, rationale="x", rubric_delta_pp=None)
        out = apply_verify_gate(
            result, is_retry=False, repo_path=str(tmp_path),
            verify_cmd="exit 1", timeout_seconds=10,
        )
        assert out.verdict == v, f"verdict {v} should pass through but became {out.verdict}"


def test_apply_verify_gate_passes_accept_through_when_ci_green(tmp_path: Path) -> None:
    result = ValidationResult(verdict="accept", rationale="ok", rubric_delta_pp=1.5)
    out = apply_verify_gate(
        result, is_retry=False, repo_path=str(tmp_path),
        verify_cmd="true", timeout_seconds=10,
    )
    assert out.verdict == "accept"
    assert out.rubric_delta_pp == 1.5


def test_apply_verify_gate_downgrades_accept_on_ci_failure(tmp_path: Path) -> None:
    result = ValidationResult(verdict="accept", rationale="ok", rubric_delta_pp=1.5)
    out = apply_verify_gate(
        result, is_retry=False, repo_path=str(tmp_path),
        verify_cmd="exit 1", timeout_seconds=10,
    )
    assert out.verdict == "reject_retry"
    assert "verify_cmd" in out.rationale
    # Rubric delta is preserved for visibility into "what goose claimed it did"
    assert out.rubric_delta_pp == 1.5


def test_apply_verify_gate_downgrades_to_reject_hard_on_retry(tmp_path: Path) -> None:
    result = ValidationResult(verdict="soft_accept", rationale="ok", rubric_delta_pp=0.1)
    out = apply_verify_gate(
        result, is_retry=True, repo_path=str(tmp_path),
        verify_cmd="exit 1", timeout_seconds=10,
    )
    assert out.verdict == "reject_hard"


def test_apply_verify_gate_no_cmd_is_noop(tmp_path: Path) -> None:
    """Apps with no verify_cmd preserve the original verdict (back-compat)."""
    result = ValidationResult(verdict="accept", rationale="ok", rubric_delta_pp=2.0)
    out = apply_verify_gate(
        result, is_retry=False, repo_path=str(tmp_path),
        verify_cmd=None, timeout_seconds=10,
    )
    assert out.verdict == "accept"
    assert out.rubric_delta_pp == 2.0


# ---------------------------------------------------------------------------
# advance_roadmap
# ---------------------------------------------------------------------------


def _rm(*ids_with_status):
    return Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id=i, objective=f"o-{i}", status=s) for i, s in ids_with_status],
    )


def test_advance_accept_marks_done() -> None:
    rm = _rm(("s1", "in_flight"), ("s2", "queued"))
    advance_roadmap(rm, "s1", "accept")
    assert rm.slices[0].status == "done"


def test_advance_soft_accept_marks_done() -> None:
    rm = _rm(("s1", "in_flight"))
    advance_roadmap(rm, "s1", "soft_accept")
    assert rm.slices[0].status == "done"


def test_advance_reject_retry_requeues() -> None:
    rm = _rm(("s1", "in_flight"))
    advance_roadmap(rm, "s1", "reject_retry")
    assert rm.slices[0].status == "queued"


def test_advance_reject_hard_skips() -> None:
    rm = _rm(("s1", "in_flight"))
    advance_roadmap(rm, "s1", "reject_hard")
    assert rm.slices[0].status == "skipped"
    assert "reject_hard" in rm.slices[0].notes


def test_advance_escalate_blocks() -> None:
    rm = _rm(("s1", "in_flight"))
    advance_roadmap(rm, "s1", "escalate")
    assert rm.slices[0].status == "blocked"


# ---------------------------------------------------------------------------
# next_queued_slice
# ---------------------------------------------------------------------------


def test_next_queued_returns_first_unblocked() -> None:
    rm = Roadmap(
        app_id="test-app",
        slices=[
            RoadmapSlice(slice_id="a", objective="A", status="done"),
            RoadmapSlice(slice_id="b", objective="B", status="queued", blocked_by=["a"]),
            RoadmapSlice(slice_id="c", objective="C", status="queued"),
        ],
    )
    nxt = next_queued_slice(rm)
    assert nxt is not None
    assert nxt.slice_id == "b"


def test_next_queued_skips_blocked() -> None:
    rm = Roadmap(
        app_id="test-app",
        slices=[
            RoadmapSlice(slice_id="a", objective="A", status="queued"),
            RoadmapSlice(slice_id="b", objective="B", status="queued", blocked_by=["a"]),
        ],
    )
    nxt = next_queued_slice(rm)
    assert nxt is not None
    assert nxt.slice_id == "a"


def test_next_queued_returns_none_when_exhausted() -> None:
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="a", objective="A", status="done")],
    )
    assert next_queued_slice(rm) is None


# ---------------------------------------------------------------------------
# captain_tick — end-to-end behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path: Path) -> AppWorkspace:
    w = AppWorkspace("test-app", base=tmp_path)
    w.ensure()
    return w


def test_tick_dispatches_first_slice_when_roadmap_present(ws: AppWorkspace) -> None:
    rm = Roadmap(
        app_id="test-app",
        slices=[
            RoadmapSlice(slice_id="s1", objective="Add a TODO", phase="docs"),
            RoadmapSlice(slice_id="s2", objective="Add a test", blocked_by=["s1"]),
        ],
    )
    write_roadmap(ws, rm)

    status = captain_tick(ws, repo_path="/tmp/r")
    assert "dispatched s1" in status

    cs = read_current_slice(ws)
    assert cs is not None
    assert cs.slice_id == "s1"
    assert "Add a TODO" in cs.user_prompt

    rm2 = read_roadmap(ws)
    assert rm2.slices[0].status == "in_flight"

    log = read_captain_log(ws)
    assert log[-1].kind == "dispatch"


def test_tick_validates_completion_and_dispatches_next(ws: AppWorkspace) -> None:
    rm = Roadmap(
        app_id="test-app",
        slices=[
            RoadmapSlice(slice_id="s1", objective="A", status="in_flight"),
            RoadmapSlice(slice_id="s2", objective="B", status="queued"),
        ],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    status = captain_tick(ws, repo_path="/tmp/r")

    rm2 = read_roadmap(ws)
    assert rm2.slices[0].status == "done"
    assert rm2.slices[1].status == "in_flight"

    cs = read_current_slice(ws)
    assert cs is not None
    assert cs.slice_id == "s2"
    assert not ws.slice_complete_path.exists()

    log = read_captain_log(ws)
    kinds = [e.kind for e in log]
    assert "validate" in kinds
    assert "dispatch" in kinds
    assert "accept" in [e.verdict for e in log if e.verdict]


def test_tick_verify_gate_downgrades_when_ci_fails(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: registered app with verify_cmd that fails → captain
    issues reject_retry instead of accept and re-queues the slice."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp

    repo = tmp_path / "repo"
    repo.mkdir()

    # Stub the registry lookup so the validator sees a verify_cmd.
    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        verify_cmd="exit 1",
        verify_timeout_seconds=10,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    validate_entries = [e for e in log if e.kind == "validate"]
    assert len(validate_entries) == 1
    assert validate_entries[0].verdict == "reject_retry"
    assert "verify_cmd" in validate_entries[0].rationale

    # Slice was re-queued (advance_roadmap set queued) and then re-dispatched
    # as a retry by captain_tick's dispatch step. Roadmap shows in_flight again
    # but the new current_slice carries a parent_slice_id marking it as a retry.
    cs = read_current_slice(ws)
    assert cs is not None
    assert cs.parent_slice_id == "s1"
    assert cs.slice_id == "s1-retry"


def test_tick_roadmap_complete_emits_event_and_opens_pr(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: last queued slice completes → captain emits roadmap_complete,
    pushes branch, opens PR. Subprocess calls are mocked."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    repo.mkdir()

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_push=True,
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    push_calls: list[dict] = []
    pr_calls: list[dict] = []

    def fake_push(*, repo_path: str, branch: str, **_kw):
        push_calls.append({"repo_path": repo_path, "branch": branch})
        return mf.CmdResult(ok=True, summary="ok")

    def fake_open_pr(*, repo_path: str, base: str, head: str,
                     title: str, body: str, **_kw):
        pr_calls.append({
            "base": base, "head": head, "title": title, "body": body,
        })
        return mf.CmdResult(
            ok=True, summary="ok",
            stdout="https://github.com/owner/repo/pull/42",
        )

    monkeypatch.setattr(mf, "push_captain_branch", fake_push)
    monkeypatch.setattr(mf, "open_pull_request", fake_open_pr)

    # Roadmap with one slice that's about to be marked done.
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    status = captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    # Slice marked done → roadmap complete → no further dispatch
    rm2 = read_roadmap(ws)
    assert rm2.slices[0].status == "done"
    assert "roadmap_complete" in status

    log = read_captain_log(ws)
    kinds = [e.kind for e in log]
    rationales = [e.rationale for e in log]
    # roadmap_complete event landed with the right kind
    assert "roadmap_complete" in kinds
    assert "pull_request_opened" in kinds
    # PR url logged
    assert any("github.com/owner/repo/pull/42" in r for r in rationales)

    # Push happened twice — once on accept (auto_push), once on roadmap_complete
    assert len(push_calls) >= 1
    assert all(c["branch"] == "codex/captain-test-app" for c in push_calls)

    # Exactly one PR open call with the right shape
    assert len(pr_calls) == 1
    assert pr_calls[0]["base"] == "main"
    assert pr_calls[0]["head"] == "codex/captain-test-app"
    assert "test-app" in pr_calls[0]["title"]


def test_tick_no_pr_when_auto_open_pr_disabled(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto_open_pr=False → roadmap_complete event is logged but no PR opened."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    repo.mkdir()

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        auto_push=False,
        auto_open_pr=False,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    pr_called = False

    def fake_open_pr(**_kw):
        nonlocal pr_called
        pr_called = True
        return mf.CmdResult(ok=True, summary="ok")

    monkeypatch.setattr(mf, "open_pull_request", fake_open_pr)

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    kinds = [e.kind for e in log]
    # Event still logged
    assert "roadmap_complete" in kinds
    # But no PR was opened
    assert pr_called is False
    assert "pull_request_opened" not in kinds


def test_tick_verify_gate_passes_through_when_ci_green(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify gate green → accept verdict preserved."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp

    repo = tmp_path / "repo"
    repo.mkdir()

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        verify_cmd="true",
        verify_timeout_seconds=10,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    validate_entries = [e for e in log if e.kind == "validate"]
    assert validate_entries[0].verdict == "accept"

    rm2 = read_roadmap(ws)
    assert rm2.slices[0].status == "done"


def test_tick_replan_reports_when_roadmap_exhausted(ws: AppWorkspace) -> None:
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    status = captain_tick(ws, repo_path="/tmp/r")
    assert "replan" in status.lower() or "exhausted" in status.lower()


def test_tick_completion_with_no_roadmap_escalates(ws: AppWorkspace) -> None:
    write_slice_complete(
        ws,
        SliceComplete(slice_id="orphan", app_id="test-app", duration_seconds=1,
                      goose_exit_code=0, summary="x", files_changed=["a.py"]),
    )
    status = captain_tick(ws, repo_path="/tmp/r")
    assert "escalate" in status

    log = read_captain_log(ws)
    assert log[-1].verdict == "escalate"
    assert not ws.slice_complete_path.exists()


def test_tick_reject_retry_requeues_slice(ws: AppWorkspace) -> None:
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=1,
                      goose_exit_code=7, summary="failed", files_changed=[],
                      failure_tail="bad"),
    )

    captain_tick(ws, repo_path="/tmp/r")

    rm2 = read_roadmap(ws)
    # First failure → reject_retry → status flips to queued
    assert rm2.slices[0].status == "in_flight"  # then re-dispatched in same tick
    cs = read_current_slice(ws)
    assert cs is not None
    assert cs.parent_slice_id is not None  # marked as a retry


# ---------------------------------------------------------------------------
# build_current_slice
# ---------------------------------------------------------------------------


def test_build_current_slice_includes_phase_and_objective() -> None:
    rs = RoadmapSlice(slice_id="s1", objective="Add API contracts", phase="foundation")
    cs = build_current_slice(rs, app_id="test-app", repo_path="/tmp/r")
    assert cs.slice_id == "s1"
    assert "Add API contracts" in cs.user_prompt
    assert "foundation" in cs.user_prompt
    assert cs.parent_slice_id is None


def test_build_current_slice_marks_retry() -> None:
    rs = RoadmapSlice(slice_id="s1", objective="o")
    cs = build_current_slice(rs, app_id="test-app", repo_path="/tmp/r", parent_slice_id="s1")
    assert cs.parent_slice_id == "s1"


# ---------------------------------------------------------------------------
# C3 — branch baseline + scorecard delta in PR body
# ---------------------------------------------------------------------------


def _git_init_repo(path: Path, base: str = "main") -> None:
    """Bootstrap a tiny throwaway git repo for branch-baseline tests."""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", base], cwd=path, check=True)
    # README so the repo isn't empty (and so docs_present scores).
    (path / "README.md").write_text("# test\n")
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "add", "README.md"],
        cwd=path, check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        cwd=path, check=True,
    )


def test_tick_writes_branch_baseline_on_first_dispatch(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First dispatch onto a captain branch snapshots the scorecard."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A")],
    )
    write_roadmap(ws, rm)

    assert not ws.branch_baseline_path.exists()
    captain_tick(ws, repo_path=str(repo))
    assert ws.branch_baseline_path.exists()

    # Sanity: the baseline parses as a Scorecard with reasonable shape.
    from chad_captain.scorecard import read_baseline
    sc = read_baseline(ws.branch_baseline_path)
    assert sc is not None
    assert 0.0 <= sc.aggregate <= 1.0
    assert any(d.name == "docs_present" for d in sc.dimensions)


def test_tick_branch_baseline_not_overwritten_on_resume(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a branch baseline already exists, captain does NOT overwrite it
    on a subsequent dispatch (idempotent across slices in the same PR)."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.scorecard import read_baseline, write_baseline, Scorecard, DimensionScore

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    sentinel = Scorecard(
        repo_path=str(repo),
        dimensions=[DimensionScore(name="sentinel", score=0.123)],
        aggregate=0.123,
    )
    write_baseline(ws.branch_baseline_path, sentinel)

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A")],
    )
    write_roadmap(ws, rm)
    captain_tick(ws, repo_path=str(repo))

    sc = read_baseline(ws.branch_baseline_path)
    assert sc is not None
    assert sc.aggregate == 0.123
    assert sc.dimensions[0].name == "sentinel"


def test_roadmap_complete_pr_body_includes_scorecard_delta(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a branch baseline exists at roadmap_complete, the PR body
    embeds the before/after scorecard delta and the baseline is cleared."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.scorecard import (
        DimensionScore,
        Scorecard,
        write_baseline,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_push=True,
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    # Pre-populate a low-aggregate baseline so the live score will look better.
    pre = Scorecard(
        repo_path=str(repo),
        dimensions=[DimensionScore(name="docs_present", score=0.0)],
        aggregate=0.0,
    )
    write_baseline(ws.branch_baseline_path, pre)

    pr_calls: list[dict] = []

    def fake_push(*, repo_path: str, branch: str, **_kw):
        return mf.CmdResult(ok=True, summary="ok")

    def fake_open_pr(*, repo_path: str, base: str, head: str,
                     title: str, body: str, **_kw):
        pr_calls.append({"body": body})
        return mf.CmdResult(
            ok=True, summary="ok",
            stdout="https://github.com/owner/repo/pull/99",
        )

    monkeypatch.setattr(mf, "push_captain_branch", fake_push)
    monkeypatch.setattr(mf, "open_pull_request", fake_open_pr)

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    # PR opened with scorecard section in body
    assert len(pr_calls) == 1
    body = pr_calls[0]["body"]
    assert "Scorecard delta" in body
    assert "Aggregate:" in body
    # Baseline cleared on successful PR open
    assert not ws.branch_baseline_path.exists()


def test_roadmap_complete_pr_body_omits_delta_when_no_baseline(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No branch baseline on disk → PR body still ships, just without
    the scorecard delta section. Best-effort, not blocking."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_push=True,
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    pr_calls: list[dict] = []

    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    def fake_open_pr(*, body: str, **_kw):
        pr_calls.append({"body": body})
        return mf.CmdResult(ok=True, summary="ok", stdout="https://x")
    monkeypatch.setattr(mf, "open_pull_request", fake_open_pr)

    assert not ws.branch_baseline_path.exists()

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert len(pr_calls) == 1
    body = pr_calls[0]["body"]
    assert "Scorecard delta" not in body


def test_roadmap_complete_baseline_preserved_on_pr_open_failure(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If PR open fails, the branch baseline must NOT be cleared so the
    next captain tick can retry and still produce a delta-bearing PR."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.scorecard import (
        DimensionScore,
        Scorecard,
        write_baseline,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_push=True,
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    pre = Scorecard(
        repo_path=str(repo),
        dimensions=[DimensionScore(name="docs_present", score=0.0)],
        aggregate=0.0,
    )
    write_baseline(ws.branch_baseline_path, pre)

    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(ok=False, summary="exit 1: gh auth required"),
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    # PR open failed → baseline still on disk for retry.
    assert ws.branch_baseline_path.exists()


# ---------------------------------------------------------------------------
# C4 — merge detection + post-merge cycle
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C8 — circuit breaker
# ---------------------------------------------------------------------------


def _seed_validate_log(
    ws: AppWorkspace, *,
    verdicts: list[str],
    app_id: str = "test-app",
    deltas: list[float | None] | None = None,
) -> None:
    """Append N validate entries with the given verdicts in order.
    Optional ``deltas`` parallel-list sets rubric_delta_pp per entry."""
    from chad_captain.protocol import CaptainLogEntry, append_captain_log
    for i, v in enumerate(verdicts):
        delta = deltas[i] if deltas is not None and i < len(deltas) else None
        append_captain_log(
            ws,
            CaptainLogEntry(
                app_id=app_id, slice_id=f"s{i}",
                kind="validate", verdict=v, rationale=f"seeded {v}",
                rubric_delta_pp=delta,
            ),
        )


def test_circuit_breaker_trips_after_threshold_consecutive_bads(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three consecutive reject_hard validate entries → pause_until written
    + circuit_breaker_tripped escalation logged."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp

    repo = tmp_path / "repo"
    repo.mkdir()

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
        circuit_breaker_threshold=3,
        circuit_breaker_pause_minutes=30,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    # Seed 2 bad verdicts, then drive a 3rd via captain_tick
    _seed_validate_log(ws, verdicts=["reject_hard", "reject_hard"])

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s2", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    # Slice complete with cheats → escalate verdict (also a bad one)
    write_slice_complete(
        ws,
        SliceComplete(
            slice_id="s2", app_id="test-app", duration_seconds=5,
            goose_exit_code=0, summary="x", files_changed=["a.py"],
            cheat_flags=["assert-true-only:tests/x.py"],
        ),
    )

    status = captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert ws.pause_until_path.exists()
    log = read_captain_log(ws)
    tripped = [
        e for e in log
        if (e.references or {}).get("event") == "circuit_breaker_tripped"
    ]
    assert len(tripped) == 1
    assert "consecutive bad verdicts" in tripped[0].rationale
    # Status reflects the pause
    assert "paused" in (status or "")


def test_circuit_breaker_does_not_trip_on_mixed_verdicts(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One accept among the recent verdicts → not consecutive, no trip."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp

    repo = tmp_path / "repo"
    repo.mkdir()

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous", circuit_breaker_threshold=3,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    _seed_validate_log(ws, verdicts=["reject_hard", "accept", "reject_hard"])

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s3", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s3", app_id="test-app", duration_seconds=5,
                      goose_exit_code=1, summary="bad", files_changed=[]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert not ws.pause_until_path.exists()


def test_low_yield_streak_trips_after_threshold_zero_deltas(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """N consecutive soft_accepts with abs(delta) < 0.5pp →
    low_yield_streak escalation + dispatch pause. This is the
    rubric-saturation guard."""
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain.validator import _maybe_trip_low_yield_streak

    repo = tmp_path / "repo"
    repo.mkdir()
    reg = RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
        low_yield_streak_threshold=3,
        low_yield_pause_minutes=15,
    )

    _seed_validate_log(
        ws,
        verdicts=["soft_accept", "soft_accept", "soft_accept"],
        deltas=[0.0, 0.1, 0.0],
    )

    _maybe_trip_low_yield_streak(ws, reg)

    assert ws.pause_until_path.exists()
    log = read_captain_log(ws)
    tripped = [
        e for e in log
        if (e.references or {}).get("event") == "low_yield_streak"
    ]
    assert len(tripped) == 1
    assert "low-yield streak" in tripped[0].rationale
    assert (tripped[0].references or {}).get("threshold") == "3"


def test_low_yield_streak_does_not_retrip_within_same_streak(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If we already wrote a low_yield_streak escalation, don't write
    another one on the very next tick. Escalation is one event per streak."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp

    repo = tmp_path / "repo"
    repo.mkdir()
    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
        low_yield_streak_threshold=3,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    # First trip
    _seed_validate_log(
        ws, verdicts=["soft_accept", "soft_accept", "soft_accept"],
        deltas=[0.0, 0.0, 0.0],
    )
    from chad_captain.validator import _maybe_trip_low_yield_streak
    _maybe_trip_low_yield_streak(ws, fake_reg.apps[0])

    # Second call on same streak — no new escalation
    _maybe_trip_low_yield_streak(ws, fake_reg.apps[0])

    log = read_captain_log(ws)
    tripped = [
        e for e in log
        if (e.references or {}).get("event") == "low_yield_streak"
    ]
    assert len(tripped) == 1


def test_low_yield_streak_does_not_retrip_after_pause_resume(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """Live failure: pause expires → 1 new validate fires → guard
    re-trips on same trailing window because old (already-counted)
    validates are still in the look-back. Captain stuck in 30min
    pause-resume-pause cycles forever.

    Fix: window resets after each low_yield_streak escalation.
    Only validates AFTER the escalation count toward the next
    threshold."""
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain.validator import _maybe_trip_low_yield_streak
    from chad_captain.protocol import CaptainLogEntry, append_captain_log

    repo = tmp_path / "repo"
    repo.mkdir()
    reg = RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous", low_yield_streak_threshold=3,
    )

    # Streak fires
    _seed_validate_log(
        ws, verdicts=["soft_accept"] * 3, deltas=[0.0, 0.0, 0.0],
    )
    _maybe_trip_low_yield_streak(ws, reg)

    # Pause expires, ONE new validate fires (still soft_accept 0pp).
    # Without the window reset, the trailing 3 includes the ORIGINAL
    # 2 + this new 1 → guard would re-trip. With the reset, only
    # the new 1 counts → below threshold → no re-trip.
    append_captain_log(ws, CaptainLogEntry(
        app_id="test-app", slice_id="s4", kind="validate",
        verdict="soft_accept", rationale="seeded", rubric_delta_pp=0.0,
    ))
    _maybe_trip_low_yield_streak(ws, reg)

    log = read_captain_log(ws)
    tripped = [
        e for e in log
        if (e.references or {}).get("event") == "low_yield_streak"
    ]
    assert len(tripped) == 1, f"expected 1 trip, got {len(tripped)}"


def test_low_yield_streak_retrips_after_fresh_threshold_post_escalation(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """If, AFTER an escalation, N more zero-delta soft_accepts happen,
    THEN the streak should re-trip — that's a genuinely new failure."""
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain.validator import _maybe_trip_low_yield_streak
    from chad_captain.protocol import CaptainLogEntry, append_captain_log

    repo = tmp_path / "repo"
    repo.mkdir()
    reg = RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous", low_yield_streak_threshold=3,
    )

    _seed_validate_log(
        ws, verdicts=["soft_accept"] * 3, deltas=[0.0, 0.0, 0.0],
    )
    _maybe_trip_low_yield_streak(ws, reg)
    # 3 fresh post-escalation zero-delta validates
    for i in range(3):
        append_captain_log(ws, CaptainLogEntry(
            app_id="test-app", slice_id=f"sn{i}", kind="validate",
            verdict="soft_accept", rationale="seeded", rubric_delta_pp=0.0,
        ))
    _maybe_trip_low_yield_streak(ws, reg)

    log = read_captain_log(ws)
    tripped = [
        e for e in log
        if (e.references or {}).get("event") == "low_yield_streak"
    ]
    assert len(tripped) == 2


def test_reuse_regression_blocks_new_parallel_package(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live failure: PR #145 shipped a 2nd parallel `entitlements.py`
    module without rejection. The C14 gate now turns reuse_consistency
    drops into reject_retry verdicts."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.scorecard import write_baseline, score_repo
    from chad_captain.protocol import write_roadmap
    from chad_captain.validator import captain_tick

    repo = tmp_path / "repo"
    repo.mkdir()
    # Set up a clean baseline (no parallel packages).
    (repo / "apps" / "billing").mkdir(parents=True)
    (repo / "apps" / "billing" / "models.py").write_text("class Plan: pass\n")
    write_baseline(ws.slice_baseline_path, score_repo(repo))

    # Now the slice "lands" by creating a top-level billing/ directory →
    # introduces the parallel-package smell.
    (repo / "billing").mkdir()
    (repo / "billing" / "models.py").write_text("class Plan2: pass\n")

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="add billing", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(
            slice_id="s1", app_id="test-app", duration_seconds=5,
            goose_exit_code=0, summary="ok",
            files_changed=["billing/models.py"],
        ),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=True)

    log = read_captain_log(ws)
    validates = [e for e in log if e.kind == "validate"]
    assert validates, "expected a validate entry"
    last = validates[-1]
    assert last.verdict == "reject_retry"
    assert "parallel package" in last.rationale or "reuse_consistency" in last.rationale
    assert "billing" in last.rationale  # name of the new dup is surfaced


def test_reuse_regression_does_not_fire_on_clean_slice(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No regression in reuse_consistency → no override. Slice ships."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.scorecard import write_baseline, score_repo
    from chad_captain.protocol import write_roadmap
    from chad_captain.validator import captain_tick

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "apps" / "billing").mkdir(parents=True)
    (repo / "apps" / "billing" / "models.py").write_text("class Plan: pass\n")
    write_baseline(ws.slice_baseline_path, score_repo(repo))

    # Clean slice — adds a test file inside the existing package.
    (repo / "apps" / "billing" / "tests").mkdir()
    (repo / "apps" / "billing" / "tests" / "test_x.py").write_text("def test(): pass\n")

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="add tests", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(
            slice_id="s1", app_id="test-app", duration_seconds=5,
            goose_exit_code=0, summary="ok",
            files_changed=["apps/billing/tests/test_x.py"],
        ),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=True)
    log = read_captain_log(ws)
    validates = [e for e in log if e.kind == "validate"]
    assert validates, "expected a validate entry"
    # Should NOT be reject_retry — accept or soft_accept depending on delta.
    assert validates[-1].verdict in ("accept", "soft_accept")


def test_low_yield_streak_ignores_real_deltas(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single soft_accept with delta >= 0.5pp resets the streak."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.validator import _maybe_trip_low_yield_streak

    repo = tmp_path / "repo"
    repo.mkdir()
    reg = RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous", low_yield_streak_threshold=3,
    )

    _seed_validate_log(
        ws, verdicts=["soft_accept", "soft_accept", "soft_accept"],
        deltas=[0.0, 1.5, 0.0],  # middle one has real delta
    )
    _maybe_trip_low_yield_streak(ws, reg)
    assert not ws.pause_until_path.exists()


def test_paused_app_skips_dispatch(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If pause_until is in the future, captain returns 'paused' and does
    not dispatch — even when the roadmap has queued slices."""
    from datetime import datetime, timedelta, timezone
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.validator import _write_pause_until

    repo = tmp_path / "repo"
    repo.mkdir()

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo), mode="autonomous",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    _write_pause_until(ws, until)

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="queued")],
    )
    write_roadmap(ws, rm)

    status = captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert "paused" in (status or "")
    # No dispatch happened
    assert not ws.current_slice_path.exists()


def test_paused_app_auto_clears_when_pause_expires(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired pause file (now > until) is auto-deleted on the next
    tick and dispatch resumes."""
    from datetime import datetime, timedelta, timezone
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.validator import _write_pause_until

    repo = tmp_path / "repo"
    repo.mkdir()

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo), mode="autonomous",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    _write_pause_until(ws, expired)

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="queued")],
    )
    write_roadmap(ws, rm)

    status = captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert not ws.pause_until_path.exists()  # auto-cleared
    assert "dispatched" in (status or "")  # dispatch resumed


def test_clear_pause_helper_returns_true_only_when_present(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    from chad_captain.validator import _write_pause_until, clear_pause
    from datetime import datetime, timedelta, timezone

    assert clear_pause(ws) is False
    _write_pause_until(ws, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    assert clear_pause(ws) is True
    assert not ws.pause_until_path.exists()


# ---------------------------------------------------------------------------
# C10 — post-merge verify gate
# ---------------------------------------------------------------------------


def _post_merge_setup(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    *, verify_cmd: str | None,
):
    """Wire a roadmap-complete + already-opened-PR + MERGED state so
    captain_tick will run _maybe_handle_pr_merge → _post_merge_verify."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.protocol import (
        CaptainLogEntry,
        append_captain_log,
        write_roadmap,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    if not repo.exists():
        _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_open_pr=True,
        verify_cmd=verify_cmd,
        verify_timeout_seconds=10,
        circuit_breaker_pause_minutes=45,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id="test-app", kind="pull_request_opened",
            references={
                "pr_url": "https://x", "branch": "codex/captain-test-app",
            },
        ),
    )
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    monkeypatch.setattr(
        mf, "get_pr_state",
        lambda **_kw: ("MERGED", {"mergeCommit": {"oid": "abc"}, "mergedAt": "now"}),
    )
    monkeypatch.setattr(
        mf, "refresh_base_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "delete_local_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok", stdout="https://x"),
    )
    return repo


def test_post_merge_verify_passes_silently_on_clean_main(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_cmd='true' → passes first try → no escalation, no pause."""
    repo = _post_merge_setup(ws, tmp_path, monkeypatch, verify_cmd="true")

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    failed = [
        e for e in log
        if (e.references or {}).get("event") == "post_merge_verify_failed"
    ]
    assert failed == []
    assert not ws.pause_until_path.exists()


def test_post_merge_verify_failure_pauses_dispatch(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_cmd='false' → fails 3 attempts → critical escalation + pause."""
    repo = _post_merge_setup(ws, tmp_path, monkeypatch, verify_cmd="false")

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    failed = [
        e for e in log
        if (e.references or {}).get("event") == "post_merge_verify_failed"
    ]
    assert len(failed) == 1
    assert "main is broken" in failed[0].rationale.lower() \
        or "main is broken" in failed[0].rationale
    assert (failed[0].references or {}).get("severity") == "critical"
    assert ws.pause_until_path.exists()


def test_post_merge_verify_skipped_without_verify_cmd(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No verify_cmd → no verify run, no pause."""
    repo = _post_merge_setup(ws, tmp_path, monkeypatch, verify_cmd=None)

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert not ws.pause_until_path.exists()
    log = read_captain_log(ws)
    failed = [
        e for e in log
        if (e.references or {}).get("event") == "post_merge_verify_failed"
    ]
    assert failed == []


def test_post_merge_verify_recovers_from_flake(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First two attempts fail, third passes → no escalation (flake absorbed)."""
    import chad_captain.validator as vm

    repo = _post_merge_setup(ws, tmp_path, monkeypatch, verify_cmd="true")

    attempts = [0]

    def flaky_gate(*, repo_path: str, verify_cmd: str | None, timeout_seconds: int):
        attempts[0] += 1
        if attempts[0] < 3:
            return False, f"flake on attempt {attempts[0]}"
        return True, "ok on third try"

    monkeypatch.setattr(vm, "run_verify_gate", flaky_gate)

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert attempts[0] == 3
    assert not ws.pause_until_path.exists()


# ---------------------------------------------------------------------------
# C9 — pending vs hard merge failure
# ---------------------------------------------------------------------------


def _common_auto_merge_setup(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    *, merge_summary: str, merge_ok: bool = False,
):
    """Helper that wires a roadmap_complete tick with a controllable
    auto_merge_pr response."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    if not repo.exists():
        _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_push=True, auto_open_pr=True, auto_merge=True,
        # PR7 R3#7: auto_merge requires verify_cmd
        verify_cmd="true",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok", stdout="https://x"),
    )
    monkeypatch.setattr(
        mf, "auto_merge_pr",
        lambda **_kw: mf.CmdResult(ok=merge_ok, summary=merge_summary),
    )
    return repo


def test_auto_merge_pending_failure_is_silent(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gh 'unstable status' = CI still running. Captain MUST NOT log an
    escalation — it just retries next tick."""
    repo = _common_auto_merge_setup(
        ws, tmp_path, monkeypatch,
        merge_summary="exit 1: GraphQL: Pull request is in unstable status",
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    failed = [
        e for e in log
        if e.kind == "escalation_raised"
        and (e.references or {}).get("event") == "auto_merge_failed"
    ]
    assert failed == []  # pending → silent


def test_auto_merge_hard_failure_dedupes_within_window(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hard auto_merge failures dedup within 30min. Two ticks → one escalation."""
    repo = _common_auto_merge_setup(
        ws, tmp_path, monkeypatch,
        merge_summary="exit 1: branch protection requires 1 review",
    )

    def write_and_tick():
        rm = Roadmap(
            app_id="test-app",
            slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
        )
        write_roadmap(ws, rm)
        write_slice_complete(
            ws,
            SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                          goose_exit_code=0, summary="done", files_changed=["a.py"]),
        )
        captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    write_and_tick()
    # Mark slice done again so roadmap_complete fires twice
    write_and_tick()

    log = read_captain_log(ws)
    failed = [
        e for e in log
        if e.kind == "escalation_raised"
        and (e.references or {}).get("event") == "auto_merge_failed"
    ]
    assert len(failed) == 1  # second tick was deduped


def test_is_pending_merge_failure_patterns() -> None:
    from chad_captain.validator import _is_pending_merge_failure
    assert _is_pending_merge_failure("Pull request is in unstable status")
    assert _is_pending_merge_failure(
        "GraphQL: PR is not in a state to allow checks (mergePullRequest)"
    )
    assert _is_pending_merge_failure("Required status check 'tests' is pending")
    assert not _is_pending_merge_failure("branch protection requires 1 review")
    assert not _is_pending_merge_failure("merge conflict in path/to/file.py")
    assert not _is_pending_merge_failure("")


# ---------------------------------------------------------------------------
# C7 — stall watchdog
# ---------------------------------------------------------------------------


def _write_inflight_slice(
    ws: AppWorkspace, *, started_at: str, timeout_seconds: int = 1800,
    slice_id: str = "s1",
) -> None:
    from chad_captain.protocol import write_current_slice
    write_current_slice(
        ws,
        CurrentSlice(
            slice_id=slice_id, app_id="test-app",
            objective="o", system_prompt="s", user_prompt="u",
            repo_path="/tmp/r",
            timeout_seconds=timeout_seconds,
            started_at=started_at,
        ),
    )


def test_watchdog_kills_stalled_slice_past_timeout_plus_grace(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """Slice in flight beyond timeout + grace → synthesize SliceComplete(-9)
    + emit stall_detected + clear current_slice. Validator path then
    routes through kill_replan on the next phase of the same tick."""
    from datetime import datetime, timedelta, timezone
    from chad_captain.protocol import read_slice_complete

    repo = tmp_path / "repo"
    repo.mkdir()

    # 30min timeout + 5min grace = 35min limit; fixture sets started 60min ago
    long_ago = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    _write_inflight_slice(ws, started_at=long_ago, timeout_seconds=1800)

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    kinds = [e.kind for e in log]
    # Stall detected + validate=kill_replan both fired in this tick
    assert "stall_detected" in kinds
    validates = [e for e in log if e.kind == "validate"]
    assert len(validates) == 1
    assert validates[0].verdict == "kill_replan"
    # slice_complete consumed by validate (cleared at end)
    assert read_slice_complete(ws) is None
    # kill_replan re-queued s1; dispatch path then picked it up as a retry
    cs = read_current_slice(ws)
    assert cs is not None
    assert cs.parent_slice_id == "s1"
    assert cs.slice_id == "s1-retry"


def test_watchdog_skips_when_slice_within_window(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """Slice in flight under the limit → watchdog no-op, no stall_detected."""
    from datetime import datetime, timedelta, timezone

    repo = tmp_path / "repo"
    repo.mkdir()
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _write_inflight_slice(ws, started_at=recent, timeout_seconds=1800)

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    # current_slice still present, no stall logged, no validate (no completion yet)
    assert ws.current_slice_path.exists()
    log = read_captain_log(ws)
    assert "stall_detected" not in [e.kind for e in log]


def test_watchdog_skips_when_started_at_unset(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """current_slice on disk but runner hasn't picked up (started_at=None)
    → no watchdog (it's a goose-runner queue issue, not a stall)."""
    from chad_captain.protocol import write_current_slice
    repo = tmp_path / "repo"
    repo.mkdir()
    write_current_slice(
        ws,
        CurrentSlice(
            slice_id="s1", app_id="test-app", objective="o",
            system_prompt="s", user_prompt="u", repo_path="/tmp/r",
            started_at=None,  # explicit
        ),
    )
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    assert "stall_detected" not in [e.kind for e in log]


def test_watchdog_no_op_when_slice_complete_already_present(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """If SliceComplete already exists, watchdog defers to validator —
    even if started_at looks ancient."""
    from datetime import datetime, timedelta, timezone

    repo = tmp_path / "repo"
    repo.mkdir()
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    _write_inflight_slice(ws, started_at=long_ago, timeout_seconds=600)

    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    # No stall — the real completion was processed
    assert "stall_detected" not in [e.kind for e in log]
    validates = [e for e in log if e.kind == "validate"]
    assert len(validates) == 1
    # accept (clean exit + files) not kill_replan
    assert validates[0].verdict == "accept"


def test_watchdog_handles_invalid_started_at_gracefully(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """Bad timestamp → log warning + no-op, never crash."""
    from chad_captain.protocol import write_current_slice
    repo = tmp_path / "repo"
    repo.mkdir()
    write_current_slice(
        ws,
        CurrentSlice(
            slice_id="s1", app_id="test-app", objective="o",
            system_prompt="s", user_prompt="u", repo_path="/tmp/r",
            started_at="not-a-real-iso-timestamp",
        ),
    )
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)

    # Should not raise
    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)
    log = read_captain_log(ws)
    assert "stall_detected" not in [e.kind for e in log]


def test_post_merge_cycle_runs_when_pr_merged(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Captain has an open PR, all slices done. Next tick polls gh, sees
    MERGED, refreshes main, deletes branch, clears roadmap, emits events."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.protocol import (
        CaptainLogEntry,
        append_captain_log,
        write_roadmap,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    # Pre-load history so _maybe_handle_pr_merge sees a pull_request_opened.
    pr_url = "https://github.com/owner/repo/pull/42"
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id="test-app", slice_id=None, kind="pull_request_opened",
            rationale="PR opened",
            references={"pr_url": pr_url, "branch": "codex/captain-test-app"},
        ),
    )

    # Roadmap: all slices terminal (one done) → roadmap_complete is True.
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    refresh_calls: list[dict] = []
    delete_calls: list[dict] = []
    push_calls: list[dict] = []
    open_pr_calls: list[dict] = []

    monkeypatch.setattr(
        mf, "get_pr_state",
        lambda **_kw: ("MERGED", {"mergeCommit": {"oid": "abc"}, "mergedAt": "now"}),
    )

    def fake_refresh(*, repo_path: str, base_branch: str = "main", **_kw):
        refresh_calls.append({"base": base_branch})
        return mf.CmdResult(ok=True, summary="ok")

    def fake_delete(*, repo_path: str, branch: str, **_kw):
        delete_calls.append({"branch": branch})
        return mf.CmdResult(ok=True, summary="deleted")

    def fake_push(*, repo_path: str, branch: str, **_kw):
        push_calls.append({"branch": branch})
        return mf.CmdResult(ok=True, summary="ok")

    def fake_open_pr(**kw):
        open_pr_calls.append(kw)
        return mf.CmdResult(ok=True, summary="ok", stdout="https://x")

    monkeypatch.setattr(mf, "refresh_base_branch", fake_refresh)
    monkeypatch.setattr(mf, "delete_local_branch", fake_delete)
    monkeypatch.setattr(mf, "push_captain_branch", fake_push)
    monkeypatch.setattr(mf, "open_pull_request", fake_open_pr)

    status = captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert "post_merge_cycle" in (status or "")
    # post-merge log events landed
    log = read_captain_log(ws)
    kinds = [e.kind for e in log]
    assert "pull_request_merged" in kinds
    assert "post_merge_cycle" in kinds
    # Refresh + delete were attempted once each
    assert len(refresh_calls) == 1 and refresh_calls[0]["base"] == "main"
    assert len(delete_calls) == 1 and delete_calls[0]["branch"] == "codex/captain-test-app"
    # No new PR was opened on this tick — we're cleaning up the merged one
    assert len(open_pr_calls) == 0
    # Roadmap got cleared so the next tick replans
    assert not ws.roadmap_path.exists()


def test_post_merge_cycle_skipped_when_pr_still_open(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR state OPEN → no merge cycle, no state mutation, captain holds."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.protocol import (
        CaptainLogEntry,
        append_captain_log,
        write_roadmap,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id="test-app", slice_id=None, kind="pull_request_opened",
            rationale="PR opened",
            references={"pr_url": "https://x", "branch": "codex/captain-test-app"},
        ),
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    monkeypatch.setattr(mf, "get_pr_state", lambda **_kw: ("OPEN", {}))
    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok", stdout="https://x"),
    )
    refreshed = []
    monkeypatch.setattr(
        mf, "refresh_base_branch",
        lambda **kw: refreshed.append(kw) or mf.CmdResult(ok=True, summary="ok"),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    kinds = [e.kind for e in log]
    assert "pull_request_merged" not in kinds
    assert "post_merge_cycle" not in kinds
    assert refreshed == []
    # Roadmap state preserved (PR not merged yet → captain holds)
    assert ws.roadmap_path.exists()


def test_post_merge_cycle_skipped_when_no_pending_pr(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pull_request_opened in log → don't poll gh at all."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_open_pr=False,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    polled = []
    monkeypatch.setattr(
        mf, "get_pr_state",
        lambda **kw: polled.append(kw) or (None, {}),
    )
    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok", stdout="https://x"),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    # No PR opened ever → nothing to poll
    assert polled == []


def test_auto_merge_invokes_gh_pr_merge_when_enabled(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto_merge=True + non-regressing scorecard → captain calls
    gh pr merge after opening the PR. No need for admiral."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.scorecard import (
        DimensionScore,
        Scorecard,
        write_baseline,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_push=True,
        auto_open_pr=True,
        auto_merge=True,
        auto_merge_method="squash",
        # PR7 R3#7: auto_merge requires verify_cmd
        verify_cmd="true",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    pre = Scorecard(
        repo_path=str(repo),
        dimensions=[DimensionScore(name="docs_present", score=0.0)],
        aggregate=0.0,
    )
    write_baseline(ws.branch_baseline_path, pre)

    merge_calls: list[dict] = []

    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(
            ok=True, summary="ok", stdout="https://github.com/o/r/pull/1",
        ),
    )

    def fake_merge(*, repo_path: str, head: str, method: str = "squash", **_kw):
        merge_calls.append({"head": head, "method": method})
        return mf.CmdResult(ok=True, summary="ok")
    monkeypatch.setattr(mf, "auto_merge_pr", fake_merge)

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert len(merge_calls) == 1
    assert merge_calls[0]["head"] == "codex/captain-test-app"
    assert merge_calls[0]["method"] == "squash"

    # An auto_merge_initiated reference should land in the log.
    log = read_captain_log(ws)
    initiated = [
        e for e in log
        if (e.references or {}).get("event") == "auto_merge_initiated"
    ]
    assert len(initiated) == 1
    assert initiated[0].references.get("merged_by") == "captain"


def test_auto_merge_skipped_when_flag_disabled(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default auto_merge=False → captain opens PR, never calls gh pr merge."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_push=True,
        auto_open_pr=True,
        auto_merge=False,  # explicit
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok", stdout="https://x"),
    )
    merge_calls = []
    monkeypatch.setattr(
        mf, "auto_merge_pr",
        lambda **kw: merge_calls.append(kw) or mf.CmdResult(ok=True, summary="ok"),
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)
    assert merge_calls == []


def test_auto_merge_blocked_when_scorecard_regression(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate scorecard delta < min_delta → captain logs escalation
    instead of merging. PR stays open for admiral."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.scorecard import (
        DimensionScore,
        Scorecard,
        write_baseline,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_push=True,
        auto_open_pr=True,
        auto_merge=True,
        # PR7 R3#7: auto_merge requires verify_cmd
        verify_cmd="true",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    # Baseline aggregate higher than what live repo will score → forced
    # negative delta (live repo is brand-new with only README + git → low score).
    pre = Scorecard(
        repo_path=str(repo),
        dimensions=[DimensionScore(name="docs_present", score=1.0)],
        aggregate=1.0,
    )
    write_baseline(ws.branch_baseline_path, pre)

    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok", stdout="https://x"),
    )
    merge_calls = []
    monkeypatch.setattr(
        mf, "auto_merge_pr",
        lambda **kw: merge_calls.append(kw) or mf.CmdResult(ok=True, summary="ok"),
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    # Did NOT merge
    assert merge_calls == []
    # DID escalate
    log = read_captain_log(ws)
    escalations = [
        e for e in log
        if e.kind == "escalation_raised"
        and (e.references or {}).get("event") == "auto_merge_blocked"
    ]
    assert len(escalations) == 1
    assert "below min_delta" in escalations[0].rationale


def test_auto_merge_failure_escalates_and_leaves_pr_open(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gh pr merge fails (e.g. branch protection) → log escalation,
    leave PR open. _maybe_handle_pr_merge will not see MERGED state
    and admiral can resolve."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_push=True,
        auto_open_pr=True,
        auto_merge=True,
        # PR7 R3#7: auto_merge requires verify_cmd
        verify_cmd="true",
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok", stdout="https://x"),
    )
    monkeypatch.setattr(
        mf, "auto_merge_pr",
        lambda **_kw: mf.CmdResult(
            ok=False, summary="exit 1: branch protection requires 1 review",
        ),
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="in_flight")],
    )
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    escalations = [
        e for e in log
        if e.kind == "escalation_raised"
        and (e.references or {}).get("event") == "auto_merge_failed"
    ]
    assert len(escalations) == 1
    assert "branch protection" in escalations[0].rationale


def test_post_merge_cycle_ignores_already_handled_merge(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If pull_request_merged is already in the log AFTER the latest
    pull_request_opened, the cycle has already run — don't re-trigger."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.protocol import (
        CaptainLogEntry,
        append_captain_log,
        write_roadmap,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app",
        name="Test",
        repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    append_captain_log(
        ws,
        CaptainLogEntry(app_id="test-app", kind="pull_request_opened",
                        references={"pr_url": "https://x", "branch": "codex/captain-test-app"}),
    )
    append_captain_log(
        ws,
        CaptainLogEntry(app_id="test-app", kind="pull_request_merged",
                        references={"pr_url": "https://x"}),
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    polled = []
    monkeypatch.setattr(
        mf, "get_pr_state",
        lambda **kw: polled.append(kw) or ("MERGED", {}),
    )
    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok", stdout="https://x"),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)
    # Already-handled — don't poll
    assert polled == []


# ---------------------------------------------------------------------------
# Phase A: feature backlog ship-mark on roadmap merge
# ---------------------------------------------------------------------------


def test_backlog_explicit_tag_marks_shipped(tmp_path: Path) -> None:
    from chad_captain.protocol import (
        FeatureBacklog, FeatureBacklogItem,
        read_feature_backlog, write_feature_backlog,
    )
    from chad_captain.validator import _mark_backlog_items_shipped
    ws = AppWorkspace("bl-test", base=tmp_path)
    ws.ensure()
    write_feature_backlog(ws, FeatureBacklog(
        app_id="bl-test",
        items=[
            FeatureBacklogItem(id="fb-001", title="Cover A/B testing dashboard", priority=0.9),
            FeatureBacklogItem(id="fb-002", title="Email automation flow", priority=0.7),
        ],
    ))
    rm = Roadmap(app_id="bl-test", slices=[
        RoadmapSlice(slice_id="S1", objective="x",
                     title="Add A/B test endpoint [fb-001]", status="done"),
        RoadmapSlice(slice_id="S2", objective="y",
                     title="Bump linter version", status="done"),
    ])
    shipped = _mark_backlog_items_shipped(ws, rm, pr_url="https://github.com/x/y/pull/99")
    assert shipped == ["fb-001"]
    bl2 = read_feature_backlog(ws)
    assert bl2.by_id("fb-001").status == "shipped"
    assert bl2.by_id("fb-001").shipped_in == "https://github.com/x/y/pull/99"
    assert bl2.by_id("fb-002").status == "queued"


def test_backlog_token_overlap_marks_shipped(tmp_path: Path) -> None:
    from chad_captain.protocol import (
        FeatureBacklog, FeatureBacklogItem,
        read_feature_backlog, write_feature_backlog,
    )
    from chad_captain.validator import _mark_backlog_items_shipped
    ws = AppWorkspace("bl-test", base=tmp_path)
    ws.ensure()
    write_feature_backlog(ws, FeatureBacklog(
        app_id="bl-test",
        items=[
            FeatureBacklogItem(id="fb-001", title="Cover image variation testing", priority=0.9),
        ],
    ))
    rm = Roadmap(app_id="bl-test", slices=[
        RoadmapSlice(slice_id="S1", objective="x",
                     title="Cover image variation upload UI", status="done"),
    ])
    shipped = _mark_backlog_items_shipped(ws, rm, pr_url="PR#42")
    assert shipped == ["fb-001"]
    bl2 = read_feature_backlog(ws)
    assert bl2.by_id("fb-001").status == "shipped"


def test_backlog_no_overlap_leaves_queued(tmp_path: Path) -> None:
    from chad_captain.protocol import (
        FeatureBacklog, FeatureBacklogItem,
        read_feature_backlog, write_feature_backlog,
    )
    from chad_captain.validator import _mark_backlog_items_shipped
    ws = AppWorkspace("bl-test", base=tmp_path)
    ws.ensure()
    write_feature_backlog(ws, FeatureBacklog(
        app_id="bl-test",
        items=[
            FeatureBacklogItem(id="fb-001", title="Cover A/B testing dashboard", priority=0.9),
        ],
    ))
    rm = Roadmap(app_id="bl-test", slices=[
        RoadmapSlice(slice_id="S1", objective="x",
                     title="Update database migration script", status="done"),
    ])
    shipped = _mark_backlog_items_shipped(ws, rm, pr_url="PR#1")
    assert shipped == []
    bl2 = read_feature_backlog(ws)
    assert bl2.by_id("fb-001").status == "queued"


def test_backlog_skips_already_shipped(tmp_path: Path) -> None:
    from chad_captain.protocol import (
        FeatureBacklog, FeatureBacklogItem,
        read_feature_backlog, write_feature_backlog,
    )
    from chad_captain.validator import _mark_backlog_items_shipped
    ws = AppWorkspace("bl-test", base=tmp_path)
    ws.ensure()
    write_feature_backlog(ws, FeatureBacklog(
        app_id="bl-test",
        items=[
            FeatureBacklogItem(id="fb-001", title="Cover A/B testing", status="shipped",
                               shipped_in="PR#1", priority=0.9),
        ],
    ))
    rm = Roadmap(app_id="bl-test", slices=[
        RoadmapSlice(slice_id="S1", objective="x",
                     title="Cover A/B testing followup [fb-001]", status="done"),
    ])
    shipped = _mark_backlog_items_shipped(ws, rm, pr_url="PR#2")
    # Already shipped items aren't re-shipped.
    assert shipped == []
    bl2 = read_feature_backlog(ws)
    assert bl2.by_id("fb-001").shipped_in == "PR#1"  # not overwritten


# ===========================================================================
# Cycle B — PR-conflict 3-source loop break
# ===========================================================================


def test_pr_conflict_emits_log_and_admiral_note_once(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the captain's pending PR is OPEN+DIRTY, _maybe_handle_pr_merge:
      - returns True (suppresses the re-emit fall-through)
      - emits ONE pr_conflict log entry
      - writes ONE admiral_note
    A second tick must NOT re-emit either (de-dup by reading log history).
    """
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.protocol import (
        CaptainLogEntry,
        append_captain_log,
        list_unread_admiral_notes,
        read_captain_log,
        write_roadmap,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    pr_url = "https://github.com/owner/repo/pull/174"
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id="test-app", slice_id=None, kind="pull_request_opened",
            rationale="PR opened",
            references={"pr_url": pr_url, "branch": "codex/captain-test-app"},
        ),
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    monkeypatch.setattr(
        mf, "get_pr_state",
        lambda **_kw: (
            "OPEN",
            {
                "number": 174, "url": pr_url,
                "mergeStateStatus": "DIRTY", "mergeable": "CONFLICTING",
                "isDraft": False,
            },
        ),
    )
    # Mock these so they fail loudly if reached (they shouldn't be).
    sentinel_calls = {"push": 0, "open_pr": 0}

    def fake_push(**_kw):
        sentinel_calls["push"] += 1
        return mf.CmdResult(ok=True, summary="ok")

    def fake_open_pr(**_kw):
        sentinel_calls["open_pr"] += 1
        return mf.CmdResult(ok=True, summary="ok", stdout=pr_url)

    monkeypatch.setattr(mf, "push_captain_branch", fake_push)
    monkeypatch.setattr(mf, "open_pull_request", fake_open_pr)

    # Tick 1: should emit pr_conflict + admiral_note, suppress re-emit.
    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    pr_conflict_entries = [e for e in log if e.kind == "pr_conflict"]
    assert len(pr_conflict_entries) == 1
    entry = pr_conflict_entries[0]
    assert entry.references["merge_state_status"] == "DIRTY"
    assert entry.references["pr_url"] == pr_url

    notes = list_unread_admiral_notes(ws)
    assert len(notes) == 1
    note_body = notes[0].read_text()
    assert "DIRTY" in note_body
    assert pr_url in note_body
    # Suppression: roadmap_complete + new pull_request_opened did NOT fire
    # this tick (push and open_pr were never called).
    assert sentinel_calls["push"] == 0
    assert sentinel_calls["open_pr"] == 0
    # Count check: only the 1 seeded pull_request_opened exists; no new one.
    pr_opened_count = sum(1 for e in log if e.kind == "pull_request_opened")
    assert pr_opened_count == 1
    # No roadmap_complete was emitted this tick (handler didn't run).
    assert all(e.kind != "roadmap_complete" for e in log)

    # Tick 2: same conflict state — must NOT re-emit pr_conflict.
    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)
    log2 = read_captain_log(ws)
    pr_conflict_entries_2 = [e for e in log2 if e.kind == "pr_conflict"]
    assert len(pr_conflict_entries_2) == 1, "pr_conflict must not re-emit"


def test_roadmap_complete_log_dedups_within_run(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive ticks with all-done roadmap must NOT emit two
    roadmap_complete entries when no intervening terminal event happened.
    """
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.protocol import (
        CaptainLogEntry, append_captain_log, read_captain_log, write_roadmap,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_open_pr=False,  # keep test simple — just probe the dedup
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)
    log_after_tick1 = read_captain_log(ws)
    rc1 = [e for e in log_after_tick1 if e.kind == "roadmap_complete"]
    assert len(rc1) == 1

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)
    log_after_tick2 = read_captain_log(ws)
    rc2 = [e for e in log_after_tick2 if e.kind == "roadmap_complete"]
    # Cycle B: dedup. Second tick must NOT add another roadmap_complete.
    assert len(rc2) == 1


def test_pull_request_opened_suppressed_when_already_exists(
    ws: AppWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When open_pull_request reports PR_ALREADY_EXISTS_MARKER, the
    pull_request_opened log entry must NOT be appended again."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain.protocol import (
        CaptainLogEntry, append_captain_log, read_captain_log, write_roadmap,
    )
    import chad_captain.merge_facilitator as mf

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="Test", repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test-app",
        pr_base_branch="main",
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    pr_url = "https://github.com/owner/repo/pull/9"

    # No pre-existing pull_request_opened entry — _maybe_handle_pr_merge
    # short-circuits (no pending_pr_url) and we fall through to
    # _handle_roadmap_complete. open_pull_request reports already-exists.

    monkeypatch.setattr(
        mf, "push_captain_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ok"),
    )
    monkeypatch.setattr(
        mf, "open_pull_request",
        lambda **_kw: mf.CmdResult(
            ok=True,
            summary=f"{mf.PR_ALREADY_EXISTS_MARKER} (#9, OPEN)",
            stdout=pr_url,
        ),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    pr_opened = [e for e in log if e.kind == "pull_request_opened"]
    # Suppression: marker means "no fresh open" → no log entry.
    assert len(pr_opened) == 0
    # roadmap_complete still fires (first time).
    assert any(e.kind == "roadmap_complete" for e in log)


# ---------------------------------------------------------------------------
# Cycle C — pluggable validator + dispatched-slice snapshot + retry context
# ---------------------------------------------------------------------------


def _stub_registry(monkeypatch: pytest.MonkeyPatch, *apps) -> None:
    """Helper: replace load_registry() with a stub returning the given apps."""
    from chad_captain.apps_registry import AppsRegistry
    fake = AppsRegistry(apps=list(apps))
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake,
    )


def test_dispatch_writes_snapshot_before_current_slice(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """Cycle C HIGH-3 R2 fix: snapshot must exist on disk before
    current_slice.json (the runner's go-signal). At end of dispatch tick,
    both files exist and contain the same slice."""
    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="O")])
    write_roadmap(ws, rm)

    status = captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)
    assert "dispatched s1" in status
    assert ws.last_dispatched_slice_path.exists()
    assert ws.current_slice_path.exists()

    from chad_captain.protocol import read_last_dispatched_slice
    snap = read_last_dispatched_slice(ws)
    assert snap is not None
    assert snap.slice_id == "s1"
    assert "O" in snap.user_prompt  # real prompt, not blank


def test_validation_clears_snapshot_after_consuming(ws: AppWorkspace) -> None:
    """Cycle C HIGH-2 R1 fix: prompts don't linger on disk."""
    from chad_captain.protocol import (
        CurrentSlice as _CS,
        write_last_dispatched_slice,
    )
    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )
    write_last_dispatched_slice(ws, _CS(
        slice_id="s1", app_id="test-app", objective="A", title="A",
        system_prompt="SYS", user_prompt="USR", repo_path="/tmp/r",
    ))

    captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)

    # Snapshot was for s1; it's been consumed → should be gone.
    assert not ws.last_dispatched_slice_path.exists()


def test_validation_uses_snapshot_prompts_when_present(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom validator receives real prompts from the snapshot, not blanks."""
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain.protocol import (
        CurrentSlice as _CS,
        write_last_dispatched_slice,
    )

    captured: dict = {}

    def fake_validate(*, ws, complete, dispatched_slice, repo_path,
                      reg_app, score_delta, was_retry, use_baseline_scorecard):
        from chad_captain.validator import ValidationResult
        captured["system_prompt"] = dispatched_slice.system_prompt
        captured["user_prompt"] = dispatched_slice.user_prompt
        captured["slice_id"] = dispatched_slice.slice_id
        return ValidationResult(verdict="accept", rationale="custom ok")

    import sys
    import types
    mod = types.ModuleType("test_cycle_c_validator")
    mod.validate_app_completion = fake_validate
    sys.modules["test_cycle_c_validator"] = mod

    _stub_registry(
        monkeypatch,
        RegisteredApp(
            app_id="test-app", name="T", repo_path="/tmp/r",
            mode="autonomous",
            validator_module="test_cycle_c_validator",
        ),
    )

    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )
    write_last_dispatched_slice(ws, _CS(
        slice_id="s1", app_id="test-app", objective="A", title="A",
        system_prompt="MY SYSTEM PROMPT", user_prompt="MY USER PROMPT",
        repo_path="/tmp/r",
    ))

    captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)

    assert captured["slice_id"] == "s1"
    assert captured["system_prompt"] == "MY SYSTEM PROMPT"
    assert captured["user_prompt"] == "MY USER PROMPT"

    log = read_captain_log(ws)
    accepts = [e for e in log if e.kind == "validate" and e.verdict == "accept"]
    assert len(accepts) == 1
    assert "custom ok" in accepts[0].rationale


def test_custom_validator_missing_module_escalates(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cycle C HIGH-1 R1 fix: fail-CLOSED on missing module (not silent
    fallback to default chain — default may accept silently)."""
    from chad_captain.apps_registry import RegisteredApp
    _stub_registry(
        monkeypatch,
        RegisteredApp(
            app_id="test-app", name="T", repo_path="/tmp/r",
            mode="autonomous",
            validator_module="nonexistent.module.path",
        ),
    )

    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)

    log = read_captain_log(ws)
    # Exactly one validate entry, and it must be escalate (not accept).
    validates = [e for e in log if e.kind == "validate"]
    assert len(validates) == 1
    assert validates[0].verdict == "escalate"

    # Roadmap slice must NOT be marked done — escalate is a blocked status.
    rm2 = read_roadmap(ws)
    assert rm2.slices[0].status == "blocked"


def test_custom_validator_missing_attribute_escalates(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Module loads but lacks validate_app_completion → escalate."""
    import sys
    import types
    from chad_captain.apps_registry import RegisteredApp

    mod = types.ModuleType("test_cycle_c_empty_validator")
    sys.modules["test_cycle_c_empty_validator"] = mod  # no validate_app_completion

    _stub_registry(
        monkeypatch,
        RegisteredApp(
            app_id="test-app", name="T", repo_path="/tmp/r",
            mode="autonomous",
            validator_module="test_cycle_c_empty_validator",
        ),
    )

    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)

    log = read_captain_log(ws)
    validates = [e for e in log if e.kind == "validate"]
    assert len(validates) == 1
    assert validates[0].verdict == "escalate"


def test_custom_validator_runtime_error_escalates(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom validator raises → captain catches, escalates, doesn't crash."""
    import sys
    import types
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain.protocol import (
        CurrentSlice as _CS,
        write_last_dispatched_slice,
    )

    mod = types.ModuleType("test_cycle_c_crash_validator")

    def crash(**_kwargs):
        raise RuntimeError("validator boom")

    mod.validate_app_completion = crash
    sys.modules["test_cycle_c_crash_validator"] = mod

    _stub_registry(
        monkeypatch,
        RegisteredApp(
            app_id="test-app", name="T", repo_path="/tmp/r",
            mode="autonomous",
            validator_module="test_cycle_c_crash_validator",
        ),
    )

    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )
    write_last_dispatched_slice(ws, _CS(
        slice_id="s1", app_id="test-app", objective="A", title="A",
        system_prompt="SYS", user_prompt="USR", repo_path="/tmp/r",
    ))

    captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)

    log = read_captain_log(ws)
    validates = [e for e in log if e.kind == "validate"]
    assert len(validates) == 1
    assert validates[0].verdict == "escalate"
    assert "validator boom" in validates[0].rationale


def test_custom_validator_with_missing_snapshot_escalates(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cycle C HIGH-1 R2 fix: when validator_module is set and the snapshot
    is missing, captain escalates (does NOT call custom validator with blank
    proxy prompts)."""
    import sys
    import types
    from chad_captain.apps_registry import RegisteredApp

    called = {"n": 0}

    def should_not_run(**_kwargs):
        called["n"] += 1
        from chad_captain.validator import ValidationResult
        return ValidationResult(verdict="accept", rationale="should not run")

    mod = types.ModuleType("test_cycle_c_unused_validator")
    mod.validate_app_completion = should_not_run
    sys.modules["test_cycle_c_unused_validator"] = mod

    _stub_registry(
        monkeypatch,
        RegisteredApp(
            app_id="test-app", name="T", repo_path="/tmp/r",
            mode="autonomous",
            validator_module="test_cycle_c_unused_validator",
        ),
    )

    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )
    # NO snapshot written — simulates upgrade or lost file.

    captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)

    assert called["n"] == 0  # custom validator must NOT have been called
    log = read_captain_log(ws)
    validates = [e for e in log if e.kind == "validate"]
    assert len(validates) == 1
    assert validates[0].verdict == "escalate"
    assert "snapshot" in validates[0].rationale.lower()


def test_default_validator_uses_proxy_when_snapshot_missing(
    ws: AppWorkspace,
) -> None:
    """Back-compat: no validator_module + no snapshot = default chain runs
    against a proxy slice (blank prompts). Existing behavior preserved."""
    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )
    # No snapshot, no registered app at all.

    captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)

    log = read_captain_log(ws)
    validates = [e for e in log if e.kind == "validate"]
    assert len(validates) == 1
    # Default chain: clean exit + files_changed → accept.
    assert validates[0].verdict == "accept"


def test_retry_context_threaded_into_next_dispatch_prompt(
    ws: AppWorkspace,
) -> None:
    """Cycle C MED-5 R1 fix: rejected slice's rationale shows up in the
    retry's user_prompt as 'PRIOR ATTEMPT FAILED: ...'."""
    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    # Trigger reject_retry: clean exit but no files changed.
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="zero changes",
                      files_changed=[]),
    )

    captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)

    # First tick: validate reject_retry + retry_context written + slice
    # re-queued + retry dispatched (the captain dispatches in same tick).
    assert ws.current_slice_path.exists()
    cs = read_current_slice(ws)
    assert cs.slice_id == "s1-retry"
    assert "PRIOR ATTEMPT FAILED" in cs.user_prompt
    assert "no files changed" in cs.user_prompt.lower()
    # Sidecar cleared after consumption.
    assert not ws.retry_context_path.exists()


def test_retry_context_cleared_when_stale_slice_id(ws: AppWorkspace) -> None:
    """Defensive clear: a sidecar pointing at a different slice id must not
    bleed into an unrelated retry."""
    from chad_captain.protocol import RetryContext, write_retry_context
    write_retry_context(
        ws,
        RetryContext(slice_id="ghost-slice", rationale="not-this-slice"),
    )
    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A")])
    write_roadmap(ws, rm)

    # Manually trigger a "retry" detection by seeding a recent reject_retry
    # validate entry for s1 (so is_retry path is taken), but no real prior
    # rejection of s1 specifically — sidecar is for ghost-slice, must clear.
    from chad_captain.protocol import CaptainLogEntry, append_captain_log
    append_captain_log(ws, CaptainLogEntry(
        app_id="test-app", slice_id="s1", kind="validate",
        verdict="reject_retry", rationale="prior",
    ))

    captain_tick(ws, repo_path="/tmp/r", use_baseline_scorecard=False)

    # Stale sidecar for "ghost-slice" must be gone (defensive clear),
    # and retry prompt must NOT contain "not-this-slice".
    assert not ws.retry_context_path.exists()
    cs = read_current_slice(ws)
    assert cs is not None
    assert "not-this-slice" not in cs.user_prompt


def test_kill_replan_in_bad_verdicts_trips_circuit_breaker(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cycle C HIGH-3 R2 fix: kill_replan now counts toward the circuit
    breaker so unbounded timeout loops are bounded."""
    from chad_captain.validator import _BAD_VERDICTS
    assert "kill_replan" in _BAD_VERDICTS, (
        "kill_replan must be in _BAD_VERDICTS so circuit breaker counts "
        "repeated timeouts (Cycle C HIGH-3 R2 fix)"
    )


# ---------------------------------------------------------------------------
# Cycle D — auto_replan policy + roadmap_drained event
# ---------------------------------------------------------------------------


def test_roadmap_drained_event_emitted_before_exhausted_replan(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When roadmap has no dispatchable queued slice but is not yet
    terminal, captain logs roadmap_drained before triggering replan."""
    rm = Roadmap(
        app_id="test-app",
        slices=[
            # All slices are blocked by a non-existent dep — none dispatchable,
            # not in terminal state either.
            RoadmapSlice(slice_id="s1", objective="A", status="blocked"),
            RoadmapSlice(slice_id="s2", objective="B", status="queued",
                         blocked_by=["nonexistent"]),
        ],
    )
    write_roadmap(ws, rm)

    # Stub replan so we don't depend on LLM/research config in tests.
    from chad_captain.replanner import replan as _real_replan
    def fake_replan(ws, repo_path, *, trigger="exhausted", **kw):
        new_rm = Roadmap(
            app_id=ws.app_id,
            slices=[RoadmapSlice(slice_id="post-replan",
                                 objective="post", status="queued")],
        )
        write_roadmap(ws, new_rm)
        return new_rm
    monkeypatch.setattr("chad_captain.replanner.replan", fake_replan)

    # Drained-but-not-terminal: is_roadmap_complete is False (s2 is queued
    # but un-dispatchable), so we go through the auto_replan path.
    captain_tick(ws, repo_path="/tmp/r", auto_replan=True,
                 use_baseline_scorecard=False)

    log = read_captain_log(ws)
    drained = [e for e in log if e.kind == "roadmap_drained"]
    assert len(drained) == 1
    refs = drained[0].references
    assert refs["blocked"] == "1"
    assert refs["queued"] == "1"


def test_roadmap_drained_not_emitted_when_auto_replan_off(
    ws: AppWorkspace,
) -> None:
    """auto_replan=False → captain just returns 'roadmap exhausted'; no
    drained event (admiral controls replan timing)."""
    rm = Roadmap(
        app_id="test-app",
        slices=[
            RoadmapSlice(slice_id="s1", objective="A", status="blocked"),
            RoadmapSlice(slice_id="s2", objective="B", status="queued",
                         blocked_by=["nonexistent"]),
        ],
    )
    write_roadmap(ws, rm)

    captain_tick(ws, repo_path="/tmp/r", auto_replan=False,
                 use_baseline_scorecard=False)

    log = read_captain_log(ws)
    assert not any(e.kind == "roadmap_drained" for e in log)


def test_registered_app_auto_replan_field_default_true() -> None:
    """Back-compat default — daemon's pre-Cycle-D behavior preserved."""
    from chad_captain.apps_registry import RegisteredApp
    a = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x")
    assert a.auto_replan is True


def test_registered_app_auto_replan_can_be_disabled() -> None:
    """T1 (Spark) opts out via auto_replan=False."""
    from chad_captain.apps_registry import RegisteredApp
    a = RegisteredApp(app_id="spark", name="Spark", repo_path="/tmp/s",
                      auto_replan=False)
    assert a.auto_replan is False


def test_daemon_passes_per_app_auto_replan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """daemon._tick_one threads RegisteredApp.auto_replan into captain_tick."""
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain import daemon

    captured: dict = {}

    def fake_tick(ws, *, repo_path, auto_replan):
        captured["auto_replan"] = auto_replan
        return "ok"

    monkeypatch.setattr(daemon, "captain_tick", fake_tick)
    monkeypatch.setenv("CHAD_FLEET_APPS_DIR", str(tmp_path))

    app = RegisteredApp(
        app_id="t", name="T", repo_path="/tmp/r",
        mode="autonomous", auto_replan=False,
    )
    status = daemon._tick_one(app)
    assert status == "ok"
    assert captured["auto_replan"] is False


# ---------------------------------------------------------------------------
# Cycle E — RoadmapSlice.custom_prompt passthrough
# ---------------------------------------------------------------------------


def test_custom_system_prompt_replaces_default() -> None:
    from chad_captain.validator import build_current_slice
    rs = RoadmapSlice(
        slice_id="s1", objective="O",
        custom_system_prompt="YOU ARE A MANUSCRIPT EDITOR.",
    )
    cs = build_current_slice(rs, app_id="t", repo_path="/tmp/r")
    assert cs.system_prompt == "YOU ARE A MANUSCRIPT EDITOR."
    # User prompt path unchanged when only system was customized.
    assert "OBJECTIVE: O" in cs.user_prompt


def test_custom_user_prompt_replaces_default() -> None:
    from chad_captain.validator import build_current_slice
    rs = RoadmapSlice(
        slice_id="s1", objective="O",
        custom_user_prompt="REWRITE CHAPTER 3 IN A LOWER REGISTER.",
    )
    cs = build_current_slice(rs, app_id="t", repo_path="/tmp/r")
    assert cs.user_prompt.startswith("REWRITE CHAPTER 3 IN A LOWER REGISTER.")
    # No "OBJECTIVE: ..." prefix when user provides their own prompt.
    assert "OBJECTIVE:" not in cs.user_prompt


def test_custom_prompts_both_replaced() -> None:
    from chad_captain.validator import build_current_slice
    rs = RoadmapSlice(
        slice_id="s1", objective="O",
        custom_system_prompt="SYS",
        custom_user_prompt="USR",
    )
    cs = build_current_slice(rs, app_id="t", repo_path="/tmp/r")
    assert cs.system_prompt == "SYS"
    assert cs.user_prompt.strip() == "USR"


def test_custom_user_prompt_still_appends_extra_context() -> None:
    """Cycle C retry plumbing must keep working with custom prompts."""
    from chad_captain.validator import build_current_slice
    rs = RoadmapSlice(
        slice_id="s1", objective="O",
        custom_user_prompt="MY CUSTOM PROMPT",
    )
    cs = build_current_slice(
        rs, app_id="t", repo_path="/tmp/r",
        extra_context="PRIOR ATTEMPT FAILED: bad things",
    )
    assert "MY CUSTOM PROMPT" in cs.user_prompt
    assert "PRIOR ATTEMPT FAILED: bad things" in cs.user_prompt


def test_default_prompts_when_custom_unset() -> None:
    """Back-compat: existing roadmaps without custom_* fields use defaults."""
    from chad_captain.validator import build_current_slice
    rs = RoadmapSlice(slice_id="s1", objective="add a TODO")
    cs = build_current_slice(rs, app_id="t", repo_path="/tmp/r")
    assert "careful coding agent" in cs.system_prompt
    assert "OBJECTIVE: add a TODO" in cs.user_prompt


def test_roadmap_slice_custom_prompt_round_trips() -> None:
    """Pydantic round-trip preserves custom_* fields."""
    rs = RoadmapSlice(
        slice_id="s1", objective="O",
        custom_system_prompt="SYS",
        custom_user_prompt="USR",
    )
    raw = rs.model_dump_json()
    loaded = RoadmapSlice.model_validate_json(raw)
    assert loaded.custom_system_prompt == "SYS"
    assert loaded.custom_user_prompt == "USR"


def test_roadmap_slice_back_compat_loads_without_custom_fields() -> None:
    """JSON written before Cycle E lacks custom_* — must still load."""
    legacy = (
        '{"slice_id": "old", "objective": "O", "title": "", "phase": "", '
        '"estimated_minutes": 30, "blocked_by": [], "status": "queued", '
        '"notes": ""}'
    )
    rs = RoadmapSlice.model_validate_json(legacy)
    assert rs.custom_system_prompt is None
    assert rs.custom_user_prompt is None


# ---------------------------------------------------------------------------
# PR2 R3-HIGH-2 — post-merge refresh failure fail-closed
# ---------------------------------------------------------------------------


def test_post_merge_refresh_failure_pauses_dispatch_and_keeps_roadmap(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Codex R3 finding: when refresh_base_branch fails, captain previously
    cleared roadmap + emitted post_merge_cycle. Next tick could re-dispatch
    new work on the stale captain branch. Fix: pause dispatch, KEEP roadmap,
    don't emit post_merge_cycle."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain import merge_facilitator as mf

    repo = tmp_path / "repo"
    repo.mkdir()
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="T", repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/test-branch",
        pr_base_branch="main",
        auto_open_pr=True,
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    # Seed a pull_request_opened entry so _maybe_handle_pr_merge has work.
    from chad_captain.protocol import CaptainLogEntry, append_captain_log
    append_captain_log(ws, CaptainLogEntry(
        app_id="test-app", slice_id=None, kind="pull_request_opened",
        rationale="seed",
        references={"pr_url": "https://github.com/o/r/pull/1"},
    ))

    # Stub gh: PR is MERGED so post-merge handler runs.
    monkeypatch.setattr(
        mf, "get_pr_state",
        lambda **_kw: ("MERGED", {"mergeCommit": {"oid": "abc123"},
                                   "mergedAt": "2026-05-05T00:00:00Z"}),
    )
    # Refresh fails:
    monkeypatch.setattr(
        mf, "refresh_base_branch",
        lambda **_kw: mf.CmdResult(ok=False, summary="fetch failed: timeout"),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    # Pause file MUST be set (dispatch blocked).
    assert ws.pause_until_path.exists()
    pause_data = __import__("json").loads(ws.pause_until_path.read_text())
    assert pause_data.get("reason") == "post_merge_refresh_failed"

    # Roadmap MUST still exist (don't clear when refresh failed).
    assert ws.roadmap_path.exists()

    # post_merge_cycle MUST NOT have been emitted.
    log = read_captain_log(ws)
    assert not any(e.kind == "post_merge_cycle" for e in log)
    # Escalation WAS emitted with the right event tag.
    escalations = [
        e for e in log
        if e.kind == "escalation_raised"
        and (e.references or {}).get("event") == "post_merge_refresh_failed"
    ]
    assert len(escalations) == 1
    assert "stale-branch" in escalations[0].rationale.lower()


def test_post_merge_refresh_success_clears_roadmap_and_emits_cycle(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Back-compat: when refresh succeeds, the original behavior holds —
    roadmap cleared, post_merge_cycle emitted."""
    from chad_captain.apps_registry import AppsRegistry, RegisteredApp
    from chad_captain import merge_facilitator as mf

    repo = tmp_path / "repo"
    repo.mkdir()
    rm = Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="A", status="done")],
    )
    write_roadmap(ws, rm)

    fake_reg = AppsRegistry(apps=[RegisteredApp(
        app_id="test-app", name="T", repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/test-branch",
        pr_base_branch="main",
        auto_open_pr=True,
        verify_cmd=None,  # skip post-merge verify
    )])
    monkeypatch.setattr(
        "chad_captain.apps_registry.load_registry", lambda: fake_reg,
    )

    from chad_captain.protocol import CaptainLogEntry, append_captain_log
    append_captain_log(ws, CaptainLogEntry(
        app_id="test-app", slice_id=None, kind="pull_request_opened",
        rationale="seed",
        references={"pr_url": "https://github.com/o/r/pull/1"},
    ))

    monkeypatch.setattr(
        mf, "get_pr_state",
        lambda **_kw: ("MERGED", {"mergeCommit": {"oid": "abc123"},
                                   "mergedAt": "2026-05-05T00:00:00Z"}),
    )
    monkeypatch.setattr(
        mf, "refresh_base_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="ff: at abc123"),
    )
    monkeypatch.setattr(
        mf, "delete_local_branch",
        lambda **_kw: mf.CmdResult(ok=True, summary="deleted"),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    assert any(e.kind == "post_merge_cycle" for e in log)
    # Roadmap WAS cleared.
    assert not ws.roadmap_path.exists()


# ---------------------------------------------------------------------------
# PR10 R3#7 v6 §validation L2: verify_cmd wrapper enforcement
# ---------------------------------------------------------------------------


def test_custom_validator_skipped_when_verify_cmd_fails(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Custom validator MUST NOT run when verify_cmd fails. Even if the
    custom validator would have returned accept, the L1 build gate
    short-circuits to reject_retry."""
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain.protocol import (
        CurrentSlice as _CS,
        write_last_dispatched_slice,
    )

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    custom_called = {"n": 0}

    def fake_validate(*, ws, complete, dispatched_slice, repo_path,
                      reg_app, score_delta, was_retry, use_baseline_scorecard):
        from chad_captain.validator import ValidationResult
        custom_called["n"] += 1
        # Custom validator would shamelessly accept everything.
        return ValidationResult(verdict="accept", rationale="custom always accepts")

    import sys
    import types
    mod = types.ModuleType("test_pr10_permissive_validator")
    mod.validate_app_completion = fake_validate
    sys.modules["test_pr10_permissive_validator"] = mod

    _stub_registry(
        monkeypatch,
        RegisteredApp(
            app_id="test-app", name="T", repo_path=str(repo),
            mode="autonomous",
            validator_module="test_pr10_permissive_validator",
            verify_cmd="false",  # always exits non-zero
            verify_timeout_seconds=30,
        ),
    )

    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )
    write_last_dispatched_slice(ws, _CS(
        slice_id="s1", app_id="test-app", objective="A", title="A",
        system_prompt="x", user_prompt="y", repo_path=str(repo),
    ))

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert custom_called["n"] == 0, "custom validator was called despite failed verify_cmd"
    log = read_captain_log(ws)
    rejects = [e for e in log if e.kind == "validate" and e.verdict == "reject_retry"]
    assert len(rejects) == 1
    assert "verify_cmd failed BEFORE custom validator" in rejects[0].rationale


def test_custom_validator_runs_when_verify_cmd_passes(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Happy path: verify_cmd passes, custom validator runs and its
    verdict (accept) is honored."""
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain.protocol import (
        CurrentSlice as _CS,
        write_last_dispatched_slice,
    )

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    custom_called = {"n": 0}

    def fake_validate(*, ws, complete, dispatched_slice, repo_path,
                      reg_app, score_delta, was_retry, use_baseline_scorecard):
        from chad_captain.validator import ValidationResult
        custom_called["n"] += 1
        return ValidationResult(verdict="accept", rationale="custom ok")

    import sys
    import types
    mod = types.ModuleType("test_pr10_passing_validator")
    mod.validate_app_completion = fake_validate
    sys.modules["test_pr10_passing_validator"] = mod

    _stub_registry(
        monkeypatch,
        RegisteredApp(
            app_id="test-app", name="T", repo_path=str(repo),
            mode="autonomous",
            validator_module="test_pr10_passing_validator",
            verify_cmd="true",  # always exits 0
            verify_timeout_seconds=30,
        ),
    )

    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )
    write_last_dispatched_slice(ws, _CS(
        slice_id="s1", app_id="test-app", objective="A", title="A",
        system_prompt="x", user_prompt="y", repo_path=str(repo),
    ))

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    assert custom_called["n"] == 1
    log = read_captain_log(ws)
    accepts = [e for e in log if e.kind == "validate" and e.verdict == "accept"]
    assert len(accepts) == 1
    assert "custom ok" in accepts[0].rationale


def test_custom_validator_with_no_verify_cmd_runs_normally(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When verify_cmd is unset, the pre-check is skipped (back-compat)
    and the custom validator owns the verdict."""
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain.protocol import (
        CurrentSlice as _CS,
        write_last_dispatched_slice,
    )

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    custom_called = {"n": 0}

    def fake_validate(*, ws, complete, dispatched_slice, repo_path,
                      reg_app, score_delta, was_retry, use_baseline_scorecard):
        from chad_captain.validator import ValidationResult
        custom_called["n"] += 1
        return ValidationResult(verdict="accept", rationale="custom ok")

    import sys
    import types
    mod = types.ModuleType("test_pr10_no_verify_validator")
    mod.validate_app_completion = fake_validate
    sys.modules["test_pr10_no_verify_validator"] = mod

    _stub_registry(
        monkeypatch,
        RegisteredApp(
            app_id="test-app", name="T", repo_path=str(repo),
            mode="autonomous",
            validator_module="test_pr10_no_verify_validator",
            # verify_cmd intentionally unset
        ),
    )

    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )
    write_last_dispatched_slice(ws, _CS(
        slice_id="s1", app_id="test-app", objective="A", title="A",
        system_prompt="x", user_prompt="y", repo_path=str(repo),
    ))

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)
    assert custom_called["n"] == 1


def test_default_validator_is_unaffected_by_verify_pre_check(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Default chain already runs apply_verify_gate at the END; the new
    pre-check is custom-validator-only. Confirm default chain still
    runs verify_cmd via the existing post-process path."""
    from chad_captain.apps_registry import RegisteredApp

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    _stub_registry(
        monkeypatch,
        RegisteredApp(
            app_id="test-app", name="T", repo_path=str(repo),
            mode="autonomous",
            verify_cmd="false",  # always fails
            verify_timeout_seconds=30,
        ),
    )

    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="A",
                                      status="in_flight")])
    write_roadmap(ws, rm)
    write_slice_complete(
        ws,
        SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=5,
                      goose_exit_code=0, summary="done", files_changed=["a.py"]),
    )

    captain_tick(ws, repo_path=str(repo), use_baseline_scorecard=False)

    log = read_captain_log(ws)
    rejects = [e for e in log if e.kind == "validate"
               and e.verdict in ("reject_retry", "reject_hard")]
    assert len(rejects) == 1
    # Default chain's verify_gate uses different rationale prefix —
    # NOT the new "BEFORE custom validator" message.
    assert "BEFORE custom validator" not in rejects[0].rationale
    assert "verify_cmd" in rejects[0].rationale


# ---------------------------------------------------------------------------
# PR12 R3#7 v6 §validation close: remote verify_cmd via SSH
# ---------------------------------------------------------------------------


def test_run_verify_gate_local_path_unchanged(tmp_path: Path) -> None:
    """No verify_host => existing local subprocess path."""
    from chad_captain.validator import run_verify_gate
    passed, summary = run_verify_gate(
        repo_path=str(tmp_path), verify_cmd="true", timeout_seconds=10,
    )
    assert passed is True
    assert "passed" in summary


def test_run_verify_gate_with_verify_host_invokes_ssh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """verify_host present => ssh argv built + executed."""
    import subprocess
    from chad_captain.apps_registry import VerifyHost
    from chad_captain.validator import run_verify_gate

    captured = {}

    class FakeProc:
        returncode = 0
        stdout = "remote ok"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)

    vh = VerifyHost(
        hostname="ci.example.com", user="builder", port=2222,
        identity_file="/keys/id", remote_workdir="/srv/build",
        ssh_options=["ConnectTimeout=10"],
    )
    passed, summary = run_verify_gate(
        repo_path="/ignored", verify_cmd="make check",
        timeout_seconds=120, verify_host=vh,
    )
    assert passed is True
    argv = captured["argv"]
    assert argv[0] == "ssh"
    assert "-i" in argv and "/keys/id" in argv
    assert "-p" in argv and "2222" in argv
    assert "-o" in argv and "ConnectTimeout=10" in argv
    # BatchMode default added since user didn't set it.
    assert "BatchMode=yes" in argv
    # Target + remote command at the end.
    assert "builder@ci.example.com" in argv
    assert any("cd /srv/build && make check" in a for a in argv)


def test_run_verify_gate_remote_failure_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote non-zero exit => passed=False with stderr tail."""
    import subprocess
    from chad_captain.apps_registry import VerifyHost
    from chad_captain.validator import run_verify_gate

    class FakeProc:
        returncode = 2
        stdout = ""
        stderr = "build broke at line 42"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeProc())

    vh = VerifyHost(hostname="h")
    passed, summary = run_verify_gate(
        repo_path="/ignored", verify_cmd="make check",
        timeout_seconds=10, verify_host=vh,
    )
    assert passed is False
    assert "exit 2" in summary
    assert "build broke at line 42" in summary


def test_run_verify_gate_remote_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSH timeout => passed=False with a timeout summary."""
    import subprocess
    from chad_captain.apps_registry import VerifyHost
    from chad_captain.validator import run_verify_gate

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", fake_run)

    vh = VerifyHost(hostname="h")
    passed, summary = run_verify_gate(
        repo_path="/ignored", verify_cmd="make check",
        timeout_seconds=5, verify_host=vh,
    )
    assert passed is False
    assert "timed out" in summary


def test_run_verify_gate_user_batchmode_not_double_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If user already specified BatchMode, captain must not duplicate it."""
    import subprocess
    from chad_captain.apps_registry import VerifyHost
    from chad_captain.validator import run_verify_gate

    captured = {}

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)

    vh = VerifyHost(hostname="h", ssh_options=["BatchMode=no"])
    run_verify_gate(
        repo_path="/x", verify_cmd="t", timeout_seconds=5, verify_host=vh,
    )
    batch_modes = [a for a in captured["argv"] if a.startswith("BatchMode=")]
    assert batch_modes == ["BatchMode=no"]


# ---------------------------------------------------------------------------
# PR15 R3#7 v6 §6.4: producer-pending check on roadmap_complete
# ---------------------------------------------------------------------------


def test_pending_produces_empty_when_no_manifest(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """No task_manifest declared => no gate fires (back-compat)."""
    from chad_captain.validator import _pending_produces
    monkeypatch.setenv("CHAD_FLEET_ARTIFACTS_DIR", str(tmp_path / "art"))
    assert _pending_produces(ws) == set()


def test_pending_produces_returns_all_when_bus_empty(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Manifest declares produces but bus has no manifest yet => all pending."""
    from chad_captain.protocol import TaskManifest, write_task_manifest
    from chad_captain.validator import _pending_produces
    monkeypatch.setenv("CHAD_FLEET_ARTIFACTS_DIR", str(tmp_path / "art"))
    write_task_manifest(ws, TaskManifest(
        task_id="t-1", produces=["spec.v1", "fixtures.v1"],
    ))
    assert _pending_produces(ws) == {"spec.v1", "fixtures.v1"}


def test_pending_produces_subtracts_published(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Bus manifest contains some declared artifacts => only the rest pending."""
    import json
    from chad_captain.protocol import TaskManifest, write_task_manifest
    from chad_captain.validator import _pending_produces
    art_root = tmp_path / "art"
    monkeypatch.setenv("CHAD_FLEET_ARTIFACTS_DIR", str(art_root))
    write_task_manifest(ws, TaskManifest(
        task_id="t-1", produces=["spec.v1", "fixtures.v1", "report.v1"],
    ))
    bus_manifest_dir = art_root / "t-1"
    bus_manifest_dir.mkdir(parents=True)
    (bus_manifest_dir / "manifest.json").write_text(json.dumps({
        "task_id": "t-1",
        "artifacts": {
            "spec.v1": {"name": "spec.v1", "schema_id": "s",
                        "produced_at": "now",
                        "produced_by_app_id": "p"},
            "fixtures.v1": {"name": "fixtures.v1", "schema_id": "s",
                            "produced_at": "now",
                            "produced_by_app_id": "p"},
        },
    }))
    assert _pending_produces(ws) == {"report.v1"}


def test_pending_produces_empty_when_all_published(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import json
    from chad_captain.protocol import TaskManifest, write_task_manifest
    from chad_captain.validator import _pending_produces
    art_root = tmp_path / "art"
    monkeypatch.setenv("CHAD_FLEET_ARTIFACTS_DIR", str(art_root))
    write_task_manifest(ws, TaskManifest(
        task_id="t-1", produces=["spec.v1"],
    ))
    bus_manifest_dir = art_root / "t-1"
    bus_manifest_dir.mkdir(parents=True)
    (bus_manifest_dir / "manifest.json").write_text(json.dumps({
        "task_id": "t-1",
        "artifacts": {
            "spec.v1": {"name": "spec.v1", "schema_id": "s",
                        "produced_at": "now",
                        "produced_by_app_id": "p"},
        },
    }))
    assert _pending_produces(ws) == set()


def test_handle_roadmap_complete_blocks_pr_when_producer_pending(
    ws: AppWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Roadmap structurally complete + manifest.produces missing from bus
    => log roadmap_complete_pending_producer + DO NOT call PR open path."""
    from chad_captain.apps_registry import RegisteredApp
    from chad_captain.protocol import TaskManifest, write_task_manifest
    from chad_captain.validator import _handle_roadmap_complete

    repo = tmp_path / "repo"
    _git_init_repo(repo)
    monkeypatch.setenv("CHAD_FLEET_ARTIFACTS_DIR", str(tmp_path / "art"))

    write_task_manifest(ws, TaskManifest(
        task_id="t-7", produces=["spec.v1"],
    ))

    push_calls = {"n": 0}

    def boom_push(*a, **kw):
        push_calls["n"] += 1
        raise AssertionError("push_captain_branch must NOT be called")

    monkeypatch.setattr(
        "chad_captain.merge_facilitator.push_captain_branch", boom_push,
    )

    reg_app = RegisteredApp(
        app_id="test-app", name="T", repo_path=str(repo),
        mode="autonomous",
        captain_branch="codex/captain-test",
        auto_push=True, auto_open_pr=True,
        verify_cmd="true",
    )
    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s1", objective="O",
                                      status="done")])

    _handle_roadmap_complete(ws, str(repo), rm, reg_app)

    assert push_calls["n"] == 0
    log = read_captain_log(ws, limit=10)
    pending = [e for e in log if e.kind == "roadmap_complete_pending_producer"]
    assert len(pending) == 1
    assert "spec.v1" in pending[0].rationale

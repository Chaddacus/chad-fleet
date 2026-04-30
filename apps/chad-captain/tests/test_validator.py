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


def test_nonzero_exit_first_attempt_retries() -> None:
    r = validate_slice(complete=_complete(exit_code=7), slice_=_slice())
    assert r.verdict == "reject_retry"


def test_nonzero_exit_after_retry_hard_rejects() -> None:
    r = validate_slice(complete=_complete(exit_code=7), slice_=_slice(parent="s1"))
    assert r.verdict == "reject_hard"


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
    assert cs.slice_id.endswith("-retry")

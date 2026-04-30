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
    advance_roadmap,
    build_current_slice,
    captain_tick,
    next_queued_slice,
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

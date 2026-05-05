"""Round-trip + I/O tests for the captain ↔ goose-runner ↔ dashboard protocol."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chad_captain.protocol import (
    AdmiralNote,
    AppWorkspace,
    CaptainLogEntry,
    CurrentSlice,
    ProgressEvent,
    Roadmap,
    RoadmapSlice,
    SliceComplete,
    append_captain_log,
    append_progress,
    clear_current_slice,
    clear_slice_complete,
    consume_admiral_note,
    list_unread_admiral_notes,
    read_captain_log,
    read_current_slice,
    read_roadmap,
    read_slice_complete,
    write_admiral_note,
    write_current_slice,
    write_roadmap,
    write_slice_complete,
)


@pytest.fixture
def ws(tmp_path: Path) -> AppWorkspace:
    w = AppWorkspace("test-app", base=tmp_path)
    w.ensure()
    return w


# --- CurrentSlice round-trip ---


def test_current_slice_roundtrip(ws: AppWorkspace) -> None:
    s = CurrentSlice(
        slice_id="2026-04-30-01",
        app_id="test-app",
        objective="Add a TODO comment to README",
        system_prompt="You are a careful coder.",
        user_prompt="Edit README.md to add a TODO at the bottom.",
        repo_path="/tmp/some-repo",
        expected_rubric_categories=["documentation"],
    )
    write_current_slice(ws, s)
    assert ws.current_slice_path.exists()

    s2 = read_current_slice(ws)
    assert s2 is not None
    assert s2.slice_id == s.slice_id
    assert s2.objective == s.objective
    assert s2.expected_rubric_categories == ["documentation"]


def test_clear_current_slice_idempotent(ws: AppWorkspace) -> None:
    # Calling clear when nothing is there is a no-op, not an error.
    clear_current_slice(ws)
    assert not ws.current_slice_path.exists()

    s = CurrentSlice(
        slice_id="x",
        app_id="test-app",
        objective="o",
        system_prompt="s",
        user_prompt="u",
        repo_path="/tmp",
    )
    write_current_slice(ws, s)
    clear_current_slice(ws)
    assert not ws.current_slice_path.exists()


# --- Progress jsonl ---


def test_progress_append_creates_file(ws: AppWorkspace) -> None:
    e1 = ProgressEvent(slice_id="s1", kind="slice_started", detail={"pid": 1234})
    e2 = ProgressEvent(slice_id="s1", kind="tool_call", detail={"tool": "developer__edit"})
    append_progress(ws, e1)
    append_progress(ws, e2)

    lines = ws.progress_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["kind"] == "slice_started"
    assert json.loads(lines[1])["detail"] == {"tool": "developer__edit"}


# --- SliceComplete round-trip ---


def test_slice_complete_roundtrip(ws: AppWorkspace) -> None:
    sc = SliceComplete(
        slice_id="s1",
        app_id="test-app",
        duration_seconds=42.0,
        goose_exit_code=0,
        summary="Edited README.md, ran tests.",
        files_changed=["README.md"],
    )
    write_slice_complete(ws, sc)
    sc2 = read_slice_complete(ws)
    assert sc2 is not None
    assert sc2.slice_id == "s1"
    assert sc2.duration_seconds == 42.0
    assert sc2.goose_exit_code == 0


def test_clear_slice_complete(ws: AppWorkspace) -> None:
    sc = SliceComplete(slice_id="s1", app_id="test-app", duration_seconds=1.0, goose_exit_code=0, summary="ok")
    write_slice_complete(ws, sc)
    assert ws.slice_complete_path.exists()
    clear_slice_complete(ws)
    assert not ws.slice_complete_path.exists()


# --- Captain log ---


def test_captain_log_append_and_read(ws: AppWorkspace) -> None:
    e1 = CaptainLogEntry(
        app_id="test-app", slice_id="s1", kind="dispatch",
        rationale="initial slice",
    )
    e2 = CaptainLogEntry(
        app_id="test-app", slice_id="s1", kind="validate", verdict="accept",
        rubric_delta_pp=1.2, rationale="passed all gates",
    )
    append_captain_log(ws, e1)
    append_captain_log(ws, e2)

    log = read_captain_log(ws)
    assert len(log) == 2
    assert log[0].kind == "dispatch"
    assert log[1].verdict == "accept"
    assert log[1].rubric_delta_pp == 1.2


def test_captain_log_skips_corrupt_lines(ws: AppWorkspace, tmp_path: Path) -> None:
    # Manually inject a bogus line, ensure read_captain_log skips it cleanly.
    ws.captain_log_path.parent.mkdir(parents=True, exist_ok=True)
    ws.captain_log_path.write_text(
        '{"app_id":"x","kind":"validate"}\n'
        "this is not json\n"
        '{"app_id":"x","kind":"dispatch"}\n'
    )
    log = read_captain_log(ws)
    # All three entries are missing required fields per Pydantic; ensure no
    # crash. Strict-mode validation drops the entries, so length may be 0.
    assert isinstance(log, list)


def test_captain_log_limit(ws: AppWorkspace) -> None:
    for i in range(10):
        append_captain_log(
            ws,
            CaptainLogEntry(app_id="test-app", kind="dispatch", rationale=f"#{i}"),
        )
    last3 = read_captain_log(ws, limit=3)
    assert len(last3) == 3
    assert last3[-1].rationale == "#9"


# --- Roadmap ---


def test_roadmap_roundtrip(ws: AppWorkspace) -> None:
    r = Roadmap(
        app_id="test-app",
        objective_summary="Reach 80/100 on enterprise rubric",
        slices=[
            RoadmapSlice(slice_id="s1", objective="Add API contracts", phase="foundation"),
            RoadmapSlice(slice_id="s2", objective="Add tests", phase="foundation", blocked_by=["s1"]),
        ],
    )
    write_roadmap(ws, r)
    r2 = read_roadmap(ws)
    assert r2 is not None
    assert len(r2.slices) == 2
    assert r2.slices[1].blocked_by == ["s1"]


# --- Admiral notes ---


def test_admiral_note_lifecycle(ws: AppWorkspace) -> None:
    note = AdmiralNote(
        note_id="20260430-001",
        app_id="test-app",
        body="Stop pursuing api-contracts; switch to docs work.",
    )
    path = write_admiral_note(ws, note)
    assert path.exists()

    unread = list_unread_admiral_notes(ws)
    assert len(unread) == 1
    assert unread[0] == path

    # consume
    consume_admiral_note(ws, path)
    assert not path.exists()
    assert (ws.admiral_notes_consumed_dir / path.name).exists()
    assert list_unread_admiral_notes(ws) == []


def test_admiral_notes_consumed_subdir_excluded_from_unread(ws: AppWorkspace, tmp_path: Path) -> None:
    # `list_unread_admiral_notes` globs *.json NON-recursively, so files under
    # the `consumed/` subdir do not show up. (The glob('*.json') pattern only
    # matches direct children of admiral_notes/ — verifying that here.)
    note = AdmiralNote(note_id="n1", app_id="test-app", body="hi")
    write_admiral_note(ws, note)
    p = list_unread_admiral_notes(ws)[0]
    consume_admiral_note(ws, p)
    # Drop a fresh note alongside the consumed/ subdir
    write_admiral_note(ws, AdmiralNote(note_id="n2", app_id="test-app", body="hi2"))
    unread = list_unread_admiral_notes(ws)
    assert len(unread) == 1
    assert unread[0].name == "n2.json"


# --- Workspace plumbing ---


def test_workspace_paths_under_base(tmp_path: Path) -> None:
    ws = AppWorkspace("foo", base=tmp_path)
    assert ws.root == tmp_path / "foo"
    assert ws.current_slice_path == tmp_path / "foo" / "current_slice.json"
    assert ws.captain_log_path == tmp_path / "foo" / "captain_log.jsonl"
    assert ws.research_path == tmp_path / "foo" / "research" / "app-profile.json"


def test_workspace_ensure_creates_tree(tmp_path: Path) -> None:
    ws = AppWorkspace("bar", base=tmp_path)
    assert not ws.root.exists()
    ws.ensure()
    assert ws.root.is_dir()
    assert ws.admiral_notes_dir.is_dir()
    assert ws.admiral_notes_consumed_dir.is_dir()
    assert ws.research_path.parent.is_dir()


# ---------------------------------------------------------------------------
# Cycle C — last_dispatched_slice snapshot + retry_context sidecar
# ---------------------------------------------------------------------------


def test_last_dispatched_slice_roundtrip(ws: AppWorkspace) -> None:
    from chad_captain.protocol import (
        clear_last_dispatched_slice,
        read_last_dispatched_slice,
        write_last_dispatched_slice,
    )
    s = CurrentSlice(
        slice_id="cyc-c-01",
        app_id="test-app",
        objective="cycle c roundtrip",
        title="cycle c",
        system_prompt="SYS PROMPT WITH SUBSTANCE",
        user_prompt="USER PROMPT WITH SUBSTANCE",
        repo_path="/tmp/r",
    )
    write_last_dispatched_slice(ws, s)
    assert ws.last_dispatched_slice_path.exists()
    loaded = read_last_dispatched_slice(ws)
    assert loaded is not None
    assert loaded.slice_id == "cyc-c-01"
    assert loaded.system_prompt == "SYS PROMPT WITH SUBSTANCE"
    assert loaded.user_prompt == "USER PROMPT WITH SUBSTANCE"

    clear_last_dispatched_slice(ws)
    assert not ws.last_dispatched_slice_path.exists()
    assert read_last_dispatched_slice(ws) is None


def test_last_dispatched_slice_returns_none_when_corrupt(ws: AppWorkspace) -> None:
    from chad_captain.protocol import read_last_dispatched_slice
    ws.last_dispatched_slice_path.write_text("not-json")
    assert read_last_dispatched_slice(ws) is None


def test_clear_last_dispatched_slice_no_op_when_absent(ws: AppWorkspace) -> None:
    from chad_captain.protocol import clear_last_dispatched_slice
    # Should not raise even if file never existed.
    clear_last_dispatched_slice(ws)
    assert not ws.last_dispatched_slice_path.exists()


def test_retry_context_roundtrip(ws: AppWorkspace) -> None:
    from chad_captain.protocol import (
        RetryContext,
        clear_retry_context,
        read_retry_context,
        write_retry_context,
    )
    ctx = RetryContext(
        slice_id="cyc-c-01",
        rationale="pytest exit 1: assert 0 == 1",
        retry_hint="adjust the assertion threshold",
    )
    write_retry_context(ws, ctx)
    loaded = read_retry_context(ws)
    assert loaded is not None
    assert loaded.slice_id == "cyc-c-01"
    assert loaded.rationale == "pytest exit 1: assert 0 == 1"
    assert loaded.retry_hint == "adjust the assertion threshold"

    clear_retry_context(ws)
    assert read_retry_context(ws) is None


def test_retry_context_returns_none_when_corrupt(ws: AppWorkspace) -> None:
    from chad_captain.protocol import read_retry_context
    ws.retry_context_path.write_text("not-json")
    assert read_retry_context(ws) is None


def test_workspace_cycle_c_paths(tmp_path: Path) -> None:
    w = AppWorkspace("baz", base=tmp_path)
    assert w.last_dispatched_slice_path == tmp_path / "baz" / "last_dispatched_slice.json"
    assert w.retry_context_path == tmp_path / "baz" / "retry_context.json"


# ---------------------------------------------------------------------------
# PR8: backlog generation lock + twin_holds dir
# ---------------------------------------------------------------------------


def test_twin_holds_dir_created_by_ensure(tmp_path: Path) -> None:
    """ws.ensure() must materialize twin_holds_dir for Twin to write into."""
    w = AppWorkspace("hold-test", base=tmp_path)
    w.ensure()
    assert w.twin_holds_dir.exists()
    assert w.twin_holds_dir.is_dir()


def test_twin_holds_dir_path_is_under_root(tmp_path: Path) -> None:
    w = AppWorkspace("hold-test", base=tmp_path)
    assert w.twin_holds_dir == w.root / "twin_holds"


def test_feature_backlog_default_generation_zero(ws: AppWorkspace) -> None:
    from chad_captain.protocol import FeatureBacklog
    bl = FeatureBacklog(app_id="x")
    assert bl.generation == 0


def test_update_feature_backlog_increments_generation(ws: AppWorkspace) -> None:
    from chad_captain.protocol import (
        FeatureBacklog, FeatureBacklogItem, read_feature_backlog,
        update_feature_backlog, write_feature_backlog,
    )
    write_feature_backlog(ws, FeatureBacklog(app_id=ws.app_id))
    update_feature_backlog(
        ws,
        lambda b: b.items.append(FeatureBacklogItem(id="fb-001", title="x")),
    )
    on_disk = read_feature_backlog(ws)
    assert on_disk.generation == 1
    assert len(on_disk.items) == 1


def test_update_feature_backlog_cas_conflict_raises(ws: AppWorkspace) -> None:
    """Stale expected_generation => BacklogGenerationConflict."""
    from chad_captain.protocol import (
        BacklogGenerationConflict, FeatureBacklog, FeatureBacklogItem,
        update_feature_backlog, write_feature_backlog,
    )
    write_feature_backlog(ws, FeatureBacklog(app_id=ws.app_id, generation=5))
    with pytest.raises(BacklogGenerationConflict, match="generation mismatch"):
        update_feature_backlog(
            ws,
            lambda b: b.items.append(FeatureBacklogItem(id="fb-001", title="x")),
            expected_generation=99,
        )


def test_update_feature_backlog_cas_match_proceeds(ws: AppWorkspace) -> None:
    """Matching expected_generation => write succeeds and increments."""
    from chad_captain.protocol import (
        FeatureBacklog, FeatureBacklogItem, read_feature_backlog,
        update_feature_backlog, write_feature_backlog,
    )
    write_feature_backlog(ws, FeatureBacklog(app_id=ws.app_id, generation=5))
    update_feature_backlog(
        ws,
        lambda b: b.items.append(FeatureBacklogItem(id="fb-001", title="x")),
        expected_generation=5,
    )
    assert read_feature_backlog(ws).generation == 6


def _backlog_appender_proc(base_dir: str, app_id: str, item_id: str) -> None:
    """Module-level worker for the concurrent-writer test (must be picklable
    for multiprocessing spawn)."""
    from chad_captain.protocol import (
        AppWorkspace, FeatureBacklogItem, update_feature_backlog,
    )
    w = AppWorkspace(app_id, base=Path(base_dir))
    update_feature_backlog(
        w,
        lambda b: b.items.append(FeatureBacklogItem(id=item_id, title=item_id)),
    )


def test_update_feature_backlog_serializes_concurrent_writers(
    ws: AppWorkspace, tmp_path: Path,
) -> None:
    """Two processes appending in parallel must NOT lose updates — flock
    serializes them and all items end up on disk with generation=N.
    """
    import multiprocessing
    from chad_captain.protocol import (
        FeatureBacklog, read_feature_backlog, write_feature_backlog,
    )

    write_feature_backlog(ws, FeatureBacklog(app_id=ws.app_id))

    base_dir = str(ws.root.parent)
    procs = [
        multiprocessing.Process(
            target=_backlog_appender_proc,
            args=(base_dir, ws.app_id, f"fb-{i:03d}"),
        )
        for i in range(4)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    final = read_feature_backlog(ws)
    assert final.generation == 4
    ids = {it.id for it in final.items}
    assert ids == {"fb-000", "fb-001", "fb-002", "fb-003"}


# ---------------------------------------------------------------------------
# PR9 v6 §6.1 + §6.3.1: trigger queue + goose PID + scope-change abort
# ---------------------------------------------------------------------------


def test_pending_replan_reasons_path_under_root(tmp_path: Path) -> None:
    w = AppWorkspace("pq-test", base=tmp_path)
    assert w.pending_replan_reasons_path == w.root / "pending_replan_reasons.jsonl"


def test_enqueue_then_drain_replan_reasons(ws: AppWorkspace) -> None:
    from chad_captain.protocol import drain_replan_reasons, enqueue_replan_reason
    enqueue_replan_reason(ws, reason="manual", detail="d1")
    enqueue_replan_reason(ws, reason="scope_change", detail="d2")
    enqueue_replan_reason(ws, reason="cost_breach", detail="d3")
    drained = drain_replan_reasons(ws)
    # Sorted by priority (scope_change=0, cost_breach=2, manual=4).
    assert [d["reason"] for d in drained] == ["scope_change", "cost_breach", "manual"]
    # Queue is empty after drain.
    assert drain_replan_reasons(ws) == []


def test_drain_replan_reasons_when_empty(ws: AppWorkspace) -> None:
    from chad_captain.protocol import drain_replan_reasons
    assert drain_replan_reasons(ws) == []


def test_unknown_reason_gets_lowest_priority(ws: AppWorkspace) -> None:
    from chad_captain.protocol import drain_replan_reasons, enqueue_replan_reason
    enqueue_replan_reason(ws, reason="totally_made_up")
    enqueue_replan_reason(ws, reason="scope_change")
    drained = drain_replan_reasons(ws)
    assert drained[0]["reason"] == "scope_change"
    assert drained[1]["reason"] == "totally_made_up"
    assert drained[1]["priority"] == 99


def test_goose_pid_path_under_root(tmp_path: Path) -> None:
    w = AppWorkspace("pid-test", base=tmp_path)
    assert w.goose_pid_path == w.root / "goose.pid"


def test_write_read_clear_goose_pid(ws: AppWorkspace) -> None:
    from chad_captain.protocol import (
        clear_goose_pid, read_goose_pid, write_goose_pid,
    )
    assert read_goose_pid(ws) is None
    write_goose_pid(ws, 12345)
    assert read_goose_pid(ws) == 12345
    clear_goose_pid(ws)
    assert read_goose_pid(ws) is None
    # Clear when already gone is a no-op (no error).
    clear_goose_pid(ws)


def test_send_goose_abort_signal_no_pid_returns_false(ws: AppWorkspace) -> None:
    from chad_captain.protocol import send_goose_abort_signal
    assert send_goose_abort_signal(ws) is False


def test_send_goose_abort_signal_dead_pid_returns_false(ws: AppWorkspace) -> None:
    """Stale PID file (process already exited) => False, no exception."""
    from chad_captain.protocol import send_goose_abort_signal, write_goose_pid
    # PID 1 is init/launchd; we can't kill it, but ProcessLookupError comes
    # from a non-existent PID. Use an obviously-impossible PID.
    write_goose_pid(ws, 999_999_999)
    assert send_goose_abort_signal(ws) is False


def test_send_goose_abort_signal_real_subprocess(ws: AppWorkspace) -> None:
    """Spawn a real long-running subprocess, write its PID, abort.
    The subprocess must exit due to SIGTERM."""
    import subprocess
    import time
    from chad_captain.protocol import send_goose_abort_signal, write_goose_pid
    proc = subprocess.Popen(["sleep", "30"])
    try:
        write_goose_pid(ws, proc.pid)
        assert send_goose_abort_signal(ws) is True
        # Wait briefly for SIGTERM delivery.
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        assert proc.poll() is not None, "subprocess did not exit after SIGTERM"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_roadmapslice_status_accepts_superseded_by_scope_change() -> None:
    """New status literal must be accepted by Pydantic validation."""
    s = RoadmapSlice(
        slice_id="s1", objective="x",
        status="superseded_by_scope_change",
    )
    assert s.status == "superseded_by_scope_change"

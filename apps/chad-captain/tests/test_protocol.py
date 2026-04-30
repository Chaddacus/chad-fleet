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

"""Cycle 7 tests: chad-week note — ad-hoc observations on any state."""

from __future__ import annotations

import json

import pytest

from week_intake.cli import main
from week_intake.lifecycle import (
    TransitionError,
    abandon_item,
    complete_item,
    record_note,
)
from week_intake.protocol import WeekFolder
from week_intake.types import RouteTarget, WeekItem


WEEK = "2026-W19"


@pytest.fixture
def tmp_week(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path))
    yield tmp_path


def _seed(state: str, item_id: str = "wk-001") -> WeekItem:
    item = WeekItem(
        item_id=item_id, week=WEEK, raw_text="x", title=item_id,
        kind="wip", state=state, confidence=0.9,
        target=RouteTarget(app_id="chad-agent"),
    )
    WeekFolder(week=WEEK).upsert_item(item)
    return item


# ---------------------------------------------------------------------------
# record_note core
# ---------------------------------------------------------------------------


def test_note_appends_to_parsed_item(tmp_week) -> None:
    _seed("parsed")
    item = record_note(WEEK, "wk-001", "discovered apps/social/SocialAccount lives here")
    assert len(item.notes) == 1
    assert "apps/social" in item.notes[0].text


def test_note_does_not_bump_revision(tmp_week) -> None:
    item = _seed("parsed")
    assert item.revision == 0
    record_note(WEEK, "wk-001", "an observation")
    folder = WeekFolder(week=WEEK)
    reread = folder.get_item("wk-001")
    assert reread.revision == 0  # unchanged


def test_note_advances_updated_at(tmp_week) -> None:
    item = _seed("parsed")
    original_updated = item.updated_at
    import time
    time.sleep(0.01)
    record_note(WEEK, "wk-001", "x")
    reread = WeekFolder(week=WEEK).get_item("wk-001")
    assert reread.updated_at > original_updated


@pytest.mark.parametrize(
    "state",
    ["parsed", "needs_clarification", "ready", "routed", "in_progress",
     "blocked", "done", "abandoned"],
)
def test_note_allowed_from_every_state(tmp_week, state) -> None:
    """Notes are observations — allowed regardless of state, including terminal."""
    _seed(state)
    item = record_note(WEEK, "wk-001", f"note from {state}")
    assert len(item.notes) == 1


def test_note_appends_not_replaces(tmp_week) -> None:
    _seed("parsed")
    record_note(WEEK, "wk-001", "first")
    record_note(WEEK, "wk-001", "second")
    item = WeekFolder(week=WEEK).get_item("wk-001")
    assert len(item.notes) == 2
    assert item.notes[0].text == "first"
    assert item.notes[1].text == "second"


def test_note_strips_whitespace(tmp_week) -> None:
    _seed("parsed")
    record_note(WEEK, "wk-001", "  padded  \n")
    item = WeekFolder(week=WEEK).get_item("wk-001")
    assert item.notes[0].text == "padded"


def test_note_rejects_empty_text(tmp_week) -> None:
    _seed("parsed")
    with pytest.raises(ValueError):
        record_note(WEEK, "wk-001", "")


def test_note_rejects_whitespace_only(tmp_week) -> None:
    _seed("parsed")
    with pytest.raises(ValueError):
        record_note(WEEK, "wk-001", "   \n  ")


def test_note_missing_item_raises(tmp_week) -> None:
    with pytest.raises(TransitionError, match="not found"):
        record_note(WEEK, "wk-ghost", "x")


def test_note_round_trips_through_jsonl(tmp_week) -> None:
    _seed("parsed")
    record_note(WEEK, "wk-001", "persisted")
    fresh_folder = WeekFolder(week=WEEK)
    item = fresh_folder.get_item("wk-001")
    assert item.notes[0].text == "persisted"
    assert isinstance(item.notes[0].at, str)
    assert item.notes[0].by == "chad"


def test_note_preserved_across_lifecycle_transitions(tmp_week) -> None:
    _seed("routed")
    record_note(WEEK, "wk-001", "before complete")
    complete_item(WEEK, "wk-001")
    record_note(WEEK, "wk-001", "after complete")
    item = WeekFolder(week=WEEK).get_item("wk-001")
    assert len(item.notes) == 2
    assert item.notes[0].text == "before complete"
    assert item.notes[1].text == "after complete"
    assert item.state == "done"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_note_table_output(tmp_week, capsys) -> None:
    _seed("parsed")
    rc = main(["note", "wk-001", "--week", WEEK, "--text", "hello world"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wk-001" in out
    assert "1 total" in out
    assert "hello world" in out


def test_cli_note_json_output(tmp_week, capsys) -> None:
    _seed("parsed")
    rc = main(["note", "wk-001", "--week", WEEK, "--text", "x", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["notes"][0]["text"] == "x"


def test_cli_note_truncates_long_snippet_in_table(tmp_week, capsys) -> None:
    _seed("parsed")
    long_text = "a" * 200
    rc = main(["note", "wk-001", "--week", WEEK, "--text", long_text])
    assert rc == 0
    out = capsys.readouterr().out
    assert "..." in out  # truncated for display
    # Full text still in storage (not truncated)
    item = WeekFolder(week=WEEK).get_item("wk-001")
    assert item.notes[0].text == long_text


def test_cli_note_missing_item_exits_1(tmp_week, capsys) -> None:
    rc = main(["note", "wk-002", "--week", WEEK, "--text", "x"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_note_empty_text_exits_2(tmp_week, capsys) -> None:
    """argparse --text required → empty arg from --text='' → fails our explicit check."""
    _seed("parsed")
    rc = main(["note", "wk-001", "--week", WEEK, "--text", ""])
    assert rc == 1
    err = capsys.readouterr().err
    assert "non-empty" in err

"""CLI tests for `chad-week complete | abandon | reopen` + route guard."""

from __future__ import annotations

import json

import pytest

from week_intake.cli import main
from week_intake.protocol import WeekFolder
from week_intake.types import RouteTarget, WeekItem


WEEK = "2026-W19"


@pytest.fixture
def tmp_week(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path))
    yield tmp_path


def _seed(state: str, item_id: str = "wk-001", note_id: str | None = None) -> None:
    WeekFolder(week=WEEK).upsert_item(WeekItem(
        item_id=item_id, week=WEEK, raw_text="x", title=item_id,
        kind="wip", state=state, confidence=0.9,
        target=RouteTarget(app_id="chad-agent"),
        captain_note_id=note_id,
    ))


# ---------------------------------------------------------------------------
# complete / abandon / reopen happy paths
# ---------------------------------------------------------------------------


def test_cli_complete_table(tmp_week, capsys) -> None:
    _seed("routed")
    rc = main(["complete", "wk-001", "--week", WEEK])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wk-001" in out and "routed" in out and "done" in out


def test_cli_abandon_with_reason(tmp_week, capsys) -> None:
    _seed("blocked")
    rc = main(["abandon", "wk-001", "--week", WEEK, "--reason", "stale"])
    assert rc == 0
    item = WeekFolder(week=WEEK).get_item("wk-001")
    assert item.lifecycle_log[-1].reason == "stale"


def test_cli_reopen_restores_state(tmp_week, capsys) -> None:
    _seed("routed")
    main(["complete", "wk-001", "--week", WEEK])
    rc = main(["reopen", "wk-001", "--week", WEEK])
    assert rc == 0
    item = WeekFolder(week=WEEK).get_item("wk-001")
    assert item.state == "routed"


def test_cli_complete_json_format(tmp_week, capsys) -> None:
    _seed("routed")
    rc = main(["complete", "wk-001", "--week", WEEK, "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "done"
    assert payload["lifecycle_log"][0]["transition"] == "complete"


# ---------------------------------------------------------------------------
# Transition errors
# ---------------------------------------------------------------------------


def test_cli_complete_from_parsed_exits_1(tmp_week, capsys) -> None:
    _seed("parsed")
    rc = main(["complete", "wk-001", "--week", WEEK])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "parsed" in err


def test_cli_abandon_from_done_exits_1(tmp_week, capsys) -> None:
    _seed("routed")
    main(["complete", "wk-001", "--week", WEEK])
    rc = main(["abandon", "wk-001", "--week", WEEK])
    assert rc == 1


def test_cli_reopen_from_routed_exits_1(tmp_week, capsys) -> None:
    _seed("routed")
    rc = main(["reopen", "wk-001", "--week", WEEK])
    assert rc == 1


def test_cli_complete_missing_item_exits_1(tmp_week, capsys) -> None:
    rc = main(["complete", "wk-ghost", "--week", WEEK])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_legacy_reopen_warns(tmp_week, capsys) -> None:
    # Pre-cycle-5 item: no lifecycle_log.
    folder = WeekFolder(week=WEEK)
    folder.upsert_item(WeekItem(
        item_id="wk-leg", week=WEEK, raw_text="x", state="abandoned",
        kind="wip", confidence=0.5,
    ))
    rc = main(["reopen", "wk-leg", "--week", WEEK])
    assert rc == 0
    out = capsys.readouterr().out
    assert "needs_clarification" in out
    assert "warning" in out.lower()


# ---------------------------------------------------------------------------
# Route guard (cycle 5 tightened)
# ---------------------------------------------------------------------------


def test_cli_route_refuses_done_item(tmp_week, capsys) -> None:
    _seed("done")
    rc = main(["route", "wk-001", "--week", WEEK, "--app", "chad-agent"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "reopen" in err


def test_cli_route_refuses_abandoned_item(tmp_week, capsys) -> None:
    _seed("abandoned")
    rc = main(["route", "wk-001", "--week", WEEK, "--app", "chad-agent"])
    assert rc == 1


def test_cli_route_refuses_in_progress_with_note(tmp_week, capsys) -> None:
    _seed("in_progress", note_id="dn-1")
    rc = main(["route", "wk-001", "--week", WEEK, "--app", "chad-agent"])
    assert rc == 6
    err = capsys.readouterr().err
    assert "captain_note_id" in err


def test_cli_route_refuses_blocked_with_note(tmp_week, capsys) -> None:
    _seed("blocked", note_id="dn-1")
    rc = main(["route", "wk-001", "--week", WEEK, "--app", "chad-agent"])
    assert rc == 6


def test_cli_route_refuses_ready_with_legacy_note(tmp_week, capsys) -> None:
    _seed("ready", note_id="dn-old")
    rc = main(["route", "wk-001", "--week", WEEK, "--app", "chad-agent"])
    assert rc == 6

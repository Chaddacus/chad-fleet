"""CLI integration tests for `chad-week brief`."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from week_intake.cli import main
from week_intake.protocol import WeekFolder
from week_intake.types import RouteTarget, WeekItem

WEEK = "2026-W19"
WEEK_START = datetime.fromisocalendar(2026, 19, 1).replace(tzinfo=timezone.utc)


def _ts(days: float = 1.0) -> str:
    return (WEEK_START + timedelta(days=days)).isoformat()


@pytest.fixture
def tmp_week(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path))
    yield tmp_path


def _seed(item: WeekItem) -> None:
    WeekFolder(week=item.week).upsert_item(item)


def _bundle(captain_log_tail=None, paused_until=None, queued=None):
    return {
        "captain_log_tail": captain_log_tail or [],
        "paused_until": paused_until,
        "current_slice": None,
        "admiral_notes_queued": [{"note_id": n} for n in (queued or [])],
        "admiral_notes_consumed": [],
    }


def test_cli_brief_no_llm_renders_markdown(tmp_week, capsys) -> None:
    _seed(WeekItem(
        item_id="wk-001", week=WEEK, raw_text="x", title="x",
        kind="wip", state="routed", confidence=0.9,
        target=RouteTarget(app_id="chad-agent"), captain_note_id="n",
    ))
    log = [{"ts": _ts(1), "kind": "pull_request_opened"}]
    with patch("week_intake.status.get_app_status_http",
               return_value=_bundle(captain_log_tail=log, queued=["n"])):
        with patch("week_intake.brief.claude_complete") as m:
            rc = main(["brief", "--week", WEEK, "--no-llm"])
    assert rc == 0
    assert m.call_count == 0  # --no-llm: no shellout
    out = capsys.readouterr().out
    assert f"# Week {WEEK}" in out
    assert "chad-agent" in out
    assert "(narrative unavailable)" in out


def test_cli_brief_json_format_round_trips(tmp_week, capsys) -> None:
    _seed(WeekItem(
        item_id="wk-001", week=WEEK, raw_text="x", title="x",
        kind="wip", state="routed", confidence=0.9,
        target=RouteTarget(app_id="chad-agent"), captain_note_id="n",
    ))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        rc = main(["brief", "--week", WEEK, "--no-llm", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["week"] == WEEK
    assert payload["narrative"] == ""
    assert payload["prompt_version"] == 1
    assert payload["used_cache"] is False
    assert "totals" in payload
    assert "apps" in payload
    assert "attention_items" in payload
    assert payload["apps"][0]["app_id"] == "chad-agent"


def test_cli_brief_empty_week_renders_cleanly(tmp_week, capsys) -> None:
    rc = main(["brief", "--week", WEEK, "--no-llm"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"# Week {WEEK}" in out
    assert "0 items" in out


def test_cli_brief_with_llm_calls_claude_complete(tmp_week, capsys) -> None:
    _seed(WeekItem(
        item_id="wk-001", week=WEEK, raw_text="x", title="x",
        kind="wip", state="routed", confidence=0.9,
        target=RouteTarget(app_id="chad-agent"), captain_note_id="n",
    ))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch(
            "week_intake.brief.claude_complete", return_value="all calm."
        ) as m:
            rc = main(["brief", "--week", WEEK])
    assert rc == 0
    assert m.call_count == 1
    out = capsys.readouterr().out
    assert "all calm." in out


def test_cli_brief_refresh_busts_cache(tmp_week, capsys) -> None:
    _seed(WeekItem(
        item_id="wk-001", week=WEEK, raw_text="x", title="x",
        kind="wip", state="routed", confidence=0.9,
        target=RouteTarget(app_id="chad-agent"), captain_note_id="n",
    ))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch(
            "week_intake.brief.claude_complete",
            side_effect=["first.", "second."],
        ) as m:
            main(["brief", "--week", WEEK])
            main(["brief", "--week", WEEK, "--refresh"])
    assert m.call_count == 2

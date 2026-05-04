"""CLI tests for `chad-week status` — width-aware rendering + JSON output."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from week_intake.cli import _attn_indicator, _print_status_table, main
from week_intake.protocol import WeekFolder
from week_intake.types import RouteTarget, WeekItem


def _ts(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _future_iso(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def tmp_week(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path))
    yield tmp_path


def _seed(week_dir, item: WeekItem) -> None:
    folder = WeekFolder(week=item.week)
    folder.upsert_item(item)


# ---------------------------------------------------------------------------
# _attn_indicator unit
# ---------------------------------------------------------------------------


def test_attn_indicator_escalation() -> None:
    assert _attn_indicator({"attention_reason": "escalation"}) == "!E"


def test_attn_indicator_pause() -> None:
    assert _attn_indicator({"attention_reason": "pause"}) == "!P"


def test_attn_indicator_pause_parse_error() -> None:
    assert _attn_indicator({"attention_reason": "pause_parse_error"}) == "!?"


def test_attn_indicator_none() -> None:
    assert _attn_indicator({"attention_reason": None}) == "-"
    assert _attn_indicator({}) == "-"


# ---------------------------------------------------------------------------
# Width-aware table rendering
# ---------------------------------------------------------------------------


def _fake_terminal(cols: int):
    return shutil.os.terminal_size((cols, 24))


def _sample_report() -> dict:
    return {
        "by_state": {"routed": 1},
        "by_app": {"chad-agent": 1},
        "items": [
            {
                "item_id": "wk-001",
                "state": "routed",
                "kind": "wip",
                "app_id": "chad-agent",
                "captain_note_status": "queued",
                "captain_note_id": "n-1",
                "title": "Ship the very long titled feature for chad-agent",
                "slice_in_flight": "implement-the-thing",
                "pause_active": False,
                "pause_reason": None,
                "pause_parse_error": False,
                "last_captain_action": "dispatch",
                "last_meaningful_action": "dispatch",
                "last_action_ts": "2026-05-04T10:00:00+00:00",
                "last_action_rationale": "go",
                "latest_meaningful_is_escalate": False,
                "needs_attention": False,
                "attention_reason": None,
            }
        ],
        "totals": {
            "items": 1,
            "routed": 1,
            "captain_unreachable": 0,
            "needs_attention": 0,
        },
    }


def test_table_wide_terminal_shows_slice_and_action(capsys) -> None:
    with patch("shutil.get_terminal_size", return_value=_fake_terminal(200)):
        _print_status_table("2026-W19", _sample_report())
    out = capsys.readouterr().out
    assert "SLICE" in out
    assert "ACTION" in out
    assert "ATTN" in out
    assert "implement-the-thing" in out


def test_table_medium_terminal_drops_slice_keeps_action(capsys) -> None:
    with patch("shutil.get_terminal_size", return_value=_fake_terminal(100)):
        _print_status_table("2026-W19", _sample_report())
    out = capsys.readouterr().out
    assert "SLICE" not in out
    assert "ACTION" in out
    assert "ATTN" in out


def test_table_narrow_terminal_drops_slice_and_action(capsys) -> None:
    with patch("shutil.get_terminal_size", return_value=_fake_terminal(70)):
        _print_status_table("2026-W19", _sample_report())
    out = capsys.readouterr().out
    assert "SLICE" not in out
    assert "ACTION" not in out
    assert "ATTN" in out


def test_table_lines_fit_terminal_width(capsys) -> None:
    """Title is truncated to remaining width so rows don't blow past cols."""
    cols = 80
    with patch("shutil.get_terminal_size", return_value=_fake_terminal(cols)):
        _print_status_table("2026-W19", _sample_report())
    out = capsys.readouterr().out
    # Find the row containing the data (after headers).
    data_lines = [ln for ln in out.splitlines() if "wk-001" in ln]
    assert data_lines, "expected a data row"
    for ln in data_lines:
        assert len(ln) <= cols, f"row exceeds {cols} cols: {len(ln)!r}"


def test_table_empty_items_renders_summary_only(capsys) -> None:
    report = {
        "by_state": {},
        "by_app": {},
        "items": [],
        "totals": {
            "items": 0,
            "routed": 0,
            "captain_unreachable": 0,
            "needs_attention": 0,
        },
    }
    _print_status_table("2026-W19", report)
    out = capsys.readouterr().out
    assert "0 items" in out


def test_table_attn_marker_renders_for_escalation(capsys) -> None:
    report = _sample_report()
    report["items"][0]["attention_reason"] = "escalation"
    report["items"][0]["needs_attention"] = True
    report["items"][0]["latest_meaningful_is_escalate"] = True
    report["totals"]["needs_attention"] = 1
    with patch("shutil.get_terminal_size", return_value=_fake_terminal(200)):
        _print_status_table("2026-W19", report)
    out = capsys.readouterr().out
    assert "!E" in out
    assert "1 need attention" in out


# ---------------------------------------------------------------------------
# End-to-end CLI: status --format json
# ---------------------------------------------------------------------------


def test_cli_status_json_includes_cycle2_fields(tmp_week, capsys) -> None:
    item = WeekItem(
        item_id="wk-001",
        week="2026-W19",
        raw_text="ship",
        title="ship",
        kind="wip",
        state="routed",
        confidence=0.9,
        target=RouteTarget(app_id="chad-agent"),
        captain_note_id="note-A",
    )
    _seed(tmp_week, item)
    bundle = {
        "admiral_notes_queued": [{"note_id": "note-A"}],
        "paused_until": _future_iso(30),
        "current_slice": {"title": "build-it"},
        "captain_log_tail": [{"ts": _ts(5), "kind": "dispatch", "rationale": "go"}],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        rc = main(["status", "--week", "2026-W19", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    row = payload["items"][0]
    assert row["captain_note_status"] == "queued"
    assert row["pause_active"] is True
    assert row["slice_in_flight"] == "build-it"
    assert row["last_meaningful_action"] == "dispatch"
    assert row["needs_attention"] is True
    assert row["attention_reason"] == "pause"
    assert payload["totals"]["needs_attention"] == 1


def test_cli_status_table_format_runs_without_error(tmp_week, capsys) -> None:
    item = WeekItem(
        item_id="wk-001",
        week="2026-W19",
        raw_text="ship",
        title="ship",
        kind="wip",
        state="parsed",
    )
    _seed(tmp_week, item)
    rc = main(["status", "--week", "2026-W19"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wk-001" in out

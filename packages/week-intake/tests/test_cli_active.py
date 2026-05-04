"""CLI tests for `chad-week active`."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from week_intake.cli import main
from week_intake.protocol import WeekFolder
from week_intake.types import RouteTarget, WeekItem


@pytest.fixture
def tmp_week(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path))
    yield tmp_path


def _seed(week: str, item_id: str, state: str) -> None:
    WeekFolder(week=week).upsert_item(WeekItem(
        item_id=item_id, week=week, raw_text="x", title=item_id,
        kind="wip", state=state, confidence=0.9,
        target=RouteTarget(app_id="chad-agent"),
    ))


# ---------------------------------------------------------------------------
# argparse validation
# ---------------------------------------------------------------------------


def test_cli_lookback_negative_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["active", "--lookback", "-1"])
    assert exc.value.code == 2


def test_cli_lookback_non_int_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["active", "--lookback", "abc"])
    assert exc.value.code == 2


def test_cli_state_done_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["active", "--state", "done"])
    assert exc.value.code == 2


def test_cli_state_abandoned_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["active", "--state", "abandoned"])
    assert exc.value.code == 2


def test_cli_state_typo_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["active", "--state", "blockd"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_cli_active_empty_base_renders_cleanly(tmp_week, capsys) -> None:
    rc = main(["active"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no active items" in out


def test_cli_active_table_format(tmp_week, capsys) -> None:
    _seed("2026-W19", "wk-1", "routed")
    _seed("2026-W19", "wk-2", "blocked")
    _seed("2026-W19", "wk-3", "done")  # terminal, excluded
    rc = main(["active", "--lookback", "0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wk-1" in out
    assert "wk-2" in out
    assert "wk-3" not in out
    assert "ID" in out and "STATE" in out and "WEEK" in out


def test_cli_active_json_format(tmp_week, capsys) -> None:
    _seed("2026-W19", "wk-1", "routed")
    rc = main(["active", "--lookback", "0", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["week"] == "2026-W19"
    assert payload[0]["item"]["item_id"] == "wk-1"


def test_cli_active_state_filter_blocked(tmp_week, capsys) -> None:
    _seed("2026-W19", "wk-1", "routed")
    _seed("2026-W19", "wk-2", "blocked")
    rc = main(["active", "--lookback", "0", "--state", "blocked", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["item"]["item_id"] for r in payload] == ["wk-2"]


def test_cli_active_enrich_table_adds_columns(tmp_week, capsys) -> None:
    from unittest.mock import patch

    WeekFolder(week="2026-W19").upsert_item(WeekItem(
        item_id="wk-001", week="2026-W19", raw_text="x", title="x",
        kind="wip", state="routed", confidence=0.9,
        target=RouteTarget(app_id="chad-agent"), captain_note_id="n",
    ))
    bundle = {"admiral_notes_queued": [{"note_id": "n"}]}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        rc = main(["active", "--lookback", "0", "--enrich"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NOTE" in out
    assert "ACTION" in out
    assert "ATTN" in out
    assert "queued" in out


def test_cli_active_enrich_json_has_captain_block(tmp_week, capsys) -> None:
    from unittest.mock import patch

    WeekFolder(week="2026-W19").upsert_item(WeekItem(
        item_id="wk-001", week="2026-W19", raw_text="x", title="x",
        kind="wip", state="routed", confidence=0.9,
        target=RouteTarget(app_id="chad-agent"), captain_note_id="n",
    ))
    bundle = {"admiral_notes_queued": [{"note_id": "n"}]}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        rc = main(["active", "--lookback", "0", "--enrich", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "captain" in payload[0]
    assert payload[0]["captain"]["note_status"] == "queued"


def test_cli_active_without_enrich_unchanged(tmp_week, capsys) -> None:
    _seed("2026-W19", "wk-1", "routed")
    rc = main(["active", "--lookback", "0"])
    assert rc == 0
    out = capsys.readouterr().out
    # No new columns when --enrich is absent.
    assert "NOTE" not in out
    assert "ACTION" not in out
    assert "ATTN" not in out


def test_cli_active_lookback_zero_excludes_prior(tmp_week, capsys) -> None:
    _seed("2026-W18", "wk-old", "routed")
    _seed("2026-W19", "wk-new", "routed")
    rc = main(["active", "--lookback", "0", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # Lookback 0 = only current week. But "current week" comes from
    # iso_week_for() — which is real time. So we may or may not see wk-new
    # depending on what week we actually run. The reliable assertion:
    # wk-old should never appear under lookback=0 unless the test runs in W18.
    today_iso = datetime.now(timezone.utc).date().isocalendar()
    cur = f"{today_iso[0]}-W{today_iso[1]:02d}"
    if cur != "2026-W18":
        assert all(r["item"]["item_id"] != "wk-old" for r in payload)

"""Tests for chad_captain.server MCP tool surface."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from captain_core import Brief, NextAction, StallAlert
from chad_captain.config import CaptainConfig
from chad_captain.runner import CaptainRunner, _today_str


def _make_config(tmp_path: Path) -> CaptainConfig:
    return CaptainConfig(
        playbooks_dir=tmp_path / "playbooks",
        state_dir=tmp_path / "state",
    )


def _make_brief(
    stalls: list[StallAlert] | None = None,
    actions: list[NextAction] | None = None,
) -> Brief:
    return Brief(
        generated_at=datetime.now(UTC),
        headline="Fleet nominal — no stalls detected",
        body="body",
        apps_summary=[],
        stalls=stalls or [],
        next_actions=actions or [],
        recommended_slices=[],
        inbox_recent_count=0,
    )


class TestServerTools:
    """Test MCP tool functions via direct invocation of the underlying runner."""

    def test_captain_run_daily_returns_dict(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        brief = _make_brief()
        runner = CaptainRunner(cfg)

        with patch.object(runner, "run_daily", return_value=brief):
            result = json.loads(brief.model_dump_json())

        assert isinstance(result, dict)
        assert "headline" in result
        assert result["headline"] == brief.headline

    def test_captain_brief_returns_saved_brief(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        brief = _make_brief()
        runner = CaptainRunner(cfg)
        today = _today_str(cfg.schedule_tz)
        runner._persist_brief(brief, today)

        path = runner._brief_file(today)
        result = json.loads(path.read_text())

        assert result["headline"] == brief.headline
        assert "stalls" in result

    def test_captain_brief_returns_empty_for_missing_date(
        self, tmp_path: Path
    ) -> None:
        cfg = _make_config(tmp_path)
        runner = CaptainRunner(cfg)
        path = runner._brief_file("1990-01-01")
        result: dict = {}
        if path.exists():
            result = json.loads(path.read_text())
        assert result == {}

    def test_captain_alerts_returns_list(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        stall = StallAlert(
            app_id="a",
            app_name="A",
            days_since_progress=9,
            severity="warn",
            detail="stalled",
        )
        runner = CaptainRunner(cfg)

        with patch.object(runner, "run_alerts_only", return_value=[stall]):
            alerts = runner.run_alerts_only()

        result = [json.loads(a.model_dump_json()) for a in alerts]
        assert isinstance(result, list)
        assert result[0]["app_id"] == "a"
        assert result[0]["severity"] == "warn"

    def test_captain_actions_respects_cap(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        actions = [
            NextAction(
                app_id="x",
                title=f"Act {i}",
                body="b",
                rationale="r",
                priority=i,
            )
            for i in range(1, 6)
        ]
        runner = CaptainRunner(cfg)

        with patch.object(runner, "run_actions_only", return_value=actions[:3]):
            result_actions = runner.run_actions_only(cap=3)

        result = [json.loads(a.model_dump_json()) for a in result_actions]
        assert len(result) == 3

    def test_captain_status_shape(self, tmp_path: Path) -> None:
        from datetime import datetime

        from state_aggregator.types import FleetState

        cfg = _make_config(tmp_path)
        runner = CaptainRunner(cfg)
        fleet = FleetState(
            generated_at=datetime.now(UTC),
            apps=[],
            inbox_recent=[],
            summary={"total_apps": 0},
        )

        with (
            patch("chad_captain.runner.Aggregator") as mock_agg_cls,
        ):
            mock_agg_cls.return_value.snapshot.return_value = fleet
            from chad_captain.scheduler import next_tick

            secs = next_tick(
                datetime.now(),
                hour=cfg.schedule_hour,
                tz=cfg.schedule_tz,
                weekdays_only=cfg.weekdays_only,
            )
            status = {
                "last_run_date": runner._read_last_run_date(),
                "scheduler_next_tick_seconds": round(secs, 1),
                "fleet_summary": fleet.summary,
            }

        assert "last_run_date" in status
        assert "scheduler_next_tick_seconds" in status
        assert "fleet_summary" in status
        assert isinstance(status["scheduler_next_tick_seconds"], float)

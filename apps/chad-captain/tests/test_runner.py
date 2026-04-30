"""Tests for chad_captain.runner.CaptainRunner."""

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
        schedule_tz="America/New_York",
    )


def _make_brief(
    stalls: list[StallAlert] | None = None,
    actions: list[NextAction] | None = None,
) -> Brief:
    return Brief(
        generated_at=datetime.now(UTC),
        headline="Fleet nominal — no stalls detected",
        body="body text",
        apps_summary=[],
        stalls=stalls or [],
        next_actions=actions or [],
        recommended_slices=[],
        inbox_recent_count=0,
    )


@pytest.fixture()
def config(tmp_path: Path) -> CaptainConfig:
    return _make_config(tmp_path)


class TestRunDaily:
    def test_run_daily_returns_brief(self, config: CaptainConfig) -> None:
        brief = _make_brief()
        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.load_playbooks_dir", return_value={}),
            patch("chad_captain.runner.compose_daily_brief", return_value=brief),
            patch("chad_captain.runner._build_notifier") as mock_notifier_fn,
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            mock_hub = MagicMock()
            mock_hub.send.return_value = []
            mock_notifier_fn.return_value = mock_hub

            runner = CaptainRunner(config)
            result = runner.run_daily()

        assert result.headline == "Fleet nominal — no stalls detected"

    def test_run_daily_emits_brief_notification(self, config: CaptainConfig) -> None:
        brief = _make_brief()
        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.load_playbooks_dir", return_value={}),
            patch("chad_captain.runner.compose_daily_brief", return_value=brief),
            patch("chad_captain.runner._build_notifier") as mock_notifier_fn,
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            mock_hub = MagicMock()
            mock_hub.send.return_value = []
            mock_notifier_fn.return_value = mock_hub

            runner = CaptainRunner(config)
            runner.run_daily()

        mock_hub.send.assert_called()
        call_args = mock_hub.send.call_args_list[0][0][0]
        assert call_args.channel == config.notifier_channel_brief

    def test_critical_stall_emits_alert_notification(
        self, config: CaptainConfig
    ) -> None:
        stall = StallAlert(
            app_id="a",
            app_name="App A",
            days_since_progress=15,
            severity="critical",
            detail="Critical stall",
        )
        brief = _make_brief(stalls=[stall])
        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.load_playbooks_dir", return_value={}),
            patch("chad_captain.runner.compose_daily_brief", return_value=brief),
            patch("chad_captain.runner._build_notifier") as mock_notifier_fn,
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            mock_hub = MagicMock()
            mock_hub.send.return_value = []
            mock_notifier_fn.return_value = mock_hub

            runner = CaptainRunner(config)
            runner.run_daily()

        channels_sent = [c[0][0].channel for c in mock_hub.send.call_args_list]
        assert config.notifier_channel_brief in channels_sent
        assert config.notifier_channel_alert in channels_sent

    def test_warn_stall_does_not_emit_alert(self, config: CaptainConfig) -> None:
        stall = StallAlert(
            app_id="b",
            app_name="App B",
            days_since_progress=5,
            severity="warn",
            detail="Warning stall",
        )
        brief = _make_brief(stalls=[stall])
        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.load_playbooks_dir", return_value={}),
            patch("chad_captain.runner.compose_daily_brief", return_value=brief),
            patch("chad_captain.runner._build_notifier") as mock_notifier_fn,
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            mock_hub = MagicMock()
            mock_hub.send.return_value = []
            mock_notifier_fn.return_value = mock_hub

            runner = CaptainRunner(config)
            runner.run_daily()

        channels_sent = [c[0][0].channel for c in mock_hub.send.call_args_list]
        assert config.notifier_channel_alert not in channels_sent

    def test_idempotency_skips_notify_on_same_date(
        self, config: CaptainConfig
    ) -> None:
        brief = _make_brief()
        today = _today_str(config.schedule_tz)
        # Pre-persist brief and state
        runner = CaptainRunner(config)
        runner._persist_brief(brief, today)
        runner._write_last_run_date(today)

        with (
            patch("chad_captain.runner._build_notifier") as mock_notifier_fn,
        ):
            mock_hub = MagicMock()
            mock_hub.send.return_value = []
            mock_notifier_fn.return_value = mock_hub

            result = runner.run_daily()

        mock_hub.send.assert_not_called()
        assert result.headline == brief.headline

    def test_idempotency_recomposes_if_file_missing(
        self, config: CaptainConfig
    ) -> None:
        brief = _make_brief()
        today = _today_str(config.schedule_tz)
        runner = CaptainRunner(config)
        # Write last_run_date but NOT the brief file
        runner._write_last_run_date(today)

        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.load_playbooks_dir", return_value={}),
            patch("chad_captain.runner.compose_daily_brief", return_value=brief),
            patch("chad_captain.runner._build_notifier") as mock_notifier_fn,
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            mock_hub = MagicMock()
            mock_hub.send.return_value = []
            mock_notifier_fn.return_value = mock_hub

            result = runner.run_daily()

        # Re-composed but notify still skipped
        mock_hub.send.assert_not_called()
        assert result.headline == brief.headline

    def test_persistence_saves_brief_file(self, config: CaptainConfig) -> None:
        brief = _make_brief()
        today = _today_str(config.schedule_tz)
        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.load_playbooks_dir", return_value={}),
            patch("chad_captain.runner.compose_daily_brief", return_value=brief),
            patch("chad_captain.runner._build_notifier") as mock_notifier_fn,
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            mock_hub = MagicMock()
            mock_hub.send.return_value = []
            mock_notifier_fn.return_value = mock_hub

            runner = CaptainRunner(config)
            runner.run_daily()

        brief_file = runner._brief_file(today)
        assert brief_file.exists()
        data = json.loads(brief_file.read_text())
        assert data["headline"] == brief.headline

    def test_persistence_saves_last_run_date(self, config: CaptainConfig) -> None:
        brief = _make_brief()
        today = _today_str(config.schedule_tz)
        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.load_playbooks_dir", return_value={}),
            patch("chad_captain.runner.compose_daily_brief", return_value=brief),
            patch("chad_captain.runner._build_notifier") as mock_notifier_fn,
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            mock_hub = MagicMock()
            mock_hub.send.return_value = []
            mock_notifier_fn.return_value = mock_hub

            runner = CaptainRunner(config)
            runner.run_daily()

        assert runner._read_last_run_date() == today


class TestRunAlertsOnly:
    def test_alerts_only_returns_list(self, config: CaptainConfig) -> None:
        stall = StallAlert(
            app_id="x",
            app_name="X App",
            days_since_progress=8,
            severity="warn",
            detail="8 days",
        )
        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.detect_stalls", return_value=[stall]),
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            runner = CaptainRunner(config)
            result = runner.run_alerts_only()

        assert len(result) == 1
        assert result[0].app_id == "x"

    def test_alerts_only_empty(self, config: CaptainConfig) -> None:
        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.detect_stalls", return_value=[]),
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            runner = CaptainRunner(config)
            result = runner.run_alerts_only()

        assert result == []


class TestRunActionsOnly:
    def test_actions_only_respects_cap(self, config: CaptainConfig) -> None:
        actions = [
            NextAction(
                app_id="a",
                title=f"Action {i}",
                body="body",
                rationale="r",
                priority=i,
            )
            for i in range(1, 11)
        ]
        with (
            patch("chad_captain.runner.Aggregator") as mock_agg,
            patch("chad_captain.runner.load_playbooks_dir", return_value={}),
            patch("chad_captain.runner.next_actions", return_value=actions),
        ):
            mock_agg.return_value.snapshot.return_value = MagicMock()
            runner = CaptainRunner(config)
            result = runner.run_actions_only(cap=3)

        assert len(result) == 3
        assert result[0].title == "Action 1"

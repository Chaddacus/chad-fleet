"""Tests for chad_captain.config."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chad_captain.config import CaptainConfig, load_config


class TestLoadConfigDefaults:
    def test_default_schedule_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CHAD_CAPTAIN_SCHEDULE_HOUR", raising=False)
        monkeypatch.delenv("CHAD_CAPTAIN_PLAYBOOKS", raising=False)
        cfg = load_config()
        assert cfg.schedule_hour == 9

    def test_default_tz(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CHAD_CAPTAIN_TZ", raising=False)
        monkeypatch.delenv("CHAD_CAPTAIN_PLAYBOOKS", raising=False)
        cfg = load_config()
        assert cfg.schedule_tz == "America/New_York"

    def test_default_weekdays_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CHAD_CAPTAIN_WEEKDAYS_ONLY", raising=False)
        monkeypatch.delenv("CHAD_CAPTAIN_PLAYBOOKS", raising=False)
        cfg = load_config()
        assert cfg.weekdays_only is True

    def test_default_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CAPTAIN_ENABLED", raising=False)
        monkeypatch.delenv("CHAD_CAPTAIN_PLAYBOOKS", raising=False)
        cfg = load_config()
        assert cfg.enabled is True


class TestEnvOverrides:
    def test_schedule_hour_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CHAD_CAPTAIN_SCHEDULE_HOUR", "7")
        monkeypatch.delenv("CHAD_CAPTAIN_PLAYBOOKS", raising=False)
        cfg = load_config()
        assert cfg.schedule_hour == 7

    def test_enabled_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CAPTAIN_ENABLED", "false")
        monkeypatch.delenv("CHAD_CAPTAIN_PLAYBOOKS", raising=False)
        cfg = load_config()
        assert cfg.enabled is False

    def test_playbooks_dir_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CHAD_CAPTAIN_PLAYBOOKS", str(tmp_path))
        cfg = load_config()
        assert cfg.playbooks_dir == tmp_path

    def test_state_dir_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CHAD_CAPTAIN_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.delenv("CHAD_CAPTAIN_PLAYBOOKS", raising=False)
        cfg = load_config()
        assert cfg.state_dir == tmp_path / "state"

    def test_weekdays_only_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHAD_CAPTAIN_WEEKDAYS_ONLY", "0")
        monkeypatch.delenv("CHAD_CAPTAIN_PLAYBOOKS", raising=False)
        cfg = load_config()
        assert cfg.weekdays_only is False


class TestTzValidation:
    def test_valid_tz(self) -> None:
        cfg = CaptainConfig(
            playbooks_dir=Path("/tmp"),
            schedule_tz="Europe/London",
        )
        assert cfg.schedule_tz == "Europe/London"

    def test_invalid_tz_raises(self) -> None:
        with pytest.raises(Exception):
            CaptainConfig(
                playbooks_dir=Path("/tmp"),
                schedule_tz="Not/ATimezone",
            )


class TestHourValidation:
    def test_hour_boundary_low(self) -> None:
        cfg = CaptainConfig(playbooks_dir=Path("/tmp"), schedule_hour=0)
        assert cfg.schedule_hour == 0

    def test_hour_boundary_high(self) -> None:
        cfg = CaptainConfig(playbooks_dir=Path("/tmp"), schedule_hour=23)
        assert cfg.schedule_hour == 23

    def test_hour_out_of_range_raises(self) -> None:
        with pytest.raises(Exception):
            CaptainConfig(playbooks_dir=Path("/tmp"), schedule_hour=25)

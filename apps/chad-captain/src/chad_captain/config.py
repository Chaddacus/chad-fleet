"""Environment-driven configuration for chad-captain."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, field_validator


_DEFAULT_PLAYBOOKS_DIR = (
    Path(__file__).resolve().parents[4]
    / "packages"
    / "captain-playbooks"
    / "playbooks"
)
_DEFAULT_STATE_DIR = Path.home() / ".chad" / "captain"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


class CaptainConfig(BaseModel):
    playbooks_dir: Path
    schedule_hour: int = 9
    schedule_tz: str = "America/New_York"
    weekdays_only: bool = True
    notifier_channel_brief: str = "captain.daily-brief"
    notifier_channel_alert: str = "captain.alert"
    state_dir: Path = _DEFAULT_STATE_DIR
    enabled: bool = True

    @field_validator("schedule_tz")
    @classmethod
    def validate_tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, KeyError) as exc:
            raise ValueError(f"Unknown timezone: {v!r}") from exc
        return v

    @field_validator("schedule_hour")
    @classmethod
    def validate_hour(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError(f"schedule_hour must be 0-23, got {v}")
        return v


def load_config() -> CaptainConfig:
    """Build CaptainConfig from environment variables with sensible defaults."""
    raw_playbooks = os.environ.get("CHAD_CAPTAIN_PLAYBOOKS", "").strip()
    playbooks_dir = Path(raw_playbooks) if raw_playbooks else _DEFAULT_PLAYBOOKS_DIR

    raw_state = os.environ.get("CHAD_CAPTAIN_STATE_DIR", "").strip()
    state_dir = Path(raw_state) if raw_state else _DEFAULT_STATE_DIR

    return CaptainConfig(
        playbooks_dir=playbooks_dir,
        schedule_hour=_env_int("CHAD_CAPTAIN_SCHEDULE_HOUR", 9),
        schedule_tz=os.environ.get("CHAD_CAPTAIN_TZ", "America/New_York").strip()
        or "America/New_York",
        weekdays_only=_env_bool("CHAD_CAPTAIN_WEEKDAYS_ONLY", True),
        notifier_channel_brief=os.environ.get(
            "CHAD_CAPTAIN_CHANNEL_BRIEF", "captain.daily-brief"
        ),
        notifier_channel_alert=os.environ.get(
            "CHAD_CAPTAIN_CHANNEL_ALERT", "captain.alert"
        ),
        state_dir=state_dir,
        enabled=_env_bool("CAPTAIN_ENABLED", True),
    )

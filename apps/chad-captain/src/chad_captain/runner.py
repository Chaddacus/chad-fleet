"""Main pipeline: state snapshot -> captain-core brief -> notifications -> persist."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from captain_core import (
    Brief,
    NextAction,
    StallAlert,
    compose_daily_brief,
    detect_stalls,
    load_playbooks_dir,
    next_actions,
)
from notifier_hub_core import Notification, NotifierHub, SendResult
from state_aggregator import Aggregator

from chad_captain.config import CaptainConfig


def _today_str(tz: str) -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via a sibling temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_state(state_file: Path) -> dict[str, Any]:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass
    return {}


def _highest_severity(stalls: list[StallAlert]) -> str:
    for sev in ("critical", "warn", "info"):
        if any(s.severity == sev for s in stalls):
            return sev
    return "info"


def _build_notifier() -> NotifierHub:
    """Build a NotifierHub with available adapters."""
    hub = NotifierHub()
    # Register adapters if env credentials present; skip silently if not.
    try:
        from notifier_hub_dashboard_inbox import DashboardInboxAdapter  # type: ignore[attr-defined]
        hub.register(DashboardInboxAdapter())
    except Exception:  # noqa: BLE001
        pass
    try:
        from notifier_hub_ntfy import NtfyAdapter  # type: ignore[attr-defined]
        hub.register(NtfyAdapter())
    except Exception:  # noqa: BLE001
        pass
    return hub


class CaptainRunner:
    """Orchestrates state -> brief -> notification -> persist pipeline."""

    def __init__(self, config: CaptainConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _state_file(self) -> Path:
        return self._config.state_dir / "state.json"

    def _brief_file(self, date_str: str) -> Path:
        return self._config.state_dir / "briefs" / f"{date_str}.json"

    def _read_last_run_date(self) -> str | None:
        state = _load_state(self._state_file())
        return state.get("last_run_date")

    def _write_last_run_date(self, date_str: str) -> None:
        state = _load_state(self._state_file())
        state["last_run_date"] = date_str
        _atomic_write(self._state_file(), json.dumps(state, indent=2))

    def _persist_brief(self, brief: Brief, date_str: str) -> None:
        path = self._brief_file(date_str)
        _atomic_write(path, brief.model_dump_json(indent=2))

    def _build_brief(self) -> Brief:
        aggregator = Aggregator()
        fleet_state = aggregator.snapshot()
        playbooks = load_playbooks_dir(self._config.playbooks_dir)
        return compose_daily_brief(fleet_state, playbooks)

    def _emit_notifications(
        self, brief: Brief
    ) -> list[SendResult]:
        hub = _build_notifier()
        severity = _highest_severity(brief.stalls)

        main_notif = Notification(
            title=brief.headline,
            body=brief.body,
            severity=severity,  # type: ignore[arg-type]
            channel=self._config.notifier_channel_brief,
        )
        results = hub.send(main_notif)

        # Critical stalls each get a separate alert.
        for stall in brief.stalls:
            if stall.severity == "critical":
                alert = Notification(
                    title=f"Critical stall: {stall.app_name}",
                    body=stall.detail,
                    severity="critical",
                    channel=self._config.notifier_channel_alert,
                )
                results.extend(hub.send(alert))

        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_daily(self) -> Brief:
        """Full pipeline: read state, compose brief, notify, persist.

        Idempotent: if last_run_date equals today in schedule_tz, skips
        the notify step and returns the saved brief (or re-composes if
        the saved file is missing).
        """
        today = _today_str(self._config.schedule_tz)
        last_run = self._read_last_run_date()
        already_ran = last_run == today

        if already_ran:
            # Try to return saved brief without re-notifying.
            saved_path = self._brief_file(today)
            if saved_path.exists():
                try:
                    return Brief.model_validate_json(saved_path.read_text())
                except Exception:
                    pass
            # File missing — recompose but skip notify.
            brief = self._build_brief()
            self._persist_brief(brief, today)
            return brief

        brief = self._build_brief()
        self._emit_notifications(brief)
        self._persist_brief(brief, today)
        self._write_last_run_date(today)
        return brief

    def run_alerts_only(self) -> list[StallAlert]:
        """Return current stall alerts without re-emitting the brief."""
        aggregator = Aggregator()
        fleet_state = aggregator.snapshot()
        return detect_stalls(fleet_state)

    def run_actions_only(self, cap: int = 7) -> list[NextAction]:
        """Return top-N next actions without re-emitting the brief."""
        aggregator = Aggregator()
        fleet_state = aggregator.snapshot()
        playbooks = load_playbooks_dir(self._config.playbooks_dir)
        actions = next_actions(fleet_state, playbooks)
        return actions[:cap]

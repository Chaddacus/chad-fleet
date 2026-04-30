"""FastMCP server exposing Captain tools for ad-hoc queries."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from chad_captain.config import CaptainConfig, load_config
from chad_captain.runner import CaptainRunner, _today_str
from chad_captain.scheduler import next_tick

try:
    from fastmcp import FastMCP

    _USE_FASTMCP = True
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[assignment,no-redef]

        _USE_FASTMCP = True
    except ImportError:
        _USE_FASTMCP = False
        FastMCP = None  # type: ignore[assignment,misc]


def _make_server(config: CaptainConfig | None = None) -> Any:
    if not _USE_FASTMCP or FastMCP is None:
        raise RuntimeError(
            "Neither 'fastmcp' nor 'mcp' package is available. "
            "Install fastmcp>=0.2 to use the MCP server."
        )

    cfg = config or load_config()
    runner = CaptainRunner(cfg)
    mcp = FastMCP("chad-captain")

    @mcp.tool()
    def captain_run_daily() -> dict:  # type: ignore[return]
        """Run the daily brief pipeline and return the Brief as JSON.

        Idempotent within the current calendar day: if already run today,
        returns the saved brief without re-notifying.
        """
        brief = runner.run_daily()
        return json.loads(brief.model_dump_json())

    @mcp.tool()
    def captain_brief(date: str | None = None) -> dict:  # type: ignore[return]
        """Return the saved Brief for *date* (YYYY-MM-DD) or today.

        Returns an empty dict if no brief has been saved for that date.
        """
        date_str = date or _today_str(cfg.schedule_tz)
        path = runner._brief_file(date_str)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return {}
        return {}

    @mcp.tool()
    def captain_alerts() -> list:  # type: ignore[return]
        """Return the current list of StallAlerts without emitting the brief."""
        alerts = runner.run_alerts_only()
        return [json.loads(a.model_dump_json()) for a in alerts]

    @mcp.tool()
    def captain_actions(cap: int = 7) -> list:  # type: ignore[return]
        """Return the top-N NextActions without emitting the brief."""
        actions = runner.run_actions_only(cap=cap)
        return [json.loads(a.model_dump_json()) for a in actions]

    @mcp.tool()
    def captain_app_state(app_id: str) -> dict:  # type: ignore[return]
        """Return the AppSnapshot for a single app by ID."""
        from state_aggregator import Aggregator

        fleet = Aggregator().snapshot()
        for app in fleet.apps:
            if app.id == app_id:
                return json.loads(app.model_dump_json())
        return {}

    @mcp.tool()
    def captain_status() -> dict:  # type: ignore[return]
        """Return last_run_date, scheduler_next_tick_seconds, and fleet_summary."""
        from state_aggregator import Aggregator

        last_run = runner._read_last_run_date()
        now = datetime.now()
        secs = next_tick(
            now,
            hour=cfg.schedule_hour,
            tz=cfg.schedule_tz,
            weekdays_only=cfg.weekdays_only,
        )
        fleet = Aggregator().snapshot()
        return {
            "last_run_date": last_run,
            "scheduler_next_tick_seconds": round(secs, 1),
            "fleet_summary": fleet.summary,
        }

    return mcp


def create_server(config: CaptainConfig | None = None) -> Any:
    """Public factory — returns the configured FastMCP app."""
    return _make_server(config)

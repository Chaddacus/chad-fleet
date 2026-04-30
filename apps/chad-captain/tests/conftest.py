"""Shared fixtures for chad-captain tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from captain_core import Brief, NextAction, StallAlert
from state_aggregator.types import AppSnapshot, FleetState


@pytest.fixture()
def tmp_state_dir(tmp_path: Path) -> Path:
    return tmp_path / "captain"


@pytest.fixture()
def sample_stall() -> StallAlert:
    return StallAlert(
        app_id="app-1",
        app_name="App One",
        days_since_progress=10,
        severity="critical",
        detail="No commits in 10 days",
    )


@pytest.fixture()
def sample_action() -> NextAction:
    return NextAction(
        app_id="app-1",
        title="Ship the MVP",
        body="Get it done.",
        rationale="playbook:growth / rec 1",
        priority=1,
        playbook_slug="growth",
    )


@pytest.fixture()
def sample_brief(sample_stall: StallAlert, sample_action: NextAction) -> Brief:
    return Brief(
        generated_at=datetime.now(UTC),
        headline="1 critical stall: App One blocked 10 day(s)",
        body="Daily brief body text.",
        apps_summary=[
            {
                "app_id": "app-1",
                "app_name": "App One",
                "state": "active",
                "mode": "build",
                "last_progress_days": 10,
                "stall_severity": "critical",
                "top_action": "Ship the MVP",
            }
        ],
        stalls=[sample_stall],
        next_actions=[sample_action],
        recommended_slices=[],
        inbox_recent_count=0,
    )


@pytest.fixture()
def empty_fleet_state() -> FleetState:
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=[],
        inbox_recent=[],
        summary={"total_apps": 0, "by_state": {}, "blocked_count": 0,
                  "total_runs": 0, "unmatched_runs": 0, "inbox_count": 0},
    )


@pytest.fixture()
def fleet_state_with_app(sample_stall: StallAlert) -> FleetState:
    from datetime import timedelta

    app = AppSnapshot(
        id="app-1",
        name="App One",
        state="active",
        mode="build",
        cadence="weekly",
        owner_brand="chad",
        last_progress_at=datetime.now(UTC) - timedelta(days=10),
    )
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=[app],
        inbox_recent=[],
        summary={"total_apps": 1, "by_state": {"active": 1}, "blocked_count": 0,
                  "total_runs": 0, "unmatched_runs": 0, "inbox_count": 0},
    )

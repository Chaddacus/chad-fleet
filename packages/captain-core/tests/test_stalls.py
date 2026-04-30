"""Tests for stalls.py: stall detection across all modes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from state_aggregator import FleetState

from captain_core.stalls import detect_stalls
from captain_core.types import StallAlert
from tests.conftest import fleet_all_modes, fleet_launch_driven, make_app


def _fleet(*apps) -> FleetState:
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=list(apps),
        inbox_recent=[],
        summary={"total_apps": len(apps), "by_state": {}, "blocked_count": 0},
    )


# ---------------------------------------------------------------------------
# continuous mode
# ---------------------------------------------------------------------------

def test_continuous_no_stall_fresh() -> None:
    app = make_app(mode="continuous", days_since_progress=1)
    stalls = detect_stalls(_fleet(app))
    assert stalls == []


def test_continuous_warn_at_threshold() -> None:
    app = make_app(mode="continuous", days_since_progress=3)
    stalls = detect_stalls(_fleet(app))
    assert len(stalls) == 1
    assert stalls[0].severity == "warn"


def test_continuous_critical_at_threshold() -> None:
    app = make_app(mode="continuous", days_since_progress=7)
    stalls = detect_stalls(_fleet(app))
    assert len(stalls) == 1
    assert stalls[0].severity == "critical"


def test_continuous_critical_escalation() -> None:
    app = make_app(mode="continuous", days_since_progress=10)
    stalls = detect_stalls(_fleet(app))
    assert stalls[0].severity == "critical"
    assert stalls[0].days_since_progress == 10


# ---------------------------------------------------------------------------
# event_driven mode
# ---------------------------------------------------------------------------

def test_event_driven_no_stall_fresh() -> None:
    app = make_app(mode="event_driven", days_since_progress=5)
    stalls = detect_stalls(_fleet(app))
    assert stalls == []


def test_event_driven_warn() -> None:
    app = make_app(mode="event_driven", days_since_progress=14)
    stalls = detect_stalls(_fleet(app))
    assert len(stalls) == 1
    assert stalls[0].severity == "warn"


def test_event_driven_critical() -> None:
    app = make_app(mode="event_driven", days_since_progress=30)
    stalls = detect_stalls(_fleet(app))
    assert stalls[0].severity == "critical"


# ---------------------------------------------------------------------------
# archived / shipped exemption
# ---------------------------------------------------------------------------

def test_archived_never_stalls() -> None:
    app = make_app(mode="archived", state="archived", days_since_progress=365)
    stalls = detect_stalls(_fleet(app))
    assert stalls == []


def test_shipped_never_stalls() -> None:
    app = make_app(mode="shipped", state="shipped", days_since_progress=200)
    stalls = detect_stalls(_fleet(app))
    assert stalls == []


# ---------------------------------------------------------------------------
# blocked-app special case
# ---------------------------------------------------------------------------

def test_blocked_app_always_info() -> None:
    app = make_app(state="blocked", mode="archived", days_since_progress=0,
                   blocked_reason="waiting for vendor")
    stalls = detect_stalls(_fleet(app))
    assert len(stalls) == 1
    assert stalls[0].severity == "info"
    assert "waiting for vendor" in stalls[0].detail


def test_blocked_app_escalates_to_critical() -> None:
    app = make_app(state="blocked", mode="continuous", days_since_progress=8,
                   blocked_reason="infra outage")
    stalls = detect_stalls(_fleet(app))
    assert stalls[0].severity == "critical"


def test_blocked_app_detail_includes_reason() -> None:
    app = make_app(state="blocked", mode="continuous", days_since_progress=1,
                   blocked_reason="waiting for VA response")
    stalls = detect_stalls(_fleet(app))
    assert "waiting for VA response" in stalls[0].detail


# ---------------------------------------------------------------------------
# launch_driven mode
# ---------------------------------------------------------------------------

def test_launch_driven_near_launch_critical() -> None:
    near = (datetime.now(UTC) + timedelta(days=4)).strftime("%Y-%m-%d")
    app = make_app(mode="launch_driven", days_since_progress=2,
                   metadata={"launch_date": near})
    stalls = detect_stalls(_fleet(app))
    assert stalls[0].severity == "critical"


def test_launch_driven_far_from_launch_no_stall() -> None:
    far = (datetime.now(UTC) + timedelta(days=60)).strftime("%Y-%m-%d")
    app = make_app(mode="launch_driven", days_since_progress=1,
                   metadata={"launch_date": far})
    stalls = detect_stalls(_fleet(app))
    assert stalls == []


# ---------------------------------------------------------------------------
# Threshold override
# ---------------------------------------------------------------------------

def test_custom_threshold_override() -> None:
    app = make_app(mode="continuous", days_since_progress=2)
    # Lower the warn threshold to 1 day
    stalls = detect_stalls(_fleet(app), thresholds={"continuous": (1, 5)})
    assert len(stalls) == 1
    assert stalls[0].severity == "warn"


# ---------------------------------------------------------------------------
# Severity sort order
# ---------------------------------------------------------------------------

def test_stalls_sorted_critical_first() -> None:
    apps = [
        make_app("w", "Warn App", mode="continuous", days_since_progress=4),
        make_app("c", "Crit App", mode="continuous", days_since_progress=8),
    ]
    stalls = detect_stalls(_fleet(*apps))
    assert stalls[0].severity == "critical"
    assert stalls[1].severity == "warn"

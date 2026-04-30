"""Pydantic round-trip tests for FleetState types."""

from datetime import UTC, datetime

import pytest

from state_aggregator.types import AppSnapshot, FleetState, InboxItem


def _now() -> datetime:
    return datetime.now(UTC)


def test_app_snapshot_roundtrip():
    snap = AppSnapshot(
        id="app-1",
        name="My App",
        state="active",
        mode="continuous",
        cadence="weekly",
        owner_brand="chad-simon",
        last_progress_at=_now(),
    )
    dumped = snap.model_dump(mode="json")
    restored = AppSnapshot.model_validate(dumped)
    assert restored.id == "app-1"
    assert restored.state == "active"
    assert restored.blocked_reason is None
    assert restored.obsessive_loop_runs == []
    assert restored.baseline is None


def test_app_snapshot_with_optionals():
    snap = AppSnapshot(
        id="app-blocked",
        name="Blocked App",
        state="blocked",
        mode="launch_driven",
        cadence="daily",
        owner_brand="internal",
        last_progress_at=_now(),
        blocked_reason="waiting on API key",
        obsessive_loop_runs=[{"run_id": "r1", "status": "done"}],
        baseline={"score": 95},
        metadata={"priority": "high"},
    )
    dumped = snap.model_dump(mode="json")
    restored = AppSnapshot.model_validate(dumped)
    assert restored.blocked_reason == "waiting on API key"
    assert len(restored.obsessive_loop_runs) == 1
    assert restored.baseline == {"score": 95}
    assert restored.metadata["priority"] == "high"


def test_inbox_item_roundtrip():
    item = InboxItem(
        ts=_now(),
        channel="zoom",
        severity="warn",
        title="Disk usage high",
        body="85% used on /dev/sda1",
    )
    dumped = item.model_dump(mode="json")
    restored = InboxItem.model_validate(dumped)
    assert restored.severity == "warn"
    assert restored.channel == "zoom"


def test_inbox_item_severity_literal():
    with pytest.raises(Exception):
        InboxItem(
            ts=_now(),
            channel="slack",
            severity="debug",  # not in Literal
            title="t",
            body="b",
        )


def test_fleet_state_roundtrip():
    apps = [
        AppSnapshot(
            id="a1",
            name="App One",
            state="active",
            mode="continuous",
            cadence="daily",
            owner_brand="chad-simon",
            last_progress_at=_now(),
        )
    ]
    inbox = [
        InboxItem(
            ts=_now(),
            channel="email",
            severity="info",
            title="Hello",
            body="World",
        )
    ]
    fleet = FleetState(
        generated_at=_now(),
        apps=apps,
        inbox_recent=inbox,
        summary={"total_apps": 1, "by_state": {"active": 1}, "blocked_count": 0},
    )
    dumped = fleet.model_dump(mode="json")
    restored = FleetState.model_validate(dumped)
    assert len(restored.apps) == 1
    assert restored.apps[0].id == "a1"
    assert len(restored.inbox_recent) == 1
    assert restored.summary["total_apps"] == 1

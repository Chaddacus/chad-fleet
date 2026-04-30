"""Tests for the FastAPI server using TestClient with mocked Aggregator."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from state_aggregator.server import app
from state_aggregator.types import AppSnapshot, FleetState, InboxItem


def _now() -> datetime:
    return datetime.now(UTC)


def _make_fleet_state(apps: list[AppSnapshot] | None = None) -> FleetState:
    apps = apps or []
    return FleetState(
        generated_at=_now(),
        apps=apps,
        inbox_recent=[],
        summary={"total_apps": len(apps), "by_state": {}, "blocked_count": 0},
    )


def _make_app_snapshot(id: str = "app-1", state: str = "active") -> AppSnapshot:
    return AppSnapshot(
        id=id,
        name=f"App {id}",
        state=state,
        mode="continuous",
        cadence="daily",
        owner_brand="chad-simon",
        last_progress_at=_now(),
    )


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_get_state_empty(client):
    fleet = _make_fleet_state()
    with patch("state_aggregator.server._aggregator") as mock_agg:
        mock_agg.snapshot.return_value = fleet
        resp = client.get("/api/state")

    assert resp.status_code == 200
    data = resp.json()
    assert "apps" in data
    assert "inbox_recent" in data
    assert "summary" in data
    assert data["summary"]["total_apps"] == 0


def test_get_state_with_apps(client):
    snap = _make_app_snapshot("alpha")
    fleet = _make_fleet_state([snap])
    with patch("state_aggregator.server._aggregator") as mock_agg:
        mock_agg.snapshot.return_value = fleet
        resp = client.get("/api/state")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["apps"]) == 1
    assert data["apps"][0]["id"] == "alpha"


def test_get_app_found(client):
    snap = _make_app_snapshot("my-app")
    fleet = _make_fleet_state([snap])
    with patch("state_aggregator.server._aggregator") as mock_agg:
        mock_agg.snapshot.return_value = fleet
        resp = client.get("/api/apps/my-app")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "my-app"


def test_get_app_not_found(client):
    fleet = _make_fleet_state()
    with patch("state_aggregator.server._aggregator") as mock_agg:
        mock_agg.snapshot.return_value = fleet
        resp = client.get("/api/apps/does-not-exist")

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_get_state_includes_inbox(client):
    inbox_item = InboxItem(
        ts=_now(),
        channel="zoom",
        severity="warn",
        title="Alert",
        body="Something is wrong",
    )
    fleet = FleetState(
        generated_at=_now(),
        apps=[],
        inbox_recent=[inbox_item],
        summary={"total_apps": 0, "by_state": {}, "blocked_count": 0, "inbox_count": 1},
    )
    with patch("state_aggregator.server._aggregator") as mock_agg:
        mock_agg.snapshot.return_value = fleet
        resp = client.get("/api/state")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["inbox_recent"]) == 1
    assert data["inbox_recent"][0]["severity"] == "warn"

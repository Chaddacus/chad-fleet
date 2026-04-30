"""Tests for the Aggregator using in-memory fake sources."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from state_aggregator.aggregator import Aggregator, _UNMATCHED_ID
from state_aggregator.sources import StateSource
from state_aggregator.types import FleetState


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FakeRegistrySource:
    name = "tracked-app-registry"

    def __init__(self, apps: list[dict]):
        self._apps = apps

    def fetch(self) -> dict:
        return {"apps": self._apps}


class FakeOLSource:
    name = "obsessive-loop"

    def __init__(self, runs: list[dict]):
        self._runs = runs

    def fetch(self) -> dict:
        return {"runs": self._runs}


class FakeInboxSource:
    name = "notifier-inbox"

    def __init__(self, items: list[dict]):
        self._items = items

    def fetch(self) -> dict:
        return {"items": self._items}


def _make_app(id: str, repo_path: str | None = None, state: str = "active") -> dict:
    now = _now()
    return {
        "id": id,
        "name": id.capitalize(),
        "repo_path": repo_path,
        "repo_url": None,
        "mode": "continuous",
        "cadence": "daily",
        "owner_brand": "chad-simon",
        "owner_agents": [],
        "state": state,
        "last_progress_at": now,
        "blocked_reason": None,
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }


def _make_inbox_item(severity: str = "info") -> dict:
    return {
        "ts": _now(),
        "channel": "zoom",
        "severity": severity,
        "title": "Test",
        "body": "body",
    }


def test_snapshot_empty_sources():
    agg = Aggregator(sources=[
        FakeRegistrySource([]),
        FakeOLSource([]),
        FakeInboxSource([]),
    ])
    state = agg.snapshot()
    assert isinstance(state, FleetState)
    assert state.apps == []
    assert state.inbox_recent == []
    assert state.summary["total_apps"] == 0
    assert state.summary["blocked_count"] == 0


def test_snapshot_apps_from_registry():
    apps = [_make_app("alpha"), _make_app("beta")]
    agg = Aggregator(sources=[
        FakeRegistrySource(apps),
        FakeOLSource([]),
        FakeInboxSource([]),
    ])
    state = agg.snapshot()
    assert len(state.apps) == 2
    ids = {a.id for a in state.apps}
    assert ids == {"alpha", "beta"}


def test_snapshot_pairs_runs_by_repo_path():
    apps = [_make_app("alpha", repo_path="/repos/alpha")]
    runs = [
        {"run_id": "r1", "status": "complete", "repo_path": "/repos/alpha"},
        {"run_id": "r2", "status": "in_progress", "repo_path": "/repos/alpha"},
    ]
    agg = Aggregator(sources=[
        FakeRegistrySource(apps),
        FakeOLSource(runs),
        FakeInboxSource([]),
    ])
    state = agg.snapshot()
    alpha = next(a for a in state.apps if a.id == "alpha")
    assert len(alpha.obsessive_loop_runs) == 2
    run_ids = {r["run_id"] for r in alpha.obsessive_loop_runs}
    assert run_ids == {"r1", "r2"}


def test_snapshot_unmatched_runs_go_to_synthetic_app():
    apps = [_make_app("alpha", repo_path="/repos/alpha")]
    runs = [
        {"run_id": "r1", "repo_path": "/repos/alpha"},
        {"run_id": "r-orphan", "repo_path": "/repos/unknown"},
    ]
    agg = Aggregator(sources=[
        FakeRegistrySource(apps),
        FakeOLSource(runs),
        FakeInboxSource([]),
    ])
    state = agg.snapshot()
    unmatched = next((a for a in state.apps if a.id == _UNMATCHED_ID), None)
    assert unmatched is not None
    assert len(unmatched.obsessive_loop_runs) == 1
    assert unmatched.obsessive_loop_runs[0]["run_id"] == "r-orphan"


def test_snapshot_summary_counts():
    apps = [
        _make_app("a1", state="active"),
        _make_app("a2", state="active"),
        _make_app("a3", state="blocked"),
    ]
    agg = Aggregator(sources=[
        FakeRegistrySource(apps),
        FakeOLSource([]),
        FakeInboxSource([]),
    ])
    state = agg.snapshot()
    assert state.summary["total_apps"] == 3
    assert state.summary["by_state"]["active"] == 2
    assert state.summary["by_state"]["blocked"] == 1
    assert state.summary["blocked_count"] == 1


def test_snapshot_inbox_items():
    items = [_make_inbox_item("info"), _make_inbox_item("critical")]
    agg = Aggregator(sources=[
        FakeRegistrySource([]),
        FakeOLSource([]),
        FakeInboxSource(items),
    ])
    state = agg.snapshot()
    assert len(state.inbox_recent) == 2
    severities = {i.severity for i in state.inbox_recent}
    assert severities == {"info", "critical"}


def test_snapshot_baseline_from_latest_run():
    apps = [_make_app("alpha", repo_path="/repos/alpha")]
    runs = [
        {"run_id": "r1", "repo_path": "/repos/alpha", "baseline": {"score": 70}},
        {"run_id": "r2", "repo_path": "/repos/alpha", "baseline": {"score": 90}},
    ]
    agg = Aggregator(sources=[
        FakeRegistrySource(apps),
        FakeOLSource(runs),
        FakeInboxSource([]),
    ])
    state = agg.snapshot()
    alpha = next(a for a in state.apps if a.id == "alpha")
    # Latest run (r2) baseline should win
    assert alpha.baseline == {"score": 90}


def test_snapshot_no_unmatched_when_all_match():
    apps = [_make_app("alpha", repo_path="/repos/alpha")]
    runs = [{"run_id": "r1", "repo_path": "/repos/alpha"}]
    agg = Aggregator(sources=[
        FakeRegistrySource(apps),
        FakeOLSource(runs),
        FakeInboxSource([]),
    ])
    state = agg.snapshot()
    ids = {a.id for a in state.apps}
    assert _UNMATCHED_ID not in ids

"""Tests for RegistrySource using tmp_path."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from state_aggregator.sources import RegistrySource


def _make_registry(tmp_path: Path) -> Path:
    """Create a minimal apps.json in tmp_path, return the registry dir."""
    registry_dir = tmp_path / "registry"
    registry_dir.mkdir(parents=True)

    now = datetime.now(UTC).isoformat()
    apps = {
        "app-alpha": {
            "id": "app-alpha",
            "name": "Alpha",
            "repo_path": "/repos/alpha",
            "repo_url": None,
            "mode": "continuous",
            "cadence": "daily",
            "owner_brand": "chad-simon",
            "owner_agents": [],
            "state": "active",
            "last_progress_at": now,
            "blocked_reason": None,
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        },
        "app-beta": {
            "id": "app-beta",
            "name": "Beta",
            "repo_path": "/repos/beta",
            "repo_url": None,
            "mode": "launch_driven",
            "cadence": "weekly",
            "owner_brand": "internal",
            "owner_agents": [],
            "state": "blocked",
            "last_progress_at": now,
            "blocked_reason": "needs token",
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        },
    }
    (registry_dir / "apps.json").write_text(json.dumps(apps))
    (registry_dir / "events.jsonl").write_text("")
    return registry_dir


def test_registry_source_returns_apps(tmp_path):
    registry_dir = _make_registry(tmp_path)
    src = RegistrySource(registry_dir=registry_dir)
    result = src.fetch()

    assert "apps" in result
    assert len(result["apps"]) == 2
    ids = {a["id"] for a in result["apps"]}
    assert "app-alpha" in ids
    assert "app-beta" in ids


def test_registry_source_app_fields(tmp_path):
    registry_dir = _make_registry(tmp_path)
    src = RegistrySource(registry_dir=registry_dir)
    result = src.fetch()

    alpha = next(a for a in result["apps"] if a["id"] == "app-alpha")
    assert alpha["name"] == "Alpha"
    assert alpha["state"] == "active"
    assert alpha["repo_path"] == "/repos/alpha"


def test_registry_source_empty_dir(tmp_path):
    registry_dir = tmp_path / "empty_registry"
    registry_dir.mkdir()
    (registry_dir / "apps.json").write_text("{}")
    (registry_dir / "events.jsonl").write_text("")
    src = RegistrySource(registry_dir=registry_dir)
    result = src.fetch()
    assert result == {"apps": []}


def test_registry_source_env_var(tmp_path, monkeypatch):
    registry_dir = _make_registry(tmp_path)
    monkeypatch.setenv("CHAD_FLEET_REGISTRY_DIR", str(registry_dir))
    src = RegistrySource()  # no explicit dir — picks up env var
    result = src.fetch()
    assert len(result["apps"]) == 2

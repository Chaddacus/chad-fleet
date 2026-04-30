"""Shared test fixtures for tracked-app-registry tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracked_app_registry import Registry, TrackedApp


@pytest.fixture()
def reg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Registry:
    """Registry backed by a per-test temp directory."""
    monkeypatch.setenv("CHAD_FLEET_REGISTRY_DIR", str(tmp_path))
    return Registry(
        events_path=tmp_path / "events.jsonl",
        view_path=tmp_path / "apps.json",
    )


def make_app(
    app_id: str = "test-app",
    name: str = "Test App",
    mode: str = "continuous",
    owner_brand: str = "internal",
    state: str = "active",
) -> TrackedApp:
    now = datetime.now(UTC)
    return TrackedApp(
        id=app_id,
        name=name,
        mode=mode,  # type: ignore[arg-type]
        cadence="continuous",
        owner_brand=owner_brand,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        last_progress_at=now,
        created_at=now,
        updated_at=now,
    )

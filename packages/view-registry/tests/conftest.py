"""Shared test fixtures for view-registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from view_registry import Registry, SavedView


@pytest.fixture()
def reg(tmp_path: Path) -> Registry:
    """Registry backed by a per-test temp directory."""
    return Registry(
        view_path=tmp_path / "views.json",
        events_path=tmp_path / "events.jsonl",
    )


def make_view(
    reg: Registry,
    name: str = "Test View",
    prompt: str = "Show me test data",
    description: str = "",
    app_scope: list[str] | None = None,
    tags: list[str] | None = None,
) -> SavedView:
    return reg.create(
        name=name,
        prompt=prompt,
        description=description,
        app_scope=app_scope,
        tags=tags,
    )

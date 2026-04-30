"""Basic create/get/update/list round-trip tests."""

from __future__ import annotations

import pytest

from tracked_app_registry import Registry, TrackedApp

from .conftest import make_app


def test_create_and_get(reg: Registry) -> None:
    app = make_app("spark-of-defiance", name="Spark of Defiance")
    reg.create(app)
    fetched = reg.get("spark-of-defiance")
    assert fetched is not None
    assert fetched.id == "spark-of-defiance"
    assert fetched.name == "Spark of Defiance"


def test_get_missing_returns_none(reg: Registry) -> None:
    assert reg.get("does-not-exist") is None


def test_create_duplicate_raises(reg: Registry) -> None:
    app = make_app("dup")
    reg.create(app)
    with pytest.raises(ValueError, match="already exists"):
        reg.create(app)


def test_update_name(reg: Registry) -> None:
    reg.create(make_app("my-app"))
    updated = reg.update("my-app", name="Updated Name")
    assert updated.name == "Updated Name"
    # Persisted in view
    assert reg.get("my-app").name == "Updated Name"


def test_update_missing_raises(reg: Registry) -> None:
    from tracked_app_registry.registry import AppNotFound

    with pytest.raises(AppNotFound):
        reg.update("ghost-app", name="X")


def test_list_all(reg: Registry) -> None:
    reg.create(make_app("app-a"))
    reg.create(make_app("app-b"))
    apps = reg.list()
    ids = {a.id for a in apps}
    assert ids == {"app-a", "app-b"}


def test_update_does_not_change_id(reg: Registry) -> None:
    reg.create(make_app("stable-id"))
    updated = reg.update("stable-id", id="evil-override")
    assert updated.id == "stable-id"

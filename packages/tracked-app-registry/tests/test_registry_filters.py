"""List filter tests (state, owner_brand)."""

from __future__ import annotations

from tracked_app_registry import Registry

from .conftest import make_app


def test_filter_by_state(reg: Registry) -> None:
    reg.create(make_app("active-app", state="active"))
    reg.create(make_app("paused-app", state="paused"))
    actives = reg.list(state="active")
    assert len(actives) == 1
    assert actives[0].id == "active-app"


def test_filter_by_owner_brand(reg: Registry) -> None:
    reg.create(make_app("chad-app", owner_brand="chad-simon"))
    reg.create(make_app("internal-app", owner_brand="internal"))
    chad_apps = reg.list(owner_brand="chad-simon")
    assert len(chad_apps) == 1
    assert chad_apps[0].id == "chad-app"


def test_filter_by_state_and_owner(reg: Registry) -> None:
    reg.create(make_app("a1", state="active", owner_brand="chad-simon"))
    reg.create(make_app("a2", state="paused", owner_brand="chad-simon"))
    reg.create(make_app("a3", state="active", owner_brand="internal"))
    result = reg.list(state="active", owner_brand="chad-simon")
    assert len(result) == 1
    assert result[0].id == "a1"


def test_list_no_filter_returns_all(reg: Registry) -> None:
    reg.create(make_app("x"))
    reg.create(make_app("y"))
    reg.create(make_app("z"))
    assert len(reg.list()) == 3


def test_filter_returns_empty_when_no_match(reg: Registry) -> None:
    reg.create(make_app("only-active", state="active"))
    assert reg.list(state="shipped") == []

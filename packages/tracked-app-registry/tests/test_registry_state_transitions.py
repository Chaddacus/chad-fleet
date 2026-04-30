"""State transition tests."""

from __future__ import annotations

import pytest

from tracked_app_registry import Registry

from .conftest import make_app


def test_set_state_active_to_paused(reg: Registry) -> None:
    reg.create(make_app("app-x"))
    updated = reg.set_state("app-x", "paused")
    assert updated.state == "paused"
    assert reg.get("app-x").state == "paused"


def test_set_state_blocked_with_reason(reg: Registry) -> None:
    reg.create(make_app("app-y"))
    updated = reg.set_state("app-y", "blocked", blocked_reason="waiting for API keys")
    assert updated.state == "blocked"
    assert updated.blocked_reason == "waiting for API keys"


def test_set_state_emits_event(reg: Registry) -> None:
    reg.create(make_app("app-z"))
    reg.set_state("app-z", "shipped")
    evts = reg.events(app_id="app-z")
    state_evts = [e for e in evts if e.type == "app.state_changed"]
    assert len(state_evts) == 1
    assert state_evts[0].payload["new_state"] == "shipped"
    assert state_evts[0].payload["old_state"] == "active"


def test_archive_sets_state(reg: Registry) -> None:
    reg.create(make_app("archive-me"))
    reg.archive("archive-me")
    app = reg.get("archive-me")
    assert app.state == "archived"


def test_archive_emits_event(reg: Registry) -> None:
    reg.create(make_app("archive-event"))
    reg.archive("archive-event")
    evts = reg.events(app_id="archive-event")
    archived_evts = [e for e in evts if e.type == "app.archived"]
    assert len(archived_evts) == 1


def test_set_state_missing_raises(reg: Registry) -> None:
    from tracked_app_registry.registry import AppNotFound

    with pytest.raises(AppNotFound):
        reg.set_state("ghost", "paused")

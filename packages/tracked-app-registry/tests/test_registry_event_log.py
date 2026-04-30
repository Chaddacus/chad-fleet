"""Event log ordering and view-rebuild tests."""

from __future__ import annotations

from tracked_app_registry import Registry

from .conftest import make_app


def test_create_emits_event(reg: Registry) -> None:
    reg.create(make_app("evt-app"))
    evts = reg.events(app_id="evt-app")
    assert len(evts) == 1
    assert evts[0].type == "app.created"
    assert evts[0].app_id == "evt-app"


def test_event_ordering_preserved(reg: Registry) -> None:
    reg.create(make_app("ordered-app"))
    reg.set_state("ordered-app", "paused")
    reg.set_state("ordered-app", "active")
    evts = reg.events(app_id="ordered-app")
    types = [e.type for e in evts]
    assert types == ["app.created", "app.state_changed", "app.state_changed"]
    assert evts[0].ts <= evts[1].ts <= evts[2].ts


def test_rebuild_view_matches_live_view(reg: Registry) -> None:
    reg.create(make_app("rebuild-app"))
    reg.set_state("rebuild-app", "paused")
    reg.update("rebuild-app", name="Rebuilt App")

    # Wipe the view file to force a cold rebuild
    reg._view_path.unlink()
    reg.rebuild_view()

    app = reg.get("rebuild-app")
    assert app is not None
    assert app.name == "Rebuilt App"
    assert app.state == "paused"


def test_rebuild_view_from_empty_log(reg: Registry) -> None:
    reg.rebuild_view()
    assert reg.list() == []


def test_events_since_filter(reg: Registry) -> None:
    from datetime import UTC, datetime

    reg.create(make_app("since-app"))
    first_evt = reg.events(app_id="since-app")[0]
    reg.set_state("since-app", "paused")

    later_evts = reg.events(app_id="since-app", since=first_evt.ts)
    assert len(later_evts) == 1
    assert later_evts[0].type == "app.state_changed"

"""Registry create/get/update/list/delete/pin/tag/render tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from view_registry import Registry, SavedView, ViewNotFound

from .conftest import make_view


# ---- create ----

def test_create_returns_saved_view(reg: Registry) -> None:
    view = make_view(reg, name="Spark Launch Readiness")
    assert isinstance(view, SavedView)
    assert view.id == "spark-launch-readiness"
    assert view.name == "Spark Launch Readiness"


def test_create_auto_slug(reg: Registry) -> None:
    view = make_view(reg, name="My Cool View!!")
    assert view.id == "my-cool-view"


def test_create_dedup_slug(reg: Registry) -> None:
    v1 = make_view(reg, name="Revenue Dashboard")
    v2 = make_view(reg, name="Revenue Dashboard")
    assert v1.id == "revenue-dashboard"
    assert v2.id == "revenue-dashboard-1"


def test_create_dedup_increments(reg: Registry) -> None:
    make_view(reg, name="Overlap")
    make_view(reg, name="Overlap")
    v3 = make_view(reg, name="Overlap")
    assert v3.id == "overlap-2"


def test_create_with_app_scope(reg: Registry) -> None:
    view = make_view(reg, name="Scoped View", app_scope=["spark-app"])
    assert view.app_scope == ["spark-app"]


def test_create_with_tags(reg: Registry) -> None:
    view = make_view(reg, name="Tagged View", tags=["revenue", "weekly"])
    assert "revenue" in view.tags
    assert "weekly" in view.tags


def test_create_defaults(reg: Registry) -> None:
    view = make_view(reg)
    assert view.pinned is False
    assert view.app_scope == []
    assert view.tags == []
    assert view.last_rendered_at is None
    assert view.last_render_html is None
    assert view.last_render_tsx is None


def test_create_writes_event(reg: Registry) -> None:
    view = make_view(reg, name="Event Test")
    events = reg.events(view.id)
    assert len(events) == 1
    assert events[0]["type"] == "created"
    assert events[0]["view_id"] == view.id


# ---- get ----

def test_get_existing(reg: Registry) -> None:
    view = make_view(reg, name="Get Me")
    fetched = reg.get(view.id)
    assert fetched is not None
    assert fetched.id == view.id
    assert fetched.name == "Get Me"


def test_get_missing_returns_none(reg: Registry) -> None:
    assert reg.get("does-not-exist") is None


# ---- update ----

def test_update_name(reg: Registry) -> None:
    view = make_view(reg, name="Old Name")
    updated = reg.update(view.id, name="New Name")
    assert updated.name == "New Name"
    assert reg.get(view.id).name == "New Name"


def test_update_prompt(reg: Registry) -> None:
    view = make_view(reg, name="Prompt Update", prompt="old prompt")
    updated = reg.update(view.id, prompt="new prompt")
    assert updated.prompt == "new prompt"


def test_update_description(reg: Registry) -> None:
    view = make_view(reg, name="Desc Update")
    updated = reg.update(view.id, description="A description")
    assert updated.description == "A description"


def test_update_app_scope(reg: Registry) -> None:
    view = make_view(reg, name="Scope Update")
    updated = reg.update(view.id, app_scope=["app-a", "app-b"])
    assert updated.app_scope == ["app-a", "app-b"]


def test_update_does_not_change_id(reg: Registry) -> None:
    view = make_view(reg, name="Stable ID")
    # update does not accept id, so the id stays the same
    updated = reg.update(view.id, name="New Name")
    assert updated.id == view.id


def test_update_missing_raises(reg: Registry) -> None:
    with pytest.raises(ViewNotFound):
        reg.update("ghost-view", name="X")


def test_update_writes_event(reg: Registry) -> None:
    view = make_view(reg, name="Update Event")
    reg.update(view.id, name="Updated")
    events = reg.events(view.id)
    types = [e["type"] for e in events]
    assert "updated" in types


# ---- list ----

def test_list_all(reg: Registry) -> None:
    make_view(reg, name="View A")
    make_view(reg, name="View B")
    views = reg.list()
    assert len(views) == 2


def test_list_filter_by_app(reg: Registry) -> None:
    make_view(reg, name="App View", app_scope=["spark-app"])
    make_view(reg, name="Global View")
    result = reg.list(app="spark-app")
    assert len(result) == 1
    assert result[0].name == "App View"


def test_list_filter_by_tag(reg: Registry) -> None:
    make_view(reg, name="Tagged", tags=["revenue"])
    make_view(reg, name="Untagged")
    result = reg.list(tag="revenue")
    assert len(result) == 1
    assert result[0].name == "Tagged"


def test_list_filter_pinned_only(reg: Registry) -> None:
    v1 = make_view(reg, name="Pinned View")
    reg.pin(v1.id)
    make_view(reg, name="Unpinned View")
    result = reg.list(pinned_only=True)
    assert len(result) == 1
    assert result[0].id == v1.id


def test_list_combined_filters(reg: Registry) -> None:
    make_view(reg, name="Match", app_scope=["myapp"], tags=["weekly"])
    make_view(reg, name="Wrong App", app_scope=["other"], tags=["weekly"])
    make_view(reg, name="Wrong Tag", app_scope=["myapp"], tags=["monthly"])
    result = reg.list(app="myapp", tag="weekly")
    assert len(result) == 1
    assert result[0].name == "Match"


def test_list_empty_registry(reg: Registry) -> None:
    assert reg.list() == []


def test_list_no_match_returns_empty(reg: Registry) -> None:
    make_view(reg, name="One View")
    assert reg.list(app="nonexistent") == []


# ---- record_render ----

def test_record_render(reg: Registry) -> None:
    view = make_view(reg, name="Render Test")
    updated = reg.record_render(view.id, html="<div>hi</div>", tsx="<Div />")
    assert updated.last_render_html == "<div>hi</div>"
    assert updated.last_render_tsx == "<Div />"
    assert updated.last_rendered_at is not None


def test_record_render_persisted(reg: Registry) -> None:
    view = make_view(reg, name="Render Persist")
    reg.record_render(view.id, html="<p>hello</p>", tsx="<P />")
    fetched = reg.get(view.id)
    assert fetched.last_render_html == "<p>hello</p>"


def test_record_render_writes_event(reg: Registry) -> None:
    view = make_view(reg, name="Render Event")
    reg.record_render(view.id, html="<x/>", tsx="<X/>")
    events = reg.events(view.id)
    types = [e["type"] for e in events]
    assert "rendered" in types


def test_record_render_missing_raises(reg: Registry) -> None:
    with pytest.raises(ViewNotFound):
        reg.record_render("ghost", html="<x/>", tsx="<X/>")


# ---- pin / unpin ----

def test_pin(reg: Registry) -> None:
    view = make_view(reg, name="Pin Me")
    updated = reg.pin(view.id)
    assert updated.pinned is True
    assert reg.get(view.id).pinned is True


def test_unpin(reg: Registry) -> None:
    view = make_view(reg, name="Unpin Me")
    reg.pin(view.id)
    updated = reg.unpin(view.id)
    assert updated.pinned is False
    assert reg.get(view.id).pinned is False


def test_pin_idempotent(reg: Registry) -> None:
    view = make_view(reg, name="Pin Idempotent")
    reg.pin(view.id)
    reg.pin(view.id)
    assert reg.get(view.id).pinned is True


def test_unpin_idempotent(reg: Registry) -> None:
    view = make_view(reg, name="Unpin Idempotent")
    reg.unpin(view.id)
    assert reg.get(view.id).pinned is False


def test_pin_missing_raises(reg: Registry) -> None:
    with pytest.raises(ViewNotFound):
        reg.pin("ghost")


def test_unpin_missing_raises(reg: Registry) -> None:
    with pytest.raises(ViewNotFound):
        reg.unpin("ghost")


# ---- add_tag / remove_tag ----

def test_add_tag(reg: Registry) -> None:
    view = make_view(reg, name="Tag Add")
    updated = reg.add_tag(view.id, "revenue")
    assert "revenue" in updated.tags


def test_add_tag_idempotent(reg: Registry) -> None:
    view = make_view(reg, name="Tag Idempotent")
    reg.add_tag(view.id, "revenue")
    reg.add_tag(view.id, "revenue")
    fetched = reg.get(view.id)
    assert fetched.tags.count("revenue") == 1


def test_remove_tag(reg: Registry) -> None:
    view = make_view(reg, name="Tag Remove", tags=["revenue"])
    updated = reg.remove_tag(view.id, "revenue")
    assert "revenue" not in updated.tags


def test_remove_tag_idempotent(reg: Registry) -> None:
    view = make_view(reg, name="Tag Remove Idempotent")
    reg.remove_tag(view.id, "nonexistent")
    assert reg.get(view.id).tags == []


def test_add_tag_missing_raises(reg: Registry) -> None:
    with pytest.raises(ViewNotFound):
        reg.add_tag("ghost", "tag")


def test_remove_tag_missing_raises(reg: Registry) -> None:
    with pytest.raises(ViewNotFound):
        reg.remove_tag("ghost", "tag")


# ---- delete ----

def test_delete(reg: Registry) -> None:
    view = make_view(reg, name="Delete Me")
    reg.delete(view.id)
    assert reg.get(view.id) is None


def test_delete_writes_event(reg: Registry) -> None:
    view = make_view(reg, name="Delete Event")
    reg.delete(view.id)
    events = reg.events(view.id)
    types = [e["type"] for e in events]
    assert "deleted" in types


def test_delete_missing_raises(reg: Registry) -> None:
    with pytest.raises(ViewNotFound):
        reg.delete("ghost")


def test_deleted_view_not_in_list(reg: Registry) -> None:
    view = make_view(reg, name="Gone View")
    reg.delete(view.id)
    assert reg.list() == []


# ---- atomic write ----

def test_atomic_write_uses_tempdir(tmp_path: Path) -> None:
    """Atomic writes should produce a clean final file under tmp_path."""
    reg = Registry(
        view_path=tmp_path / "views.json",
        events_path=tmp_path / "events.jsonl",
    )
    make_view(reg, name="Atomic Test")
    assert (tmp_path / "views.json").exists()
    # No leftover .tmp_ files
    leftovers = list(tmp_path.glob(".tmp_*"))
    assert leftovers == []

"""Event serialization and discriminated-union parsing tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from view_registry.events import (
    ViewCreatedEvent,
    ViewDeletedEvent,
    ViewPinnedEvent,
    ViewRenderedEvent,
    ViewTaggedEvent,
    ViewUnpinnedEvent,
    ViewUntaggedEvent,
    ViewUpdatedEvent,
    parse_event,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _base(type_: str, extra: dict | None = None) -> dict:
    d: dict = {"type": type_, "view_id": "test-view", "at": _now().isoformat()}
    if extra:
        d.update(extra)
    return d


def test_parse_created_event() -> None:
    raw = _base("created", {"payload": {"id": "test-view", "name": "Test"}})
    evt = parse_event(raw)
    assert isinstance(evt, ViewCreatedEvent)
    assert evt.type == "created"
    assert evt.view_id == "test-view"


def test_parse_updated_event() -> None:
    raw = _base("updated", {"fields": {"name": "New Name"}})
    evt = parse_event(raw)
    assert isinstance(evt, ViewUpdatedEvent)
    assert evt.fields == {"name": "New Name"}


def test_parse_rendered_event() -> None:
    raw = _base("rendered", {"html_len": 100, "tsx_len": 50})
    evt = parse_event(raw)
    assert isinstance(evt, ViewRenderedEvent)
    assert evt.html_len == 100
    assert evt.tsx_len == 50


def test_parse_pinned_event() -> None:
    raw = _base("pinned")
    evt = parse_event(raw)
    assert isinstance(evt, ViewPinnedEvent)
    assert evt.type == "pinned"


def test_parse_unpinned_event() -> None:
    raw = _base("unpinned")
    evt = parse_event(raw)
    assert isinstance(evt, ViewUnpinnedEvent)
    assert evt.type == "unpinned"


def test_parse_tagged_event() -> None:
    raw = _base("tagged", {"tag": "revenue"})
    evt = parse_event(raw)
    assert isinstance(evt, ViewTaggedEvent)
    assert evt.tag == "revenue"


def test_parse_untagged_event() -> None:
    raw = _base("untagged", {"tag": "revenue"})
    evt = parse_event(raw)
    assert isinstance(evt, ViewUntaggedEvent)
    assert evt.tag == "revenue"


def test_parse_deleted_event() -> None:
    raw = _base("deleted")
    evt = parse_event(raw)
    assert isinstance(evt, ViewDeletedEvent)
    assert evt.type == "deleted"


def test_actor_default() -> None:
    raw = _base("pinned")
    evt = parse_event(raw)
    assert evt.actor == "user"


def test_actor_custom() -> None:
    raw = _base("pinned")
    raw["actor"] = "system"
    evt = parse_event(raw)
    assert evt.actor == "system"


def test_round_trip_created() -> None:
    raw = _base("created", {"payload": {"id": "test-view", "name": "Test"}})
    evt = parse_event(raw)
    dumped = evt.model_dump(mode="json")
    assert dumped["type"] == "created"
    assert dumped["view_id"] == "test-view"
    assert dumped["payload"] == {"id": "test-view", "name": "Test"}


def test_round_trip_rendered() -> None:
    raw = _base("rendered", {"html_len": 200, "tsx_len": 80})
    evt = parse_event(raw)
    dumped = evt.model_dump(mode="json")
    assert dumped["html_len"] == 200
    assert dumped["tsx_len"] == 80


def test_invalid_type_raises() -> None:
    raw = _base("nonexistent_type")
    with pytest.raises(Exception):
        parse_event(raw)


def test_event_at_is_datetime() -> None:
    raw = _base("pinned")
    evt = parse_event(raw)
    assert isinstance(evt.at, datetime)

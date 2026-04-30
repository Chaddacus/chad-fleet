"""Discriminated-union event types for view-registry."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class _BaseEvent(BaseModel):
    view_id: str
    at: datetime
    actor: str = "user"


class ViewCreatedEvent(_BaseEvent):
    type: Literal["created"] = "created"
    payload: dict


class ViewUpdatedEvent(_BaseEvent):
    type: Literal["updated"] = "updated"
    fields: dict


class ViewRenderedEvent(_BaseEvent):
    type: Literal["rendered"] = "rendered"
    html_len: int
    tsx_len: int


class ViewPinnedEvent(_BaseEvent):
    type: Literal["pinned"] = "pinned"


class ViewUnpinnedEvent(_BaseEvent):
    type: Literal["unpinned"] = "unpinned"


class ViewTaggedEvent(_BaseEvent):
    type: Literal["tagged"] = "tagged"
    tag: str


class ViewUntaggedEvent(_BaseEvent):
    type: Literal["untagged"] = "untagged"
    tag: str


class ViewDeletedEvent(_BaseEvent):
    type: Literal["deleted"] = "deleted"


SavedViewEvent = Annotated[
    Union[
        ViewCreatedEvent,
        ViewUpdatedEvent,
        ViewRenderedEvent,
        ViewPinnedEvent,
        ViewUnpinnedEvent,
        ViewTaggedEvent,
        ViewUntaggedEvent,
        ViewDeletedEvent,
    ],
    Field(discriminator="type"),
]


def parse_event(raw: dict) -> SavedViewEvent:
    """Parse a raw dict into the correct SavedViewEvent subtype."""
    from pydantic import TypeAdapter

    adapter: TypeAdapter[SavedViewEvent] = TypeAdapter(SavedViewEvent)
    return adapter.validate_python(raw)

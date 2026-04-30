"""Pydantic data models for view-registry."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SavedView(BaseModel):
    id: str
    name: str
    description: str = ""
    prompt: str
    app_scope: list[str] = []
    pinned: bool = False
    tags: list[str] = []
    created_at: datetime
    updated_at: datetime
    last_rendered_at: datetime | None = None
    last_render_html: str | None = None
    last_render_tsx: str | None = None

"""Pydantic data models for tracked-app-registry."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TrackedApp(BaseModel):
    id: str
    name: str
    repo_path: str | None = None
    repo_url: str | None = None
    mode: Literal["launch_driven", "continuous", "event_driven"]
    cadence: str
    owner_brand: Literal["chad-simon", "chadacys", "internal", "external"]
    owner_agents: list[str] = []
    state: Literal["active", "paused", "blocked", "shipped", "archived"] = "active"
    last_progress_at: datetime
    blocked_reason: str | None = None
    metadata: dict = {}
    created_at: datetime
    updated_at: datetime


class Event(BaseModel):
    ts: datetime
    type: Literal["app.created", "app.updated", "app.state_changed", "app.archived"]
    app_id: str
    payload: dict

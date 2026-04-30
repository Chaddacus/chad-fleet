"""Pydantic models for the unified fleet state snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AppSnapshot(BaseModel):
    """Per-tracked-app slice of fleet state."""

    id: str
    name: str
    state: str
    mode: str
    cadence: str
    owner_brand: str
    last_progress_at: datetime
    blocked_reason: str | None = None
    obsessive_loop_runs: list[dict] = []  # summaries only, last N runs
    baseline: dict | None = None  # latest baseline scorecard summary
    metadata: dict = {}


class InboxItem(BaseModel):
    ts: datetime
    channel: str
    severity: Literal["info", "warn", "critical"]
    title: str
    body: str


class FleetState(BaseModel):
    generated_at: datetime
    apps: list[AppSnapshot]
    inbox_recent: list[InboxItem]  # last 50 by default
    summary: dict  # cross-cut counts: total_apps, by_state, blocked_count, etc.

"""Pydantic output types for captain-core reasoning APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Playbook(BaseModel):
    slug: str
    title: str
    domain: str
    applies_to: list[str]
    last_updated: str
    summary: str
    when_to_consult: list[str]   # bullets
    recommendations: list[str]    # numbered items, full paragraph each
    anti_patterns: list[str]
    decision_rubric: str | None = None
    sources: list[str] = []
    raw: str                      # full markdown body


class StallAlert(BaseModel):
    app_id: str
    app_name: str
    days_since_progress: int
    severity: Literal["info", "warn", "critical"]
    detail: str


class NextAction(BaseModel):
    app_id: str
    title: str
    body: str
    rationale: str       # which playbook + recommendation it traces to
    priority: int        # 1 = highest
    playbook_slug: str | None = None
    deadline: datetime | None = None


class RecommendedSlice(BaseModel):
    """Optional output — playbook-suggested obsessive-loop slice for an app."""
    app_id: str
    objective: str
    target_category: str | None = None
    rationale: str


class Brief(BaseModel):
    generated_at: datetime
    headline: str                       # one-line top of brief
    body: str                           # multi-paragraph narrative
    apps_summary: list[dict]            # short per-app blurbs
    stalls: list[StallAlert]
    next_actions: list[NextAction]      # ordered by priority, capped at 7
    recommended_slices: list[RecommendedSlice] = []
    inbox_recent_count: int

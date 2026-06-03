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


class SessionSnapshot(BaseModel):
    """One agent session, normalized across runtimes (Claude, auto_runtime
    captain tracks, Codex). The hub's "all my sessions" surface reads these."""

    id: str
    source: str  # "claude" | "auto-runtime" | "codex"
    title: str
    cwd: str | None = None
    updated_at: datetime
    status: str | None = None  # e.g. an auto_runtime objective state


class ToolSnapshot(BaseModel):
    """One MCP server the operator has registered. Safe projection — names/transport/scope
    only, never args/headers/urls/env (those carry tokens)."""

    name: str
    transport: str  # "stdio" | "http" | "sse" | "unknown"
    source: str  # "user" (~/.claude.json) | "project" (~/.mcp.json)
    detail: str | None = None  # command basename or remote host — never secrets


class EmailMessage(BaseModel):
    """One inbox message — read-fast list view (no body; full body via the email-mcp read tool)."""

    id: str
    subject: str = ""
    from_: str = ""  # sender display/address; `from` is reserved in Python
    date: str = ""
    unread: bool = False
    snippet: str = ""


class FleetState(BaseModel):
    generated_at: datetime
    apps: list[AppSnapshot]
    inbox_recent: list[InboxItem]  # last 50 by default
    sessions: list[SessionSnapshot] = []  # most-recent agent sessions, all runtimes
    tools: list[ToolSnapshot] = []  # registered MCP servers (safe projection)
    email: list[EmailMessage] = []  # recent inbox messages (read via the email connector)
    summary: dict  # cross-cut counts: total_apps, by_state, blocked_count, etc.

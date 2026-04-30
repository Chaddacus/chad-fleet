"""Pydantic data models for notifier-hub."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Action(BaseModel):
    label: str
    url: str | None = None
    command: str | None = None


class Notification(BaseModel):
    title: str
    body: str
    severity: Literal["info", "warn", "critical"] = "info"
    channel: str
    actions: list[Action] = []


class SendResult(BaseModel):
    adapter: str
    ok: bool
    detail: str | None = None

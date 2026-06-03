"""Credential loading — the ONE place calendar secrets are read (mirrors email-mcp / Codex #5).

Both the read projection (aggregator CalendarSource) and the action surface (MCP server) obtain
their backend through this module, so CalDAV credentials never live in two places. App password
first (provider-agnostic, shippable); OAuth is a later backend.

Env contract:
  CALENDAR_CALDAV_URL   e.g. https://apidata.googleusercontent.com/caldav/v2/ (Google) or any CalDAV
  CALENDAR_USER         account email / principal
  CALENDAR_PASSWORD     app password (whitespace-normalized like email-mcp)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CalendarAccount:
    caldav_url: str
    user: str
    password: str


def _clean_password(raw: str) -> str:
    """Strip ALL whitespace from an app password (incl. Google's U+00A0 gaps). See email-mcp."""
    return "".join(raw.split())


def load_account() -> CalendarAccount | None:
    """Build an account from env, or None if calendar is not configured (hub runs without it)."""
    url = (os.environ.get("CALENDAR_CALDAV_URL") or "").strip()
    user = (os.environ.get("CALENDAR_USER") or "").strip()
    password = _clean_password(os.environ.get("CALENDAR_PASSWORD") or "")
    if not (url and user and password):
        return None
    return CalendarAccount(caldav_url=url, user=user, password=password)

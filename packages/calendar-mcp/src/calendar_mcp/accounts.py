"""Credential loading — the ONE place calendar secrets are read (mirrors email-mcp / Codex #5).

Both the read projection (aggregator CalendarSource) and the action surface (MCP server) obtain
their backend through this module, so credentials never live in two places. Two backends:

PRIMARY — Google Calendar API via a service account (headless: no consent flow, no token rotation):
  GOOGLE_CALENDAR_SA_JSON   the service-account key JSON (full string)
  GOOGLE_CALENDAR_ID        calendar to read — your gmail address (the calendar shared with the SA)

FALLBACK — CalDAV (any provider):
  CALENDAR_CALDAV_URL, CALENDAR_USER, CALENDAR_PASSWORD (app password, whitespace-normalized)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GoogleCalendarAccount:
    sa_info: dict
    calendar_id: str


@dataclass(frozen=True)
class CalendarAccount:
    caldav_url: str
    user: str
    password: str


def _clean_password(raw: str) -> str:
    """Strip ALL whitespace from an app password (incl. Google's U+00A0 gaps). See email-mcp."""
    return "".join(raw.split())


def load_google_account() -> GoogleCalendarAccount | None:
    """Build a Google service-account config from env, or None if not configured."""
    raw = os.environ.get("GOOGLE_CALENDAR_SA_JSON") or ""
    calendar_id = (os.environ.get("GOOGLE_CALENDAR_ID") or "").strip()
    if not (raw.strip() and calendar_id):
        return None
    try:
        info = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(info, dict) or "client_email" not in info:
        return None
    return GoogleCalendarAccount(sa_info=info, calendar_id=calendar_id)


def load_account() -> CalendarAccount | None:
    """Build a CalDAV account from env, or None if not configured (Google is preferred)."""
    url = (os.environ.get("CALENDAR_CALDAV_URL") or "").strip()
    user = (os.environ.get("CALENDAR_USER") or "").strip()
    password = _clean_password(os.environ.get("CALENDAR_PASSWORD") or "")
    if not (url and user and password):
        return None
    return CalendarAccount(caldav_url=url, user=user, password=password)

"""Calendar read projection — reads THROUGH the calendar-mcp connector (Codex review #5).

This source never opens its own CalDAV connection. It obtains the one shared backend from
`calendar_mcp.get_backend()` (whose credentials live in `calendar_mcp.accounts`) and asks it for
upcoming events. If calendar is unconfigured (or `caldav` isn't installed) the backend is None /
raises, and this returns []. Actions (create) go through the agent holding the calendar-mcp tools.
"""

from __future__ import annotations


class CalendarSource:
    """Upcoming-events projection via the calendar connector. Injectable backend for tests."""

    name = "calendar"

    def __init__(self, backend=None, days: int = 14, limit: int = 50) -> None:
        self._backend = backend
        self._days = days
        self._limit = limit

    def fetch(self) -> dict:
        """Returns {"calendar": [ {id, summary, start, end, location, all_day}, ... ]}."""
        backend = self._backend
        if backend is None:
            try:
                from calendar_mcp import get_backend

                backend = get_backend()
            except Exception:
                return {"calendar": []}
        if backend is None:
            return {"calendar": []}
        try:
            return {"calendar": backend.list_events(self._days, self._limit)}
        except Exception:
            # never let a flaky calendar break the whole snapshot
            return {"calendar": []}

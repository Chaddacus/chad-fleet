"""calendar-mcp — CalDAV calendar connector. One credential home for the hub's calendar tab.

Mirrors email-mcp: a read path (aggregator CalendarSource) and an action surface (stdio MCP),
both obtaining the single backend from `get_backend()`.
"""

from __future__ import annotations

from .backend import CalendarBackend, CalDavBackend, GoogleCalendarBackend, get_backend

__all__ = ["CalendarBackend", "CalDavBackend", "GoogleCalendarBackend", "get_backend"]

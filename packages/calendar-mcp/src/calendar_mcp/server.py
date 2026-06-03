"""Stdio MCP server — the calendar ACTION surface a captain holds via `allowed_tools`.

Exposes `calendar_list` (read) and `calendar_create` (action), backed by the one connector
(`backend.get_backend()`). The hub never calls these directly; the admiral dispatches a captain
that holds them (read via projection, act via agent).

Uses FastMCP (the `mcp` package). Run: `calendar-mcp` (console script) or `python -m calendar_mcp.server`.
"""

from __future__ import annotations

from .backend import get_backend


def _require_backend():
    backend = get_backend()
    if backend is None:
        raise RuntimeError("calendar not configured — set CALENDAR_CALDAV_URL/USER/PASSWORD")
    return backend


def build_server():
    """Construct the FastMCP server. Imported lazily so the library is usable without `mcp`."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("calendar-mcp")

    @mcp.tool()
    def calendar_list(days: int = 14, limit: int = 50) -> list[dict]:
        """List upcoming events in the next `days` (soonest first): id, summary, start, end, location."""
        return _require_backend().list_events(days, limit)

    @mcp.tool()
    def calendar_create(summary: str, start: str, end: str, location: str = "") -> str:
        """Create an event. start/end are iCal datetimes (e.g. 20260610T150000Z). Returns 'created'."""
        return _require_backend().create_event(summary, start, end, location)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()

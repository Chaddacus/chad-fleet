"""FastMCP server exposing the tracked-app-registry as MCP tools.

Run with:
    python -m tracked_app_registry.server
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .registry import Registry

mcp = FastMCP("tracked-app-registry")
_registry = Registry()


@mcp.tool()
def tracked_app_create(app: dict) -> dict:
    """Create a new tracked app. app must match the TrackedApp schema."""
    from .models import TrackedApp

    record = TrackedApp.model_validate(app)
    result = _registry.create(record)
    return result.model_dump(mode="json")


@mcp.tool()
def tracked_app_update(app_id: str, fields: dict) -> dict:
    """Update fields on an existing tracked app."""
    result = _registry.update(app_id, **fields)
    return result.model_dump(mode="json")


@mcp.tool()
def tracked_app_set_state(
    app_id: str,
    state: str,
    blocked_reason: str | None = None,
) -> dict:
    """Change the state of a tracked app."""
    result = _registry.set_state(app_id, state, blocked_reason=blocked_reason)
    return result.model_dump(mode="json")


@mcp.tool()
def tracked_app_archive(app_id: str) -> dict:
    """Archive a tracked app."""
    _registry.archive(app_id)
    return {"archived": True, "app_id": app_id}


@mcp.tool()
def tracked_app_get(app_id: str) -> dict | None:
    """Get a tracked app by id. Returns None if not found."""
    app = _registry.get(app_id)
    if app is None:
        return None
    return app.model_dump(mode="json")


@mcp.tool()
def tracked_app_list(
    state: str | None = None,
    owner_brand: str | None = None,
) -> list[dict]:
    """List tracked apps, optionally filtered by state and/or owner_brand."""
    return [a.model_dump(mode="json") for a in _registry.list(state=state, owner_brand=owner_brand)]


@mcp.tool()
def tracked_app_events(
    app_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return recent events. Optionally filter by app_id."""
    evts = _registry.events(app_id=app_id)
    return [e.model_dump(mode="json") for e in evts[-limit:]]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

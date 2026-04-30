"""FastMCP server exposing view-registry as MCP tools.

Run with:
    python -m view_registry.server
"""

from __future__ import annotations

from fastmcp import FastMCP

from .registry import Registry

mcp = FastMCP("view-registry")
_registry = Registry()


@mcp.tool()
def view_create(
    name: str,
    prompt: str,
    description: str = "",
    app_scope: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Create a new saved view."""
    result = _registry.create(
        name=name,
        prompt=prompt,
        description=description,
        app_scope=app_scope,
        tags=tags,
    )
    return result.model_dump(mode="json")


@mcp.tool()
def view_update(
    view_id: str,
    name: str | None = None,
    description: str | None = None,
    prompt: str | None = None,
    app_scope: list[str] | None = None,
) -> dict:
    """Update fields on an existing saved view."""
    result = _registry.update(
        view_id,
        name=name,
        description=description,
        prompt=prompt,
        app_scope=app_scope,
    )
    return result.model_dump(mode="json")


@mcp.tool()
def view_get(view_id: str) -> dict | None:
    """Get a saved view by id. Returns None if not found."""
    view = _registry.get(view_id)
    if view is None:
        return None
    return view.model_dump(mode="json")


@mcp.tool()
def view_list(
    app: str | None = None,
    tag: str | None = None,
    pinned_only: bool = False,
) -> list[dict]:
    """List saved views, optionally filtered by app scope, tag, and/or pinned status."""
    return [v.model_dump(mode="json") for v in _registry.list(app=app, tag=tag, pinned_only=pinned_only)]


@mcp.tool()
def view_record_render(view_id: str, html: str, tsx: str) -> dict:
    """Record a render result for a view (updates last_rendered_at, last_render_html, last_render_tsx)."""
    result = _registry.record_render(view_id, html=html, tsx=tsx)
    return result.model_dump(mode="json")


@mcp.tool()
def view_pin(view_id: str) -> dict:
    """Pin a saved view."""
    result = _registry.pin(view_id)
    return result.model_dump(mode="json")


@mcp.tool()
def view_unpin(view_id: str) -> dict:
    """Unpin a saved view."""
    result = _registry.unpin(view_id)
    return result.model_dump(mode="json")


@mcp.tool()
def view_add_tag(view_id: str, tag: str) -> dict:
    """Add a tag to a saved view."""
    result = _registry.add_tag(view_id, tag)
    return result.model_dump(mode="json")


@mcp.tool()
def view_remove_tag(view_id: str, tag: str) -> dict:
    """Remove a tag from a saved view."""
    result = _registry.remove_tag(view_id, tag)
    return result.model_dump(mode="json")


@mcp.tool()
def view_delete(view_id: str) -> dict:
    """Delete a saved view."""
    _registry.delete(view_id)
    return {"deleted": True, "view_id": view_id}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

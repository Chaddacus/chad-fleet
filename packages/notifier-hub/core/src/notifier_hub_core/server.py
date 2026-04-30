"""MCP server for notifier-hub-core."""

from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from fastmcp import FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]

from notifier_hub_core.config import load_config
from notifier_hub_core.hub import NotifierHub
from notifier_hub_core.models import Notification

_hub: NotifierHub | None = None


def _get_hub() -> NotifierHub:
    global _hub
    if _hub is None:
        _hub = NotifierHub()
    return _hub


mcp = FastMCP("notifier-hub")


@mcp.tool()
def notifier_send(notification: dict) -> list[dict]:
    """Send a notification through the hub.

    Args:
        notification: dict with keys title, body, channel, severity (optional),
                      actions (optional).

    Returns:
        List of SendResult dicts with keys: adapter, ok, detail.
    """
    hub = _get_hub()
    n = Notification.model_validate(notification)
    results = hub.send(n)
    return [r.model_dump() for r in results]


@mcp.tool()
def notifier_routes() -> dict:
    """Return the current routing configuration (for debug).

    Returns:
        Dict with keys: routes (list of {channel, adapters}) and
        fallback_adapters.
    """
    env_path = os.environ.get("CHAD_NOTIFIER_CONFIG")
    config_path = Path(env_path) if env_path else None
    try:
        config = load_config(config_path)
    except FileNotFoundError as exc:
        return {"error": str(exc), "routes": [], "fallback_adapters": []}
    return {
        "routes": [
            {"channel": r.channel, "adapters": r.adapters}
            for r in config.routes
        ],
        "fallback_adapters": config.fallback_adapters,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

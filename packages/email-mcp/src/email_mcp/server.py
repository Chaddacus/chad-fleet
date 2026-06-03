"""Stdio MCP server — the email ACTION surface a captain holds via `allowed_tools`.

Exposes four tools backed by the one connector (`backend.get_backend()`): `email_list`,
`email_read`, `email_send`, `email_archive`. The hub never calls these directly; the admiral
dispatches a captain that holds them (read via projection, act via agent).

Uses FastMCP (the `mcp` package). Run: `email-mcp` (console script) or `python -m email_mcp.server`.
"""

from __future__ import annotations

from .backend import get_backend


def _require_backend():
    backend = get_backend()
    if backend is None:
        raise RuntimeError("email not configured — set EMAIL_IMAP_HOST/USER/PASSWORD")
    return backend


def build_server():
    """Construct the FastMCP server. Imported lazily so the library is usable without `mcp`."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("email-mcp")

    @mcp.tool()
    def email_list(limit: int = 25) -> list[dict]:
        """List recent messages (newest first): id, subject, from_, date, unread."""
        return _require_backend().list_recent(limit)

    @mcp.tool()
    def email_read(msg_id: str) -> dict:
        """Read one message's full body by id."""
        return _require_backend().fetch(msg_id)

    @mcp.tool()
    def email_send(to: str, subject: str, body: str) -> str:
        """Send a plain-text email. Returns 'sent' on success."""
        _require_backend().send(to, subject, body)
        return "sent"

    @mcp.tool()
    def email_archive(msg_id: str) -> str:
        """Archive (remove from inbox) a message by id. Returns 'archived'."""
        _require_backend().archive(msg_id)
        return "archived"

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()

"""Email read projection — reads THROUGH the email-mcp connector (Codex review #5).

This source never opens its own IMAP connection. It obtains the one shared backend from
`email_mcp.get_backend()` (whose credentials live in `email_mcp.accounts`) and asks it for a
recent-message list. If email is unconfigured the backend is None and this returns []. Actions
(send/archive) go through the agent holding the email-mcp tools, never through this read path.
"""

from __future__ import annotations


class EmailSource:
    """Recent-inbox projection via the email connector. Injectable backend for tests."""

    name = "email"

    def __init__(self, backend=None, limit: int = 25) -> None:
        self._backend = backend
        self._limit = limit

    def fetch(self) -> dict:
        """Returns {"email": [ {id, subject, from_, date, unread, snippet}, ... ]}."""
        backend = self._backend
        if backend is None:
            try:
                from email_mcp import get_backend

                backend = get_backend()
            except Exception:
                return {"email": []}
        if backend is None:
            return {"email": []}
        try:
            return {"email": backend.list_recent(self._limit)}
        except Exception:
            # never let a flaky mailbox break the whole snapshot
            return {"email": []}

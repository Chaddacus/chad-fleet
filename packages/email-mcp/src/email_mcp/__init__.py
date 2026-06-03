"""email-mcp — the hub's email connector (IMAP/SMTP).

Library + stdio MCP. One credential home (`accounts.load_account`); both the aggregator's
read projection and the MCP action surface obtain their backend through `backend.get_backend`.
"""

from .accounts import EmailAccount, load_account
from .backend import ImapBackend, MailBackend, get_backend

__all__ = ["EmailAccount", "load_account", "MailBackend", "ImapBackend", "get_backend"]

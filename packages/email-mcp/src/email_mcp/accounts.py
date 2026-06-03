"""Credential loading — the ONE place email secrets are read (Codex review #5).

Both the read projection (aggregator EmailSource) and the action surface (MCP server) obtain
their backend through this module, so IMAP/SMTP credentials never live in two places. App
password first (provider-agnostic, shippable); OAuth is a later backend.

Env contract (see hub plan):
  EMAIL_IMAP_HOST, EMAIL_IMAP_PORT (default 993), EMAIL_IMAP_USER, EMAIL_IMAP_PASSWORD
  EMAIL_SMTP_HOST, EMAIL_SMTP_PORT (default 465)   [user/password reused from IMAP]
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EmailAccount:
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    user: str
    password: str


def _clean_password(raw: str) -> str:
    """Strip ALL whitespace from an app password.

    App passwords (Gmail and friends) are whitespace-free tokens; the gaps shown in the
    provider UI are presentational. Crucially, Google's UI copies those gaps as non-breaking
    spaces (U+00A0), which imaplib's ascii LOGIN encoder rejects with UnicodeEncodeError.
    `str.split()` treats regular spaces, tabs, newlines AND U+00A0 as whitespace, so this
    normalizes every spacing artifact to the bare 16-char token.
    """
    return "".join(raw.split())


def load_account() -> EmailAccount | None:
    """Build an account from env, or None if email is not configured (hub runs without it)."""
    host = (os.environ.get("EMAIL_IMAP_HOST") or "").strip()
    user = (os.environ.get("EMAIL_IMAP_USER") or "").strip()
    raw_password = os.environ.get("EMAIL_IMAP_PASSWORD") or ""
    password = _clean_password(raw_password)
    if not (host and user and password):
        return None
    return EmailAccount(
        imap_host=host,
        imap_port=int(os.environ.get("EMAIL_IMAP_PORT", "993")),
        smtp_host=(os.environ.get("EMAIL_SMTP_HOST") or host).strip(),
        smtp_port=int(os.environ.get("EMAIL_SMTP_PORT", "465")),
        user=user,
        password=password,
    )

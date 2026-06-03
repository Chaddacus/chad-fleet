"""Connector tests. Live IMAP/SMTP isn't exercised here (needs a real account); these cover
credential loading, the None-when-unconfigured contract, and the MailBackend seam via a fake."""

from __future__ import annotations

import email_mcp
from email_mcp.accounts import load_account
from email_mcp.backend import MailBackend, get_backend


def test_load_account_none_when_unconfigured(monkeypatch):
    for k in ("EMAIL_IMAP_HOST", "EMAIL_IMAP_USER", "EMAIL_IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert load_account() is None
    assert get_backend() is None


def test_load_account_from_env(monkeypatch):
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_IMAP_USER", "me@example.com")
    monkeypatch.setenv("EMAIL_IMAP_PASSWORD", "app-pass")
    acct = load_account()
    assert acct is not None
    assert acct.imap_host == "imap.example.com"
    assert acct.imap_port == 993  # default
    assert acct.smtp_host == "imap.example.com"  # falls back to imap host


def test_app_password_whitespace_stripped(monkeypatch):
    """Regression: Gmail's UI copies group gaps as non-breaking spaces (U+00A0), which
    imaplib's ascii LOGIN encoder rejected with UnicodeEncodeError. The token must be cleaned."""
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.gmail.com")
    monkeypatch.setenv("EMAIL_IMAP_USER", "me@gmail.com")
    # nbsp + regular space + tab between the four groups, plus stray edges
    monkeypatch.setenv("EMAIL_IMAP_PASSWORD", "  abcd\xa0efgh ijkl\tmnop ")
    acct = load_account()
    assert acct is not None
    assert acct.password == "abcdefghijklmnop"
    acct.password.encode("ascii")  # the original crash site — must not raise


def test_host_and_user_trimmed(monkeypatch):
    monkeypatch.setenv("EMAIL_IMAP_HOST", " imap.gmail.com\n")
    monkeypatch.setenv("EMAIL_IMAP_USER", "me@gmail.com ")
    monkeypatch.setenv("EMAIL_IMAP_PASSWORD", "tok")
    acct = load_account()
    assert acct.imap_host == "imap.gmail.com"
    assert acct.user == "me@gmail.com"


class FakeBackend:
    """Satisfies the MailBackend protocol for tests."""

    def list_recent(self, limit=25):
        return [{"id": "1", "subject": "hi", "from_": "a@b.c", "date": "", "unread": True, "snippet": ""}][:limit]

    def fetch(self, msg_id):
        return {"id": msg_id, "subject": "hi", "from_": "a@b.c", "date": "", "body": "hello"}

    def archive(self, msg_id):
        return None

    def send(self, to, subject, body):
        return None


def test_fake_satisfies_protocol():
    assert isinstance(FakeBackend(), MailBackend)


def test_public_api_exports():
    assert hasattr(email_mcp, "get_backend")
    assert hasattr(email_mcp, "MailBackend")

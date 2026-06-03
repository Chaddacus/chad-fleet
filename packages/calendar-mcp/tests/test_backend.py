"""Connector tests. Live CalDAV isn't exercised here (needs a real account); these cover
credential loading, the None-when-unconfigured contract, and the CalendarBackend seam via a fake."""

from __future__ import annotations

from calendar_mcp.accounts import load_account
from calendar_mcp.backend import CalendarBackend, get_backend


def test_load_account_none_when_unconfigured(monkeypatch):
    for k in ("CALENDAR_CALDAV_URL", "CALENDAR_USER", "CALENDAR_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert load_account() is None
    assert get_backend() is None


def test_load_account_from_env(monkeypatch):
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://dav.example.com/")
    monkeypatch.setenv("CALENDAR_USER", "me@example.com")
    monkeypatch.setenv("CALENDAR_PASSWORD", "app-pass")
    acct = load_account()
    assert acct is not None
    assert acct.caldav_url == "https://dav.example.com/"
    assert acct.user == "me@example.com"


def test_app_password_whitespace_stripped(monkeypatch):
    """Mirror email-mcp: Google copies app-password gaps as U+00A0; normalize to the bare token."""
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://dav.example.com/")
    monkeypatch.setenv("CALENDAR_USER", "me@gmail.com")
    monkeypatch.setenv("CALENDAR_PASSWORD", "  abcd\xa0efgh ijkl\tmnop ")
    acct = load_account()
    assert acct is not None
    assert acct.password == "abcdefghijklmnop"
    acct.password.encode("ascii")  # must not raise


class FakeBackend:
    """Satisfies the CalendarBackend protocol for tests."""

    def list_events(self, days=14, limit=50):
        return [
            {"id": "1", "summary": "Standup", "start": "2026-06-10T15:00:00+00:00",
             "end": "2026-06-10T15:15:00+00:00", "location": "", "all_day": False},
        ][:limit]

    def create_event(self, summary, start, end, location=""):
        return "created"


def test_fake_satisfies_protocol():
    fake = FakeBackend()
    assert isinstance(fake, CalendarBackend)
    events = fake.list_events(limit=5)
    assert events[0]["summary"] == "Standup"
    assert fake.create_event("x", "20260610T150000Z", "20260610T153000Z") == "created"

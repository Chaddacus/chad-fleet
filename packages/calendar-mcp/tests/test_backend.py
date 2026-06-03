"""Connector tests. Live CalDAV isn't exercised here (needs a real account); these cover
credential loading, the None-when-unconfigured contract, and the CalendarBackend seam via a fake."""

from __future__ import annotations

import json

from calendar_mcp.accounts import load_account, load_google_account
from calendar_mcp.backend import CalendarBackend, CalDavBackend, GoogleCalendarBackend, get_backend

_ALL_ENV = (
    "CALENDAR_CALDAV_URL", "CALENDAR_USER", "CALENDAR_PASSWORD",
    "GOOGLE_CALENDAR_SA_JSON", "GOOGLE_CALENDAR_ID",
)
_FAKE_SA = json.dumps({"client_email": "sa@proj.iam.gserviceaccount.com", "private_key": "x", "type": "service_account"})


def test_load_account_none_when_unconfigured(monkeypatch):
    for k in _ALL_ENV:
        monkeypatch.delenv(k, raising=False)
    assert load_account() is None
    assert load_google_account() is None
    assert get_backend() is None


def test_google_account_loads_and_is_preferred(monkeypatch):
    for k in _ALL_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GOOGLE_CALENDAR_SA_JSON", _FAKE_SA)
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "me@gmail.com")
    acct = load_google_account()
    assert acct is not None and acct.calendar_id == "me@gmail.com"
    assert acct.sa_info["client_email"] == "sa@proj.iam.gserviceaccount.com"
    # selection prefers Google even when CalDAV is also set
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://dav.example.com/")
    monkeypatch.setenv("CALENDAR_USER", "me@example.com")
    monkeypatch.setenv("CALENDAR_PASSWORD", "tok")
    assert isinstance(get_backend(), GoogleCalendarBackend)


def test_google_account_rejects_bad_json(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_SA_JSON", "{not json")
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "me@gmail.com")
    assert load_google_account() is None


def test_caldav_fallback_when_no_google(monkeypatch):
    for k in _ALL_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://dav.example.com/")
    monkeypatch.setenv("CALENDAR_USER", "me@example.com")
    monkeypatch.setenv("CALENDAR_PASSWORD", "tok")
    assert isinstance(get_backend(), CalDavBackend)


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

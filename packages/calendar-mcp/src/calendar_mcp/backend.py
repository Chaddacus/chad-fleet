"""Calendar backend: the connector abstraction both the read projection and the MCP use.

`CalendarBackend` is the seam — `CalDavBackend` is the real implementation (lazily importing the
`caldav` lib); tests inject a fake. Read method (`list_events`) feeds the aggregator's calendar
tab; action method (`create_event`) is exposed by the MCP server for captains to call. All
credentials come from `accounts.load_account()` (one home).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from .accounts import CalendarAccount, load_account


@runtime_checkable
class CalendarBackend(Protocol):
    def list_events(self, days: int = 14, limit: int = 50) -> list[dict]: ...
    def create_event(self, summary: str, start: str, end: str, location: str = "") -> str: ...


def _iso(value) -> str:
    """Normalize a date/datetime (or str) to an ISO string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:
        return str(value)


class CalDavBackend:
    """Real CalDAV backend. Imports `caldav` lazily so the library is usable without it."""

    def __init__(self, account: CalendarAccount) -> None:
        self._account = account

    def _principal(self):
        import caldav  # lazy: only needed for live calls

        client = caldav.DAVClient(
            url=self._account.caldav_url,
            username=self._account.user,
            password=self._account.password,
        )
        return client.principal()

    def list_events(self, days: int = 14, limit: int = 50) -> list[dict]:
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days)
        out: list[dict] = []
        for cal in self._principal().calendars():
            try:
                events = cal.search(start=start, end=end, event=True, expand=True)
            except TypeError:
                # older caldav signatures
                events = cal.date_search(start=start, end=end)
            for ev in events:
                comp = getattr(ev, "vobject_instance", None)
                vevent = getattr(comp, "vevent", None) if comp is not None else None
                if vevent is None:
                    continue
                dtstart = getattr(getattr(vevent, "dtstart", None), "value", None)
                dtend = getattr(getattr(vevent, "dtend", None), "value", None)
                out.append({
                    "id": str(getattr(getattr(vevent, "uid", None), "value", "")),
                    "summary": str(getattr(getattr(vevent, "summary", None), "value", "")),
                    "start": _iso(dtstart),
                    "end": _iso(dtend),
                    "location": str(getattr(getattr(vevent, "location", None), "value", "")),
                    "all_day": not isinstance(dtstart, datetime),
                })
        out.sort(key=lambda e: e["start"])
        return out[:limit]

    def create_event(self, summary: str, start: str, end: str, location: str = "") -> str:
        cals = self._principal().calendars()
        if not cals:
            raise RuntimeError("no calendar available for create")
        ical = (
            "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//chad-fleet//calendar-mcp//EN\n"
            "BEGIN:VEVENT\n"
            f"SUMMARY:{summary}\n"
            f"DTSTART:{start}\n"
            f"DTEND:{end}\n"
            + (f"LOCATION:{location}\n" if location else "")
            + "END:VEVENT\nEND:VCALENDAR\n"
        )
        cals[0].save_event(ical)
        return "created"


def get_backend() -> CalendarBackend | None:
    """The configured backend, or None if calendar is not set up (hub runs without it)."""
    account = load_account()
    return CalDavBackend(account) if account is not None else None

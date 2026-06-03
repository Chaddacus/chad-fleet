"""CalendarSource tests — reads through an injected backend (no live CalDAV)."""

from __future__ import annotations

from state_aggregator.aggregator import Aggregator
from state_aggregator.sources import CalendarSource


class FakeBackend:
    def __init__(self, events):
        self._events = events

    def list_events(self, days=14, limit=50):
        return self._events[:limit]

    def create_event(self, summary, start, end, location=""):
        return "created"


class BrokenBackend:
    def list_events(self, days=14, limit=50):
        raise RuntimeError("calendar unreachable")


_EVENTS = [
    {"id": "a", "summary": "Standup", "start": "2026-06-10T15:00:00+00:00",
     "end": "2026-06-10T15:15:00+00:00", "location": "Zoom", "all_day": False},
    {"id": "b", "summary": "Review", "start": "2026-06-11T18:00:00+00:00",
     "end": "2026-06-11T19:00:00+00:00", "location": "", "all_day": False},
]


def test_calendar_source_reads_through_backend():
    out = CalendarSource(backend=FakeBackend(_EVENTS)).fetch()["calendar"]
    assert [e["id"] for e in out] == ["a", "b"]
    assert out[0]["summary"] == "Standup"


def test_calendar_source_empty_when_no_backend():
    assert CalendarSource(backend=None).fetch() == {"calendar": []}


def test_calendar_source_survives_backend_error():
    assert CalendarSource(backend=BrokenBackend()).fetch() == {"calendar": []}


def test_aggregator_includes_calendar_and_count():
    agg = Aggregator(sources=[CalendarSource(backend=FakeBackend(_EVENTS))])
    snap = agg.snapshot()
    assert snap.summary["calendar_count"] == 2
    assert {e.id for e in snap.calendar} == {"a", "b"}
    assert snap.calendar[0].location == "Zoom"

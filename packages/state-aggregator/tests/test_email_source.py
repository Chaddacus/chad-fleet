"""EmailSource tests — reads through an injected backend (no live IMAP)."""

from __future__ import annotations

from state_aggregator.aggregator import Aggregator
from state_aggregator.sources import EmailSource


class FakeBackend:
    def __init__(self, msgs):
        self._msgs = msgs

    def list_recent(self, limit=25):
        return self._msgs[:limit]

    def fetch(self, msg_id):  # unused here
        return {}

    def archive(self, msg_id):
        return None

    def send(self, to, subject, body):
        return None


class BrokenBackend:
    def list_recent(self, limit=25):
        raise RuntimeError("mailbox unreachable")


_MSGS = [
    {"id": "2", "subject": "newer", "from_": "x@y.z", "date": "", "unread": True, "snippet": ""},
    {"id": "1", "subject": "older", "from_": "a@b.c", "date": "", "unread": False, "snippet": ""},
]


def test_email_source_reads_through_backend():
    out = EmailSource(backend=FakeBackend(_MSGS)).fetch()["email"]
    assert [m["id"] for m in out] == ["2", "1"]
    assert out[0]["subject"] == "newer"


def test_email_source_empty_when_no_backend():
    assert EmailSource(backend=None).fetch() == {"email": []}  # email-mcp returns None unconfigured


def test_email_source_survives_backend_error():
    assert BrokenBackend()  # sanity
    assert EmailSource(backend=BrokenBackend()).fetch() == {"email": []}


def test_aggregator_includes_email_and_counts():
    agg = Aggregator(sources=[EmailSource(backend=FakeBackend(_MSGS))])
    snap = agg.snapshot()
    assert snap.summary["email_count"] == 2
    assert snap.summary["email_unread"] == 1
    assert {m.id for m in snap.email} == {"1", "2"}
    assert snap.email[0].from_ == "x@y.z"

"""Tests for InboxSource using tmp_path."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from state_aggregator.sources import InboxSource


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _write_inbox(path: Path, items: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(i) for i in items))


def test_inbox_source_missing_file(tmp_path):
    src = InboxSource(inbox_path=tmp_path / "nonexistent.jsonl")
    result = src.fetch()
    assert result == {"items": []}


def test_inbox_source_reads_items(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    items = [
        {"ts": _ts(), "channel": "zoom", "severity": "info", "title": "Hi", "body": "Hello"},
        {"ts": _ts(), "channel": "email", "severity": "warn", "title": "Disk", "body": "85%"},
    ]
    _write_inbox(inbox, items)

    src = InboxSource(inbox_path=inbox)
    result = src.fetch()
    assert len(result["items"]) == 2
    assert result["items"][0]["channel"] == "zoom"
    assert result["items"][1]["severity"] == "warn"


def test_inbox_source_last_n(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    items = [
        {"ts": _ts(), "channel": "zoom", "severity": "info", "title": f"msg-{i}", "body": "b"}
        for i in range(10)
    ]
    _write_inbox(inbox, items)

    src = InboxSource(inbox_path=inbox, last_n=3)
    result = src.fetch()
    assert len(result["items"]) == 3
    # Last 3 items
    assert result["items"][-1]["title"] == "msg-9"


def test_inbox_source_skips_malformed(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    lines = [
        json.dumps({"ts": _ts(), "channel": "zoom", "severity": "info", "title": "ok", "body": "b"}),
        "not json at all",
        json.dumps({"ts": _ts(), "channel": "zoom", "severity": "critical", "title": "alert", "body": "x"}),
    ]
    inbox.write_text("\n".join(lines))

    src = InboxSource(inbox_path=inbox)
    result = src.fetch()
    # Only 2 valid items
    assert len(result["items"]) == 2


def test_inbox_source_env_var(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox.jsonl"
    items = [
        {"ts": _ts(), "channel": "zoom", "severity": "info", "title": "env-test", "body": "b"}
    ]
    _write_inbox(inbox, items)

    monkeypatch.setenv("CHAD_NOTIFIER_INBOX_PATH", str(inbox))
    src = InboxSource()  # no explicit path — picks up env var
    result = src.fetch()
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "env-test"

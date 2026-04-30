"""Tests for DashboardInboxAdapter."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from notifier_hub_core.models import Action, Notification
from notifier_hub_dashboard_inbox import DashboardInboxAdapter


def _make_notification(**kwargs) -> Notification:
    defaults = {
        "title": "Test",
        "body": "Hello",
        "severity": "info",
        "channel": "test-chan",
        "actions": [],
    }
    defaults.update(kwargs)
    return Notification(**defaults)


def test_append_creates_file(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.jsonl"
    adapter = DashboardInboxAdapter(inbox_path=inbox)
    n = _make_notification(title="First")
    result = adapter.send(n)
    assert result.ok is True
    assert result.adapter == "dashboard-inbox"
    assert inbox.exists()


def test_multiple_notifications_preserved_in_order(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.jsonl"
    adapter = DashboardInboxAdapter(inbox_path=inbox)
    titles = ["Alpha", "Beta", "Gamma"]
    for t in titles:
        adapter.send(_make_notification(title=t))
    lines = inbox.read_text().splitlines()
    assert len(lines) == 3
    for i, t in enumerate(titles):
        assert json.loads(lines[i])["title"] == t


def test_file_created_in_nested_missing_dir(tmp_path: Path) -> None:
    inbox = tmp_path / "a" / "b" / "c" / "inbox.jsonl"
    adapter = DashboardInboxAdapter(inbox_path=inbox)
    result = adapter.send(_make_notification())
    assert result.ok is True
    assert inbox.exists()


def test_serialization_includes_all_fields(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.jsonl"
    adapter = DashboardInboxAdapter(inbox_path=inbox)
    n = _make_notification(
        title="My Title",
        body="My Body",
        severity="warn",
        channel="alerts",
        actions=[Action(label="Open", url="https://example.com")],
    )
    adapter.send(n)
    record = json.loads(inbox.read_text().strip())
    assert record["title"] == "My Title"
    assert record["body"] == "My Body"
    assert record["severity"] == "warn"
    assert record["channel"] == "alerts"
    assert record["actions"] == [{"label": "Open", "url": "https://example.com", "command": None}]
    assert "ts" in record


def test_env_var_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = tmp_path / "env_inbox.jsonl"
    monkeypatch.setenv("CHAD_NOTIFIER_INBOX_PATH", str(inbox))
    adapter = DashboardInboxAdapter()
    result = adapter.send(_make_notification())
    assert result.ok is True
    assert inbox.exists()


def test_ok_false_on_permission_error(tmp_path: Path) -> None:
    inbox_dir = tmp_path / "locked"
    inbox_dir.mkdir()
    inbox = inbox_dir / "inbox.jsonl"
    # Write once so file exists, then lock the dir
    inbox.write_text("")
    inbox.chmod(0o000)
    try:
        adapter = DashboardInboxAdapter(inbox_path=inbox)
        result = adapter.send(_make_notification())
        assert result.ok is False
        assert result.detail is not None
        assert "Error" in result.detail or "Permission" in result.detail or "error" in result.detail.lower()
    finally:
        inbox.chmod(0o644)

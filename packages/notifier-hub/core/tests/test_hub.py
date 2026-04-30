"""Tests for NotifierHub dispatch and error isolation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from notifier_hub_core.hub import NotifierHub
from notifier_hub_core.models import Notification, SendResult


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "routes.yml"
    p.write_text(textwrap.dedent(content))
    return p


class _OkAdapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[Notification] = []

    def send(self, notification: Notification) -> SendResult:
        self.calls.append(notification)
        return SendResult(adapter=self.name, ok=True)


class _FailAdapter:
    name = "fail-adapter"

    def send(self, notification: Notification) -> SendResult:  # noqa: ARG002
        raise RuntimeError("boom")


def test_adapter_registration(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: test.ch
            adapters: [a1]
    """)
    hub = NotifierHub(config_path=cfg_path)
    a1 = _OkAdapter("a1")
    hub.register(a1)
    n = Notification(title="T", body="B", channel="test.ch")
    results = hub.send(n)
    assert len(results) == 1
    assert results[0].ok
    assert len(a1.calls) == 1


def test_send_dispatches_to_multiple_adapters(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: multi.ch
            adapters: [a1, a2]
    """)
    hub = NotifierHub(config_path=cfg_path)
    a1 = _OkAdapter("a1")
    a2 = _OkAdapter("a2")
    hub.register(a1)
    hub.register(a2)
    n = Notification(title="T", body="B", channel="multi.ch")
    results = hub.send(n)
    assert len(results) == 2
    assert all(r.ok for r in results)
    assert len(a1.calls) == 1
    assert len(a2.calls) == 1


def test_adapter_failure_does_not_block_others(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: mixed.ch
            adapters: [fail-adapter, ok-adapter]
    """)
    hub = NotifierHub(config_path=cfg_path)
    hub.register(_FailAdapter())
    ok = _OkAdapter("ok-adapter")
    hub.register(ok)
    n = Notification(title="T", body="B", channel="mixed.ch")
    results = hub.send(n)
    fail_results = [r for r in results if r.adapter == "fail-adapter"]
    ok_results = [r for r in results if r.adapter == "ok-adapter"]
    assert len(fail_results) == 1
    assert not fail_results[0].ok
    assert "RuntimeError" in (fail_results[0].detail or "")
    assert len(ok_results) == 1
    assert ok_results[0].ok
    assert len(ok.calls) == 1


def test_unregistered_adapter_returns_error_result(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: test.ch
            adapters: [ghost]
    """)
    hub = NotifierHub(config_path=cfg_path)
    n = Notification(title="T", body="B", channel="test.ch")
    results = hub.send(n)
    assert len(results) == 1
    assert not results[0].ok
    assert "ghost" in (results[0].detail or "")


def test_notification_fields_passed_to_adapter(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: fields.test
            adapters: [recorder]
    """)
    hub = NotifierHub(config_path=cfg_path)
    recorder = _OkAdapter("recorder")
    hub.register(recorder)
    n = Notification(
        title="My Title",
        body="My Body",
        severity="critical",
        channel="fields.test",
    )
    hub.send(n)
    sent = recorder.calls[0]
    assert sent.title == "My Title"
    assert sent.body == "My Body"
    assert sent.severity == "critical"

"""Tests for channel matching and fallback routing logic."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from notifier_hub_core.config import ConfigNotFoundError, load_config, RoutingConfig, RouteEntry
from notifier_hub_core.hub import NotifierHub
from notifier_hub_core.models import Notification, SendResult


# --- helpers ---

def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "routes.yml"
    p.write_text(textwrap.dedent(content))
    return p


def _make_hub(config_path: Path) -> NotifierHub:
    return NotifierHub(config_path=config_path)


class _StubAdapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.received: list[Notification] = []

    def send(self, notification: Notification) -> SendResult:
        self.received.append(notification)
        return SendResult(adapter=self.name, ok=True)


# --- config loading ---

def test_load_config_basic(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: captain.daily-brief
            adapters: [dashboard-inbox, ntfy]
          - channel: drafter.new-variations
            adapters: [dashboard-inbox]
        fallback_adapters: [dashboard-inbox]
    """)
    config = load_config(cfg_path)
    assert len(config.routes) == 2
    assert config.routes[0].channel == "captain.daily-brief"
    assert config.routes[0].adapters == ["dashboard-inbox", "ntfy"]
    assert config.fallback_adapters == ["dashboard-inbox"]


def test_load_config_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigNotFoundError):
        load_config(tmp_path / "nonexistent.yml")


def test_load_config_empty_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "routes.yml"
    cfg_path.write_text("")
    config = load_config(cfg_path)
    assert config.routes == []
    assert config.fallback_adapters == []


# --- channel matching ---

def test_exact_channel_match(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: captain.daily-brief
            adapters: [ntfy]
        fallback_adapters: [dashboard-inbox]
    """)
    hub = _make_hub(cfg_path)
    ntfy = _StubAdapter("ntfy")
    hub.register(ntfy)
    n = Notification(title="T", body="B", channel="captain.daily-brief")
    results = hub.send(n)
    assert len(results) == 1
    assert results[0].adapter == "ntfy"
    assert results[0].ok


def test_glob_channel_match(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: "captain.*"
            adapters: [ntfy]
        fallback_adapters: [dashboard-inbox]
    """)
    hub = _make_hub(cfg_path)
    ntfy = _StubAdapter("ntfy")
    dashboard = _StubAdapter("dashboard-inbox")
    hub.register(ntfy)
    hub.register(dashboard)

    n = Notification(title="T", body="B", channel="captain.alert")
    results = hub.send(n)
    # glob matched ntfy route, not fallback
    assert any(r.adapter == "ntfy" for r in results)
    assert all(r.adapter != "dashboard-inbox" for r in results)


def test_fallback_used_for_unknown_channel(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: captain.daily-brief
            adapters: [ntfy]
        fallback_adapters: [dashboard-inbox]
    """)
    hub = _make_hub(cfg_path)
    dashboard = _StubAdapter("dashboard-inbox")
    hub.register(dashboard)

    n = Notification(title="T", body="B", channel="unknown.channel")
    results = hub.send(n)
    assert len(results) == 1
    assert results[0].adapter == "dashboard-inbox"
    assert results[0].ok


def test_no_fallback_returns_empty_for_unknown(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, """\
        routes:
          - channel: captain.daily-brief
            adapters: [ntfy]
    """)
    hub = _make_hub(cfg_path)
    n = Notification(title="T", body="B", channel="other.thing")
    results = hub.send(n)
    assert results == []

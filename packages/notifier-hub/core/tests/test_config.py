"""Tests for config loading."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from notifier_hub_core.config import ConfigNotFoundError, load_config


def test_load_config_via_env_var(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "routes.yml"
    cfg_path.write_text(textwrap.dedent("""\
        routes:
          - channel: env.test
            adapters: [inbox]
        fallback_adapters: []
    """))
    monkeypatch.setenv("CHAD_NOTIFIER_CONFIG", str(cfg_path))
    config = load_config()
    assert len(config.routes) == 1
    assert config.routes[0].channel == "env.test"


def test_missing_config_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigNotFoundError) as exc_info:
        load_config(tmp_path / "missing.yml")
    assert "missing.yml" in str(exc_info.value)


def test_config_with_no_fallback(tmp_path: Path) -> None:
    cfg_path = tmp_path / "routes.yml"
    cfg_path.write_text(textwrap.dedent("""\
        routes:
          - channel: only.route
            adapters: [ntfy]
    """))
    config = load_config(cfg_path)
    assert config.fallback_adapters == []


def test_config_routes_order_preserved(tmp_path: Path) -> None:
    cfg_path = tmp_path / "routes.yml"
    cfg_path.write_text(textwrap.dedent("""\
        routes:
          - channel: first
            adapters: [a]
          - channel: second
            adapters: [b]
          - channel: third
            adapters: [c]
    """))
    config = load_config(cfg_path)
    channels = [r.channel for r in config.routes]
    assert channels == ["first", "second", "third"]

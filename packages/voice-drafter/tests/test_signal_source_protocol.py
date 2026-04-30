"""Tests for SignalSource protocol compliance and OmniMemSignalSource behavior."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from voice_drafter.signal_source import OmniMemSignalSource, SignalSource
from voice_drafter.types import SignalItem, SignalPack


# ---- Protocol fixture --------------------------------------------------------

class _FixtureSource:
    """Minimal fixture implementation that satisfies the SignalSource protocol."""

    name = "fixture"

    def fetch(self, since: datetime, limit: int) -> SignalPack:
        items = [
            SignalItem(id=f"item-{i}", text=f"Signal {i}", score=float(i) / limit)
            for i in range(min(limit, 3))
        ]
        return SignalPack(items=items, source=self.name, generated_at=datetime.now(timezone.utc))


class TestSignalSourceProtocol:
    def test_fixture_satisfies_protocol(self):
        src = _FixtureSource()
        # runtime_checkable Protocol check
        assert isinstance(src, SignalSource)

    def test_fetch_returns_signal_pack(self):
        src = _FixtureSource()
        pack = src.fetch(since=datetime.now(timezone.utc), limit=3)
        assert isinstance(pack, SignalPack)
        assert pack.source == "fixture"

    def test_fetch_respects_limit(self):
        src = _FixtureSource()
        pack = src.fetch(since=datetime.now(timezone.utc), limit=2)
        assert len(pack.items) <= 2

    def test_items_are_signal_items(self):
        src = _FixtureSource()
        pack = src.fetch(since=datetime.now(timezone.utc), limit=3)
        for item in pack.items:
            assert isinstance(item, SignalItem)
            assert 0.0 <= item.score <= 1.0

    def test_omni_mem_satisfies_protocol(self):
        src = OmniMemSignalSource()
        assert isinstance(src, SignalSource)


# ---- OmniMemSignalSource subprocess shape -----------------------------------

class TestOmniMemSignalSource:
    def _proc(self, stdout: str = "[]", returncode: int = 0) -> MagicMock:
        m = MagicMock()
        m.stdout = stdout
        m.stderr = ""
        m.returncode = returncode
        return m

    def test_subprocess_command_shape(self):
        src = OmniMemSignalSource(container="omni-mem", query="test query")
        with patch("subprocess.run", return_value=self._proc("[]")) as mock_run:
            src.fetch(since=datetime.now(timezone.utc), limit=5)

        args = mock_run.call_args[0][0]
        assert args[0] == "docker"
        assert args[1] == "exec"
        assert "omni-mem" in args
        assert "omni-mem" in args  # container name
        assert "search" in args
        assert "--query" in args
        assert "--limit" in args
        assert "5" in args

    def test_successful_fetch_parses_items(self):
        src = OmniMemSignalSource()
        items_json = json.dumps([
            {"id": "m1", "content": "first signal", "score": 0.9},
            {"id": "m2", "content": "second signal", "score": 0.7},
        ])
        with patch("subprocess.run", return_value=self._proc(items_json)):
            pack = src.fetch(since=datetime.now(timezone.utc), limit=5)

        assert len(pack.items) == 2
        assert pack.items[0].id == "m1"
        assert pack.items[0].score == 0.9

    def test_failed_subprocess_returns_empty_pack(self):
        src = OmniMemSignalSource()
        with patch("subprocess.run", return_value=self._proc("", returncode=1)):
            pack = src.fetch(since=datetime.now(timezone.utc), limit=5)

        assert pack.items == []
        assert pack.source == "omni-mem"

    def test_timeout_returns_empty_pack(self):
        src = OmniMemSignalSource(timeout=1)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=1)):
            pack = src.fetch(since=datetime.now(timezone.utc), limit=5)

        assert pack.items == []

    def test_docker_not_found_returns_empty_pack(self):
        src = OmniMemSignalSource()
        with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
            pack = src.fetch(since=datetime.now(timezone.utc), limit=5)

        assert pack.items == []

    def test_invalid_json_returns_empty_pack(self):
        src = OmniMemSignalSource()
        with patch("subprocess.run", return_value=self._proc("not valid json")):
            pack = src.fetch(since=datetime.now(timezone.utc), limit=5)

        assert pack.items == []

    def test_memory_id_field_mapped(self):
        src = OmniMemSignalSource()
        items_json = json.dumps([{"memory_id": "mem-99", "text": "body here", "relevance": 0.6}])
        with patch("subprocess.run", return_value=self._proc(items_json)):
            pack = src.fetch(since=datetime.now(timezone.utc), limit=5)

        assert pack.items[0].id == "mem-99"
        assert pack.items[0].score == 0.6

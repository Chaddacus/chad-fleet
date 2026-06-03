"""Tests for SessionsSource (the hub's 'all my sessions' adapter)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from state_aggregator.aggregator import Aggregator
from state_aggregator.sources import SessionsSource
from state_aggregator.types import FleetState


def _write(p, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _build_dirs(tmp_path):
    # Claude project session
    claude = tmp_path / "claude_projects"
    _write(claude / "-Users-x-code-foo" / "abc-123.jsonl",
           json.dumps({"cwd": "/Users/x/code/foo", "type": "user"}) + "\n")
    # auto_runtime captain track
    autorun = tmp_path / "autonomy"
    _write(autorun / "trk-deadbeef" / "objective.state.json", json.dumps({
        "track_id": "trk-deadbeef", "task": "build the thing",
        "cwd": "/Users/x/code/bar", "state": "RUNNING",
        "updated_at": "2026-06-02T10:00:00+00:00",
    }))
    # Codex index
    codex = tmp_path / "session_index.jsonl"
    _write(codex, json.dumps({
        "id": "cdx-1", "thread_name": "refactor auth",
        "updated_at": "2026-06-01T09:00:00+00:00",
    }) + "\n")
    return claude, autorun, codex


def test_sessions_source_unifies_all_runtimes(tmp_path):
    claude, autorun, codex = _build_dirs(tmp_path)
    src = SessionsSource(claude_projects=claude, autoruntime_root=autorun, codex_index=codex)
    out = src.fetch()["sessions"]
    by_source = {s["source"] for s in out}
    assert by_source == {"claude", "auto-runtime", "codex"}, by_source

    track = next(s for s in out if s["source"] == "auto-runtime")
    assert track["id"] == "trk-deadbeef"
    assert track["title"] == "build the thing"
    assert track["cwd"] == "/Users/x/code/bar"
    assert track["status"] == "RUNNING"

    claude_s = next(s for s in out if s["source"] == "claude")
    assert claude_s["id"] == "abc-123"
    assert claude_s["cwd"] == "/Users/x/code/foo"  # recovered from first jsonl line


def test_sessions_sorted_recent_first_and_capped(tmp_path):
    claude, autorun, codex = _build_dirs(tmp_path)
    src = SessionsSource(claude_projects=claude, autoruntime_root=autorun,
                         codex_index=codex, last_n=2)
    out = src.fetch()["sessions"]
    assert len(out) == 2  # capped
    ts = [s["updated_at"] for s in out]
    assert ts == sorted(ts, reverse=True)  # most-recent first


def test_missing_sources_are_silent(tmp_path):
    src = SessionsSource(
        claude_projects=tmp_path / "nope",
        autoruntime_root=tmp_path / "nope2",
        codex_index=tmp_path / "nope.jsonl",
    )
    assert src.fetch() == {"sessions": []}


def test_aggregator_includes_sessions_in_snapshot(tmp_path):
    claude, autorun, codex = _build_dirs(tmp_path)
    agg = Aggregator(sources=[
        SessionsSource(claude_projects=claude, autoruntime_root=autorun, codex_index=codex),
    ])
    snap = agg.snapshot()
    assert isinstance(snap, FleetState)
    assert snap.summary["session_count"] == 3
    assert snap.summary["sessions_by_source"] == {"claude": 1, "auto-runtime": 1, "codex": 1}
    assert {s.source for s in snap.sessions} == {"claude", "auto-runtime", "codex"}

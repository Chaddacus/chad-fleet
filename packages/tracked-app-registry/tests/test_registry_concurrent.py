"""Sequential-write integrity tests.

NOTE: True concurrent writes (multiple processes/threads hitting the same JSONL
simultaneously) are out of scope for this module — the append pattern uses O_APPEND
which is atomic per-write at the OS level, but cross-process interlocking requires
advisory locking (e.g. fcntl.flock) which is not implemented here.
These tests verify that sequential appends from the same process leave the log intact.
"""

from __future__ import annotations

from tracked_app_registry import Registry

from .conftest import make_app


def test_sequential_appends_leave_log_intact(reg: Registry) -> None:
    """Multiple sequential writes must not corrupt the JSONL event log."""
    for i in range(10):
        reg.create(make_app(f"app-{i}", name=f"App {i}"))

    evts = reg.events()
    assert len(evts) == 10
    # Each event should be parseable (Event.model_validate succeeded in events())
    for evt in evts:
        assert evt.type == "app.created"


def test_interleaved_creates_and_updates(reg: Registry) -> None:
    """Create and update interleaved in one process; all events recorded."""
    reg.create(make_app("interleave-a"))
    reg.create(make_app("interleave-b"))
    reg.update("interleave-a", name="A Updated")
    reg.set_state("interleave-b", "paused")
    reg.archive("interleave-a")

    all_evts = reg.events()
    assert len(all_evts) == 5
    types = [e.type for e in all_evts]
    assert types.count("app.created") == 2
    assert types.count("app.updated") == 1
    assert types.count("app.state_changed") == 1
    assert types.count("app.archived") == 1

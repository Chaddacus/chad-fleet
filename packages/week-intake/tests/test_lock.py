"""WeekFolder.lock() tests — flock-based mutual exclusion."""

from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path

from week_intake.protocol import WeekFolder
from week_intake.types import WeekItem


def _hold_lock_for(base_path: str, week: str, hold_seconds: float, ready_pipe) -> None:
    """Worker process: acquire the lock, signal readiness, hold for N seconds."""
    folder = WeekFolder(week=week, base=Path(base_path))
    with folder.lock():
        ready_pipe.send("locked")
        time.sleep(hold_seconds)
        ready_pipe.send("released")
    ready_pipe.close()


def test_lock_blocks_concurrent_acquisition(tmp_path) -> None:
    """A second acquirer must wait until the first releases."""
    parent_conn, child_conn = mp.Pipe()
    proc = mp.Process(
        target=_hold_lock_for,
        args=(str(tmp_path), "2026-W19", 0.5, child_conn),
    )
    proc.start()

    # Wait for the worker to confirm it has the lock.
    msg = parent_conn.recv()
    assert msg == "locked"

    folder = WeekFolder(week="2026-W19", base=tmp_path)
    t0 = time.monotonic()
    with folder.lock():
        # We could only acquire after the worker released.
        elapsed = time.monotonic() - t0

    assert elapsed >= 0.4, f"second acquirer didn't wait (elapsed={elapsed:.3f}s)"
    proc.join(timeout=2)
    assert proc.exitcode == 0


def test_lock_serializes_appends(tmp_path) -> None:
    """Holding the lock guarantees no partial-write races for the items file."""
    folder = WeekFolder(week="2026-W19", base=tmp_path)
    with folder.lock():
        folder.append_item(WeekItem(item_id="wk-001", week="2026-W19", raw_text="a"))
        folder.append_item(WeekItem(item_id="wk-002", week="2026-W19", raw_text="b"))
    items = folder.list_items()
    assert {it.item_id for it in items} == {"wk-001", "wk-002"}

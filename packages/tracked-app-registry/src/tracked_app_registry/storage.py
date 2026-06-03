"""Atomic file operations and JSONL event log helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp-file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_")
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_JSONL_ATOMIC_WRITE_LIMIT = 4096
"""POSIX PIPE_BUF guarantees writes ≤ this size are atomic in O_APPEND mode.

Lines larger than this need exclusive flock during the write to prevent
interleaved partial-line corruption. Twin's tail reader buffers until
newline so a partial line is never advanced past, but a writer racing
on a large line can still produce torn output without the flock.
"""


def append_jsonl(path: Path, record: dict) -> None:
    """Append one JSON record to a JSONL file (PR6 R3#6: atomic append).

    Uses ``os.open(O_APPEND)`` + single ``os.write`` per encoded line so
    the write is one syscall, atomic up to PIPE_BUF (~4KB) on POSIX. For
    larger lines an exclusive ``fcntl.flock`` serializes writers so a
    concurrent reader (Twin tail loop) never sees interleaved bytes.
    Caller responsibility (per FLEET_PROCESS v6 §R3#6): diff snippets
    > 4KB belong in a referenced file, not in the log JSON.
    """
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, default=str) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(path, flags, 0o644)
    try:
        if len(payload) > _JSONL_ATOMIC_WRITE_LIMIT:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                os.write(fd, payload)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        else:
            # Single write under PIPE_BUF — POSIX guarantees atomicity in
            # O_APPEND mode without needing a lock.
            os.write(fd, payload)
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> list[dict]:
    """Read all records from a JSONL file; skip blank/corrupt lines."""
    if not path.exists():
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def read_json(path: Path) -> dict:
    """Read a JSON file; return empty dict if missing or corrupt."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

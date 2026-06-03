"""Tests for the PR6 R3#6 safe JSONL append helper.

Validates that:
- Single-record append produces a parseable line
- Concurrent appenders never produce a torn line that breaks parsers
- Records larger than PIPE_BUF still serialize cleanly via flock
"""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from tracked_app_registry.storage import (
    _JSONL_ATOMIC_WRITE_LIMIT,
    append_jsonl,
    read_jsonl,
)


def test_single_append_produces_parseable_line(tmp_path: Path) -> None:
    p = tmp_path / "log.jsonl"
    append_jsonl(p, {"k": "v", "n": 1})
    records = read_jsonl(p)
    assert records == [{"k": "v", "n": 1}]


def test_sequential_appends_in_order(tmp_path: Path) -> None:
    p = tmp_path / "log.jsonl"
    for i in range(20):
        append_jsonl(p, {"i": i})
    records = read_jsonl(p)
    assert [r["i"] for r in records] == list(range(20))


def test_large_record_still_atomic_via_flock(tmp_path: Path) -> None:
    """Records > PIPE_BUF take the flock path; verify they still produce
    one parseable line (no truncation, no interleaving with itself)."""
    p = tmp_path / "log.jsonl"
    big_payload = "x" * (_JSONL_ATOMIC_WRITE_LIMIT * 2)
    append_jsonl(p, {"big": big_payload})
    records = read_jsonl(p)
    assert len(records) == 1
    assert records[0]["big"] == big_payload


def _appender(path_str: str, base: int, count: int) -> None:
    """Worker for the concurrency test — append `count` records starting at
    `base` to allow checking which records survived."""
    p = Path(path_str)
    for i in range(count):
        append_jsonl(p, {"src": base, "seq": i, "filler": "x" * 100})


def test_concurrent_appenders_produce_no_torn_lines(tmp_path: Path) -> None:
    """Spawn N processes, each appending M records. Every line must parse;
    total record count must equal N*M; no record may have a missing field
    (a torn line would survive read_jsonl as a valid JSON object only if
    the partial bytes happened to be valid JSON, which is exceedingly
    unlikely — but read_jsonl drops unparseable lines silently, so we
    additionally count lines and assert the count matches expectation)."""
    p = tmp_path / "log.jsonl"
    n_procs = 4
    per_proc = 50

    procs = [
        multiprocessing.Process(target=_appender, args=(str(p), i, per_proc))
        for i in range(n_procs)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join()

    # Count raw lines in the file — proves no writer's data was lost or
    # interleaved into a partial-line state that read_jsonl skipped.
    with open(p, encoding="utf-8") as fh:
        raw_lines = fh.read().splitlines()
    assert len(raw_lines) == n_procs * per_proc, (
        f"expected {n_procs * per_proc} lines, got {len(raw_lines)} — "
        f"likely torn writes"
    )
    # Every line parses cleanly.
    parsed = [json.loads(line) for line in raw_lines]
    assert len(parsed) == n_procs * per_proc
    # Every (src, seq) tuple is unique — no record was duplicated by the
    # interleaving of writers.
    pairs = {(r["src"], r["seq"]) for r in parsed}
    assert len(pairs) == n_procs * per_proc

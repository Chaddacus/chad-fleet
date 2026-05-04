"""Filesystem protocol for week-intake.

Per-week storage layout (under ``~/.chad/week/<iso-week>/``):

    items.jsonl          one WeekItem per line, append-only
    items.index.json     {item_id: line_offset, ...}  optional, regenerable
    drivers.log          chad-twin/chad-agent decision log (append-only)

ISO week is Monday-anchored, ``YYYY-Www`` (e.g. ``2026-W18``).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from tracked_app_registry.storage import append_jsonl, atomic_write

from week_intake.types import WeekItem

DEFAULT_WEEK_BASE = Path.home() / ".chad" / "week"


def week_base() -> Path:
    """Root of all per-week dirs. Override with CHAD_WEEK_DIR."""
    raw = os.environ.get("CHAD_WEEK_DIR")
    return Path(raw).expanduser() if raw else DEFAULT_WEEK_BASE


def iso_week_for(d: date | None = None) -> str:
    """Return Monday-anchored ISO-week tag like '2026-W18' for the given date."""
    d = d or datetime.now(timezone.utc).date()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def parse_iso_week(tag: str) -> date:
    """Inverse of iso_week_for: tag → Monday of that ISO week."""
    if "-W" not in tag:
        raise ValueError(f"not an ISO-week tag: {tag!r}")
    year_str, week_str = tag.split("-W", 1)
    return date.fromisocalendar(int(year_str), int(week_str), 1)


class WeekFolder:
    """Filesystem paths + helpers for one ISO week's intake folder."""

    def __init__(self, week: str | None = None, base: Path | None = None) -> None:
        self.week = week or iso_week_for()
        # Validate; raises ValueError if malformed
        parse_iso_week(self.week)
        self.root = (base or week_base()) / self.week

    @property
    def items_path(self) -> Path:
        return self.root / "items.jsonl"

    @property
    def drivers_log_path(self) -> Path:
        return self.root / "drivers.log"

    @property
    def lock_path(self) -> Path:
        """Per-week lock file used to serialize allocate-and-append windows.

        Tested with fcntl.flock; held only briefly across an atomic ID
        allocation followed by an append.
        """
        return self.root / ".items.lock"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Hold an exclusive flock on this week's lock file.

        Used by the parser/intake path to make ``next_item_id`` →
        ``append_items`` atomic against concurrent intakes. Posix-only
        (fcntl); good enough for single-machine local use.
        """
        self.ensure()
        # Open in append mode so the file is created if missing without
        # truncating. Lock is released when the fd closes.
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    # ---- item I/O ---------------------------------------------------------

    def append_item(self, item: WeekItem) -> None:
        self.ensure()
        append_jsonl(self.items_path, item.model_dump(mode="json"))

    def append_items(self, items: list[WeekItem]) -> None:
        self.ensure()
        for it in items:
            append_jsonl(self.items_path, it.model_dump(mode="json"))

    def list_items(self) -> list[WeekItem]:
        """Read items, tolerating corrupt lines (skip them). UX/read-only path.

        Used by `chad-week list` and `chad-week status` where best-effort
        rendering is preferable to a hard failure.
        """
        return self._list_items_impl(strict=False)

    def list_items_strict(self) -> list[WeekItem]:
        """Read items, raising ``ValueError`` on any corrupt line.

        Used by ``upsert_item``: a full-file rewrite that silently skipped
        corrupt lines would permanently lose them. Fail closed instead.
        """
        return self._list_items_impl(strict=True)

    def _list_items_impl(self, *, strict: bool) -> list[WeekItem]:
        if not self.items_path.exists():
            return []
        latest: dict[str, WeekItem] = {}
        with self.items_path.open("r", encoding="utf-8") as fp:
            for line_no, raw in enumerate(fp, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    it = WeekItem.model_validate(json.loads(raw))
                except (json.JSONDecodeError, Exception) as e:
                    if strict:
                        raise ValueError(
                            f"corrupt line {line_no} in {self.items_path}: {e}"
                        ) from e
                    continue
                latest[it.item_id] = it
        return list(latest.values())

    def get_item(self, item_id: str) -> WeekItem | None:
        for it in self.list_items():
            if it.item_id == item_id:
                return it
        return None

    def upsert_item(self, item: WeekItem) -> None:
        """Replace ``item_id`` (or append if new) via atomic full-file rewrite.

        Reads existing items via ``list_items_strict`` to refuse silently
        dropping any corrupt rows. Writes a fresh JSONL via
        ``atomic_write`` so a partial write can never leave the file in a
        torn state. Callers should hold ``WeekFolder.lock()`` when racing
        against other writers.
        """
        item.touch()
        items = self.list_items_strict()
        replaced = False
        for i, existing in enumerate(items):
            if existing.item_id == item.item_id:
                items[i] = item
                replaced = True
                break
        if not replaced:
            items.append(item)
        self.ensure()
        payload = "\n".join(
            json.dumps(it.model_dump(mode="json"), default=str) for it in items
        )
        if payload:
            payload += "\n"
        atomic_write(self.items_path, payload)

    # ---- driver log -------------------------------------------------------

    def log_driver(self, message: str) -> None:
        """Append one line to drivers.log with an ISO timestamp prefix.

        Uses POSIX append-mode write (O_APPEND), so concurrent writers
        from different processes don't clobber each other's lines —
        unlike a read-modify-write that would lose entries under
        contention. Callers don't need to hold ``WeekFolder.lock()``
        for line atomicity, but they may want to for ordering.
        """
        self.ensure()
        ts = datetime.now(timezone.utc).isoformat()
        line = f"{ts}\t{message}\n"
        with open(self.drivers_log_path, "a", encoding="utf-8") as fp:
            fp.write(line)
            fp.flush()


def next_item_id(folder: WeekFolder) -> str:
    """Allocate the next 'wk-NNN' id for the given week, scanning existing items."""
    items = folder.list_items()
    max_n = 0
    for it in items:
        if it.item_id.startswith("wk-"):
            try:
                max_n = max(max_n, int(it.item_id.split("-", 1)[1]))
            except ValueError:
                continue
    return f"wk-{max_n + 1:03d}"


__all__ = [
    "DEFAULT_WEEK_BASE",
    "WeekFolder",
    "iso_week_for",
    "next_item_id",
    "parse_iso_week",
    "week_base",
]

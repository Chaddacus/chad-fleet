"""Notifier-inbox source."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..types import InboxItem

_DEFAULT_INBOX_PATH = Path.home() / ".chad" / "notifier" / "inbox.jsonl"


class InboxSource:
    """Reads notification inbox from a JSONL file."""

    name = "notifier-inbox"

    def __init__(self, inbox_path: Path | None = None, last_n: int = 50) -> None:
        self._inbox_path = inbox_path
        self._last_n = last_n

    def _resolve_path(self) -> Path:
        if self._inbox_path is not None:
            return self._inbox_path
        env = os.environ.get("CHAD_NOTIFIER_INBOX_PATH")
        if env:
            return Path(env)
        return _DEFAULT_INBOX_PATH

    def fetch(self) -> dict:
        """Returns {"items": [InboxItem...]}."""
        path = self._resolve_path()
        if not path.exists():
            return {"items": []}

        lines = []
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return {"items": []}

        # Take last N non-empty lines
        raw_lines = [l.strip() for l in lines if l.strip()]
        tail = raw_lines[-self._last_n :]

        items = []
        for line in tail:
            try:
                record = json.loads(line)
                item = InboxItem.model_validate(record)
                items.append(item.model_dump(mode="json"))
            except (json.JSONDecodeError, Exception):
                pass

        return {"items": items}

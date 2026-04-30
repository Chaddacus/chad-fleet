"""DashboardInboxAdapter — appends notifications as JSONL to a local inbox file."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from notifier_hub_core.models import Notification, SendResult

_DEFAULT_INBOX = Path.home() / ".chad" / "notifier" / "inbox.jsonl"


class DashboardInboxAdapter:
    name = "dashboard-inbox"

    def __init__(self, inbox_path: Path | None = None) -> None:
        env_path = os.environ.get("CHAD_NOTIFIER_INBOX_PATH")
        if inbox_path is not None:
            self._inbox_path = inbox_path
        elif env_path:
            self._inbox_path = Path(env_path)
        else:
            self._inbox_path = _DEFAULT_INBOX

    def send(self, notification: Notification) -> SendResult:
        try:
            self._inbox_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "channel": notification.channel,
                "severity": notification.severity,
                "title": notification.title,
                "body": notification.body,
                "actions": [a.model_dump() for a in notification.actions],
            }
            line = json.dumps(record) + "\n"
            with open(self._inbox_path, "a") as fh:
                fh.write(line)
                fh.flush()
            return SendResult(adapter="dashboard-inbox", ok=True)
        except Exception as e:
            return SendResult(
                adapter="dashboard-inbox",
                ok=False,
                detail=f"{type(e).__name__}: {e}",
            )

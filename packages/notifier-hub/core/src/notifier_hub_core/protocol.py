"""NotifierAdapter protocol definition."""

from __future__ import annotations

from typing import Protocol

from notifier_hub_core.models import Notification, SendResult


class NotifierAdapter(Protocol):
    name: str

    def send(self, notification: Notification) -> SendResult:
        ...

"""NtfyAdapter — pushes notifications to an ntfy.sh topic via HTTP POST."""

from __future__ import annotations

import os

import httpx

from notifier_hub_core.models import Notification, SendResult

_DEFAULT_SERVER = "https://ntfy.sh"
_PRIORITY_MAP = {"info": "3", "warn": "4", "critical": "5"}


class NtfyAdapter:
    name = "ntfy"

    def __init__(
        self,
        topic: str | None = None,
        server: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        self._topic = topic or os.environ.get("CHAD_NTFY_TOPIC")
        self._server = (server or os.environ.get("CHAD_NTFY_SERVER") or _DEFAULT_SERVER).rstrip("/")
        self._auth_token = auth_token or os.environ.get("CHAD_NTFY_AUTH_TOKEN")

    def send(self, notification: Notification) -> SendResult:
        if not self._topic:
            return SendResult(
                adapter="ntfy",
                ok=False,
                detail="No topic configured; set CHAD_NTFY_TOPIC or pass topic=",
            )
        url = f"{self._server}/{self._topic}"
        headers: dict[str, str] = {
            "Title": notification.title,
            "Priority": _PRIORITY_MAP.get(notification.severity, "3"),
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        try:
            response = httpx.post(url, content=notification.body.encode(), headers=headers, timeout=10)
            if response.is_success:
                return SendResult(adapter="ntfy", ok=True)
            return SendResult(
                adapter="ntfy",
                ok=False,
                detail=f"HTTP {response.status_code}",
            )
        except httpx.RequestError as e:
            return SendResult(
                adapter="ntfy",
                ok=False,
                detail=f"{type(e).__name__}: {e}",
            )

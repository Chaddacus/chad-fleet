"""NotifierHub: registers adapters and routes notifications."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from notifier_hub_core.config import RoutingConfig, load_config
from notifier_hub_core.models import Notification, SendResult
from notifier_hub_core.protocol import NotifierAdapter


class NotifierHub:
    def __init__(self, config_path: Path | None = None) -> None:
        self._config: RoutingConfig = load_config(config_path)
        self._adapters: dict[str, NotifierAdapter] = {}

    def register(self, adapter: NotifierAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def _resolve_adapter_names(self, channel: str) -> list[str]:
        """Return the list of adapter names for *channel* per routing config."""
        for route in self._config.routes:
            if fnmatch.fnmatch(channel, route.channel):
                return route.adapters
        return self._config.fallback_adapters

    def send(self, notification: Notification) -> list[SendResult]:
        adapter_names = self._resolve_adapter_names(notification.channel)
        results: list[SendResult] = []
        for name in adapter_names:
            adapter = self._adapters.get(name)
            if adapter is None:
                results.append(
                    SendResult(
                        adapter=name,
                        ok=False,
                        detail=f"adapter '{name}' not registered",
                    )
                )
                continue
            try:
                result = adapter.send(notification)
            except Exception as exc:  # noqa: BLE001
                result = SendResult(
                    adapter=name,
                    ok=False,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)
        return results

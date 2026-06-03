"""Tracked-app registry source."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_REGISTRY_BASE = Path.home() / ".chad" / "fleet" / "registry"


class RegistrySource:
    """Reads tracked apps from the tracked-app-registry package."""

    name = "tracked-app-registry"

    def __init__(self, registry_dir: Path | None = None) -> None:
        self._registry_dir = registry_dir

    def fetch(self) -> dict:
        """Returns {"apps": [TrackedApp.model_dump()...]}."""
        from tracked_app_registry import Registry

        kwargs: dict = {}
        if self._registry_dir is not None:
            kwargs["view_path"] = self._registry_dir / "apps.json"
            kwargs["events_path"] = self._registry_dir / "events.jsonl"
            registry = Registry(**kwargs)
        else:
            env_dir = os.environ.get("CHAD_FLEET_REGISTRY_DIR")
            if env_dir:
                d = Path(env_dir)
                registry = Registry(
                    view_path=d / "apps.json",
                    events_path=d / "events.jsonl",
                )
            else:
                registry = Registry()

        apps = registry.list()
        return {"apps": [a.model_dump(mode="json") for a in apps]}

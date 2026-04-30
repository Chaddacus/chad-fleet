"""Load and validate routing configuration from YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path("~/.chad/notifier/routes.yml").expanduser()


class ConfigNotFoundError(FileNotFoundError):
    """Raised when the routes config file cannot be found."""


@dataclass
class RouteEntry:
    channel: str
    adapters: list[str]


@dataclass
class RoutingConfig:
    routes: list[RouteEntry] = field(default_factory=list)
    fallback_adapters: list[str] = field(default_factory=list)


def load_config(config_path: Path | None = None) -> RoutingConfig:
    """Load routing config from *config_path*, env var, or default location."""
    if config_path is None:
        env_path = os.environ.get("CHAD_NOTIFIER_CONFIG")
        config_path = Path(env_path) if env_path else _DEFAULT_CONFIG_PATH

    if not config_path.is_file():
        raise ConfigNotFoundError(
            f"notifier routes config not found: {config_path}"
        )

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    routes: list[RouteEntry] = []
    for entry in raw.get("routes") or []:
        channel = entry.get("channel", "")
        adapters = entry.get("adapters") or []
        if channel:
            routes.append(RouteEntry(channel=channel, adapters=list(adapters)))

    fallback = list(raw.get("fallback_adapters") or [])
    return RoutingConfig(routes=routes, fallback_adapters=fallback)

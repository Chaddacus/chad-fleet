"""Captain app registry — who's tracked, what mode, where the repo is.

Each registered app is a ``RegisteredApp`` with:
    app_id          stable identifier (also workspace dir name)
    name            human-friendly label
    repo_path       local repo path (or manuscript dir for non-code apps)
    mode            "autonomous"   — captain dispatches goose-runner
                    "observe_only" — captain only runs scorecard + replanner;
                                      no goose-runner (admiral drives changes)
    schedule_hour   0-23 in MARKETING_TZ; the launchd plist will fire at this hour
    notes           free-form context

The registry is loaded from ``apps_registry.json`` at the captain root
(``~/.chad/captain/apps_registry.json`` by default), or from the env var
``CHAD_CAPTAIN_APPS_REGISTRY``. Adding a new app is one JSON edit + a
``chad-captain register`` invocation.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path.home() / ".chad" / "captain" / "apps_registry.json"

AppMode = Literal["autonomous", "observe_only"]


class RegisteredApp(BaseModel):
    app_id: str
    name: str
    repo_path: str
    mode: AppMode = "observe_only"
    schedule_hour: int = 9
    schedule_tz: str = "America/New_York"
    notes: str = ""


class AppsRegistry(BaseModel):
    apps: list[RegisteredApp] = Field(default_factory=list)

    def by_id(self, app_id: str) -> RegisteredApp | None:
        return next((a for a in self.apps if a.app_id == app_id), None)

    def upsert(self, app: RegisteredApp) -> None:
        for i, existing in enumerate(self.apps):
            if existing.app_id == app.app_id:
                self.apps[i] = app
                return
        self.apps.append(app)


def registry_path() -> Path:
    raw = os.environ.get("CHAD_CAPTAIN_APPS_REGISTRY", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_REGISTRY_PATH


def load_registry() -> AppsRegistry:
    path = registry_path()
    if not path.exists():
        return AppsRegistry()
    try:
        return AppsRegistry.model_validate_json(path.read_text())
    except Exception as e:
        logger.warning("registry parse failed: %s; returning empty", e)
        return AppsRegistry()


def save_registry(reg: AppsRegistry) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reg.model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# Default seeds (Spark + author-toolkit)
# ---------------------------------------------------------------------------


SPARK_DEFAULT = RegisteredApp(
    app_id="spark-of-defiance",
    name="Spark of Defiance",
    repo_path=str(Path.home() / "code" / "personal" / "spark_of_defiance"),
    mode="observe_only",  # manuscript work — admiral drives, captain scores
    schedule_hour=9,
    notes="YA progression-fantasy novel. Captain runs daily scorecard + admiral-note channel; no goose dispatch.",
)

AUTHOR_TOOLKIT_DEFAULT = RegisteredApp(
    app_id="author-toolkit",
    name="Author Toolkit",
    repo_path=str(Path.home() / "code" / "personal" / "author_toolkit_fantasy_agent_auto"),
    mode="autonomous",
    schedule_hour=10,
    notes="TypeScript author tooling. Captain dispatches goose for slice work.",
)

DEFAULT_SEEDS = (SPARK_DEFAULT, AUTHOR_TOOLKIT_DEFAULT)


def seed_default_registry(*, force: bool = False) -> AppsRegistry:
    """Write a registry containing the default Spark + author-toolkit seeds."""
    if registry_path().exists() and not force:
        return load_registry()
    reg = AppsRegistry(apps=list(DEFAULT_SEEDS))
    save_registry(reg)
    return reg


__all__ = [
    "AUTHOR_TOOLKIT_DEFAULT",
    "AppMode",
    "AppsRegistry",
    "DEFAULT_REGISTRY_PATH",
    "DEFAULT_SEEDS",
    "RegisteredApp",
    "SPARK_DEFAULT",
    "load_registry",
    "registry_path",
    "save_registry",
    "seed_default_registry",
]

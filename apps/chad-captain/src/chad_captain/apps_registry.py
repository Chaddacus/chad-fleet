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

    # Optional shell command run inside repo_path after a slice's goose run,
    # BEFORE the captain issues a verdict. Non-zero exit downgrades the verdict
    # to reject_retry — protects main from "captain landed a slice that broke
    # the build." Examples: "make check", "npm test", "uv run pytest -q".
    # When None/empty, the verify gate is skipped (back-compat default).
    verify_cmd: str | None = None
    # Wall-clock cap for verify_cmd (seconds). Slow CI shouldn't deadlock
    # the captain tick; if verify times out, treat as reject_retry.
    verify_timeout_seconds: int = 300

    # --- Integration model: captain branch + auto-PR (C2) ---
    # The branch goose-runner commits land on. Captain assumes this branch is
    # checked out before tick (admiral creates it from main once per app, then
    # captain owns it for the lifetime of the roadmap-merge cycle).
    captain_branch: str | None = None
    # PR base branch when auto-opening a PR on roadmap_complete.
    pr_base_branch: str = "main"
    # On accept verdict, push the captain branch to origin. Cheap idempotent op.
    auto_push: bool = False
    # On roadmap_complete (all slices done/skipped), push + open a draft PR
    # via `gh pr create`.
    auto_open_pr: bool = False
    # Captain self-merges the PR after open when safety gates pass:
    #   - verify_cmd already passed for every accepted slice (transitively)
    #   - aggregate scorecard delta from branch baseline is ≥ 0 (no regression)
    #   - gh pr merge succeeds (branch protection / conflicts / required checks)
    # On any gate failure, the captain logs an escalation and leaves the PR
    # open for admiral. Default False for back-compat; flip on for true
    # autonomy. Requires auto_open_pr=True (no PR → nothing to merge).
    auto_merge: bool = False
    # Merge strategy passed to `gh pr merge`. Squash keeps main history
    # one-commit-per-PR (recommended for captain branches that contain
    # one captain-runner commit per slice).
    auto_merge_method: Literal["squash", "merge", "rebase"] = "squash"
    # Minimum scorecard aggregate delta (post - pre) required to auto-merge.
    # 0.0 = no regression allowed; negative values relax it. Express as
    # raw aggregate fraction (not pp) — e.g. 0.0 means after >= before.
    auto_merge_min_delta: float = 0.0

    # --- C8 circuit breaker ---
    # When N consecutive validate entries are bad verdicts (reject_hard,
    # revert, or escalate), pause dispatch for circuit_breaker_pause_minutes
    # so the captain doesn't churn forever on a stuck app. Admiral can
    # `chad-captain unpause --app <id>` to clear the pause manually.
    circuit_breaker_threshold: int = 3
    circuit_breaker_pause_minutes: int = 60

    # --- C12 low-yield streak detector ---
    # When N consecutive validates are soft_accept with abs(delta_pp) below
    # the noise floor, pause + escalate. Catches rubric saturation (every
    # dim pinned at 1.0) AND captain spinning on cosmetic slices the
    # rubric isn't measuring. Admiral can extend the rubric, then unpause.
    low_yield_streak_threshold: int = 5
    low_yield_pause_minutes: int = 30

    # --- Cycle C: pluggable validator chain ---
    # Dotted import path for a module exporting `validate_app_completion`
    # (signature documented at chad_captain.validator.validate_app_completion).
    # When set, captain_tick uses this in place of the default validate +
    # reuse_regression + verify_gate chain. Custom validators own the entire
    # chain; their verdict is final (no post-hoc override).
    #
    # Failure modes are FAIL-CLOSED:
    #   - module fails to import → escalate
    #   - module lacks `validate_app_completion` → escalate
    #   - validator raises → escalate
    #   - dispatched-slice snapshot missing → escalate
    # Default (None) keeps the existing default chain.
    validator_module: str | None = None


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

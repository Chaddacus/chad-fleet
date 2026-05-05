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

import contextlib
import fcntl
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path.home() / ".chad" / "captain" / "apps_registry.json"
DEFAULT_REGISTRY_LOCK_PATH = Path.home() / ".chad" / "captain" / ".apps_registry.lock"

AppMode = Literal["autonomous", "observe_only"]


class RegisteredApp(BaseModel):
    app_id: str
    name: str
    repo_path: str
    mode: AppMode = "observe_only"

    # Cycle G: normalize repo_path on construction. Registries written by
    # admiral via CLI often contain `~/code/...` which the API's bare
    # `Path(entry.repo_path).exists()` checks (api.py:236, 306) treat as
    # a literal `~` directory and fail. Goose-runner only does `.resolve()`,
    # not `.expanduser()`, so symbolic home references resolved to the wrong
    # place. Normalize once here so every consumer sees an absolute path.
    @field_validator("repo_path")
    @classmethod
    def _normalize_repo_path(cls, v: str) -> str:
        if not v:
            return v
        return str(Path(v).expanduser())
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

    # --- Cycle D: auto-replan policy ---
    # When True (default), the daemon and API ticks pass `auto_replan=True`
    # to captain_tick so the captain replans automatically when the roadmap
    # is drained or absent. When False, captain only dispatches what's
    # already on the roadmap and reports "no roadmap" / "roadmap exhausted"
    # back to the caller — admiral controls when replan happens.
    #
    # Default True keeps the pre-Cycle-D daemon behavior. T1 (Spark) and
    # other manuscript-style apps should opt out (False) so the captain
    # doesn't auto-generate slices the admiral hasn't approved.
    auto_replan: bool = True

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

    # --- PR6/v8 R5#2: scaffold staging via enabled flag ---
    # When False, the daemon and CLI tick paths skip this captain entirely.
    # Set to False during scaffold transaction phase 4 (REGISTER) so a
    # captain that fails phase 5 (ACTIVATE) doesn't tick with broken state.
    # Flipped to True after activation succeeds. Existing captains are
    # enabled=True by default for back-compat.
    enabled: bool = True

    @model_validator(mode="after")
    def _verify_cmd_required_for_auto_merge(self) -> "RegisteredApp":
        """PR7 R3#7: auto_merge without verify_cmd is unsafe.

        If captain auto-merges PRs to main with no per-slice build/test
        gate, a green-looking captain can land breakage straight to main.
        Reject the registry entry rather than discovering this at merge
        time. Back-compat: apps with auto_merge=False (the default) are
        unaffected — they can still leave verify_cmd unset.
        """
        if self.auto_merge and not (self.verify_cmd and self.verify_cmd.strip()):
            raise ValueError(
                f"app {self.app_id!r} has auto_merge=True but verify_cmd is "
                f"unset; refusing to auto-merge with no per-slice build gate"
            )
        return self


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


def registry_lock_path() -> Path:
    """Path to the registry advisory lock file (sibling of registry_path).

    Lock file is separate from registry_path so locking semantics don't
    interact with the JSON file's existence/parsing.
    """
    raw = os.environ.get("CHAD_CAPTAIN_APPS_REGISTRY_LOCK", "").strip()
    if raw:
        return Path(raw).expanduser()
    rp = registry_path()
    return rp.parent / f".{rp.name}.lock"


@contextlib.contextmanager
def _locked_fd(lock_path: Path, *, exclusive: bool) -> Iterator[int]:
    """Hold an fcntl.flock on lock_path for the duration of the with-block.

    Exclusive lock blocks all other readers and writers; shared lock blocks
    only writers. Lock file is auto-created on first use.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def load_registry(*, shared: bool = True) -> AppsRegistry:
    """Load the registry under a shared (default) or exclusive lock.

    Use shared=True for read-mostly callers (daemon tick, status). Use
    shared=False when the caller will mutate-then-save and wants
    read-modify-write semantics; combine with save_registry to avoid
    losing concurrent updates.

    PR6 R3#1 fix: prior load_registry() did bare read_text() with no lock,
    so a writer mid-flush could produce torn reads. Failures now propagate
    instead of being swallowed as empty registry — silently empty registry
    is worse than a parse error visible at the call site.
    """
    path = registry_path()
    if not path.exists():
        return AppsRegistry()
    with _locked_fd(registry_lock_path(), exclusive=not shared):
        return AppsRegistry.model_validate_json(path.read_text())


def save_registry(reg: AppsRegistry) -> None:
    """Atomically save the registry under an exclusive lock.

    Tempfile + os.replace gives torn-read protection for any reader using
    load_registry; the flock ensures concurrent writers serialize and don't
    lose updates.
    """
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = reg.model_dump_json(indent=2)
    with _locked_fd(registry_lock_path(), exclusive=True):
        # NamedTemporaryFile in same dir guarantees os.replace is same-fs
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)


@contextlib.contextmanager
def registry_transaction() -> Iterator[AppsRegistry]:
    """Read-modify-write the registry atomically under an exclusive lock.

    Usage:
        with registry_transaction() as reg:
            reg.upsert(app)
            # save happens automatically on context exit

    Use this instead of `reg = load_registry(); reg.upsert(...); save_registry(reg)`
    when concurrent writers may interleave (scaffold + admin CLI both writing).
    """
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked_fd(registry_lock_path(), exclusive=True):
        reg = (
            AppsRegistry.model_validate_json(path.read_text())
            if path.exists()
            else AppsRegistry()
        )
        yield reg
        payload = reg.model_dump_json(indent=2)
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Default seeds (Spark + author-toolkit)
# ---------------------------------------------------------------------------


SPARK_DEFAULT = RegisteredApp(
    app_id="spark-of-defiance",
    name="Spark of Defiance",
    repo_path=str(Path.home() / "code" / "personal" / "spark_of_defiance"),
    mode="observe_only",  # manuscript work — admiral drives, captain scores
    auto_replan=False,    # T1/PR2: admiral controls every replan explicitly
    schedule_hour=9,
    notes="YA progression-fantasy. Captain scores daily; admiral controls all replan via `chad-captain replan --app spark-of-defiance`.",
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
    "DEFAULT_REGISTRY_LOCK_PATH",
    "DEFAULT_SEEDS",
    "RegisteredApp",
    "SPARK_DEFAULT",
    "load_registry",
    "registry_lock_path",
    "registry_path",
    "registry_transaction",
    "save_registry",
    "seed_default_registry",
]

"""Single source of truth for "is this WeekItem.target routeable?".

Both ``clarifier`` (deciding whether to flip state to ``ready``) and the
``route`` CLI (deciding the route mode + whether to refuse) call
``validate_route_target``. This prevents drift between the two
adjacent-but-separate state-transition owners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from week_intake.captain_client import fleet_base
from week_intake.types import RouteTarget
from week_intake.validation import (
    ValidationError,
    validate_repo_path,
    validate_scaffold_target,
    validate_slug,
)

RouteMode = Literal["existing_app", "new_repo", "greenfield"]


@dataclass
class RouteTargetCheck:
    """Structured result of checking a target for routeability.

    ``ok=True`` means: with the current target fields, ``router.route_item``
    will succeed (modulo external-side-effect failures: captain API down,
    captain workspace permission, etc., which can't be checked without
    side effects).

    ``missing`` lists field names the user/clarifier needs to fill before
    routing can succeed. ``reason`` is human-friendly for stderr / UX.
    """

    ok: bool
    mode: RouteMode | None = None
    missing: list[str] = field(default_factory=list)
    reason: str = ""


def validate_route_target(
    target: RouteTarget,
    *,
    captain_fleet_base: Path | None = None,
) -> RouteTargetCheck:
    """Inspect a RouteTarget and report whether it's sufficient for some mode.

    Mode resolution priority: greenfield > new_repo > existing_app.
    """
    base = captain_fleet_base if captain_fleet_base is not None else fleet_base()

    # ---- greenfield mode -------------------------------------------------
    # Requires: greenfield_name (valid slug), repo_path (valid scaffold target),
    # and app_id (valid slug).
    if target.greenfield_name:
        return _check_greenfield(target)

    # ---- new_repo mode ---------------------------------------------------
    # Requires: app_id (valid slug), repo_path (existing git worktree).
    if target.repo_path:
        return _check_new_repo(target)

    # ---- existing_app mode ----------------------------------------------
    # Requires: app_id (valid slug) AND captain workspace exists.
    if target.app_id:
        return _check_existing_app(target, base)

    return RouteTargetCheck(
        ok=False,
        mode=None,
        missing=["app_id"],
        reason="no app slug, repo path, or greenfield name set",
    )


def _check_greenfield(target: RouteTarget) -> RouteTargetCheck:
    missing: list[str] = []
    reasons: list[str] = []

    if not target.app_id:
        missing.append("app_id")
        reasons.append("greenfield route needs --app slug")
    else:
        try:
            validate_slug(target.app_id, field="app_id")
        except ValidationError as e:
            missing.append("app_id")
            reasons.append(str(e))

    try:
        validate_slug(target.greenfield_name, field="greenfield_name")
    except ValidationError as e:
        missing.append("greenfield_name")
        reasons.append(str(e))

    if not target.repo_path:
        missing.append("repo_path")
        reasons.append("greenfield needs --repo target path")
    else:
        try:
            validate_scaffold_target(target.repo_path)
        except ValidationError as e:
            missing.append("repo_path")
            reasons.append(str(e))

    if missing:
        return RouteTargetCheck(
            ok=False,
            mode="greenfield",
            missing=missing,
            reason="; ".join(reasons) or "greenfield target incomplete",
        )
    return RouteTargetCheck(ok=True, mode="greenfield")


def _check_new_repo(target: RouteTarget) -> RouteTargetCheck:
    missing: list[str] = []
    reasons: list[str] = []

    if not target.app_id:
        missing.append("app_id")
        reasons.append("new_repo route needs an app slug")
    else:
        try:
            validate_slug(target.app_id, field="app_id")
        except ValidationError as e:
            missing.append("app_id")
            reasons.append(str(e))

    try:
        validate_repo_path(target.repo_path, must_have_git=True)
    except ValidationError as e:
        missing.append("repo_path")
        reasons.append(str(e))

    if missing:
        return RouteTargetCheck(
            ok=False,
            mode="new_repo",
            missing=missing,
            reason="; ".join(reasons),
        )
    return RouteTargetCheck(ok=True, mode="new_repo")


def _check_existing_app(target: RouteTarget, base: Path) -> RouteTargetCheck:
    try:
        validate_slug(target.app_id, field="app_id")
    except ValidationError as e:
        return RouteTargetCheck(
            ok=False,
            mode="existing_app",
            missing=["app_id"],
            reason=str(e),
        )

    workspace = base / target.app_id
    if not workspace.exists():
        return RouteTargetCheck(
            ok=False,
            mode="existing_app",
            missing=["existing_workspace"],
            reason=f"app slug {target.app_id!r} has no captain workspace at {workspace}",
        )
    return RouteTargetCheck(ok=True, mode="existing_app")


__all__ = ["RouteMode", "RouteTargetCheck", "validate_route_target"]

"""Routing logic: WeekItem → admiral_note (and optional register/scaffold).

Three routing modes:

  EXISTING_APP:   ``--app <id>`` — file admiral_note in the existing
                  workspace. No registration step. The captain workspace
                  for that app MUST already exist; a typo will not silently
                  mint a fresh workspace.
  NEW_REPO:       ``--app <new_id> --repo <path>`` — register the (existing)
                  repo with captain (HTTP), then file admiral_note. The
                  ``--repo`` path must be an existing directory.
  GREENFIELD:     ``--app <new_id> --greenfield <name> --repo <path>`` —
                  scaffold the repo (refuses non-empty target), register
                  with captain, file admiral_note.

Idempotency / retry semantics:
  - Registration is idempotent: an "already registered" response from
    captain is treated as success (see ``captain_client.register_app_http``).
  - Greenfield scaffolds remove themselves on failure so the route can be
    re-run cleanly. If scaffolding succeeded but a later step failed, the
    partial scaffold is rolled back and the user can re-issue the same
    ``--greenfield`` route.
  - ``determine_mode`` is pure; ``route_item`` validates inputs BEFORE any
    side effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from week_intake.captain_client import (
    CaptainError,
    file_admiral_note,
    register_app_http,
)
from week_intake.scaffold import ScaffoldError, scaffold_greenfield
from week_intake.types import WeekItem
from week_intake.validation import (
    ValidationError,
    validate_repo_path,
    validate_scaffold_target,
    validate_slug,
)

RouteMode = Literal["existing_app", "new_repo", "greenfield"]


class RouteError(RuntimeError):
    """Routing failed (validation, registration, or note write)."""


def determine_mode(
    *,
    app_id: str | None,
    repo_path: str | None,
    greenfield_name: str | None,
) -> RouteMode:
    if greenfield_name:
        if not app_id or not repo_path:
            raise RouteError("greenfield route requires --app <slug> AND --repo <path>")
        return "greenfield"
    if repo_path:
        if not app_id:
            raise RouteError("--repo provided but no --app slug; refusing to guess")
        return "new_repo"
    if app_id:
        return "existing_app"
    raise RouteError("must supply --app, or --app + --repo, or --app + --repo + --greenfield")


def route_item(
    item: WeekItem,
    *,
    app_id: str | None,
    repo_path: str | None = None,
    greenfield_name: str | None = None,
    note_body: str | None = None,
    fleet_base: Path | None = None,
) -> WeekItem:
    """Execute the route for ``item`` and return the updated WeekItem.

    Mutates ``item.state``, ``item.target``, ``item.captain_note_id`` and
    appends a new ``updated_at``. Caller is responsible for persisting
    (e.g. ``WeekFolder.upsert_item``).

    Validation happens first; nothing is written or registered until every
    boundary check passes. On failure mid-flight, side effects are rolled
    back (greenfield scaffold removed) so retry is safe.
    """
    mode = determine_mode(
        app_id=app_id,
        repo_path=repo_path,
        greenfield_name=greenfield_name,
    )

    # ---- Pre-flight validation (no side effects yet) -------------------
    try:
        validate_slug(app_id, field="app_id")  # type: ignore[arg-type]
    except ValidationError as e:
        raise RouteError(str(e)) from e

    if mode == "new_repo":
        try:
            # New-repo route: must be an existing git worktree (not just any dir).
            # This blocks "--repo /" and "--repo $HOME" footguns.
            resolved_repo = validate_repo_path(repo_path, must_have_git=True)  # type: ignore[arg-type]
        except ValidationError as e:
            raise RouteError(str(e)) from e
    elif mode == "greenfield":
        try:
            validate_slug(greenfield_name, field="greenfield_name")  # type: ignore[arg-type]
            # Scaffold target may not exist yet, but if it exists it must be empty.
            resolved_repo = validate_scaffold_target(repo_path)  # type: ignore[arg-type]
        except ValidationError as e:
            raise RouteError(str(e)) from e
    else:
        resolved_repo = None

    body = (note_body or _default_body(item)).strip()
    if not body:
        raise RouteError("admiral note body is empty")

    # ---- Step 1: scaffold (greenfield only) ----------------------------
    scaffold_result = None
    if mode == "greenfield":
        try:
            scaffold_result = scaffold_greenfield(
                path=resolved_repo,
                name=greenfield_name,  # type: ignore[arg-type]
                description=item.title or item.raw_text[:80],
                ts=datetime.now(timezone.utc).date().isoformat(),
            )
        except ScaffoldError as e:
            raise RouteError(f"scaffold failed: {e}") from e

    try:
        # ---- Step 2: register (greenfield + new_repo) -----------------
        if mode in ("greenfield", "new_repo"):
            try:
                register_app_http(
                    app_id=app_id,  # type: ignore[arg-type]
                    name=greenfield_name or app_id,  # type: ignore[arg-type]
                    repo_path=str(resolved_repo),
                    mode="observe_only",
                    notes=f"registered by chad-week from {item.item_id}",
                )
            except CaptainError as e:
                raise RouteError(f"register failed: {e}") from e

        # ---- Step 3: file admiral_note --------------------------------
        # For existing-app mode, require the workspace to already exist —
        # a typo in --app should not silently mint a fresh workspace.
        require_existing = (mode == "existing_app")
        # Deterministic note_id makes this step idempotent: if the process
        # dies between this write and `folder.upsert_item(updated)`, retry
        # won't double-file.
        deterministic_id = f"chad-week-{item.week}-{item.item_id}"
        try:
            note_id, _path = file_admiral_note(
                app_id=app_id,  # type: ignore[arg-type]
                body=body,
                base=fleet_base,
                note_id=deterministic_id,
                require_existing_workspace=require_existing,
            )
        except CaptainError as e:
            raise RouteError(f"admiral_note write failed: {e}") from e

    except Exception:
        # Surgical rollback: remove only what scaffold created. A
        # pre-existing empty target dir is preserved untouched. We do NOT
        # roll back captain registration (idempotent on the captain side).
        if scaffold_result is not None:
            scaffold_result.cleanup()
        raise

    # ---- Step 4: update item -----------------------------------------
    item.target.app_id = app_id
    if resolved_repo is not None:
        item.target.repo_path = str(resolved_repo)
    item.target.is_new_app = mode in ("new_repo", "greenfield")
    if greenfield_name:
        item.target.greenfield_name = greenfield_name
    item.captain_note_id = note_id
    item.state = "routed"
    item.touch()
    return item


def _default_body(item: WeekItem) -> str:
    """Use item title + raw_text as the admiral note body if none was given."""
    parts: list[str] = []
    if item.title:
        parts.append(item.title.strip())
    parts.append(item.raw_text.strip())
    parts.append(f"\n(filed by chad-week from item {item.item_id})")
    return "\n\n".join(p for p in parts if p)


__all__ = ["RouteError", "RouteMode", "determine_mode", "route_item"]

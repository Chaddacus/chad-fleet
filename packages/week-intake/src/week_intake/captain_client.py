"""Thin HTTP/filesystem client for chad-captain.

We deliberately do NOT import from ``chad_captain`` (closed source, separate
release cadence). Instead we talk to captain through its two public surfaces:

  - HTTP API on :8109 (default; override with ``CHAD_CAPTAIN_API``)
       * POST /apps/register  for new tracked apps
       * GET  /apps/{id}      for status roll-ups
  - Filesystem protocol at ``~/.chad/fleet/apps/<app_id>/admiral_notes/<ts>.json``
       * Captain reads admiral notes on its next tick. Filesystem is the
         most reliable channel because it works even if the API daemon
         isn't running.

If the JSON shape captain accepts changes, this module breaks loudly —
that is the intended contract surface.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tracked_app_registry.storage import atomic_write

from week_intake.validation import validate_slug

DEFAULT_API_BASE = "http://127.0.0.1:8109"
DEFAULT_FLEET_BASE = Path.home() / ".chad" / "fleet" / "apps"


def api_base() -> str:
    return os.environ.get("CHAD_CAPTAIN_API", DEFAULT_API_BASE).rstrip("/")


def fleet_base() -> Path:
    raw = os.environ.get("CHAD_FLEET_APPS_DIR")
    return Path(raw).expanduser() if raw else DEFAULT_FLEET_BASE


class CaptainError(RuntimeError):
    """Any failure talking to chad-captain (HTTP or filesystem)."""


# ---------------------------------------------------------------------------
# Admiral notes (filesystem)
# ---------------------------------------------------------------------------


def _note_id_already_filed(notes_dir: Path, consumed_dir: Path, target_nid: str) -> Path | None:
    """Scan queued + consumed admiral_notes for one matching ``note_id``.

    Returns the path of the matching file, or None if not found. Used to
    make ``file_admiral_note`` idempotent for callers that pass a
    deterministic ``note_id`` derived from (week, item_id).
    """
    for d in (notes_dir, consumed_dir):
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("note_id") == target_nid:
                return p
    return None


def file_admiral_note(
    *,
    app_id: str,
    body: str,
    expects_response: bool = True,
    note_id: str | None = None,
    base: Path | None = None,
    require_existing_workspace: bool = False,
) -> tuple[str, Path]:
    """Write one ``admiral_notes/<ts>.json`` for the given app.

    Returns ``(note_id, path)``. Raises ``CaptainError`` if the workspace
    parent dir cannot be created.

    ``app_id`` MUST be a valid slug; otherwise this raises ``CaptainError``
    before any filesystem writes (path-traversal guard).

    When ``require_existing_workspace=True`` we refuse to create a fresh
    captain workspace for this app — the route caller is asserting that
    the app already exists, so a typo should NOT silently mint a new one.

    Idempotency: when ``note_id`` is provided AND a note with that exact
    id already exists in ``admiral_notes/`` or ``admiral_notes/consumed/``,
    we return the existing ``(note_id, path)`` without writing a new file.
    Callers that derive ``note_id`` deterministically (e.g. from a
    week + item_id pair) thus get crash-safe retries: if the process
    dies between note write and item upsert, retry won't double-file.
    """
    # Path-traversal guard: validate slug before any path join.
    try:
        validate_slug(app_id, field="app_id")
    except ValueError as e:
        raise CaptainError(str(e)) from e

    root = (base or fleet_base()) / app_id
    notes_dir = root / "admiral_notes"
    consumed_dir = notes_dir / "consumed"

    if require_existing_workspace and not root.exists():
        raise CaptainError(
            f"captain workspace for app_id={app_id!r} does not exist at {root} — "
            "refusing to mint a fresh workspace for an 'existing app' route. "
            "Register the app first, or pass --repo to register it now."
        )

    try:
        notes_dir.mkdir(parents=True, exist_ok=True)
        consumed_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise CaptainError(f"cannot create admiral_notes dir at {notes_dir}: {e}") from e

    # Idempotency check (only when a deterministic note_id is supplied).
    if note_id:
        existing = _note_id_already_filed(notes_dir, consumed_dir, note_id)
        if existing is not None:
            return note_id, existing

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    nid = note_id or f"note-{ts}"
    payload = {
        "note_id": nid,
        "app_id": app_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "body": body,
        "expects_response": expects_response,
        "captain_response": None,
        "responded_at": None,
    }
    path = notes_dir / f"{ts}.json"
    atomic_write(path, json.dumps(payload, indent=2))
    return nid, path


# ---------------------------------------------------------------------------
# App registration (HTTP)
# ---------------------------------------------------------------------------


def register_app_http(
    *,
    app_id: str,
    name: str,
    repo_path: str,
    mode: str = "observe_only",
    notes: str = "",
    timeout: float = 5.0,
) -> dict[str, Any]:
    """POST /apps/register on the captain API.

    Idempotent: if the app is already registered (captain returns 4xx with
    a body that suggests prior registration, or a fresh GET shows it
    exists), returns ``{"already_registered": True}`` instead of raising.
    Other 4xx/5xx and connection errors raise ``CaptainError``.
    """
    # Validate slug before sending anywhere.
    try:
        validate_slug(app_id, field="app_id")
    except ValueError as e:
        raise CaptainError(str(e)) from e

    # Idempotency probe: if captain already knows this app, skip the POST.
    # Only a 404 (None return) means "not registered, proceed to POST".
    # Any other CaptainError (5xx, connection, schema) is propagated — we
    # don't want to mask real problems by retrying as a fresh registration.
    existing = get_app_status_http(app_id, timeout=timeout)
    if existing is not None:
        # GET 200 means captain has a workspace + registry entry for this id.
        # Treat as already-registered regardless of the bundle's specific
        # `is_registered` field shape (which is part of /apps listing,
        # not always present on /apps/{id}).
        return {"already_registered": True, "app_id": app_id}

    url = f"{api_base()}/apps/register"
    body = {
        "app_id": app_id,
        "name": name,
        "repo_path": repo_path,
        "mode": mode,
        "notes": notes,
    }
    try:
        resp = httpx.post(url, json=body, timeout=timeout)
    except httpx.HTTPError as e:
        raise CaptainError(
            f"cannot reach captain API at {url} ({e}); is `chad-captain-api` running?"
        ) from e
    if resp.status_code == 409:
        # Captain says "already registered" — treat as success.
        return {"already_registered": True, "app_id": app_id}
    if resp.status_code >= 400:
        raise CaptainError(f"captain register returned {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise CaptainError(f"captain register returned non-JSON: {resp.text[:300]}") from e


# ---------------------------------------------------------------------------
# App status (HTTP)
# ---------------------------------------------------------------------------


def get_app_status_http(app_id: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
    """GET /apps/{id}. Returns None on 404. Raises ``CaptainError`` on connection failure."""
    try:
        validate_slug(app_id, field="app_id")
    except ValueError as e:
        raise CaptainError(str(e)) from e
    url = f"{api_base()}/apps/{app_id}"
    try:
        resp = httpx.get(url, timeout=timeout)
    except httpx.HTTPError as e:
        raise CaptainError(f"cannot reach captain API at {url} ({e})") from e
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise CaptainError(f"captain GET {url} returned {resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError) as e:
        raise CaptainError(f"captain GET {url} returned non-JSON 200: {resp.text[:300]}") from e
    if not isinstance(body, dict):
        raise CaptainError(
            f"captain GET {url} returned 200 with non-object body "
            f"(got {type(body).__name__}): {str(body)[:300]}"
        )
    return body


__all__ = [
    "CaptainError",
    "DEFAULT_API_BASE",
    "DEFAULT_FLEET_BASE",
    "api_base",
    "file_admiral_note",
    "fleet_base",
    "get_app_status_http",
    "register_app_http",
]

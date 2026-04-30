"""Event-sourced registry for tracked apps."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Event, TrackedApp
from .storage import append_jsonl, atomic_write, read_json, read_jsonl

_DEFAULT_BASE = Path.home() / ".chad" / "fleet" / "registry"


def _registry_base() -> Path:
    env = os.environ.get("CHAD_FLEET_REGISTRY_DIR")
    return Path(env) if env else _DEFAULT_BASE


def _now() -> datetime:
    return datetime.now(UTC)


def _now_str() -> str:
    return _now().isoformat().replace("+00:00", "Z")


class AppNotFound(KeyError):
    pass


class Registry:
    """Source-of-truth registry backed by a JSONL event log and a JSON materialized view."""

    def __init__(
        self,
        events_path: Path | None = None,
        view_path: Path | None = None,
    ) -> None:
        base = _registry_base()
        self._events_path = events_path or base / "events.jsonl"
        self._view_path = view_path or base / "apps.json"

    # ---- internal helpers ----

    def _emit(self, event_type: str, app_id: str, payload: dict) -> None:
        record = {
            "ts": _now_str(),
            "type": event_type,
            "app_id": app_id,
            "payload": payload,
        }
        append_jsonl(self._events_path, record)

    def _load_view(self) -> dict[str, dict]:
        data = read_json(self._view_path)
        return data if isinstance(data, dict) else {}

    def _save_view(self, apps: dict[str, dict]) -> None:
        atomic_write(self._view_path, json.dumps(apps, indent=2, default=str))

    # ---- public API ----

    def create(self, app: TrackedApp) -> TrackedApp:
        """Append app.created event and return the app."""
        apps = self._load_view()
        if app.id in apps:
            raise ValueError(f"App '{app.id}' already exists")
        payload = app.model_dump(mode="json")
        self._emit("app.created", app.id, payload)
        apps[app.id] = payload
        self._save_view(apps)
        return app

    def update(self, app_id: str, **fields: Any) -> TrackedApp:
        """Apply field updates; returns the updated app."""
        apps = self._load_view()
        if app_id not in apps:
            raise AppNotFound(app_id)
        current = apps[app_id]
        # Disallow changing id or created_at
        fields.pop("id", None)
        fields.pop("created_at", None)
        fields["updated_at"] = _now_str()
        current.update(fields)
        self._emit("app.updated", app_id, {"fields": fields})
        apps[app_id] = current
        self._save_view(apps)
        return TrackedApp.model_validate(current)

    def set_state(
        self,
        app_id: str,
        state: str,
        blocked_reason: str | None = None,
    ) -> TrackedApp:
        """Change app state; emits app.state_changed."""
        apps = self._load_view()
        if app_id not in apps:
            raise AppNotFound(app_id)
        current = apps[app_id]
        old_state = current.get("state")
        current["state"] = state
        current["blocked_reason"] = blocked_reason
        current["updated_at"] = _now_str()
        self._emit(
            "app.state_changed",
            app_id,
            {"old_state": old_state, "new_state": state, "blocked_reason": blocked_reason},
        )
        apps[app_id] = current
        self._save_view(apps)
        return TrackedApp.model_validate(current)

    def archive(self, app_id: str) -> None:
        """Mark app as archived."""
        apps = self._load_view()
        if app_id not in apps:
            raise AppNotFound(app_id)
        current = apps[app_id]
        current["state"] = "archived"
        current["updated_at"] = _now_str()
        self._emit("app.archived", app_id, {})
        apps[app_id] = current
        self._save_view(apps)

    def get(self, app_id: str) -> TrackedApp | None:
        """Return app by id, or None if not found."""
        apps = self._load_view()
        raw = apps.get(app_id)
        if raw is None:
            return None
        return TrackedApp.model_validate(raw)

    def list(
        self,
        state: str | None = None,
        owner_brand: str | None = None,
    ) -> list[TrackedApp]:
        """Return apps, optionally filtered by state and/or owner_brand."""
        apps = self._load_view()
        result = []
        for raw in apps.values():
            if state is not None and raw.get("state") != state:
                continue
            if owner_brand is not None and raw.get("owner_brand") != owner_brand:
                continue
            result.append(TrackedApp.model_validate(raw))
        return result

    def events(
        self,
        app_id: str | None = None,
        since: datetime | None = None,
    ) -> list[Event]:
        """Return raw events, optionally filtered by app_id and/or since timestamp."""
        raw_events = read_jsonl(self._events_path)
        result = []
        for rec in raw_events:
            if app_id is not None and rec.get("app_id") != app_id:
                continue
            evt = Event.model_validate(rec)
            if since is not None and evt.ts <= since:
                continue
            result.append(evt)
        return result

    def rebuild_view(self) -> None:
        """Replay the event log from scratch to regenerate the materialized view."""
        raw_events = read_jsonl(self._events_path)
        apps: dict[str, dict] = {}
        for rec in raw_events:
            etype = rec.get("type")
            app_id = rec.get("app_id", "")
            payload = rec.get("payload", {})

            if etype == "app.created":
                apps[app_id] = dict(payload)
            elif etype == "app.updated":
                if app_id in apps:
                    apps[app_id].update(payload.get("fields", {}))
            elif etype == "app.state_changed":
                if app_id in apps:
                    apps[app_id]["state"] = payload.get("new_state")
                    apps[app_id]["blocked_reason"] = payload.get("blocked_reason")
                    apps[app_id]["updated_at"] = rec.get("ts")
            elif etype == "app.archived":
                if app_id in apps:
                    apps[app_id]["state"] = "archived"
                    apps[app_id]["updated_at"] = rec.get("ts")

        self._save_view(apps)

"""Event-sourced registry for saved views."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .storage import append_jsonl, atomic_write, read_json, read_jsonl
from .types import SavedView

_DEFAULT_BASE = Path.home() / ".chad" / "dashboard" / "views"


def _registry_base() -> Path:
    env = os.environ.get("CHAD_VIEW_REGISTRY_DIR")
    return Path(env) if env else _DEFAULT_BASE


def _now() -> datetime:
    return datetime.now(UTC)


def _now_str() -> str:
    return _now().isoformat().replace("+00:00", "Z")


def _slugify(name: str) -> str:
    """Convert a display name to a URL-safe slug."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


class ViewNotFound(KeyError):
    pass


class Registry:
    """Source-of-truth registry backed by a JSONL event log and a JSON materialized view."""

    def __init__(
        self,
        view_path: Path | None = None,
        events_path: Path | None = None,
    ) -> None:
        base = _registry_base()
        self._view_path = view_path or base / "views.json"
        self._events_path = events_path or base / "events.jsonl"

    # ---- internal helpers ----

    def _emit(self, event_type: str, view_id: str, payload: dict) -> None:
        record = {
            "type": event_type,
            "view_id": view_id,
            "at": _now_str(),
            "actor": "user",
            **payload,
        }
        append_jsonl(self._events_path, record)

    def _load_view(self) -> dict[str, dict]:
        data = read_json(self._view_path)
        return data if isinstance(data, dict) else {}

    def _save_view(self, views: dict[str, dict]) -> None:
        atomic_write(self._view_path, json.dumps(views, indent=2, default=str))

    def _get_raw(self, views: dict[str, dict], view_id: str) -> dict:
        if view_id not in views:
            raise ViewNotFound(view_id)
        return views[view_id]

    # ---- public API ----

    def create(
        self,
        name: str,
        prompt: str,
        *,
        description: str = "",
        app_scope: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> SavedView:
        """Create a new view. Auto-slugifies id; deduplicates against existing."""
        views = self._load_view()
        base_slug = _slugify(name)
        slug = base_slug
        counter = 1
        while slug in views:
            slug = f"{base_slug}-{counter}"
            counter += 1

        now_str = _now_str()
        raw: dict[str, Any] = {
            "id": slug,
            "name": name,
            "description": description,
            "prompt": prompt,
            "app_scope": app_scope or [],
            "pinned": False,
            "tags": tags or [],
            "created_at": now_str,
            "updated_at": now_str,
            "last_rendered_at": None,
            "last_render_html": None,
            "last_render_tsx": None,
        }
        self._emit("created", slug, {"payload": raw})
        views[slug] = raw
        self._save_view(views)
        return SavedView.model_validate(raw)

    def update(
        self,
        view_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        prompt: str | None = None,
        app_scope: list[str] | None = None,
    ) -> SavedView:
        """Apply field updates; returns the updated view."""
        views = self._load_view()
        current = self._get_raw(views, view_id)

        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if description is not None:
            fields["description"] = description
        if prompt is not None:
            fields["prompt"] = prompt
        if app_scope is not None:
            fields["app_scope"] = app_scope
        fields["updated_at"] = _now_str()

        current.update(fields)
        self._emit("updated", view_id, {"fields": fields})
        views[view_id] = current
        self._save_view(views)
        return SavedView.model_validate(current)

    def get(self, view_id: str) -> SavedView | None:
        """Return view by id, or None if not found."""
        views = self._load_view()
        raw = views.get(view_id)
        if raw is None:
            return None
        return SavedView.model_validate(raw)

    def list(
        self,
        *,
        app: str | None = None,
        tag: str | None = None,
        pinned_only: bool = False,
    ) -> list[SavedView]:
        """Return views, optionally filtered by app scope, tag, and/or pinned status."""
        views = self._load_view()
        result = []
        for raw in views.values():
            if app is not None and app not in raw.get("app_scope", []):
                continue
            if tag is not None and tag not in raw.get("tags", []):
                continue
            if pinned_only and not raw.get("pinned", False):
                continue
            result.append(SavedView.model_validate(raw))
        return result

    def record_render(self, view_id: str, *, html: str, tsx: str) -> SavedView:
        """Record a render result; updates last_rendered_at, last_render_html, last_render_tsx."""
        views = self._load_view()
        current = self._get_raw(views, view_id)

        now_str = _now_str()
        current["last_rendered_at"] = now_str
        current["last_render_html"] = html
        current["last_render_tsx"] = tsx
        current["updated_at"] = now_str

        self._emit("rendered", view_id, {"html_len": len(html), "tsx_len": len(tsx)})
        views[view_id] = current
        self._save_view(views)
        return SavedView.model_validate(current)

    def pin(self, view_id: str) -> SavedView:
        """Pin a view."""
        views = self._load_view()
        current = self._get_raw(views, view_id)

        current["pinned"] = True
        current["updated_at"] = _now_str()
        self._emit("pinned", view_id, {})
        views[view_id] = current
        self._save_view(views)
        return SavedView.model_validate(current)

    def unpin(self, view_id: str) -> SavedView:
        """Unpin a view."""
        views = self._load_view()
        current = self._get_raw(views, view_id)

        current["pinned"] = False
        current["updated_at"] = _now_str()
        self._emit("unpinned", view_id, {})
        views[view_id] = current
        self._save_view(views)
        return SavedView.model_validate(current)

    def add_tag(self, view_id: str, tag: str) -> SavedView:
        """Add a tag to a view (idempotent)."""
        views = self._load_view()
        current = self._get_raw(views, view_id)

        tags: list[str] = current.get("tags", [])
        if tag not in tags:
            tags.append(tag)
            current["tags"] = tags
            current["updated_at"] = _now_str()
            self._emit("tagged", view_id, {"tag": tag})
            views[view_id] = current
            self._save_view(views)
        return SavedView.model_validate(current)

    def remove_tag(self, view_id: str, tag: str) -> SavedView:
        """Remove a tag from a view (idempotent)."""
        views = self._load_view()
        current = self._get_raw(views, view_id)

        tags: list[str] = current.get("tags", [])
        if tag in tags:
            tags.remove(tag)
            current["tags"] = tags
            current["updated_at"] = _now_str()
            self._emit("untagged", view_id, {"tag": tag})
            views[view_id] = current
            self._save_view(views)
        return SavedView.model_validate(current)

    def delete(self, view_id: str) -> None:
        """Delete a view and emit deleted event."""
        views = self._load_view()
        self._get_raw(views, view_id)  # raises ViewNotFound if missing

        self._emit("deleted", view_id, {})
        del views[view_id]
        self._save_view(views)

    def events(self, view_id: str | None = None) -> list[dict]:
        """Return raw event dicts, optionally filtered by view_id."""
        raw_events = read_jsonl(self._events_path)
        if view_id is None:
            return raw_events
        return [r for r in raw_events if r.get("view_id") == view_id]

"""FastAPI REST API for view-registry.

Run with:
    uvicorn view_registry.api:app --port 8108
Or via environment variable VIEW_REGISTRY_PORT to override the default port.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from .registry import Registry, ViewNotFound
from .types import SavedView

app = FastAPI(title="view-registry", version="0.0.0")
_registry = Registry()

DEFAULT_PORT = int(os.environ.get("VIEW_REGISTRY_PORT", "8108"))


# ---- request bodies ----


class CreateViewBody(BaseModel):
    name: str
    prompt: str
    description: str = ""
    app_scope: list[str] = []
    tags: list[str] = []


class UpdateViewBody(BaseModel):
    name: str | None = None
    description: str | None = None
    prompt: str | None = None
    app_scope: list[str] | None = None


class RecordRenderBody(BaseModel):
    html: str
    tsx: str


class AddTagBody(BaseModel):
    tag: str


# ---- helpers ----


def _not_found(view_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"View '{view_id}' not found")


def _safe_get(view_id: str) -> SavedView:
    view = _registry.get(view_id)
    if view is None:
        raise _not_found(view_id)
    return view


# ---- endpoints ----


@app.get("/views", response_model=list[SavedView])
def list_views(
    app: str | None = None,
    tag: str | None = None,
    pinned: bool = False,
) -> list[SavedView]:
    return _registry.list(app=app, tag=tag, pinned_only=pinned)


@app.post("/views", response_model=SavedView, status_code=201)
def create_view(body: CreateViewBody) -> SavedView:
    return _registry.create(
        name=body.name,
        prompt=body.prompt,
        description=body.description,
        app_scope=body.app_scope or None,
        tags=body.tags or None,
    )


@app.get("/views/{view_id}", response_model=SavedView)
def get_view(view_id: str) -> SavedView:
    return _safe_get(view_id)


@app.put("/views/{view_id}", response_model=SavedView)
def update_view(view_id: str, body: UpdateViewBody) -> SavedView:
    _safe_get(view_id)
    try:
        return _registry.update(
            view_id,
            name=body.name,
            description=body.description,
            prompt=body.prompt,
            app_scope=body.app_scope,
        )
    except ViewNotFound:
        raise _not_found(view_id)


@app.delete("/views/{view_id}", status_code=204, response_class=Response)
def delete_view(view_id: str) -> Response:
    try:
        _registry.delete(view_id)
    except ViewNotFound:
        raise _not_found(view_id)
    return Response(status_code=204)


@app.post("/views/{view_id}/pin", response_model=SavedView)
def pin_view(view_id: str) -> SavedView:
    try:
        return _registry.pin(view_id)
    except ViewNotFound:
        raise _not_found(view_id)


@app.post("/views/{view_id}/unpin", response_model=SavedView)
def unpin_view(view_id: str) -> SavedView:
    try:
        return _registry.unpin(view_id)
    except ViewNotFound:
        raise _not_found(view_id)


@app.post("/views/{view_id}/render", response_model=SavedView)
def record_render(view_id: str, body: RecordRenderBody) -> SavedView:
    try:
        return _registry.record_render(view_id, html=body.html, tsx=body.tsx)
    except ViewNotFound:
        raise _not_found(view_id)


@app.post("/views/{view_id}/tags", response_model=SavedView)
def add_tag(view_id: str, body: AddTagBody) -> SavedView:
    try:
        return _registry.add_tag(view_id, body.tag)
    except ViewNotFound:
        raise _not_found(view_id)


@app.delete("/views/{view_id}/tags/{tag}", response_model=SavedView)
def remove_tag(view_id: str, tag: str) -> SavedView:
    try:
        return _registry.remove_tag(view_id, tag)
    except ViewNotFound:
        raise _not_found(view_id)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=DEFAULT_PORT)


if __name__ == "__main__":
    main()

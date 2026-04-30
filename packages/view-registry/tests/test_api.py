"""FastAPI REST endpoint tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from view_registry.api import app, _registry
from view_registry.registry import Registry


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the module-level _registry with a temp-dir-backed one."""
    import view_registry.api as api_module

    fresh = Registry(
        view_path=tmp_path / "views.json",
        events_path=tmp_path / "events.jsonl",
    )
    monkeypatch.setattr(api_module, "_registry", fresh)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ---- list ----

def test_list_empty(client: TestClient) -> None:
    resp = client.get("/views")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_returns_views(client: TestClient) -> None:
    client.post("/views", json={"name": "View A", "prompt": "show a"})
    client.post("/views", json={"name": "View B", "prompt": "show b"})
    resp = client.get("/views")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_filter_by_app(client: TestClient) -> None:
    client.post("/views", json={"name": "Scoped", "prompt": "p", "app_scope": ["myapp"]})
    client.post("/views", json={"name": "Global", "prompt": "p"})
    resp = client.get("/views?app=myapp")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Scoped"


def test_list_filter_by_tag(client: TestClient) -> None:
    client.post("/views", json={"name": "Tagged", "prompt": "p", "tags": ["weekly"]})
    client.post("/views", json={"name": "Plain", "prompt": "p"})
    resp = client.get("/views?tag=weekly")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Tagged"


def test_list_filter_pinned(client: TestClient) -> None:
    r1 = client.post("/views", json={"name": "Pinnable", "prompt": "p"})
    view_id = r1.json()["id"]
    client.post(f"/views/{view_id}/pin")
    client.post("/views", json={"name": "Unpinned", "prompt": "p"})
    resp = client.get("/views?pinned=true")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == view_id


# ---- create ----

def test_create_view(client: TestClient) -> None:
    resp = client.post("/views", json={"name": "My View", "prompt": "show me stuff"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "my-view"
    assert data["name"] == "My View"
    assert data["prompt"] == "show me stuff"


def test_create_view_with_all_fields(client: TestClient) -> None:
    payload = {
        "name": "Full View",
        "prompt": "show everything",
        "description": "A full view",
        "app_scope": ["app-a"],
        "tags": ["important"],
    }
    resp = client.post("/views", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["description"] == "A full view"
    assert data["app_scope"] == ["app-a"]
    assert "important" in data["tags"]


# ---- get ----

def test_get_existing(client: TestClient) -> None:
    r = client.post("/views", json={"name": "Get Me", "prompt": "p"})
    view_id = r.json()["id"]
    resp = client.get(f"/views/{view_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == view_id


def test_get_missing(client: TestClient) -> None:
    resp = client.get("/views/does-not-exist")
    assert resp.status_code == 404


# ---- update ----

def test_update_view(client: TestClient) -> None:
    r = client.post("/views", json={"name": "Old Name", "prompt": "p"})
    view_id = r.json()["id"]
    resp = client.put(f"/views/{view_id}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


def test_update_missing(client: TestClient) -> None:
    resp = client.put("/views/ghost", json={"name": "X"})
    assert resp.status_code == 404


# ---- delete ----

def test_delete_view(client: TestClient) -> None:
    r = client.post("/views", json={"name": "Delete Me", "prompt": "p"})
    view_id = r.json()["id"]
    resp = client.delete(f"/views/{view_id}")
    assert resp.status_code == 204
    assert client.get(f"/views/{view_id}").status_code == 404


def test_delete_missing(client: TestClient) -> None:
    resp = client.delete("/views/ghost")
    assert resp.status_code == 404


# ---- pin / unpin ----

def test_pin_view(client: TestClient) -> None:
    r = client.post("/views", json={"name": "Pin Me", "prompt": "p"})
    view_id = r.json()["id"]
    resp = client.post(f"/views/{view_id}/pin")
    assert resp.status_code == 200
    assert resp.json()["pinned"] is True


def test_unpin_view(client: TestClient) -> None:
    r = client.post("/views", json={"name": "Unpin Me", "prompt": "p"})
    view_id = r.json()["id"]
    client.post(f"/views/{view_id}/pin")
    resp = client.post(f"/views/{view_id}/unpin")
    assert resp.status_code == 200
    assert resp.json()["pinned"] is False


def test_pin_missing(client: TestClient) -> None:
    resp = client.post("/views/ghost/pin")
    assert resp.status_code == 404


def test_unpin_missing(client: TestClient) -> None:
    resp = client.post("/views/ghost/unpin")
    assert resp.status_code == 404


# ---- render ----

def test_record_render(client: TestClient) -> None:
    r = client.post("/views", json={"name": "Render View", "prompt": "p"})
    view_id = r.json()["id"]
    resp = client.post(f"/views/{view_id}/render", json={"html": "<div/>", "tsx": "<Div/>"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["last_render_html"] == "<div/>"
    assert data["last_render_tsx"] == "<Div/>"
    assert data["last_rendered_at"] is not None


def test_render_missing(client: TestClient) -> None:
    resp = client.post("/views/ghost/render", json={"html": "<x/>", "tsx": "<X/>"})
    assert resp.status_code == 404


# ---- tags ----

def test_add_tag(client: TestClient) -> None:
    r = client.post("/views", json={"name": "Tag Test", "prompt": "p"})
    view_id = r.json()["id"]
    resp = client.post(f"/views/{view_id}/tags", json={"tag": "important"})
    assert resp.status_code == 200
    assert "important" in resp.json()["tags"]


def test_remove_tag(client: TestClient) -> None:
    r = client.post("/views", json={"name": "Remove Tag", "prompt": "p", "tags": ["old-tag"]})
    view_id = r.json()["id"]
    resp = client.delete(f"/views/{view_id}/tags/old-tag")
    assert resp.status_code == 200
    assert "old-tag" not in resp.json()["tags"]


def test_add_tag_missing(client: TestClient) -> None:
    resp = client.post("/views/ghost/tags", json={"tag": "x"})
    assert resp.status_code == 404


def test_remove_tag_missing(client: TestClient) -> None:
    resp = client.delete("/views/ghost/tags/x")
    assert resp.status_code == 404

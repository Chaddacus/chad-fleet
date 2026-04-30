"""Tests for the captain HTTP API."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chad_captain.api import create_app
from chad_captain.protocol import (
    AdmiralNote,
    AppWorkspace,
    CaptainLogEntry,
    Roadmap,
    RoadmapSlice,
    SliceComplete,
    append_captain_log,
    write_admiral_note,
    write_roadmap,
)


@pytest.fixture()
def fleet_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "fleet"
    base.mkdir()
    monkeypatch.setenv("CHAD_FLEET_APPS_DIR", str(base))
    # Isolate the apps registry so tests don't pick up the real ~/.chad/captain/apps_registry.json.
    monkeypatch.setenv("CHAD_CAPTAIN_APPS_REGISTRY", str(tmp_path / "captain" / "apps_registry.json"))
    return base


@pytest.fixture()
def client(fleet_base: Path) -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def ws(fleet_base: Path) -> AppWorkspace:
    w = AppWorkspace("alpha")
    w.ensure()
    return w


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_returns_zero_when_empty(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["registered_apps"] == 0


# ---------------------------------------------------------------------------
# /apps
# ---------------------------------------------------------------------------


def test_apps_list_discovers_workspace_with_roadmap(client: TestClient, ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[
        RoadmapSlice(slice_id="s1", objective="o"),
    ]))
    r = client.get("/apps")
    body = r.json()
    assert body["count"] == 1
    assert body["apps"][0]["app_id"] == "alpha"
    # Enriched fields when registry doesn't know the app
    assert body["apps"][0]["mode"] == "autonomous"
    assert body["apps"][0]["repo_path"] is None


def test_apps_list_discovers_workspace_with_admiral_notes_only(
    client: TestClient, ws: AppWorkspace,
) -> None:
    write_admiral_note(ws, AdmiralNote(note_id="n1", app_id=ws.app_id, body="hi"))
    r = client.get("/apps")
    body = r.json()
    assert body["count"] == 1


def test_fleet_endpoint_returns_bundles(client: TestClient, ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[
        RoadmapSlice(slice_id="s1", objective="o", status="queued"),
    ]))
    r = client.get("/fleet")
    assert r.status_code == 200
    body = r.json()
    assert "generated_at" in body
    assert body["count"] == 1
    bundle = body["apps"][0]
    assert bundle["app_id"] == "alpha"
    assert bundle["roadmap"]["slices"][0]["slice_id"] == "s1"
    # Scorecard is None because no repo_path is registered
    assert bundle["scorecard"] is None


# ---------------------------------------------------------------------------
# /apps/{id}
# ---------------------------------------------------------------------------


def test_app_state_returns_404_for_unknown_app(client: TestClient) -> None:
    r = client.get("/apps/missing")
    assert r.status_code == 404


def test_app_state_includes_roadmap_and_log(client: TestClient, ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[
        RoadmapSlice(slice_id="s1", objective="o"),
    ]))
    append_captain_log(ws, CaptainLogEntry(
        app_id=ws.app_id, slice_id="s1", kind="dispatch", rationale="dispatched",
    ))
    r = client.get(f"/apps/{ws.app_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["roadmap"]["app_id"] == "alpha"
    assert len(body["captain_log_tail"]) == 1
    assert body["captain_log_tail"][0]["kind"] == "dispatch"


def test_app_roadmap_returns_404_when_missing(client: TestClient, ws: AppWorkspace) -> None:
    write_admiral_note(ws, AdmiralNote(note_id="n", app_id=ws.app_id, body="x"))
    r = client.get(f"/apps/{ws.app_id}/roadmap")
    assert r.status_code == 404


def test_app_log_returns_entries(client: TestClient, ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[]))  # register
    for i in range(3):
        append_captain_log(ws, CaptainLogEntry(
            app_id=ws.app_id, kind="dispatch", rationale=f"r{i}",
        ))
    r = client.get(f"/apps/{ws.app_id}/log")
    body = r.json()
    assert body["count"] == 3


# ---------------------------------------------------------------------------
# /apps/{id}/scorecard
# ---------------------------------------------------------------------------


def test_app_scorecard_runs_against_repo(client: TestClient, ws: AppWorkspace,
                                          tmp_path: Path) -> None:
    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[]))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# r")
    (repo / "main.py").write_text("x = 1\n")
    r = client.get(f"/apps/{ws.app_id}/scorecard?repo_path={repo}")
    assert r.status_code == 200
    body = r.json()
    assert any(d["name"] == "tests_present" for d in body["dimensions"])


def test_app_scorecard_400_when_repo_missing(client: TestClient, ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[]))
    r = client.get(f"/apps/{ws.app_id}/scorecard?repo_path=/no/such/path")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /apps/{id}/note
# ---------------------------------------------------------------------------


def test_post_note_writes_file_and_lists_unread(client: TestClient, ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[]))
    r = client.post(f"/apps/{ws.app_id}/note", json={"body": "Try this approach instead."})
    assert r.status_code == 200
    note_id = r.json()["note_id"]

    state = client.get(f"/apps/{ws.app_id}").json()
    assert any(note_id in u for u in state["unread_admiral_notes"])


def test_post_note_twice_in_same_second_yields_distinct_ids(
    client: TestClient, ws: AppWorkspace,
) -> None:
    """Regression: second-precision timestamps caused id collisions and
    silent overwrite when two notes landed in the same wall-clock second."""
    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[]))
    r1 = client.post(f"/apps/{ws.app_id}/note", json={"body": "first"})
    r2 = client.post(f"/apps/{ws.app_id}/note", json={"body": "second"})
    assert r1.status_code == 200 and r2.status_code == 200
    id1 = r1.json()["note_id"]
    id2 = r2.json()["note_id"]
    assert id1 != id2, f"note_ids collided: {id1!r} == {id2!r}"

    # Both notes must exist on disk with distinct content
    state = client.get(f"/apps/{ws.app_id}").json()
    unread = state["unread_admiral_notes"]
    assert len(unread) == 2, f"expected 2 unread notes, got {unread!r}"


# ---------------------------------------------------------------------------
# /apps/{id}/replan + /apps/{id}/tick
# ---------------------------------------------------------------------------


def test_post_replan_with_no_llm_writes_roadmap(client: TestClient, ws: AppWorkspace,
                                                  tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# r")

    # Stub the live web research call
    from chad_captain.research import synthesize as syn_mod, web as web_mod
    monkeypatch.setattr(syn_mod, "research_web",
                         lambda **_kw: web_mod.WebProfile.skipped("test"))

    r = client.post(
        f"/apps/{ws.app_id}/replan",
        json={"trigger": "manual", "repo_path": str(repo), "no_llm": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == ws.app_id
    assert len(body["slices"]) >= 1


def test_post_tick_returns_status(client: TestClient, ws: AppWorkspace, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# r")

    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[
        RoadmapSlice(slice_id="s1", objective="o", status="queued"),
    ]))
    r = client.post(f"/apps/{ws.app_id}/tick", json={"repo_path": str(repo)})
    assert r.status_code == 200
    assert "status" in r.json()


def test_post_replan_aborts_in_flight_slice(
    client: TestClient, ws: AppWorkspace, tmp_path: Path, monkeypatch,
) -> None:
    """Regression: replan during in-flight slice must clear current_slice so
    the new roadmap doesn't collide with stale current_slice.json (same id,
    different objective)."""
    from chad_captain.protocol import (
        CurrentSlice,
        read_captain_log,
        read_current_slice,
        write_current_slice,
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# r")

    # Stub the live web research call
    from chad_captain.research import synthesize as syn_mod, web as web_mod
    monkeypatch.setattr(syn_mod, "research_web",
                         lambda **_kw: web_mod.WebProfile.skipped("test"))

    write_roadmap(ws, Roadmap(app_id=ws.app_id, slices=[
        RoadmapSlice(slice_id="s1", objective="o", status="in_flight"),
    ]))
    write_current_slice(ws, CurrentSlice(
        slice_id="s1",
        app_id=ws.app_id,
        objective="OLD objective",
        system_prompt="",
        user_prompt="",
        repo_path=str(repo),
    ))

    assert read_current_slice(ws) is not None  # precondition

    r = client.post(
        f"/apps/{ws.app_id}/replan",
        json={"trigger": "manual", "repo_path": str(repo), "no_llm": True},
    )
    assert r.status_code == 200, r.text

    # current_slice cleared
    assert read_current_slice(ws) is None

    # Abort log entry written
    log = list(read_captain_log(ws))
    aborted = [e for e in log if "aborted" in (e.rationale or "")]
    assert len(aborted) == 1
    assert aborted[0].slice_id == "s1"
    assert aborted[0].references.get("aborted_by") == "replan"

"""captain_client tests — filesystem path + HTTP path with httpx mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from week_intake.captain_client import (
    CaptainError,
    file_admiral_note,
    get_app_status_http,
    register_app_http,
)


def test_file_admiral_note_writes_atomic_json(tmp_path) -> None:
    nid, path = file_admiral_note(
        app_id="chad-agent",
        body="hello captain",
        base=tmp_path,
    )
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["note_id"] == nid
    assert payload["app_id"] == "chad-agent"
    assert payload["body"] == "hello captain"
    assert payload["expects_response"] is True
    # Consumed dir should also have been created so captain can move it later.
    assert (tmp_path / "chad-agent" / "admiral_notes" / "consumed").exists()


def test_register_app_http_success(monkeypatch) -> None:
    captured = {}

    def fake_get(url, timeout=None):
        # Idempotency probe: 404 → app not registered yet, proceed with POST.
        return httpx.Response(404, text="")

    def fake_post(url, json=None, timeout=None):  # noqa: A002 - matches httpx signature
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, json={"registered": True})

    with (
        patch("week_intake.captain_client.httpx.get", side_effect=fake_get),
        patch("week_intake.captain_client.httpx.post", side_effect=fake_post),
    ):
        out = register_app_http(
            app_id="thing", name="thing", repo_path="/tmp/x", mode="observe_only"
        )
    assert out == {"registered": True}
    assert captured["url"].endswith("/apps/register")
    assert captured["json"]["app_id"] == "thing"


def test_register_app_http_translates_connection_errors() -> None:
    with (
        patch("week_intake.captain_client.httpx.get", side_effect=httpx.ConnectError("refused")),
        patch("week_intake.captain_client.httpx.post", side_effect=httpx.ConnectError("refused")),
    ):
        with pytest.raises(CaptainError) as exc_info:
            register_app_http(app_id="x", name="x", repo_path="/tmp/x")
    assert "captain API" in str(exc_info.value)


def test_register_app_http_409_treated_as_success() -> None:
    """Captain returning 409 (already registered) is treated as success."""
    with (
        patch("week_intake.captain_client.httpx.get", return_value=httpx.Response(404, text="")),
        patch(
            "week_intake.captain_client.httpx.post",
            return_value=httpx.Response(409, text="already registered"),
        ),
    ):
        out = register_app_http(app_id="x", name="x", repo_path="/tmp/x")
    assert out["already_registered"] is True


def test_register_app_http_skips_post_when_already_registered() -> None:
    """If the GET probe returns is_registered=true, no POST is issued."""
    with (
        patch(
            "week_intake.captain_client.httpx.get",
            return_value=httpx.Response(200, json={"app_id": "x", "is_registered": True}),
        ),
        patch("week_intake.captain_client.httpx.post") as post_mock,
    ):
        out = register_app_http(app_id="x", name="x", repo_path="/tmp/x")
    assert out["already_registered"] is True
    post_mock.assert_not_called()


def test_register_app_http_translates_500() -> None:
    with (
        patch("week_intake.captain_client.httpx.get", return_value=httpx.Response(404, text="")),
        patch(
            "week_intake.captain_client.httpx.post",
            return_value=httpx.Response(500, text="boom"),
        ),
    ):
        with pytest.raises(CaptainError) as exc_info:
            register_app_http(app_id="x", name="x", repo_path="/tmp/x")
    assert "500" in str(exc_info.value)


def test_register_app_rejects_path_traversal_slug() -> None:
    """app_id with path-traversal characters must be rejected at the boundary."""
    for bad in ("../../etc", "x/y", "../../..", "abc/", "/abs", "x..y"):
        with pytest.raises(CaptainError):
            register_app_http(app_id=bad, name="x", repo_path="/tmp/x")


def test_file_admiral_note_rejects_path_traversal_slug(tmp_path) -> None:
    from week_intake.captain_client import file_admiral_note

    for bad in ("../escape", "..", "x/y", "/abs"):
        with pytest.raises(CaptainError):
            file_admiral_note(app_id=bad, body="hi", base=tmp_path)


def test_file_admiral_note_require_existing_blocks_typo(tmp_path) -> None:
    from week_intake.captain_client import file_admiral_note

    # No workspace pre-created → must refuse when require_existing_workspace=True.
    with pytest.raises(CaptainError) as exc_info:
        file_admiral_note(
            app_id="ghost-app",
            body="hi",
            base=tmp_path,
            require_existing_workspace=True,
        )
    assert "does not exist" in str(exc_info.value)


def test_file_admiral_note_require_existing_passes_when_workspace_exists(tmp_path) -> None:
    from week_intake.captain_client import file_admiral_note

    # Pre-create the workspace; require_existing_workspace must accept it.
    (tmp_path / "real-app").mkdir()
    nid, path = file_admiral_note(
        app_id="real-app",
        body="hi",
        base=tmp_path,
        require_existing_workspace=True,
    )
    assert path.exists()
    assert "real-app" in str(path)


def test_file_admiral_note_idempotent_with_deterministic_id(tmp_path) -> None:
    """Calling twice with the same note_id returns the existing note."""
    from week_intake.captain_client import file_admiral_note

    nid1, path1 = file_admiral_note(
        app_id="chad-agent",
        body="first",
        base=tmp_path,
        note_id="chad-week-2026-W19-wk-001",
    )
    nid2, path2 = file_admiral_note(
        app_id="chad-agent",
        body="second-attempt",  # different body — must be ignored on retry
        base=tmp_path,
        note_id="chad-week-2026-W19-wk-001",
    )
    assert nid1 == nid2 == "chad-week-2026-W19-wk-001"
    assert path1 == path2
    # Only one file landed; the second call short-circuited.
    notes = list((tmp_path / "chad-agent" / "admiral_notes").glob("*.json"))
    assert len(notes) == 1


def test_file_admiral_note_idempotent_finds_consumed_notes(tmp_path) -> None:
    """If captain already moved the note to consumed/, retry must NOT re-file."""
    from week_intake.captain_client import file_admiral_note

    nid, path = file_admiral_note(
        app_id="chad-agent",
        body="x",
        base=tmp_path,
        note_id="chad-week-2026-W19-wk-007",
    )
    # Simulate captain consuming the note.
    consumed_dir = tmp_path / "chad-agent" / "admiral_notes" / "consumed"
    consumed_dir.mkdir(parents=True, exist_ok=True)
    path.rename(consumed_dir / path.name)

    # Retry: should detect the consumed copy and short-circuit.
    nid2, path2 = file_admiral_note(
        app_id="chad-agent",
        body="x",
        base=tmp_path,
        note_id="chad-week-2026-W19-wk-007",
    )
    assert nid2 == nid
    assert "consumed" in str(path2)
    # No new note in the queued dir.
    queued = list((tmp_path / "chad-agent" / "admiral_notes").glob("*.json"))
    assert queued == []


def test_get_app_status_rejects_non_json_200() -> None:
    """A 200 response with a non-JSON body must raise CaptainError."""
    from week_intake.captain_client import CaptainError, get_app_status_http

    with patch(
        "week_intake.captain_client.httpx.get",
        return_value=httpx.Response(200, text="<html>not json</html>"),
    ):
        with pytest.raises(CaptainError) as exc_info:
            get_app_status_http("real-app")
    assert "non-JSON" in str(exc_info.value)


def test_get_app_status_rejects_200_with_array_body() -> None:
    """A 200 response with a non-object body must raise CaptainError."""
    from week_intake.captain_client import CaptainError, get_app_status_http

    with patch(
        "week_intake.captain_client.httpx.get",
        return_value=httpx.Response(200, json=[1, 2, 3]),
    ):
        with pytest.raises(CaptainError):
            get_app_status_http("real-app")


def test_get_app_status_404_returns_none() -> None:
    with patch(
        "week_intake.captain_client.httpx.get",
        return_value=httpx.Response(404, text=""),
    ):
        assert get_app_status_http("missing") is None


def test_get_app_status_ok() -> None:
    with patch(
        "week_intake.captain_client.httpx.get",
        return_value=httpx.Response(200, json={"app_id": "x", "is_registered": True}),
    ):
        out = get_app_status_http("x")
    assert out == {"app_id": "x", "is_registered": True}

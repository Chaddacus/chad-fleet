"""Router unit tests with HTTP and scaffold mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from week_intake.router import RouteError, determine_mode, route_item
from week_intake.types import WeekItem


def _item() -> WeekItem:
    return WeekItem(
        item_id="wk-001",
        week="2026-W19",
        raw_text="rewrite away-responder docs",
        title="rewrite away-responder docs",
        kind="wip",
        state="ready",
        confidence=0.9,
    )


def test_determine_mode_existing_app() -> None:
    assert determine_mode(app_id="chad-agent", repo_path=None, greenfield_name=None) == "existing_app"


def test_determine_mode_new_repo() -> None:
    assert determine_mode(app_id="new-app", repo_path="/tmp/x", greenfield_name=None) == "new_repo"


def test_determine_mode_greenfield() -> None:
    assert (
        determine_mode(app_id="new-app", repo_path="/tmp/x", greenfield_name="new-app")
        == "greenfield"
    )


def test_determine_mode_rejects_no_args() -> None:
    with pytest.raises(RouteError):
        determine_mode(app_id=None, repo_path=None, greenfield_name=None)


def test_determine_mode_rejects_repo_without_app() -> None:
    with pytest.raises(RouteError):
        determine_mode(app_id=None, repo_path="/tmp/x", greenfield_name=None)


def test_determine_mode_rejects_greenfield_without_repo() -> None:
    with pytest.raises(RouteError):
        determine_mode(app_id="x", repo_path=None, greenfield_name="x")


def test_route_item_existing_app_writes_note(tmp_path) -> None:
    """Existing app: no register, just file an admiral_note. Workspace must pre-exist."""
    item = _item()
    # Pre-create the captain workspace (existing-app mode requires it).
    (tmp_path / "chad-agent").mkdir()

    with patch("week_intake.router.register_app_http") as reg_mock:
        updated = route_item(
            item,
            app_id="chad-agent",
            repo_path=None,
            greenfield_name=None,
            fleet_base=tmp_path,
        )

    reg_mock.assert_not_called()
    assert updated.state == "routed"
    assert updated.target.app_id == "chad-agent"
    assert updated.captain_note_id is not None

    notes_dir = tmp_path / "chad-agent" / "admiral_notes"
    notes = list(notes_dir.glob("*.json"))
    assert len(notes) == 1
    payload = json.loads(notes[0].read_text(encoding="utf-8"))
    assert payload["note_id"] == updated.captain_note_id
    assert payload["app_id"] == "chad-agent"
    assert "rewrite away-responder" in payload["body"]


def test_route_item_existing_app_typo_rejected(tmp_path) -> None:
    """A typo in --app must NOT silently mint a fresh workspace."""
    item = _item()

    with pytest.raises(RouteError) as exc_info:
        route_item(
            item,
            app_id="ghost-typo",  # no pre-existing workspace
            repo_path=None,
            greenfield_name=None,
            fleet_base=tmp_path,
        )
    assert "does not exist" in str(exc_info.value)
    # Item state must remain unchanged on validation failure.
    assert item.state == "ready"
    assert item.captain_note_id is None


def test_route_item_rejects_path_traversal_app_id(tmp_path) -> None:
    item = _item()
    with pytest.raises(RouteError):
        route_item(
            item,
            app_id="../escape",
            repo_path=None,
            greenfield_name=None,
            fleet_base=tmp_path,
        )


def test_route_item_new_repo_rejects_nonexistent_path(tmp_path) -> None:
    """new_repo route must fail loudly if --repo does not exist."""
    item = _item()
    with pytest.raises(RouteError) as exc_info:
        route_item(
            item,
            app_id="new-thing",
            repo_path=str(tmp_path / "does-not-exist"),
            greenfield_name=None,
            fleet_base=tmp_path / "fleet",
        )
    assert "does not exist" in str(exc_info.value)


def test_route_item_greenfield_rolls_back_on_register_failure(tmp_path) -> None:
    """If scaffold succeeds but register fails, the scaffold is cleaned up surgically."""
    from week_intake.captain_client import CaptainError
    from week_intake.scaffold import ScaffoldResult

    item = _item()
    item.kind = "greenfield"
    repo = tmp_path / "repos" / "fresh-thing"

    cleanup_called = {"yes": False}

    def fake_scaffold(*, path, name, description, ts):
        # Simulate the real scaffold creating directories AND tracking them.
        Path(path).mkdir(parents=True, exist_ok=True)
        py = Path(path) / "pyproject.toml"
        py.write_text("[project]\nname='x'", encoding="utf-8")
        result = ScaffoldResult(
            path=Path(path),
            created_files=[py],
            created_dirs=[Path(path)],
        )

        # Wrap cleanup to confirm router calls it.
        original_cleanup = result.cleanup

        def tracked_cleanup():
            cleanup_called["yes"] = True
            original_cleanup()

        result.cleanup = tracked_cleanup  # type: ignore[method-assign]
        return result

    with (
        patch("week_intake.router.scaffold_greenfield", side_effect=fake_scaffold),
        patch("week_intake.router.register_app_http", side_effect=CaptainError("api boom")),
    ):
        with pytest.raises(RouteError):
            route_item(
                item,
                app_id="fresh-thing",
                repo_path=str(repo),
                greenfield_name="fresh-thing",
                fleet_base=tmp_path / "fleet",
            )

    assert cleanup_called["yes"] is True
    # Surgical rollback: the dir we created is gone so retry is safe.
    assert not repo.exists()
    # Item state untouched on failure.
    assert item.state == "ready"
    assert item.captain_note_id is None


def test_route_item_greenfield_preserves_preexisting_empty_dir(tmp_path) -> None:
    """If user pre-created an empty target dir, rollback must NOT delete it."""
    from week_intake.captain_client import CaptainError

    item = _item()
    item.kind = "greenfield"
    repo = tmp_path / "repos" / "preexisting"
    repo.mkdir(parents=True)  # user created this empty dir; we must preserve it

    with (
        patch("week_intake.router.register_app_http", side_effect=CaptainError("api boom")),
    ):
        with pytest.raises(RouteError):
            route_item(
                item,
                app_id="preexisting",
                repo_path=str(repo),
                greenfield_name="preexisting",
                fleet_base=tmp_path / "fleet",
            )

    # The pre-existing empty dir survives rollback.
    assert repo.exists()
    # And it's empty again (no leftover scaffold files).
    assert list(repo.iterdir()) == []


def test_route_item_greenfield_rejects_non_slug_name(tmp_path) -> None:
    item = _item()
    item.kind = "greenfield"
    repo = tmp_path / "repos" / "x"
    with pytest.raises(RouteError):
        route_item(
            item,
            app_id="ok-slug",
            repo_path=str(repo),
            greenfield_name="../pwn",  # path traversal in name
            fleet_base=tmp_path / "fleet",
        )


def test_route_item_new_repo_registers_then_notes(tmp_path) -> None:
    item = _item()
    item.kind = "github_repo"
    repo = tmp_path / "repos" / "thing"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()  # new_repo route now requires a .git dir

    with patch("week_intake.router.register_app_http") as reg_mock:
        reg_mock.return_value = {"registered": True}
        updated = route_item(
            item,
            app_id="thing",
            repo_path=str(repo),
            greenfield_name=None,
            fleet_base=tmp_path / "fleet",
        )

    reg_mock.assert_called_once()
    kwargs = reg_mock.call_args.kwargs
    assert kwargs["app_id"] == "thing"
    # validate_repo_path resolves the path before passing to register.
    assert kwargs["repo_path"] == str(repo.resolve())
    assert updated.target.is_new_app is True
    assert (tmp_path / "fleet" / "thing" / "admiral_notes").exists()


def test_route_item_greenfield_scaffolds_then_registers(tmp_path) -> None:
    item = _item()
    item.kind = "greenfield"
    repo = tmp_path / "repos" / "fresh-thing"

    with (
        patch("week_intake.router.scaffold_greenfield") as scaf_mock,
        patch("week_intake.router.register_app_http") as reg_mock,
    ):
        scaf_mock.return_value = repo
        reg_mock.return_value = {"registered": True}
        updated = route_item(
            item,
            app_id="fresh-thing",
            repo_path=str(repo),
            greenfield_name="fresh-thing",
            fleet_base=tmp_path / "fleet",
        )

    scaf_mock.assert_called_once()
    reg_mock.assert_called_once()
    assert updated.target.greenfield_name == "fresh-thing"
    assert updated.state == "routed"


def test_route_item_propagates_register_errors(tmp_path) -> None:
    from week_intake.captain_client import CaptainError

    item = _item()
    repo = tmp_path / "repos" / "thing"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()

    with patch("week_intake.router.register_app_http", side_effect=CaptainError("api down")):
        with pytest.raises(RouteError) as exc_info:
            route_item(
                item,
                app_id="thing",
                repo_path=str(repo),
                greenfield_name=None,
                fleet_base=tmp_path / "fleet",
            )
    assert "register failed" in str(exc_info.value)
    # No partial state mutations on failure.
    assert item.state == "ready"
    assert item.captain_note_id is None


# ---------------------------------------------------------------------------
# Cycle A — app_mode propagation
# ---------------------------------------------------------------------------


def test_route_item_default_app_mode_is_observe_only(tmp_path) -> None:
    """Back-compat: omitted app_mode keeps the historical observe_only default."""
    item = _item()
    repo = tmp_path / "repos" / "thing"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()

    with patch("week_intake.router.register_app_http") as reg_mock:
        reg_mock.return_value = {"registered": True}
        route_item(
            item,
            app_id="thing",
            repo_path=str(repo),
            fleet_base=tmp_path / "fleet",
        )

    assert reg_mock.called
    kwargs = reg_mock.call_args.kwargs
    assert kwargs["mode"] == "observe_only"


def test_route_item_new_repo_propagates_autonomous_mode(tmp_path) -> None:
    """new_repo mode + --app-mode autonomous reaches register_app_http."""
    item = _item()
    repo = tmp_path / "repos" / "auto-thing"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()

    with patch("week_intake.router.register_app_http") as reg_mock:
        reg_mock.return_value = {"registered": True}
        route_item(
            item,
            app_id="auto-thing",
            repo_path=str(repo),
            fleet_base=tmp_path / "fleet",
            app_mode="autonomous",
        )

    kwargs = reg_mock.call_args.kwargs
    assert kwargs["mode"] == "autonomous"


def test_route_item_greenfield_propagates_autonomous_mode(tmp_path) -> None:
    """greenfield mode + --app-mode autonomous reaches register_app_http."""
    item = _item()
    item.kind = "greenfield"
    repo = tmp_path / "repos" / "fresh-auto"

    with (
        patch("week_intake.router.scaffold_greenfield") as scaf_mock,
        patch("week_intake.router.register_app_http") as reg_mock,
    ):
        scaf_mock.return_value = repo
        reg_mock.return_value = {"registered": True}
        route_item(
            item,
            app_id="fresh-auto",
            repo_path=str(repo),
            greenfield_name="fresh-auto",
            fleet_base=tmp_path / "fleet",
            app_mode="autonomous",
        )

    kwargs = reg_mock.call_args.kwargs
    assert kwargs["mode"] == "autonomous"


def test_route_item_existing_app_with_autonomous_raises(tmp_path) -> None:
    """existing_app + --app-mode autonomous fails loudly (captain has no promote)."""
    item = _item()
    fleet_base = tmp_path / "fleet"
    (fleet_base / "chad-agent").mkdir(parents=True)

    with pytest.raises(RouteError) as exc_info:
        route_item(
            item,
            app_id="chad-agent",
            fleet_base=fleet_base,
            app_mode="autonomous",
        )
    assert "existing-app" in str(exc_info.value)
    assert "promote" in str(exc_info.value)
    # No state mutation; admiral_note never written.
    assert item.state == "ready"
    assert item.captain_note_id is None


def test_route_item_existing_app_with_observe_only_works(tmp_path) -> None:
    """Existing app + observe_only (the default) works fine; mode is no-op there."""
    item = _item()
    fleet_base = tmp_path / "fleet"
    (fleet_base / "chad-agent").mkdir(parents=True)

    with patch("week_intake.router.register_app_http") as reg_mock:
        updated = route_item(
            item,
            app_id="chad-agent",
            fleet_base=fleet_base,
            app_mode="observe_only",
        )
    # existing-app routes never call register
    reg_mock.assert_not_called()
    assert updated.state == "routed"


def test_route_item_invalid_app_mode_raises(tmp_path) -> None:
    """Garbage app_mode value fails before any side effects."""
    item = _item()
    repo = tmp_path / "repos" / "thing"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()

    with pytest.raises(RouteError) as exc_info:
        route_item(
            item,
            app_id="thing",
            repo_path=str(repo),
            fleet_base=tmp_path / "fleet",
            app_mode="ludicrous",  # type: ignore[arg-type]
        )
    assert "invalid app_mode" in str(exc_info.value)
    assert item.state == "ready"


def test_cli_route_app_mode_flag_propagates(tmp_path, monkeypatch) -> None:
    """End-to-end: chad-week route --app-mode autonomous reaches register_app_http."""
    from unittest.mock import patch as _patch

    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path / "week"))
    from week_intake.cli import main
    from week_intake.protocol import WeekFolder
    from week_intake.types import RouteTarget, WeekItem

    week = "2026-W19"
    item = WeekItem(
        item_id="wk-001", week=week, raw_text="x", title="x",
        kind="wip", state="ready", confidence=0.9,
        target=RouteTarget(app_id="autobot"),
    )
    WeekFolder(week=week).upsert_item(item)

    repo = tmp_path / "repo-autobot"
    repo.mkdir()
    (repo / ".git").mkdir()

    with _patch("week_intake.router.register_app_http") as reg_mock:
        reg_mock.return_value = {"registered": True}
        rc = main([
            "route", "wk-001",
            "--week", week,
            "--app", "autobot",
            "--repo", str(repo),
            "--app-mode", "autonomous",
            "--format", "json",
        ])

    assert rc == 0
    assert reg_mock.called
    assert reg_mock.call_args.kwargs["mode"] == "autonomous"


def test_cli_route_app_mode_default_is_observe_only(tmp_path, monkeypatch) -> None:
    """End-to-end: chad-week route without --app-mode uses observe_only."""
    from unittest.mock import patch as _patch

    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path / "week"))
    from week_intake.cli import main
    from week_intake.protocol import WeekFolder
    from week_intake.types import RouteTarget, WeekItem

    week = "2026-W19"
    item = WeekItem(
        item_id="wk-002", week=week, raw_text="x", title="x",
        kind="wip", state="ready", confidence=0.9,
        target=RouteTarget(app_id="defaultbot"),
    )
    WeekFolder(week=week).upsert_item(item)

    repo = tmp_path / "repo-defaultbot"
    repo.mkdir()
    (repo / ".git").mkdir()

    with _patch("week_intake.router.register_app_http") as reg_mock:
        reg_mock.return_value = {"registered": True}
        rc = main([
            "route", "wk-002",
            "--week", week,
            "--app", "defaultbot",
            "--repo", str(repo),
            "--format", "json",
        ])

    assert rc == 0
    assert reg_mock.call_args.kwargs["mode"] == "observe_only"

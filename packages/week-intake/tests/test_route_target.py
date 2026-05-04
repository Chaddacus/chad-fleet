"""validate_route_target: structured check of WeekItem.target."""

from __future__ import annotations

from week_intake.route_target import validate_route_target
from week_intake.types import RouteTarget


def test_existing_app_ok_when_workspace_exists(tmp_path) -> None:
    (tmp_path / "chad-agent").mkdir()
    target = RouteTarget(app_id="chad-agent")
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert check.ok
    assert check.mode == "existing_app"
    assert check.missing == []


def test_existing_app_fails_when_workspace_missing(tmp_path) -> None:
    target = RouteTarget(app_id="ghost")
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert not check.ok
    assert check.mode == "existing_app"
    assert "existing_workspace" in check.missing
    assert "ghost" in check.reason


def test_existing_app_fails_on_invalid_slug(tmp_path) -> None:
    target = RouteTarget(app_id="../escape")
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert not check.ok
    assert "app_id" in check.missing


def test_new_repo_ok_with_git_worktree(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    target = RouteTarget(app_id="thing", repo_path=str(repo))
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert check.ok
    assert check.mode == "new_repo"


def test_new_repo_fails_without_git(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    target = RouteTarget(app_id="thing", repo_path=str(plain))
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert not check.ok
    assert check.mode == "new_repo"
    assert "repo_path" in check.missing


def test_new_repo_fails_without_app_id(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    target = RouteTarget(repo_path=str(repo))
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert not check.ok
    assert check.mode == "new_repo"
    assert "app_id" in check.missing


def test_greenfield_ok_with_all_fields(tmp_path) -> None:
    target = RouteTarget(
        app_id="fresh",
        greenfield_name="fresh",
        repo_path=str(tmp_path / "fresh"),
    )
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert check.ok
    assert check.mode == "greenfield"


def test_greenfield_fails_when_target_non_empty(tmp_path) -> None:
    target_dir = tmp_path / "non-empty"
    target_dir.mkdir()
    (target_dir / "x.txt").write_text("hi")
    target = RouteTarget(
        app_id="fresh",
        greenfield_name="fresh",
        repo_path=str(target_dir),
    )
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert not check.ok
    assert check.mode == "greenfield"
    assert "repo_path" in check.missing


def test_empty_target_fails_with_app_id_missing(tmp_path) -> None:
    target = RouteTarget()
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert not check.ok
    assert check.mode is None
    assert "app_id" in check.missing


def test_greenfield_fails_with_invalid_name(tmp_path) -> None:
    target = RouteTarget(
        app_id="ok",
        greenfield_name="../pwn",
        repo_path=str(tmp_path / "fresh"),
    )
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert not check.ok
    assert check.mode == "greenfield"
    assert "greenfield_name" in check.missing


def test_greenfield_priority_over_new_repo(tmp_path) -> None:
    """If greenfield_name is set, mode is greenfield even if repo_path looks like new_repo."""
    target = RouteTarget(
        app_id="x",
        greenfield_name="x",
        repo_path=str(tmp_path / "fresh"),
    )
    check = validate_route_target(target, captain_fleet_base=tmp_path)
    assert check.mode == "greenfield"

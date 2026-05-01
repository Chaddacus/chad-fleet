"""Tests for chad_captain.merge_facilitator."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chad_captain.merge_facilitator import (
    CmdResult,
    branch_exists_local,
    current_branch,
    delete_local_branch,
    ensure_captain_branch,
    format_pr_body,
    format_pr_title,
    get_pr_state,
    is_roadmap_complete,
    open_pull_request,
    push_captain_branch,
    refresh_base_branch,
)
from chad_captain.protocol import Roadmap, RoadmapSlice


# ---------------------------------------------------------------------------
# is_roadmap_complete
# ---------------------------------------------------------------------------


def _rm(*ids_with_status: tuple[str, str]) -> Roadmap:
    return Roadmap(
        app_id="t",
        slices=[
            RoadmapSlice(slice_id=i, objective=f"o-{i}", status=s)
            for i, s in ids_with_status
        ],
    )


def test_complete_when_all_done() -> None:
    assert is_roadmap_complete(_rm(("a", "done"), ("b", "done"))) is True


def test_complete_when_done_and_skipped() -> None:
    assert is_roadmap_complete(_rm(("a", "done"), ("b", "skipped"))) is True


def test_not_complete_when_queued() -> None:
    assert is_roadmap_complete(_rm(("a", "done"), ("b", "queued"))) is False


def test_not_complete_when_in_flight() -> None:
    assert is_roadmap_complete(_rm(("a", "in_flight"))) is False


def test_not_complete_when_blocked() -> None:
    """Blocked slices need admiral input — roadmap is NOT auto-complete."""
    assert is_roadmap_complete(_rm(("a", "done"), ("b", "blocked"))) is False


def test_not_complete_for_empty_roadmap() -> None:
    assert is_roadmap_complete(_rm()) is False


def test_not_complete_for_none() -> None:
    assert is_roadmap_complete(None) is False


# ---------------------------------------------------------------------------
# push_captain_branch / open_pull_request — subprocess wrapping
# ---------------------------------------------------------------------------


def test_push_calls_git_push_with_upstream(tmp_path: Path) -> None:
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        res = push_captain_branch(
            repo_path=str(tmp_path), branch="codex/captain-x",
        )
        assert res.ok is True
        cmd = mock_run.call_args.args[0]
        assert cmd == ["git", "push", "--set-upstream", "origin", "codex/captain-x"]


def test_push_surfaces_failure(tmp_path: Path) -> None:
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="auth denied\n",
        )
        res = push_captain_branch(
            repo_path=str(tmp_path), branch="codex/captain-x",
        )
        assert res.ok is False
        assert "exit 128" in res.summary
        assert "auth denied" in res.summary


def test_push_handles_missing_git(tmp_path: Path) -> None:
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("git: not found")
        res = push_captain_branch(
            repo_path=str(tmp_path), branch="codex/captain-x",
        )
        assert res.ok is False
        assert "binary not found" in res.summary


def test_push_handles_timeout(tmp_path: Path) -> None:
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=1)
        res = push_captain_branch(
            repo_path=str(tmp_path), branch="codex/captain-x", timeout=1,
        )
        assert res.ok is False
        assert "timeout" in res.summary


def test_open_pr_calls_gh_pr_create_draft(tmp_path: Path) -> None:
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/owner/repo/pull/42\n", stderr="",
        )
        res = open_pull_request(
            repo_path=str(tmp_path),
            base="main", head="codex/captain-x",
            title="title", body="body",
        )
        assert res.ok is True
        cmd = mock_run.call_args.args[0]
        assert cmd[:2] == ["gh", "pr"]
        assert "create" in cmd
        assert "--draft" in cmd
        assert "--base" in cmd and "main" in cmd
        assert "--head" in cmd and "codex/captain-x" in cmd
        assert "github.com" in res.stdout


def test_open_pr_recovers_when_pr_already_exists(tmp_path: Path) -> None:
    """gh prints the existing PR url and exits 1 — surface as success."""
    create_result = subprocess.CompletedProcess(
        args=[], returncode=1,
        stdout="",
        stderr="a pull request for branch already exists:\n"
               "https://github.com/owner/repo/pull/42\n",
    )
    view_result = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout='{"url":"https://github.com/owner/repo/pull/42","number":42,"state":"OPEN"}',
        stderr="",
    )
    with patch("chad_captain.merge_facilitator.subprocess.run",
               side_effect=[create_result, view_result]):
        res = open_pull_request(
            repo_path=str(tmp_path),
            base="main", head="codex/captain-x",
            title="t", body="b",
        )
        assert res.ok is True
        assert "already exists" in res.summary
        assert "github.com/owner/repo/pull/42" in res.stdout


def test_open_pr_supports_non_draft(tmp_path: Path) -> None:
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://x\n", stderr="",
        )
        open_pull_request(
            repo_path=str(tmp_path), base="main", head="codex/x",
            title="t", body="b", draft=False,
        )
        cmd = mock_run.call_args.args[0]
        assert "--draft" not in cmd


# ---------------------------------------------------------------------------
# ensure_captain_branch — branch auto-create with real git
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: Path, base: str = "main") -> Path:
    """Bootstrap a tiny throwaway git repo on the given branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", base], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-qm", "init"],
        cwd=repo, check=True,
    )
    return repo


def test_current_branch_returns_initial_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, base="main")
    assert current_branch(repo_path=str(repo)) == "main"


def test_branch_exists_local_true_for_existing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert branch_exists_local(repo_path=str(repo), branch="main") is True


def test_branch_exists_local_false_for_missing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert branch_exists_local(repo_path=str(repo), branch="codex/x") is False


def test_ensure_captain_branch_already_on_target_is_noop(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, base="codex/captain-x")
    res = ensure_captain_branch(
        repo_path=str(repo), branch="codex/captain-x", base_branch="main",
    )
    assert res.ok
    assert "already on" in res.summary
    assert current_branch(repo_path=str(repo)) == "codex/captain-x"


def test_ensure_captain_branch_checks_out_existing_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, base="main")
    # Pre-create the captain branch
    subprocess.run(
        ["git", "checkout", "-q", "-b", "codex/captain-x"],
        cwd=repo, check=True,
    )
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    assert current_branch(repo_path=str(repo)) == "main"

    res = ensure_captain_branch(
        repo_path=str(repo), branch="codex/captain-x", base_branch="main",
    )
    assert res.ok
    assert "checked out" in res.summary
    assert current_branch(repo_path=str(repo)) == "codex/captain-x"


def test_ensure_captain_branch_creates_from_base(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, base="main")
    assert branch_exists_local(repo_path=str(repo), branch="codex/captain-x") is False

    res = ensure_captain_branch(
        repo_path=str(repo), branch="codex/captain-x", base_branch="main",
    )
    assert res.ok
    assert "created" in res.summary
    assert current_branch(repo_path=str(repo)) == "codex/captain-x"
    # And it actually exists now
    assert branch_exists_local(repo_path=str(repo), branch="codex/captain-x") is True


def test_ensure_captain_branch_fails_when_base_missing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, base="main")
    res = ensure_captain_branch(
        repo_path=str(repo), branch="codex/x", base_branch="nonexistent",
    )
    assert res.ok is False
    assert "could not checkout base" in res.summary


# ---------------------------------------------------------------------------
# format_pr_body / format_pr_title
# ---------------------------------------------------------------------------


def test_pr_body_lists_every_slice() -> None:
    rm = Roadmap(
        app_id="myapp",
        slices=[
            RoadmapSlice(slice_id="S1", objective="Add README", status="done"),
            RoadmapSlice(slice_id="S2", objective="Fix bug", status="done"),
            RoadmapSlice(slice_id="S3", objective="Skip", status="skipped"),
        ],
        objective_summary="Lift docs + bug fixes",
    )
    body = format_pr_body(app_id="myapp", roadmap=rm)
    assert "myapp" in body
    assert "Lift docs + bug fixes" in body
    assert "S1" in body and "Add README" in body
    assert "S2" in body and "Fix bug" in body
    assert "S3" in body
    # Status markers
    assert "✅" in body  # done
    assert "⏭️" in body  # skipped


def test_pr_body_includes_scorecard_delta_when_provided() -> None:
    rm = _rm(("S1", "done"))
    before = {
        "aggregate": 0.50,
        "dimensions": [
            {"name": "docs_present", "score": 0.0},
            {"name": "tests_present", "score": 1.0},
        ],
    }
    after = {
        "aggregate": 0.75,
        "dimensions": [
            {"name": "docs_present", "score": 1.0},
            {"name": "tests_present", "score": 1.0},
        ],
    }
    body = format_pr_body(
        app_id="x", roadmap=rm,
        scorecard_before=before, scorecard_after=after,
    )
    assert "Scorecard delta" in body
    assert "docs_present" in body
    assert "0.50" in body and "0.75" in body
    assert "+0.25" in body or "0.2500" in body


def test_pr_body_omits_scorecard_when_not_provided() -> None:
    body = format_pr_body(app_id="x", roadmap=_rm(("S1", "done")))
    assert "Scorecard delta" not in body


def test_pr_body_includes_verify_cmd_when_provided() -> None:
    body = format_pr_body(
        app_id="x", roadmap=_rm(("S1", "done")),
        verify_cmd="make check",
    )
    assert "Verify gate" in body
    assert "make check" in body


def test_pr_body_truncates_long_objectives() -> None:
    long = "x" * 500
    rm = Roadmap(app_id="x", slices=[
        RoadmapSlice(slice_id="S1", objective=long, status="done"),
    ])
    body = format_pr_body(app_id="x", roadmap=rm)
    # 200-char cap with ellipsis
    assert "..." in body
    # No raw 500-char line dumped
    assert long not in body


def test_pr_title_under_70_chars() -> None:
    rm = Roadmap(
        app_id="some-very-long-app-id",
        slices=[RoadmapSlice(slice_id=f"S{i}", objective="o", status="done") for i in range(8)],
        objective_summary=("very " * 30 + "long objective"),
    )
    title = format_pr_title(app_id=rm.app_id, roadmap=rm)
    assert len(title) <= 70
    assert "captain" in title.lower()


# ---------------------------------------------------------------------------
# get_pr_state / refresh_base_branch / delete_local_branch — C4 helpers
# ---------------------------------------------------------------------------


def test_get_pr_state_returns_merged_on_merged_pr(tmp_path: Path) -> None:
    payload = (
        '{"state":"MERGED","number":42,'
        '"url":"https://github.com/owner/repo/pull/42",'
        '"mergeCommit":{"oid":"abc123"},"isDraft":false,'
        '"mergedAt":"2026-04-30T01:00:00Z"}'
    )
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=payload, stderr="",
        )
        state, raw = get_pr_state(repo_path=str(tmp_path), head="codex/x")
        assert state == "MERGED"
        assert raw["mergeCommit"]["oid"] == "abc123"


def test_get_pr_state_returns_open_on_open_pr(tmp_path: Path) -> None:
    payload = '{"state":"OPEN","number":1,"url":"https://x"}'
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=payload, stderr="",
        )
        state, _raw = get_pr_state(repo_path=str(tmp_path), head="codex/x")
        assert state == "OPEN"


def test_get_pr_state_returns_none_when_gh_fails(tmp_path: Path) -> None:
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not found\n",
        )
        state, raw = get_pr_state(repo_path=str(tmp_path), head="codex/x")
        assert state is None
        assert raw == {}


def test_get_pr_state_returns_none_on_bad_json(tmp_path: Path) -> None:
    with patch("chad_captain.merge_facilitator.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr="",
        )
        state, _raw = get_pr_state(repo_path=str(tmp_path), head="codex/x")
        assert state is None


def test_refresh_base_branch_runs_fetch_checkout_pull(tmp_path: Path) -> None:
    fetch = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    co = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    pull = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("chad_captain.merge_facilitator.subprocess.run",
               side_effect=[fetch, co, pull]) as mock_run:
        res = refresh_base_branch(repo_path=str(tmp_path), base_branch="main")
        assert res.ok is True
        commands = [call.args[0] for call in mock_run.call_args_list]
        assert commands[0][:2] == ["git", "fetch"]
        assert commands[1][:2] == ["git", "checkout"]
        assert commands[2][:3] == ["git", "pull", "--ff-only"]


def test_refresh_base_branch_short_circuits_on_fetch_failure(tmp_path: Path) -> None:
    fetch = subprocess.CompletedProcess(
        args=[], returncode=128, stdout="", stderr="auth failed\n",
    )
    with patch("chad_captain.merge_facilitator.subprocess.run",
               side_effect=[fetch]) as mock_run:
        res = refresh_base_branch(repo_path=str(tmp_path))
        assert res.ok is False
        # Did not progress past fetch
        assert mock_run.call_count == 1


def test_delete_local_branch_real_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, base="main")
    # Create + leave the captain branch
    subprocess.run(
        ["git", "checkout", "-q", "-b", "codex/captain-x"],
        cwd=repo, check=True,
    )
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    assert branch_exists_local(repo_path=str(repo), branch="codex/captain-x") is True

    res = delete_local_branch(repo_path=str(repo), branch="codex/captain-x")
    assert res.ok is True
    assert branch_exists_local(repo_path=str(repo), branch="codex/captain-x") is False


def test_delete_local_branch_refuses_when_on_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, base="main")
    subprocess.run(
        ["git", "checkout", "-q", "-b", "codex/captain-x"],
        cwd=repo, check=True,
    )
    res = delete_local_branch(repo_path=str(repo), branch="codex/captain-x")
    assert res.ok is False
    assert "refusing" in res.summary
    # Branch still there
    assert branch_exists_local(repo_path=str(repo), branch="codex/captain-x") is True


def test_delete_local_branch_idempotent_when_missing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, base="main")
    res = delete_local_branch(repo_path=str(repo), branch="codex/never-existed")
    assert res.ok is True
    assert "already deleted" in res.summary


def test_pr_title_includes_slice_count() -> None:
    rm = Roadmap(
        app_id="x",
        slices=[
            RoadmapSlice(slice_id="S1", objective="o", status="done"),
            RoadmapSlice(slice_id="S2", objective="o", status="done"),
            RoadmapSlice(slice_id="S3", objective="o", status="skipped"),
        ],
    )
    title = format_pr_title(app_id="x", roadmap=rm)
    # 2 done slices (skipped not counted)
    assert "2 slices" in title

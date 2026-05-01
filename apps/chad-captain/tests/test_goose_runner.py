"""Goose-runner tests with a fake goose binary.

We can't (and don't want to) talk to real codex-acp from unit tests, so the
runner is exercised against a tiny shell stub that pretends to be goose:
prints some lines, edits a file in the repo, exits.
"""

from __future__ import annotations

import os
import stat
import textwrap
from pathlib import Path

import pytest

from chad_captain.goose_runner import GooseRunner, _scan_cheats
from chad_captain.protocol import (
    AppWorkspace,
    CurrentSlice,
    read_slice_complete,
    write_current_slice,
)


# ---------------------------------------------------------------------------
# Fake goose factories
# ---------------------------------------------------------------------------


def _make_fake_goose(tmp_path: Path, *, behavior: str) -> Path:
    """Create a shell script that mimics `goose run` for the given behavior."""
    bin_path = tmp_path / "fake-goose.sh"

    scripts = {
        "edit_and_succeed": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # Simulate goose: edit README, print some tool-call lines, exit 0.
            echo "tool call: developer__shell"
            echo "tool result: ok"
            echo "Editing README.md"
            echo "marker from fake goose" >> README.md
            echo "tool call: developer__edit"
            echo "All done."
            exit 0
            """
        ),
        "fail_immediately": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            echo "tool call: developer__shell"
            echo "FATAL: something broke" >&2
            exit 7
            """
        ),
        "long_running": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            for i in 1 2 3; do
              echo "tool call: heartbeat $i"
              sleep 0.05
            done
            exit 0
            """
        ),
    }
    bin_path.write_text(scripts[behavior])
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)
    return bin_path


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# initial readme\n")
    os.system(f"git -C {repo} init -q")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t add .")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t commit -qm initial")
    return repo


def _make_runner(
    tmp_path: Path,
    repo: Path,
    fake_goose: Path,
    workspace_base: Path,
) -> GooseRunner:
    runtime = tmp_path / "goose-runtime"
    (runtime / "config" / "goose").mkdir(parents=True)
    (runtime / "config" / "goose" / "config.yaml").write_text("GOOSE_PROVIDER: stub\n")
    return GooseRunner(
        app_id="test-app",
        repo_path=repo,
        goose_runtime=runtime,
        goose_bin=str(fake_goose),
        workspace_base=workspace_base,
        poll_interval=0.05,
        log_dir=tmp_path / "logs",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _queue_slice(ws: AppWorkspace, slice_id: str = "s1") -> CurrentSlice:
    s = CurrentSlice(
        slice_id=slice_id,
        app_id="test-app",
        objective="Add a marker line to README",
        system_prompt="You are a careful coder.",
        user_prompt="Edit README.md to add a marker line at the bottom.",
        repo_path="/will/be/overridden",  # runner uses its configured repo_path
    )
    write_current_slice(ws, s)
    return s


def test_runner_executes_slice_and_writes_complete(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    fake = _make_fake_goose(tmp_path, behavior="edit_and_succeed")
    runner = _make_runner(tmp_path, repo, fake, workspace_base=tmp_path / "fleet")
    _queue_slice(runner.ws)

    executed = runner.tick()
    assert executed is True

    sc = read_slice_complete(runner.ws)
    assert sc is not None
    assert sc.slice_id == "s1"
    assert sc.goose_exit_code == 0
    assert "README.md" in sc.files_changed
    # Summary should contain something from the fake goose's stdout tail
    assert sc.summary
    # Diff should be captured
    assert sc.diff_path is not None
    assert Path(sc.diff_path).exists()

    # current_slice should be cleared so we don't re-execute
    assert not runner.ws.current_slice_path.exists()


def test_runner_propagates_nonzero_exit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    fake = _make_fake_goose(tmp_path, behavior="fail_immediately")
    runner = _make_runner(tmp_path, repo, fake, workspace_base=tmp_path / "fleet")
    _queue_slice(runner.ws)

    runner.tick()
    sc = read_slice_complete(runner.ws)
    assert sc is not None
    assert sc.goose_exit_code == 7
    assert sc.failure_tail is not None
    assert "FATAL" in sc.failure_tail


def test_runner_skips_when_completion_pending(tmp_path: Path) -> None:
    """If captain hasn't consumed the previous slice_complete, runner waits."""
    repo = _init_repo(tmp_path)
    fake = _make_fake_goose(tmp_path, behavior="edit_and_succeed")
    runner = _make_runner(tmp_path, repo, fake, workspace_base=tmp_path / "fleet")

    # Pre-existing slice_complete blocks new execution.
    runner.ws.slice_complete_path.write_text('{"slice_id":"prev","app_id":"test-app","duration_seconds":1,"goose_exit_code":0,"summary":"","files_changed":[]}')
    _queue_slice(runner.ws, slice_id="s2")

    executed = runner.tick()
    assert executed is False
    # current_slice still queued, slice_complete still untouched
    assert runner.ws.current_slice_path.exists()


def test_runner_idle_when_no_slice(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    fake = _make_fake_goose(tmp_path, behavior="edit_and_succeed")
    runner = _make_runner(tmp_path, repo, fake, workspace_base=tmp_path / "fleet")
    assert runner.tick() is False


def test_runner_writes_progress_events(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    fake = _make_fake_goose(tmp_path, behavior="edit_and_succeed")
    runner = _make_runner(tmp_path, repo, fake, workspace_base=tmp_path / "fleet")
    _queue_slice(runner.ws)
    runner.tick()

    progress = runner.ws.progress_path.read_text().splitlines()
    kinds = {line for line in progress if "slice_started" in line or "slice_completing" in line or "tool_call" in line}
    # At minimum slice_started + slice_completing must appear.
    assert any("slice_started" in line for line in progress)
    assert any("slice_completing" in line for line in progress)


def test_runner_emits_recovery_complete_on_inner_crash(tmp_path: Path) -> None:
    """If _execute_slice_inner raises an unhandled exception (e.g. goose
    crashes or post-state inspection fails), the runner MUST still write a
    SliceComplete(-9) and clear current_slice so the captain doesn't get
    stuck waiting forever on a dead slice. Regression test for the
    author-toolkit MCP-server crash that left started_at set + no
    SliceComplete written, blocking dispatch until the stall watchdog
    fired ~35min later."""
    from unittest.mock import patch
    from chad_captain.protocol import read_slice_complete

    repo = _init_repo(tmp_path)
    fake = _make_fake_goose(tmp_path, behavior="edit_and_succeed")
    runner = _make_runner(tmp_path, repo, fake, workspace_base=tmp_path / "fleet")
    _queue_slice(runner.ws)

    # Simulate an unhandled exception inside _execute_slice_inner
    with patch.object(
        runner, "_execute_slice_inner",
        side_effect=RuntimeError("simulated mid-execution crash"),
    ):
        runner.tick()  # must not raise

    sc = read_slice_complete(runner.ws)
    assert sc is not None
    assert sc.goose_exit_code == -9
    assert "RuntimeError" in (sc.summary or "")
    assert "simulated mid-execution crash" in (sc.failure_tail or "")
    # current_slice MUST be cleared so dispatch can re-queue
    assert not runner.ws.current_slice_path.exists()


def test_runner_skips_in_flight_slice(tmp_path: Path) -> None:
    """If current_slice has already been started_at-stamped, don't re-execute."""
    repo = _init_repo(tmp_path)
    fake = _make_fake_goose(tmp_path, behavior="edit_and_succeed")
    runner = _make_runner(tmp_path, repo, fake, workspace_base=tmp_path / "fleet")
    s = _queue_slice(runner.ws)
    s.started_at = "2026-04-30T15:00:00+00:00"
    write_current_slice(runner.ws, s)
    assert runner.tick() is False


# ---------------------------------------------------------------------------
# Cheat-detection
# ---------------------------------------------------------------------------


def test_scan_cheats_finds_assert_true_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    p = repo / "tests" / "test_x.py"
    p.write_text("def test_x():\n    assert True\n")
    flags = _scan_cheats(repo, ["tests/test_x.py"])
    assert any("assert-true-only" in f for f in flags)


def test_scan_cheats_finds_pytest_skip(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    p = repo / "tests" / "test_y.py"
    p.write_text("import pytest\n@pytest.mark.skip\ndef test_y():\n    pass\n")
    flags = _scan_cheats(repo, ["tests/test_y.py"])
    assert any("pytest-skip-added" in f for f in flags)


def test_scan_cheats_ignores_non_test_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    p = repo / "src.py"
    p.write_text("def f():\n    assert True\n")
    flags = _scan_cheats(repo, ["src.py"])
    assert flags == []

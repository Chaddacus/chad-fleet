"""S4 — end-to-end tracer.

Wires the captain validator + goose-runner + protocol against a fake-goose
shell stub and a tiny git repo. Exercises the full roundtrip:

    captain_tick (dispatch s1) → runner.tick (execute fake-goose) →
    captain_tick (validate, dispatch s2) → runner.tick → ...

After 3 slices the roadmap should be exhausted. Asserts:
  - captain_log.jsonl has 3 dispatch + 3 validate entries (all accept)
  - roadmap shows all slices done
  - progress.jsonl has slice_started + slice_completing for each slice
"""

from __future__ import annotations

import os
import stat
import textwrap
from pathlib import Path

import pytest

from chad_captain.goose_runner import GooseRunner
from chad_captain.protocol import (
    AppWorkspace,
    Roadmap,
    RoadmapSlice,
    read_captain_log,
    read_roadmap,
    write_roadmap,
)
from chad_captain.validator import captain_tick


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "tracer-repo"
    repo.mkdir()
    (repo / "README.md").write_text("# tracer\n")
    os.system(f"git -C {repo} init -q")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t add .")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t commit -qm initial")
    return repo


def _make_fake_goose(tmp_path: Path) -> Path:
    """Fake goose that always edits a file matching the slice user prompt
    keyword and exits cleanly. Reads its own --text arg to know which file."""
    bin_path = tmp_path / "fake-goose-tracer.sh"
    # The fake reads the user prompt from the LAST argument and writes a marker
    # to a file whose name matches one of the slice keywords.
    bin_path.write_text(textwrap.dedent(
        """\
        #!/usr/bin/env bash
        # Last arg is the user prompt
        prompt="${@: -1}"

        # Map slice keyword → file to edit
        case "$prompt" in
            *S1*|*"slice 1"*) echo "added line for slice 1" >> README.md ;;
            *S2*|*"slice 2"*) echo "// slice 2 marker" > slice2.txt ;;
            *S3*|*"slice 3"*) echo "<!-- slice 3 -->" > slice3.txt ;;
            *) echo "no match" >&2; exit 1 ;;
        esac

        echo "tool call: developer__edit"
        echo "tool result: file written"
        echo "Finished slice."
        exit 0
        """
    ))
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)
    return bin_path


def _three_slice_roadmap() -> Roadmap:
    return Roadmap(
        app_id="tracer-app",
        slices=[
            RoadmapSlice(slice_id="S1", objective="Tracer slice 1: edit README"),
            RoadmapSlice(slice_id="S2", objective="Tracer slice 2: create slice2.txt"),
            RoadmapSlice(slice_id="S3", objective="Tracer slice 3: create slice3.txt"),
        ],
    )


def test_tracer_three_slice_loop(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    fake_goose = _make_fake_goose(tmp_path)
    workspace_base = tmp_path / "fleet"

    runtime = tmp_path / "goose-runtime"
    (runtime / "config" / "goose").mkdir(parents=True)
    (runtime / "config" / "goose" / "config.yaml").write_text("GOOSE_PROVIDER: stub\n")

    runner = GooseRunner(
        app_id="tracer-app",
        repo_path=repo,
        goose_runtime=runtime,
        goose_bin=str(fake_goose),
        workspace_base=workspace_base,
        poll_interval=0.01,
        log_dir=tmp_path / "logs",
    )
    ws = runner.ws

    # Seed roadmap.
    write_roadmap(ws, _three_slice_roadmap())

    # Loop: alternate captain_tick (dispatch) → runner.tick (execute) → captain_tick (validate)
    # until roadmap is exhausted or we hit a safety cap.
    max_loops = 12
    statuses: list[str] = []
    for _ in range(max_loops):
        s1 = captain_tick(ws, repo_path=str(repo))
        statuses.append(s1 or "")
        runner.tick()
        s2 = captain_tick(ws, repo_path=str(repo))
        statuses.append(s2 or "")
        rm = read_roadmap(ws)
        if all(rs.status == "done" for rs in rm.slices):
            break

    # ---- Assertions ----
    rm = read_roadmap(ws)
    assert all(rs.status == "done" for rs in rm.slices), [
        (rs.slice_id, rs.status) for rs in rm.slices
    ]

    # Captain log should record dispatch + validate(accept) for each slice.
    log = read_captain_log(ws)
    dispatches = [e for e in log if e.kind == "dispatch"]
    validates = [e for e in log if e.kind == "validate"]
    assert len(dispatches) == 3, f"got {len(dispatches)} dispatches: {[e.slice_id for e in dispatches]}"
    assert len(validates) == 3, f"got {len(validates)} validates"
    # Either accept (delta >= 0.5pp) or soft_accept (small positive delta) is OK —
    # both close out the roadmap slice. Tiny test repos typically produce small deltas.
    assert all(v.verdict in ("accept", "soft_accept") for v in validates), [v.verdict for v in validates]

    # Each slice should have a slice_started + slice_completing in progress.jsonl.
    progress_lines = ws.progress_path.read_text().splitlines()
    started = sum(1 for line in progress_lines if "slice_started" in line)
    completing = sum(1 for line in progress_lines if "slice_completing" in line)
    assert started == 3
    assert completing == 3

    # Files were actually changed.
    assert (repo / "README.md").read_text().count("slice 1") >= 1
    assert (repo / "slice2.txt").exists()
    assert (repo / "slice3.txt").exists()

    # Final tick should report roadmap exhausted.
    final = captain_tick(ws, repo_path=str(repo))
    assert "exhausted" in final.lower() or "replan" in final.lower()


def test_tracer_handles_failed_first_attempt_then_succeeds(tmp_path: Path) -> None:
    """Fake goose fails the first time, succeeds the second.

    Expected: captain dispatches s1 → runner runs → fails → captain reject_retry +
    re-dispatches s1-retry → runner runs (this time succeeds) → captain accepts.
    """
    repo = _init_repo(tmp_path)

    # Fake goose that fails on first invocation, succeeds afterwards.
    state_file = tmp_path / "fake-state"
    bin_path = tmp_path / "fake-goose-flaky.sh"
    bin_path.write_text(textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        STATE="{state_file}"
        if [ ! -f "$STATE" ]; then
            echo "first attempt — failing" >&2
            echo "first" > "$STATE"
            exit 1
        fi
        echo "tool call: developer__edit"
        echo "succeeded on retry" >> README.md
        exit 0
        """
    ))
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)

    runtime = tmp_path / "goose-runtime"
    (runtime / "config" / "goose").mkdir(parents=True)
    (runtime / "config" / "goose" / "config.yaml").write_text("GOOSE_PROVIDER: stub\n")

    runner = GooseRunner(
        app_id="flaky-app",
        repo_path=repo,
        goose_runtime=runtime,
        goose_bin=str(bin_path),
        workspace_base=tmp_path / "fleet",
        poll_interval=0.01,
        log_dir=tmp_path / "logs",
    )
    ws = runner.ws

    write_roadmap(ws, Roadmap(
        app_id="flaky-app",
        slices=[RoadmapSlice(slice_id="F1", objective="Flaky slice — fails first, succeeds on retry")],
    ))

    # Cycle until F1 is done or capped.
    for _ in range(8):
        captain_tick(ws, repo_path=str(repo))
        runner.tick()
        captain_tick(ws, repo_path=str(repo))
        rm = read_roadmap(ws)
        if rm.slices[0].status == "done":
            break

    rm = read_roadmap(ws)
    assert rm.slices[0].status == "done", f"expected done, got {rm.slices[0].status}"

    log = read_captain_log(ws)
    verdicts = [e.verdict for e in log if e.kind == "validate"]
    # Should see at least one reject_retry then a successful verdict (accept or soft_accept).
    assert "reject_retry" in verdicts, verdicts
    assert any(v in ("accept", "soft_accept") for v in verdicts), verdicts

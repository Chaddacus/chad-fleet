#!/usr/bin/env python3
"""Captain soak test — drive captain_tick against a fake-goose repo loop and
verify steady-state behavior over N cycles.

Usage:
    soak_test.py [--cycles N] [--app-id captain-soak]

Builds an ephemeral fleet workspace + a tiny git repo, plants a roadmap with
4 slices, then alternates captain_tick (dispatch) → fake goose run
(write slice_complete) → captain_tick (validate). Asserts:

    1. Every slice reaches a terminal verdict (accept / soft_accept).
    2. captain_log.jsonl has one validate entry per slice.
    3. progress.jsonl growth matches the slice count.
    4. No slice is dispatched twice (slice_id-retry only on retry verdicts).

Exits 0 on success, non-zero with the failure cause on the first violation.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import NoReturn

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


def _die(msg: str) -> NoReturn:
    print(f"SOAK FAIL: {msg}", file=sys.stderr)
    sys.exit(2)


def _init_repo(tmp: Path) -> Path:
    repo = tmp / "soak-repo"
    repo.mkdir()
    (repo / "README.md").write_text("# soak\n")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                     "-c", "user.name=t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                     "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    return repo


def _make_fake_goose(tmp: Path) -> Path:
    bin_path = tmp / "fake-goose-soak.sh"
    bin_path.write_text(textwrap.dedent(
        """\
        #!/usr/bin/env bash
        prompt="${@: -1}"
        # Each slice writes a unique marker so the captain sees real diffs.
        marker="soak-$$-$RANDOM"
        echo "$marker" >> README.md
        echo "tool call: developer__edit"
        echo "Finished slice."
        exit 0
        """
    ))
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)
    return bin_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=8,
                         help="max alternating captain/runner cycles to run")
    parser.add_argument("--app-id", default="captain-soak")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="captain-soak-"))
    try:
        repo = _init_repo(tmp)
        fake_goose = _make_fake_goose(tmp)
        workspace_base = tmp / "fleet"

        runtime = tmp / "goose-runtime"
        (runtime / "config" / "goose").mkdir(parents=True)
        (runtime / "config" / "goose" / "config.yaml").write_text(
            "GOOSE_PROVIDER: stub\n"
        )

        runner = GooseRunner(
            app_id=args.app_id,
            repo_path=repo,
            goose_runtime=runtime,
            goose_bin=str(fake_goose),
            workspace_base=workspace_base,
            poll_interval=0.01,
            log_dir=tmp / "logs",
        )
        ws = runner.ws

        n_slices = 4
        write_roadmap(ws, Roadmap(
            app_id=args.app_id,
            slices=[
                RoadmapSlice(slice_id=f"S{i}", objective=f"Soak slice {i}: append marker")
                for i in range(1, n_slices + 1)
            ],
        ))

        # Drive cycles until done or capped.
        for cycle in range(args.cycles):
            captain_tick(ws, repo_path=str(repo))
            ran = runner.tick()
            captain_tick(ws, repo_path=str(repo))
            rm = read_roadmap(ws)
            print(f"cycle {cycle:02d}: ran={ran}  "
                   f"statuses={[(s.slice_id, s.status) for s in rm.slices]}")
            if all(s.status in ("done", "skipped", "blocked") for s in rm.slices):
                break

        rm = read_roadmap(ws)
        if not all(s.status == "done" for s in rm.slices):
            _die(f"some slices not done: {[(s.slice_id, s.status) for s in rm.slices]}")

        log = read_captain_log(ws)
        validates = [e for e in log if e.kind == "validate"]
        if len(validates) < n_slices:
            _die(f"expected ≥ {n_slices} validates, got {len(validates)}")
        bad = [v for v in validates if v.verdict not in ("accept", "soft_accept")]
        if bad:
            _die(f"unexpected verdicts: {[(v.slice_id, v.verdict) for v in bad]}")

        progress = ws.progress_path.read_text().splitlines()
        starts = sum(1 for ln in progress if "slice_started" in ln)
        completings = sum(1 for ln in progress if "slice_completing" in ln)
        if starts != n_slices or completings != n_slices:
            _die(f"progress mismatch: starts={starts} completings={completings} (want {n_slices})")

        # Spot-check no double-dispatch — slice_ids in dispatch entries should
        # be unique unless a -retry suffix appeared.
        dispatch_ids = [e.slice_id for e in log if e.kind == "dispatch"]
        if len(dispatch_ids) != len(set(dispatch_ids)):
            _die(f"duplicate dispatch ids: {dispatch_ids}")

        print(f"\nSOAK PASS: {n_slices} slices done; {len(validates)} validates "
               f"({sum(1 for v in validates if v.verdict == 'accept')} accept, "
               f"{sum(1 for v in validates if v.verdict == 'soft_accept')} soft_accept).")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

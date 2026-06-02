"""Dispatch: freeze a CaptainDossier and spawn a captain as an auto_runtime track.

Slice 1 wires the two REAL backends:
  - omni-mem: save_memory captures the dossier; its returned id is the
    dossier.omni_mem_thread_id drill-down handle.
  - auto_runtime: `init` creates the track the captain IS (HUB_ARCHITECTURE D7).

Both are driven via their supported invocations (docker exec / CLI) and parsed
from JSON — no fragile cross-tree imports. ContractKernel lease grant is deferred
to Slice 4; goose execution of the track to Slice 2.
"""
from __future__ import annotations

import json
import os
import subprocess

from .types import CaptainDossier, DiscoveryResult, TaskItem

_OMNI_CONTAINER = os.getenv("OMNI_MEM_CONTAINER", "kickstarter-omni-mem")
_WORKSPACE = os.getenv("OMNI_MEM_WORKSPACE", "chadsimon")
_AUTO_RUNTIME = os.path.expanduser("~/.claude/bin/auto_runtime.py")


def _save_dossier_thread(task: TaskItem, dossier_body: str) -> str:
    """Persist the dossier to omni-mem; return its id (the thread handle)."""
    out = subprocess.run(
        ["docker", "exec", _OMNI_CONTAINER, "omni-mem", "save_memory",
         "--workspaceId", _WORKSPACE,
         "--title", f"captain-dossier:{task.task_id}",
         "--text", dossier_body],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        raise RuntimeError(f"omni-mem save failed: {out.stderr.strip()[:200]}")
    data = json.loads(out.stdout)
    return data.get("id") or data.get("observationId") or "unknown"


def _init_track(task: TaskItem, cwd: str) -> str:
    """Create the captain's auto_runtime track; return track_id."""
    out = subprocess.run(
        ["python3", _AUTO_RUNTIME, "init",
         "--task", task.title, "--cwd", cwd, "--route", "R3"],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"auto_runtime init failed: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout)["track_id"]


def set_slice_state(track_id: str, state: str, evidence: str, node_id: str = "slice-1") -> bool:
    """Set a captain's slice state on its auto_runtime track, with evidence."""
    out = subprocess.run(
        ["python3", _AUTO_RUNTIME, "update-node",
         "--track-id", track_id, "--node-id", node_id,
         "--state", state, "--evidence", evidence[:500],
         "--acceptance-source", "captain"],
        capture_output=True, text=True, timeout=30,
    )
    return out.returncode == 0


def accept_slice(track_id: str, evidence: str, node_id: str = "slice-1") -> bool:
    """Mark a captain's slice accepted on its auto_runtime track, with evidence."""
    return set_slice_state(track_id, "accepted", evidence, node_id)


def freeze_and_spawn(
    task: TaskItem,
    disc: DiscoveryResult,
    resolved_clarifications: dict[str, str],
) -> CaptainDossier:
    """Freeze the dossier, persist it, and spawn the captain track."""
    cwd = disc.repo_path or os.path.expanduser("~/code/chad-fleet")
    # Build the dossier first (sans ids), persist it, then stamp the ids.
    dossier = CaptainDossier(
        task_id=task.task_id,
        omni_mem_thread_id="",          # filled after save
        task_brief=task.raw,
        repo_path=cwd,
        rlm_ref=disc.git_head,
        resolved_clarifications=resolved_clarifications,
        allowed_tools=[],               # net-new; registry is build-surface #2
    )
    dossier.omni_mem_thread_id = _save_dossier_thread(task, dossier.model_dump_json(indent=2))
    dossier.track_id = _init_track(task, cwd)
    return dossier

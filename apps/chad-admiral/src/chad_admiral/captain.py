"""Captain execution (Slice 2): run a dossier's work via the REAL fleet executor.

Boundary-clean reuse: the admiral does NOT import chad-captain's Python. It speaks
the captain's published file protocol (write current_slice.json) and invokes its
goose-runner CLI one-shot (`python -m chad_captain.goose_runner --max-iters 1`),
then reads slice_complete.json back. This is the "communicate via events/CLI, not
imports" rule from chad-fleet's README.

SAFETY: real-repo execution is gated by the caller. The tracer drives this only
against scratch repos; ContractKernel authority-tier gating of real-repo writes is
Slice 4.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

_CAPTAIN_APP = Path(os.path.expanduser("~/code/chad-fleet/apps/chad-captain"))
_FLEET_BASE = Path(os.path.expanduser(os.getenv("FLEET_BASE", "~/.chad/fleet/apps")))
# Prepared runtime: config/ symlinks to ~/.config so goose uses the real working
# provider (gemini_oauth); state/ and data/ stay sandboxed under the admiral app.
# (The captain's own goose-runtime ships a stale Codex/gpt-5.5 config that 400s.)
_GOOSE_RUNTIME = Path(os.path.expanduser("~/code/chad-fleet/apps/chad-admiral/.goose-runtime"))

_SYSTEM_PROMPT = (
    "You are a chad-fleet captain executing ONE slice. Honor ~/.claude/CLAUDE.md. "
    "Make the smallest change that satisfies the objective, then STOP IMMEDIATELY — "
    "do not re-verify, do not explore further, do not narrate. The moment the "
    "objective is met, end your turn.\n"
    "If — and only if — you genuinely cannot proceed without a human decision "
    "(a real direction/authority ambiguity, not mere difficulty), do NOT guess: "
    "write a single line `ESCALATE: <one-line question>` and stop immediately."
)

_ESCALATE_RE = re.compile(r"ESCALATE:\s*(.+)")
# The system prompt itself contains the literal template `ESCALATE: <one-line
# question>` and goose echoes the prompt into its log/summary, so a naive search
# matches the INSTRUCTION, not a real escalation. Reject the template echo.
_ESCALATE_ECHO_MARKERS = ("<one-line question>", "stop immediately")


def _detect_escalation(slice_complete: dict) -> str | None:
    """Return the captain's escalation question if it genuinely emitted one, else
    None. Scans the goose summary + slice log tail, skipping the system-prompt echo
    (the literal template) so the instruction text can't false-positive."""
    hay = slice_complete.get("summary") or ""
    log_path = slice_complete.get("log_path")
    if log_path and os.path.exists(log_path):
        try:
            hay += "\n" + open(log_path, errors="replace").read()[-8000:]
        except Exception:
            pass
    for m in _ESCALATE_RE.finditer(hay):
        q = m.group(1).strip().strip("`").strip()
        if not q or q.startswith("<") or any(mk in q for mk in _ESCALATE_ECHO_MARKERS):
            continue  # system-prompt echo, not a real escalation
        return q[:300]
    return None


def run_captain(repo_path: str, objective: str, *, max_turns: int = 3,
                timeout_seconds: int = 120) -> dict:
    """Execute one slice for `objective` in `repo_path` via the captain goose-runner.

    Returns the SliceComplete dict (summary, files_changed, goose_exit_code, ...).
    """
    app_id = "admiral-" + uuid.uuid4().hex[:8]
    ws_root = _FLEET_BASE / app_id
    ws_root.mkdir(parents=True, exist_ok=True)
    slice_id = "slc-" + uuid.uuid4().hex[:8]

    current = {
        "slice_id": slice_id,
        "app_id": app_id,
        "objective": objective,
        "title": objective[:80],
        "system_prompt": _SYSTEM_PROMPT,
        "user_prompt": objective,
        "repo_path": repo_path,
        "max_turns": max_turns,
        "max_tool_repetitions": 3,
        "timeout_seconds": timeout_seconds,
        "started_at": None,
    }
    (ws_root / "current_slice.json").write_text(json.dumps(current, indent=2))

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    cmd = [
        "uv", "run", "--with", "pydantic", "--python", "3.11",
        "python", "-m", "chad_captain.goose_runner",
        "--app", app_id, "--repo", repo_path,
        "--workspace-base", str(_FLEET_BASE),
        "--goose-runtime", str(_GOOSE_RUNTIME),
        "--max-iters", "1",
    ]
    subprocess.run(cmd, cwd=str(_CAPTAIN_APP), env=env,
                   capture_output=True, text=True, timeout=timeout_seconds + 120)

    complete_path = ws_root / "slice_complete.json"
    # goose_runner clears current_slice and writes slice_complete on completion.
    deadline = time.time() + 10
    while not complete_path.exists() and time.time() < deadline:
        time.sleep(0.5)
    if not complete_path.exists():
        raise RuntimeError(f"captain produced no slice_complete.json (app {app_id})")
    sc = json.loads(complete_path.read_text())
    # In-band escalation (S3): if goose emitted `ESCALATE: <q>`, surface it so the
    # admiral parks the slice instead of accepting it.
    sc["escalation"] = _detect_escalation(sc)
    return sc

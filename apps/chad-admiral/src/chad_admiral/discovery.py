"""Lightweight real discovery (Slice 1).

The admiral touches each task's repo to build a current picture and surface the
gaps it genuinely cannot resolve itself. This is the cheap RLM touchpoint from
HUB_ARCHITECTURE § 3 — resolve the repo, read git HEAD, note what's missing.
Deep research-subagent discovery is the production path (deferred).

Gap detection is deterministic: repo-not-found and no-branch-specified are facts
about the world, not judgment. Genuine product-judgment clarifications (LLM) are
a later enhancement.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .types import DiscoveryResult, TaskItem

_CODE_ROOT = Path(os.path.expanduser("~/code"))
_BRANCH_HINT = re.compile(r"\b(branch|off main|feature branch|main)\b", re.I)


def _resolve_repo(repo_hint: str) -> str | None:
    """Resolve a repo hint to an absolute path under ~/code, if it exists."""
    if not repo_hint:
        return None
    cand = Path(os.path.expanduser(repo_hint))
    if cand.is_absolute() and cand.is_dir():
        return str(cand)
    p = _CODE_ROOT / repo_hint
    return str(p) if p.is_dir() else None


def _git_head(repo_path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=8,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def discover(task: TaskItem) -> DiscoveryResult:
    repo_path = _resolve_repo(task.repo_hint)
    head = _git_head(repo_path) if repo_path else None
    gaps: list[str] = []
    if not task.repo_hint:
        gaps.append(f"Task \"{task.title}\" names no repo — which repo/path does it belong to?")
    elif repo_path is None:
        gaps.append(f"Can't find repo \"{task.repo_hint}\" under ~/code — where does \"{task.title}\" live?")
    if not _BRANCH_HINT.search(task.raw):
        gaps.append(f"\"{task.title}\": cut the captain off `main`, or an existing feature branch?")
    return DiscoveryResult(task_id=task.task_id, repo_path=repo_path, git_head=head, gaps=gaps)

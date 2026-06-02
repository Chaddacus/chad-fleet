"""Deterministic intake parsing: chat text -> list[TaskItem].

This is bounded control-flow over a known shape (a bulleted task list), so it is
code, not an LLM call (global rule: deterministic where the rules are derivable).
Each bullet is expected as roughly:  "- <repo>: <what to do>"  but we degrade
gracefully when the repo prefix is absent.
"""
from __future__ import annotations

import hashlib
import re

from .types import TaskItem

_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
# "repo: rest"  — repo is a leading token of word/.-_/ chars before the first colon
_REPO_PREFIX = re.compile(r"^([\w./-]+)\s*:\s*(.+)$")


def _task_id(raw: str) -> str:
    return "tsk-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def parse_task_list(text: str) -> list[TaskItem]:
    """Extract task items from a chat message. Returns [] if no bullets found."""
    items: list[TaskItem] = []
    for line in text.splitlines():
        m = _BULLET.match(line)
        if not m:
            continue
        body = m.group(1).strip()
        repo_hint, title = "", body
        pm = _REPO_PREFIX.match(body)
        if pm:
            repo_hint, title = pm.group(1).strip(), pm.group(2).strip()
        items.append(TaskItem(
            task_id=_task_id(body),
            title=title[:80],
            repo_hint=repo_hint,
            raw=body,
        ))
    return items


def looks_like_intake(text: str) -> bool:
    """True when the message carries a task list (>=1 bullet)."""
    return any(_BULLET.match(l) for l in text.splitlines())

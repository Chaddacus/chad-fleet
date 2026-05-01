"""Captain merge facilitator — branch push + PR creation on roadmap_complete.

The captain dispatches slices, goose-runner auto-commits per slice on a per-app
branch. This module closes the loop from "slices committed" to "PR ready for
admiral review":

    1. is_roadmap_complete()  — all slices done/skipped, none in flight
    2. push_captain_branch()  — git push -u origin <branch>
    3. format_pr_body()       — markdown with scorecard delta + slice manifest
    4. open_pull_request()    — gh pr create --draft
    5. auto_merge_pr()        — gh pr merge --squash --delete-branch (C5,
                                 gated by scorecard delta + branch protection)
    6. get_pr_state()         — poll for MERGED on subsequent ticks
    7. refresh_base_branch() / delete_local_branch() — post-merge cleanup

The captain self-merges when reg_app.auto_merge is set AND the aggregate
scorecard delta clears reg_app.auto_merge_min_delta (default 0.0). gh
itself enforces branch protection / required reviewers / required CI —
on any failure the captain logs an escalation and leaves the PR open
for admiral. This is the autonomous-fleet model: safeties are automated
gates, not a required human-in-the-loop checkpoint.

External tools required: `git` (always present) and `gh` (GitHub CLI, must be
authenticated). Both are subprocess-only — no library deps. Failures are
captured into the captain log as references on the roadmap_complete entry,
never raised, so a flaky network or missing gh auth doesn't crash the tick.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass

from chad_captain.protocol import Roadmap

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Roadmap-complete detection
# ---------------------------------------------------------------------------


def is_roadmap_complete(roadmap: Roadmap | None) -> bool:
    """A roadmap is 'complete' when every slice has reached a terminal state
    (done, skipped) and nothing is still in_flight or queued. Blocked slices
    do NOT count as complete — they need admiral input first."""
    if roadmap is None or not roadmap.slices:
        return False
    terminal = {"done", "skipped"}
    return all(s.status in terminal for s in roadmap.slices)


# ---------------------------------------------------------------------------
# Subprocess wrappers (git, gh) — captured + tolerated
# ---------------------------------------------------------------------------


@dataclass
class CmdResult:
    ok: bool
    summary: str
    stdout: str = ""
    stderr: str = ""


def _run(
    cmd: list[str],
    *,
    cwd: str,
    timeout: int = 60,
) -> CmdResult:
    try:
        proc = subprocess.run(  # noqa: S603 — local cmd, args list (no shell)
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CmdResult(ok=False, summary=f"timeout after {timeout}s")
    except FileNotFoundError as e:
        return CmdResult(ok=False, summary=f"binary not found: {e}")
    except OSError as e:
        return CmdResult(ok=False, summary=f"failed to launch: {e}")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-500:].strip()
        return CmdResult(
            ok=False,
            summary=f"exit {proc.returncode}: {tail[:300]}",
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    return CmdResult(
        ok=True, summary="ok", stdout=proc.stdout, stderr=proc.stderr,
    )


def current_branch(*, repo_path: str, timeout: int = 5) -> str | None:
    """Return the currently checked-out branch in repo_path, or None if
    detached HEAD or git fails."""
    res = _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path, timeout=timeout,
    )
    if not res.ok:
        return None
    name = res.stdout.strip()
    return None if name == "HEAD" else name


def branch_exists_local(*, repo_path: str, branch: str, timeout: int = 5) -> bool:
    """Cheap check: does this branch exist locally?"""
    res = _run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_path, timeout=timeout,
    )
    return res.ok


def ensure_captain_branch(
    *,
    repo_path: str,
    branch: str,
    base_branch: str = "main",
    timeout: int = 30,
) -> CmdResult:
    """Make sure ``branch`` is checked out in ``repo_path``.

    Idempotent rules:
      1. If we're already on ``branch`` → ok, no-op.
      2. If ``branch`` exists locally → checkout ``branch``.
      3. Otherwise → checkout ``base_branch`` first (so the new branch starts
         from a clean main snapshot), then create ``branch`` from there.

    Failures (dirty worktree blocking checkout, missing base_branch, etc.) are
    captured into CmdResult.summary, never raised. Caller decides whether to
    log + skip dispatch or escalate.
    """
    cur = current_branch(repo_path=repo_path, timeout=timeout)
    if cur == branch:
        return CmdResult(ok=True, summary=f"already on {branch}")

    if branch_exists_local(repo_path=repo_path, branch=branch, timeout=timeout):
        res = _run(["git", "checkout", branch], cwd=repo_path, timeout=timeout)
        if res.ok:
            return CmdResult(ok=True, summary=f"checked out existing {branch}")
        return res

    # Need to create the branch. Make sure base_branch is checked out first
    # so the new branch starts from the right ancestor.
    if cur != base_branch:
        co = _run(["git", "checkout", base_branch], cwd=repo_path, timeout=timeout)
        if not co.ok:
            return CmdResult(
                ok=False,
                summary=f"could not checkout base {base_branch}: {co.summary}",
            )
    create = _run(
        ["git", "checkout", "-b", branch],
        cwd=repo_path, timeout=timeout,
    )
    if create.ok:
        return CmdResult(ok=True, summary=f"created {branch} from {base_branch}")
    return create


def push_captain_branch(
    *,
    repo_path: str,
    branch: str,
    remote: str = "origin",
    timeout: int = 60,
) -> CmdResult:
    """Push the captain branch with --set-upstream. Idempotent — safe to call
    repeatedly (subsequent pushes are fast-forward)."""
    return _run(
        ["git", "push", "--set-upstream", remote, branch],
        cwd=repo_path,
        timeout=timeout,
    )


def get_pr_state(
    *,
    repo_path: str,
    head: str,
    timeout: int = 30,
) -> tuple[str | None, dict]:
    """Look up the PR state for ``head`` via ``gh pr view``.

    Returns ``(state, raw)`` where ``state`` is one of ``"OPEN"``, ``"MERGED"``,
    ``"CLOSED"`` or None on lookup failure (no PR, gh down, network error).
    Caller treats None as "unknown — try again next tick."
    """
    res = _run(
        ["gh", "pr", "view", head, "--json",
         "state,number,url,mergeCommit,isDraft,mergedAt"],
        cwd=repo_path, timeout=timeout,
    )
    if not res.ok:
        return None, {}
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None, {}
    state = data.get("state")
    if not isinstance(state, str):
        return None, data
    return state.upper(), data


def auto_merge_pr(
    *,
    repo_path: str,
    head: str,
    method: str = "squash",
    delete_branch: bool = True,
    timeout: int = 120,
) -> CmdResult:
    """Squash-merge a PR via ``gh pr merge``. Captain-side autonomy gate.

    Failure modes that must NOT crash the loop:
      - branch protection requires reviewers → exit 1, stderr explains
      - merge conflicts → exit 1
      - required CI checks pending → exit 1
      - gh auth / network down → FileNotFoundError or exit != 0
    Caller (validator) treats any non-ok result as escalation_raised and
    leaves the PR open for admiral review. Idempotent — gh detects
    already-merged PRs and exits non-zero with a recoverable message.
    """
    if method not in ("squash", "merge", "rebase"):
        return CmdResult(ok=False, summary=f"invalid merge method: {method}")
    cmd = ["gh", "pr", "merge", head, f"--{method}"]
    if delete_branch:
        cmd.append("--delete-branch")
    return _run(cmd, cwd=repo_path, timeout=timeout)


def delete_local_branch(
    *,
    repo_path: str,
    branch: str,
    timeout: int = 10,
) -> CmdResult:
    """Force-delete a local branch. Used after a captain PR merges to drop
    the stale local copy so the next cycle starts from new main.

    Refuses if we're currently on ``branch`` (caller must checkout base first).
    Returns ok=True if branch already gone (already cleaned up = success).
    """
    cur = current_branch(repo_path=repo_path, timeout=timeout)
    if cur == branch:
        return CmdResult(
            ok=False,
            summary=f"refusing to delete branch we're on ({branch})",
        )
    if not branch_exists_local(repo_path=repo_path, branch=branch, timeout=timeout):
        return CmdResult(ok=True, summary=f"already deleted ({branch})")
    return _run(
        ["git", "branch", "-D", branch],
        cwd=repo_path, timeout=timeout,
    )


def refresh_base_branch(
    *,
    repo_path: str,
    base_branch: str = "main",
    remote: str = "origin",
    timeout: int = 60,
) -> CmdResult:
    """Fetch + checkout + fast-forward the base branch.

    Idempotent. Used after a captain PR merges so the next cycle starts
    from the freshly-merged main. We deliberately do NOT delete the
    captain branch — admiral may still want it for diff archaeology.
    """
    fetch = _run(
        ["git", "fetch", remote, base_branch],
        cwd=repo_path, timeout=timeout,
    )
    if not fetch.ok:
        return fetch
    co = _run(["git", "checkout", base_branch], cwd=repo_path, timeout=timeout)
    if not co.ok:
        return co
    pull = _run(
        ["git", "pull", "--ff-only", remote, base_branch],
        cwd=repo_path, timeout=timeout,
    )
    if not pull.ok:
        return pull
    return CmdResult(
        ok=True, summary=f"refreshed {base_branch} from {remote}/{base_branch}",
    )


def open_pull_request(
    *,
    repo_path: str,
    base: str,
    head: str,
    title: str,
    body: str,
    draft: bool = True,
    timeout: int = 60,
) -> CmdResult:
    """Open a PR via `gh pr create`. Returns CmdResult with PR url in stdout
    on success. If a PR for this branch already exists, gh prints the URL to
    stderr and exits 1 — we surface that as a non-error 'already exists' state
    by running `gh pr view` as a fallback."""
    cmd = [
        "gh", "pr", "create",
        "--base", base,
        "--head", head,
        "--title", title,
        "--body", body,
    ]
    if draft:
        cmd.append("--draft")
    res = _run(cmd, cwd=repo_path, timeout=timeout)
    if res.ok:
        return res

    # If PR already exists, gh prints the existing PR URL on stderr. Detect
    # and treat as success — we don't want a duplicate-PR error to look like
    # a failure to the admiral.
    if "already exists" in (res.stderr + res.stdout).lower():
        view = _run(
            ["gh", "pr", "view", head, "--json", "url,number,state"],
            cwd=repo_path, timeout=timeout,
        )
        if view.ok:
            try:
                data = json.loads(view.stdout)
                return CmdResult(
                    ok=True,
                    summary=f"PR already exists (#{data.get('number')}, {data.get('state')})",
                    stdout=data.get("url", ""),
                )
            except json.JSONDecodeError:
                pass
    return res


# ---------------------------------------------------------------------------
# PR body formatting
# ---------------------------------------------------------------------------


def format_pr_body(
    *,
    app_id: str,
    roadmap: Roadmap,
    scorecard_before: dict | None = None,
    scorecard_after: dict | None = None,
    verify_cmd: str | None = None,
) -> str:
    """Build a markdown PR body documenting the captain roadmap.

    Sections:
      - Roadmap objective
      - Slice manifest (id, status, objective, files changed if available)
      - Scorecard delta table (when before/after provided)
      - Verify gate (when verify_cmd configured)
      - Auto-generated footer
    """
    lines: list[str] = []
    lines.append(f"## Captain Roadmap — `{app_id}`")
    lines.append("")
    if roadmap.objective_summary:
        lines.append(f"**Objective:** {roadmap.objective_summary}")
        lines.append("")

    # Slice manifest
    lines.append("### Slices")
    lines.append("")
    for s in roadmap.slices:
        marker = {
            "done": "✅",
            "skipped": "⏭️",
            "blocked": "🛑",
            "queued": "⏳",
            "in_flight": "🔄",
        }.get(s.status, "?")
        obj = s.objective.replace("\n", " ").strip()
        if len(obj) > 200:
            obj = obj[:197] + "..."
        lines.append(f"- {marker} **{s.slice_id}** ({s.status}) — {obj}")
    lines.append("")

    # Scorecard delta
    if scorecard_before and scorecard_after:
        lines.append("### Scorecard delta")
        lines.append("")
        lines.append("| Dimension | Before | After | Δ |")
        lines.append("|---|---:|---:|---:|")
        before_dims = {d["name"]: d["score"] for d in scorecard_before.get("dimensions", [])}
        after_dims = {d["name"]: d["score"] for d in scorecard_after.get("dimensions", [])}
        all_names = sorted(set(before_dims) | set(after_dims))
        for name in all_names:
            b = before_dims.get(name, 0.0)
            a = after_dims.get(name, 0.0)
            delta = a - b
            sign = "+" if delta > 0 else ("" if delta == 0 else "")
            lines.append(f"| `{name}` | {b:.2f} | {a:.2f} | {sign}{delta:+.2f} |")
        lines.append("")
        agg_b = scorecard_before.get("aggregate", 0.0)
        agg_a = scorecard_after.get("aggregate", 0.0)
        lines.append(f"**Aggregate: {agg_b:.4f} → {agg_a:.4f} ({(agg_a - agg_b):+.4f})**")
        lines.append("")

    # Verify gate
    if verify_cmd:
        lines.append("### Verify gate")
        lines.append("")
        lines.append(f"All accepted slices passed `{verify_cmd}` (per-app verify_cmd).")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "🤖 Generated by [chad-captain](https://github.com/Chaddacus/chad-fleet). "
        "Each `captain-runner: <slice_id>` commit is one slice; review the "
        "per-commit diff before squash-merging."
    )
    return "\n".join(lines)


def format_pr_title(*, app_id: str, roadmap: Roadmap, max_len: int = 70) -> str:
    """Short title — defaults to under 70 chars. Captures app + slice count + headline."""
    n_slices = len([s for s in roadmap.slices if s.status == "done"])
    suffix = f" ({n_slices} slices)"
    obj = roadmap.objective_summary or "scorecard cleanup"
    prefix = f"feat({app_id}): captain — "
    # Available room for the objective text after prefix and suffix.
    room = max_len - len(prefix) - len(suffix)
    if room < 8:
        # Pathologically long app_id — drop the suffix and just truncate.
        full = (prefix + obj)[: max_len - 3] + "..."
        return full
    if len(obj) > room:
        obj = obj[: room - 3] + "..."
    return prefix + obj + suffix

"""Slice 4: ContractKernel authority gate for captain execution.

Replaces the hardcoded scratch-only safelist. Before a captain may mutate a repo,
the admiral asks the AgentOps ContractKernel (TypeScript) for a verdict via the
`authority` CLI shim:
  - lease must be grantable (no conflicting active CaptaincyLease), AND
  - the policy hook verdict's action+overrideTier must be autonomously satisfiable.

Tier policy:
  T0_ALLOW            -> execute autonomously
  T1_ADMIRAL_OVERRIDE -> execute (admiral authority), logged
  T2/T3 or DENY/REQUIRE_HUMAN_APPROVAL or lease conflict -> BLOCK + escalate (human gate)

This is fail-closed: any shim error or unparseable verdict blocks execution.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

_AGENTOPS = os.path.expanduser("~/automation_architecture")
_SHIM = os.path.join(_AGENTOPS, "bin", "authority.mjs")

_AUTONOMOUS_TIERS = {"T0_ALLOW", "T1_ADMIRAL_OVERRIDE"}


@dataclass
class AuthorityVerdict:
    allowed: bool
    tier: str
    action: str
    reason: str

    @property
    def needs_human(self) -> bool:
        return not self.allowed


def check_authority(repo_path: str, hook: str = "before_command",
                    path: str | None = None) -> AuthorityVerdict:
    """Ask the ContractKernel whether a captain may execute against repo_path."""
    cmd = ["node", _SHIM, "--repo", repo_path, "--hook", hook]
    if path is not None:
        cmd += ["--path", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:  # fail-closed
        return AuthorityVerdict(False, "T2_HUMAN_ELEVATED", "DENY", f"authority shim error: {e}")

    if out.returncode != 0:
        return AuthorityVerdict(False, "T2_HUMAN_ELEVATED", "DENY",
                                f"authority shim exit {out.returncode}: {out.stderr.strip()[:160]}")
    try:
        v = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return AuthorityVerdict(False, "T2_HUMAN_ELEVATED", "DENY", "unparseable authority verdict")

    granted = bool(v.get("granted"))
    action = v.get("action", "DENY")
    tier = v.get("overrideTier", "T2_HUMAN_ELEVATED")
    allowed = granted and action == "ALLOW" and tier in _AUTONOMOUS_TIERS
    return AuthorityVerdict(allowed=allowed, tier=tier, action=action,
                            reason=v.get("reason", ""))


def _changed_paths(repo_path: str, base_ref: str | None = None) -> list[str]:
    """All paths the slice touched: committed diff since base_ref (the captain
    auto-commits, so changes land in commits and leave a clean tree) UNION the
    current working-tree status. Independent of the executor's own files_changed
    accounting — that proved unreliable (it missed a `.env` goose wrote).
    """
    paths: set[str] = set()
    if base_ref:
        d = subprocess.run(
            ["git", "-C", repo_path, "diff", "--name-only", base_ref, "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        if d.returncode == 0:
            paths.update(p.strip() for p in d.stdout.splitlines() if p.strip())
    st = subprocess.run(
        ["git", "-C", repo_path, "status", "--porcelain", "--ignored", "-uall"],
        capture_output=True, text=True, timeout=15,
    )
    if st.returncode == 0:
        for line in st.stdout.splitlines():
            if len(line) > 3:
                p = line[3:].strip().strip('"')
                if " -> " in p:
                    p = p.split(" -> ", 1)[1]
                paths.add(p)
    return sorted(paths)


_MAX_SCAN_BYTES = 1_000_000  # don't slurp giant/binary files into the scanner


def _read_changed_content(repo_path: str, rel: str) -> str | None:
    """Read a changed file's content for the content scan. Returns None if the
    file is gone, binary, or too large. NEVER called for a path the before_file_
    write policy denied (e.g. `.env`), so this honors the no-read-.env rule."""
    full = os.path.join(repo_path, rel)
    try:
        if not os.path.isfile(full) or os.path.getsize(full) > _MAX_SCAN_BYTES:
            return None
        with open(full, "rb") as fh:
            raw = fh.read()
        if b"\x00" in raw:  # binary
            return None
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def scan_secret_content(repo_path: str, path: str, content: str) -> AuthorityVerdict:
    """Content companion to the path gate: pipe a changed file's content through
    the ContractKernel's evaluateSecurity (hard-deny secret VALUE patterns)."""
    try:
        out = subprocess.run(
            ["node", _SHIM, "--repo", repo_path, "--path", path, "--security-scan"],
            input=content, capture_output=True, text=True, timeout=30,
        )
    except Exception as e:  # fail-closed
        return AuthorityVerdict(False, "T3_POLICY_CHANGE_REQUIRED", "DENY", f"security shim error: {e}")
    if out.returncode != 0:
        return AuthorityVerdict(False, "T3_POLICY_CHANGE_REQUIRED", "DENY",
                                f"security shim exit {out.returncode}: {out.stderr.strip()[:160]}")
    try:
        v = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return AuthorityVerdict(False, "T3_POLICY_CHANGE_REQUIRED", "DENY", "unparseable security verdict")
    action = v.get("action", "DENY")
    tier = v.get("overrideTier", "T3_POLICY_CHANGE_REQUIRED")
    return AuthorityVerdict(allowed=(action == "ALLOW"), tier=tier, action=action,
                            reason=v.get("reason", ""))


def scan_changed_files(repo_path: str, files: list[str] | None = None,
                       base_ref: str | None = None) -> list[str]:
    """Post-execution gate: scan everything the slice touched (committed diff
    since base_ref + working tree). Two layers per file, fail-closed:
      1. PATH gate — before_file_write policy (e.g. `.env`/secret-path at T3).
      2. CONTENT gate — for path-allowed files only, scan the file body for
         hard-deny secret VALUE patterns (an API key dropped into config.py).
    Path-denied files are NEVER content-read, honoring the no-read-.env rule.
    `files` is unioned in. [] = clean.
    """
    candidates = set(_changed_paths(repo_path, base_ref)) | set(files or [])
    violations: list[str] = []
    for f in sorted(candidates):
        # 1. path gate — match on basename too (the rule keys on path ".env")
        path_denied = False
        for probe in {f, f.rsplit("/", 1)[-1]}:
            v = check_authority(repo_path, hook="before_file_write", path=probe)
            if not v.allowed:
                violations.append(f"{f}: {v.action}/{v.tier} ({v.reason})")
                path_denied = True
                break
        if path_denied:
            continue  # do not read content of a path-denied file (e.g. .env)
        # 2. content gate — only for path-allowed files
        content = _read_changed_content(repo_path, f)
        if content is None:
            continue
        cv = scan_secret_content(repo_path, f, content)
        if not cv.allowed:
            violations.append(f"{f}: secret-content {cv.action}/{cv.tier} ({cv.reason})")
    return violations

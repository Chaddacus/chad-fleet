"""Module-boundary guard: the shippable hub must not couple to proprietary captain internals.

The product boundary (see /Users/chadsimon/.claude/plans/joyful-hugging-pearl.md):

  - Shippable hub modules: chad-admiral, chad-dashboard, state-aggregator, genui-renderer,
    hub-contracts, email-mcp, calendar-mcp, deploy.
  - Proprietary execution engine (NOT shipped): captain-core, captain-playbooks, chad-captain.

The admiral dispatches captains over the PUBLISHED protocol (subprocess to `auto_runtime`,
dossier files) — a boundary, not an import. This guard fails if any hub module *imports* a
proprietary package or declares it as a dependency, so the OSS/proprietary split stays
verifiable now instead of surfacing as hidden coupling at packaging time (Codex review #8).

Run: `python3 scripts/check_boundaries.py`  (exit 0 = clean, 1 = violations).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Hub (shippable) module source roots that must stay free of proprietary imports.
HUB_SOURCE_ROOTS = [
    "apps/chad-admiral/src",
    "apps/chad-dashboard/app",
    "apps/chad-dashboard/features",
    "apps/chad-dashboard/lib",
    "packages/state-aggregator/src",
    "packages/genui-renderer/src",
    "packages/hub-contracts",
    "packages/email-mcp/src",
    "packages/calendar-mcp/src",
]

# Hub package manifests that must not declare a dependency on proprietary packages.
HUB_MANIFESTS = [
    "apps/chad-admiral/pyproject.toml",
    "packages/state-aggregator/pyproject.toml",
    "packages/genui-renderer/package.json",
    "apps/chad-dashboard/package.json",
    "packages/email-mcp/pyproject.toml",
    "packages/calendar-mcp/pyproject.toml",
]

# Proprietary import names (python module names + npm package names).
PROPRIETARY = ["captain_core", "chad_captain", "captain_playbooks", "captain-core", "chad-captain"]

# Python/TS import statements only — NOT arbitrary string mentions (CLI command strings that
# cross the protocol boundary, e.g. "chad-captain" as an argv token, are allowed).
_PY_IMPORT = re.compile(
    r"^\s*(?:from\s+(?P<f>[\w.]+)|import\s+(?P<i>[\w.]+))", re.MULTILINE
)
_TS_IMPORT = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?[^'"]*from\s+['"](?P<m>[^'"]+)['"]"""
)

_SKIP_DIRS = {"node_modules", ".next", "__pycache__", ".venv", "dist", "build"}


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root in HUB_SOURCE_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in {".py", ".ts", ".tsx", ".mjs"}:
                if not any(part in _SKIP_DIRS for part in p.parts):
                    files.append(p)
    return files


def _proprietary_hit(module: str) -> bool:
    head = module.split(".")[0].split("/")[0]
    return head in PROPRIETARY or module in PROPRIETARY


def find_violations() -> list[str]:
    violations: list[str] = []

    for path in _iter_source_files():
        text = path.read_text(errors="ignore")
        rel = path.relative_to(REPO_ROOT)
        if path.suffix == ".py":
            for m in _PY_IMPORT.finditer(text):
                mod = m.group("f") or m.group("i") or ""
                if _proprietary_hit(mod):
                    violations.append(f"{rel}: imports proprietary module '{mod}'")
        else:
            for m in _TS_IMPORT.finditer(text):
                if _proprietary_hit(m.group("m")):
                    violations.append(f"{rel}: imports proprietary module '{m.group('m')}'")

    for manifest in HUB_MANIFESTS:
        mpath = REPO_ROOT / manifest
        if not mpath.exists():
            continue
        text = mpath.read_text(errors="ignore")
        for name in PROPRIETARY:
            # dependency-style reference: name as a quoted/listed dep token
            if re.search(rf'["\']?{re.escape(name)}["\']?\s*[:=]', text) or re.search(
                rf'["\']{re.escape(name)}["\']', text
            ):
                violations.append(f"{manifest}: declares dependency on proprietary '{name}'")

    return sorted(set(violations))


def main() -> int:
    violations = find_violations()
    if violations:
        print("BOUNDARY VIOLATIONS — hub modules must not import proprietary captain internals:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("boundary OK: no hub module imports/declares proprietary captain internals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

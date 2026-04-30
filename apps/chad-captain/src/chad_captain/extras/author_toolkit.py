"""Author Toolkit — app-specific dimensions.

Dimensions:
    sentinel_present            — author-toolkit ships a sentinel/heartbeat file
    typescript_typecheck_clean  — `npx tsc --noEmit` exits 0 if a tsconfig exists
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from chad_captain.scorecard import DimensionScore

logger = logging.getLogger(__name__)


def sentinel_present(repo: Path) -> DimensionScore:
    """Author Toolkit's daily-run sentinel file. Its presence is a smoke
    signal that scheduled jobs are still firing."""
    for candidate in (
        repo / ".sentinel",
        repo / "sentinel.json",
        repo / "ops" / "sentinel.json",
    ):
        if candidate.exists():
            return DimensionScore(
                name="sentinel_present",
                score=1.0,
                rationale=f"sentinel found at {candidate.relative_to(repo)}",
            )
    return DimensionScore(
        name="sentinel_present",
        score=0.0,
        rationale="no sentinel file found",
    )


def typescript_typecheck_clean(repo: Path) -> DimensionScore:
    """If a tsconfig exists, run `npx tsc --noEmit` and surface the result."""
    tsconfigs = list(repo.glob("tsconfig*.json"))
    if not tsconfigs:
        return DimensionScore(
            name="typescript_typecheck_clean",
            score=1.0,
            rationale="no tsconfig — TypeScript not used",
        )
    try:
        proc = subprocess.run(
            ["npx", "--no-install", "tsc", "--noEmit"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        return DimensionScore(
            name="typescript_typecheck_clean",
            score=0.5,
            rationale=f"could not invoke tsc: {e}",
        )
    if proc.returncode == 0:
        return DimensionScore(
            name="typescript_typecheck_clean",
            score=1.0,
            rationale="tsc --noEmit clean",
        )
    error_lines = (proc.stdout + proc.stderr).strip().splitlines()
    return DimensionScore(
        name="typescript_typecheck_clean",
        score=0.0,
        rationale=f"{len(error_lines)} typecheck error line(s)",
        detail={"first_errors": error_lines[:5]},
    )


__all__ = ["sentinel_present", "typescript_typecheck_clean"]

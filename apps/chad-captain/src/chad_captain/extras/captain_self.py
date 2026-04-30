"""Captain self — dimensions for the captain bootstrapping itself.

Dimension:
    test_count_growing — running test count exceeds the captain's own bar (100)
"""

from __future__ import annotations

import re
from pathlib import Path

from chad_captain.scorecard import DimensionScore

MIN_TEST_COUNT = 100
TEST_FUNC_PATTERN = re.compile(r"^\s*def test_\w+\s*\(", re.MULTILINE)


def captain_test_count_growing(repo: Path) -> DimensionScore:
    captain_tests = repo / "apps" / "chad-captain" / "tests"
    if not captain_tests.is_dir():
        return DimensionScore(
            name="test_count_growing",
            score=0.5,
            rationale="captain tests directory not at expected path",
        )
    total = 0
    for path in captain_tests.rglob("test_*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += len(TEST_FUNC_PATTERN.findall(text))
    score = min(1.0, total / MIN_TEST_COUNT)
    return DimensionScore(
        name="test_count_growing",
        score=score,
        rationale=f"{total} captain tests (target ≥ {MIN_TEST_COUNT})",
        detail={"test_count": total},
    )


__all__ = ["captain_test_count_growing"]

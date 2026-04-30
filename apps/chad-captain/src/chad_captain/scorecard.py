"""Compliance rubric scorer — heuristic baseline for every app.

The captain's validator needs a generic, fast, deterministic way to detect
when a slice regressed something basic (tests deleted, TODOs added, secrets
checked in). This is the floor that runs on every slice — app-specific
rubrics layer on top in S7.

Pure stdlib, no LLM, no network. Each dimension is scored 0..1 with a
short rationale. The aggregate score is the unweighted mean. ``score_delta``
(before, after) returns the percentage-point delta the captain feeds into
the validator's ``score_delta`` callback.

The seven baseline dimensions:
    tests_present       — any test files exist in the repo
    tests_recent        — recent commits touch test files
    todo_pressure       — count of TODO/FIXME/XXX markers in source
    skip_pressure       — count of @pytest.mark.skip / @skip in tests
    secret_hygiene      — no obvious secrets/credentials in tracked files
    file_size_health    — no source file exceeds the giant-file threshold
    docs_present        — README + at least one .md beyond it
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Iterable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

GIANT_FILE_LINES = 1000
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "target", ".next", ".turbo",
    "out", "coverage", ".cache", ".idea", ".vscode", ".artifacts",
}
SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java",
               ".kt", ".rb", ".swift", ".c", ".h", ".cpp", ".cc", ".hpp"}
TEST_PATH_HINTS = ("test_", "_test.", "/tests/", "/test/", ".spec.", ".test.")
TODO_PATTERN = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
SKIP_PATTERN = re.compile(
    r"@(?:pytest\.mark\.)?skip\b|@unittest\.skip\b|it\.skip\(|describe\.skip\(",
    re.IGNORECASE,
)

# Secret patterns reused/adapted from the marketing reasoner. Conservative —
# false positives are OK; false negatives are not.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws-secret",      re.compile(r"\baws_secret_access_key\s*=\s*[\"'][^\"']{30,}")),
    ("github-token",    re.compile(r"\bghp_[A-Za-z0-9]{30,}")),
    ("openai-key",      re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("anthropic-key",   re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("private-key",     re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----")),
    ("password-equals", re.compile(r"\bpassword\s*[=:]\s*[\"'][^\"'\s]{6,}", re.IGNORECASE)),
)
# Filename allowlist — files we never scan for secrets (templates, examples).
SECRET_SCAN_SKIP_NAMES = {".env.example", "env.example", "example.env",
                           ".env.template", "secrets.example.json"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DimensionScore(BaseModel):
    name: str
    score: float = Field(..., ge=0.0, le=1.0)
    rationale: str = ""
    detail: dict = Field(default_factory=dict)


class Scorecard(BaseModel):
    repo_path: str
    dimensions: list[DimensionScore]
    aggregate: float = Field(..., ge=0.0, le=1.0)

    def by_name(self, name: str) -> DimensionScore | None:
        for d in self.dimensions:
            if d.name == name:
                return d
        return None


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def score_repo(
    repo_path: str | Path,
    *,
    extras: list[Callable[[Path], "DimensionScore"]] | None = None,
) -> Scorecard:
    """Score the repo against the seven baseline dimensions, plus any
    app-specific ``extras`` (callables that take repo Path and return a
    DimensionScore). Extras failing with an exception are surfaced as a
    score=0 dimension named after the callable rather than swallowed."""
    repo = Path(repo_path).expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        # Empty/missing repo → all-zero scorecard with explanatory dim.
        zero = DimensionScore(name="repo_exists", score=0.0,
                              rationale=f"repo not found: {repo}")
        return Scorecard(repo_path=str(repo), dimensions=[zero], aggregate=0.0)

    files = list(_walk_source_files(repo))
    test_files = [f for f in files if _is_test_path(f, repo)]

    dims: list[DimensionScore] = [
        _dim_tests_present(files, test_files),
        _dim_tests_recent(repo, test_files),
        _dim_todo_pressure(files),
        _dim_skip_pressure(test_files),
        _dim_secret_hygiene(repo, files),
        _dim_file_size_health(files),
        _dim_docs_present(repo),
    ]
    if extras:
        for fn in extras:
            try:
                dims.append(fn(repo))
            except Exception as e:
                logger.warning("extra dimension %s failed: %s", getattr(fn, "__name__", fn), e)
                dims.append(DimensionScore(
                    name=getattr(fn, "__name__", "extra_dimension"),
                    score=0.0,
                    rationale=f"extra dimension raised: {e}",
                ))
    aggregate = sum(d.score for d in dims) / len(dims) if dims else 0.0
    return Scorecard(repo_path=str(repo), dimensions=dims, aggregate=aggregate)


def score_delta(before: Scorecard, after: Scorecard) -> float:
    """Return ``(after - before)`` aggregate as percentage points."""
    return (after.aggregate - before.aggregate) * 100.0


# ---------------------------------------------------------------------------
# Dimension scorers
# ---------------------------------------------------------------------------


def _dim_tests_present(files: list[Path], test_files: list[Path]) -> DimensionScore:
    if not files:
        return DimensionScore(name="tests_present", score=0.0,
                              rationale="no source files in repo")
    if not test_files:
        return DimensionScore(name="tests_present", score=0.0,
                              rationale="no test files anywhere",
                              detail={"source_files": len(files)})
    # Saturate at 1 test file per 20 source files.
    ratio = len(test_files) / max(1, len(files) - len(test_files))
    score = min(1.0, ratio * 20)
    return DimensionScore(
        name="tests_present",
        score=score,
        rationale=f"{len(test_files)} test files / {len(files) - len(test_files)} non-test files",
        detail={"test_files": len(test_files), "non_test_files": len(files) - len(test_files)},
    )


def _dim_tests_recent(repo: Path, test_files: list[Path]) -> DimensionScore:
    """Have any tests been touched in the last 30 commits?"""
    import subprocess

    if not test_files:
        return DimensionScore(name="tests_recent", score=0.0,
                              rationale="no test files to evaluate")
    if not (repo / ".git").exists():
        return DimensionScore(name="tests_recent", score=0.5,
                              rationale="no git history; cannot assess recency")
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "log", "-n30", "--name-only", "--pretty=format:"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return DimensionScore(name="tests_recent", score=0.5, rationale="git log failed")
    touched = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    test_touches = sum(1 for t in touched if any(h in "/" + t for h in TEST_PATH_HINTS))
    if test_touches == 0:
        return DimensionScore(name="tests_recent", score=0.0,
                              rationale="no test files touched in last 30 commits")
    score = min(1.0, test_touches / 5)
    return DimensionScore(
        name="tests_recent", score=score,
        rationale=f"{test_touches} test files touched recently",
        detail={"recent_test_touches": test_touches},
    )


def _dim_todo_pressure(files: list[Path]) -> DimensionScore:
    total = 0
    for f in files:
        text = _safe_read(f)
        if not text:
            continue
        total += len(TODO_PATTERN.findall(text))
    # 0 markers = perfect; 50+ = floor (0.0). Linear in between.
    score = max(0.0, 1.0 - total / 50.0)
    rationale = f"{total} TODO/FIXME/XXX/HACK markers"
    return DimensionScore(
        name="todo_pressure", score=score, rationale=rationale,
        detail={"marker_count": total},
    )


def _dim_skip_pressure(test_files: list[Path]) -> DimensionScore:
    if not test_files:
        return DimensionScore(name="skip_pressure", score=1.0,
                              rationale="no test files to evaluate")
    total = 0
    for f in test_files:
        text = _safe_read(f)
        if not text:
            continue
        total += len(SKIP_PATTERN.findall(text))
    score = max(0.0, 1.0 - total / 10.0)
    return DimensionScore(
        name="skip_pressure", score=score,
        rationale=f"{total} skipped tests",
        detail={"skip_count": total},
    )


def _dim_secret_hygiene(repo: Path, files: Iterable[Path]) -> DimensionScore:
    hits: list[dict] = []
    for f in files:
        if f.name in SECRET_SCAN_SKIP_NAMES:
            continue
        # Test files commonly embed fake credentials as fixtures — skip them.
        if _is_test_path(f, repo):
            continue
        text = _safe_read(f)
        if not text:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                rel = f.relative_to(repo).as_posix() if f.is_relative_to(repo) else str(f)
                hits.append({"file": rel, "pattern": label})
                break  # one finding per file is enough
    if not hits:
        return DimensionScore(name="secret_hygiene", score=1.0,
                              rationale="no obvious secrets detected")
    return DimensionScore(
        name="secret_hygiene", score=0.0,
        rationale=f"{len(hits)} potential secret pattern(s) found",
        detail={"hits": hits[:10]},
    )


def _dim_file_size_health(files: list[Path]) -> DimensionScore:
    if not files:
        return DimensionScore(name="file_size_health", score=1.0,
                              rationale="no source files")
    giants: list[dict] = []
    for f in files:
        try:
            with f.open("rb") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            continue
        if lines > GIANT_FILE_LINES:
            giants.append({"file": str(f), "lines": lines})
    if not giants:
        return DimensionScore(name="file_size_health", score=1.0,
                              rationale=f"no files exceed {GIANT_FILE_LINES} lines")
    score = max(0.0, 1.0 - len(giants) / 10.0)
    return DimensionScore(
        name="file_size_health", score=score,
        rationale=f"{len(giants)} file(s) over {GIANT_FILE_LINES} lines",
        detail={"giants": giants[:5]},
    )


def _dim_docs_present(repo: Path) -> DimensionScore:
    has_readme = any((repo / n).exists() for n in
                     ("README.md", "README.MD", "README.rst", "README.txt", "README"))
    md_files = [p for p in _walk_files(repo) if p.suffix.lower() in {".md", ".rst"}]
    if not has_readme:
        return DimensionScore(name="docs_present", score=0.0,
                              rationale="no README found")
    if len(md_files) < 2:
        return DimensionScore(name="docs_present", score=0.5,
                              rationale="README present but no other docs",
                              detail={"md_file_count": len(md_files)})
    return DimensionScore(
        name="docs_present", score=1.0,
        rationale=f"README + {len(md_files) - 1} other doc(s)",
        detail={"md_file_count": len(md_files)},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk_files(repo: Path) -> Iterable[Path]:
    stack = [repo]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for child in entries:
            if child.is_dir():
                if child.name in SKIP_DIRS:
                    continue
                stack.append(child)
            elif child.is_file():
                yield child


def _walk_source_files(repo: Path) -> Iterable[Path]:
    for p in _walk_files(repo):
        if p.suffix.lower() in SOURCE_EXTS:
            yield p


def _is_test_path(p: Path, repo: Path | None = None) -> bool:
    """Detect test files by repo-relative path so that pytest's tmpdir
    naming (which itself contains ``test_``) doesn't false-positive."""
    if repo is not None:
        try:
            rel = p.relative_to(repo).as_posix()
        except ValueError:
            rel = p.as_posix()
    else:
        rel = p.as_posix()
    s = "/" + rel
    return any(h in s for h in TEST_PATH_HINTS)


def _safe_read(path: Path, *, max_bytes: int = 256_000) -> str:
    try:
        with path.open("rb") as fh:
            data = fh.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Baseline persistence (for captain pre/post snapshot)
# ---------------------------------------------------------------------------


def write_baseline(path: Path, scorecard: Scorecard) -> None:
    """Persist a pre-slice scorecard to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scorecard.model_dump_json(indent=2))


def read_baseline(path: Path) -> Scorecard | None:
    if not path.exists():
        return None
    try:
        return Scorecard.model_validate_json(path.read_text())
    except Exception:
        logger.warning("baseline scorecard parse failed: %s", path)
        return None


def clear_baseline(path: Path) -> None:
    if path.exists():
        path.unlink()


def make_baseline_score_delta(
    baseline_path: Path,
    repo_path: str | Path,
    *,
    extras: list[Callable[[Path], DimensionScore]] | None = None,
):
    """Return a ``score_delta`` callable for ``captain_tick``.

    Computes ``after - before`` where ``before`` is loaded from the cached
    baseline at ``baseline_path`` and ``after`` is the live repo's score
    (with the same ``extras`` applied so dimensions match).

    Returns ``None`` (so the validator falls back to its files-changed
    heuristic) if no baseline is on file.
    """
    def _delta(_slice, _complete) -> float | None:
        before = read_baseline(baseline_path)
        if before is None:
            return None
        after = score_repo(repo_path, extras=extras)
        return score_delta(before, after)

    return _delta


__all__ = [
    "DimensionScore",
    "Scorecard",
    "clear_baseline",
    "make_baseline_score_delta",
    "read_baseline",
    "score_delta",
    "score_repo",
    "write_baseline",
]

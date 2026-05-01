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
    test_density        — continuous test_LOC / source_LOC ratio (real
                          codebases saturate tests_present at 1.0; this
                          gives slices that ADD test coverage measurable
                          credit even when tests_present is already pinned)
    migrations_consistent — Django apps with models.py must have a
                          migrations/ dir with at least one migration
                          file. Catches the failure mode where the
                          captain accepts a model change but the worker
                          forgot to run makemigrations.
"""

from __future__ import annotations

import logging
import os
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
TEST_PATH_HINTS = (
    "test_", "_test.", "/tests/", "/test/", ".spec.", ".test.",
    # pytest fixtures live in conftest.py (root or nested). They commonly
    # embed fake credentials and must be treated as test code. Leading "/"
    # avoids false positives like "/foo/myconftest.py".
    "/conftest.py",
)
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
SECRET_SCAN_SKIP_GLOBS = ("**/conftest.py", "**/tests/**", "**/migrations/**")
FILE_SIZE_SKIP_GLOBS = ("**/migrations/**", "**/generated/**")


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
        _dim_test_density(files, test_files),
        _dim_migrations_consistent(repo),
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
        if _matches_any_glob(f, repo, SECRET_SCAN_SKIP_GLOBS):
            continue
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
    total_excess = 0
    repo = _common_repo_root(files)
    for f in files:
        if repo is not None and _matches_any_glob(f, repo, FILE_SIZE_SKIP_GLOBS):
            continue
        try:
            with f.open("rb") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            continue
        if lines > GIANT_FILE_LINES:
            giants.append({"file": str(f), "lines": lines})
            total_excess += lines - GIANT_FILE_LINES
    if not giants:
        return DimensionScore(name="file_size_health", score=1.0,
                              rationale=f"no files exceed {GIANT_FILE_LINES} lines")
    # Fully continuous: penalize TOTAL excess LOC, not just file count.
    # Budget = 10000 excess LOC → score 0.0. Was: count of giants / 20,
    # which gave NO delta for cutting 100 LOC out of a 3000-line file
    # (still a giant). With excess-LOC denominator, every line reduction
    # produces a measurable rubric delta (1pp per 100 LOC removed).
    # Backward-compat: 4 giants × 1500 lines = 2000 excess → 0.80;
    # 15 giants × 1500 lines = 7500 excess → 0.25 (matches old formula
    # for the test fixtures, but gives continuous credit between).
    # BUDGET is sized to give real codebases (10-15k excess LOC) headroom
    # to grow rather than bottoming out at 0.0 — which was the failure mode
    # observed live on author-toolkit (11887 excess LOC → 0.00, no signal).
    BUDGET = 20000
    score = max(0.0, 1.0 - total_excess / BUDGET)
    return DimensionScore(
        name="file_size_health", score=score,
        rationale=f"{len(giants)} file(s) over {GIANT_FILE_LINES} lines "
                  f"({total_excess} excess LOC)",
        detail={"giants": giants[:5], "total_excess_loc": total_excess},
    )


def _dim_test_density(files: list[Path], test_files: list[Path]) -> DimensionScore:
    """Continuous test-coverage proxy: total test LOC / total source LOC.
    Saturates at ratio 0.5 (1 LOC of test per 2 LOC of source). The
    headline ``tests_present`` dim saturates at 1.0 for any non-trivial
    codebase, blinding the rubric to slices that add new test files.
    This dim ALWAYS has headroom — every test added moves the score."""
    non_test = [f for f in files if f not in set(test_files)]
    if not non_test:
        return DimensionScore(name="test_density", score=1.0,
                              rationale="no source files to weight against",
                              detail={"source_loc": 0, "test_loc": 0})
    source_loc = 0
    for f in non_test:
        try:
            with f.open("rb") as fh:
                source_loc += sum(1 for _ in fh)
        except OSError:
            continue
    test_loc = 0
    for f in test_files:
        try:
            with f.open("rb") as fh:
                test_loc += sum(1 for _ in fh)
        except OSError:
            continue
    if source_loc == 0:
        return DimensionScore(name="test_density", score=1.0,
                              rationale="no source LOC",
                              detail={"source_loc": 0, "test_loc": test_loc})
    ratio = test_loc / source_loc
    # Saturation at ratio 0.5; below that, score tracks the ratio linearly.
    score = min(1.0, ratio / 0.5)
    return DimensionScore(
        name="test_density", score=score,
        rationale=f"{test_loc} test LOC / {source_loc} source LOC (ratio {ratio:.3f})",
        detail={"source_loc": source_loc, "test_loc": test_loc, "ratio": round(ratio, 4)},
    )


def _models_py_is_abstract_only(text: str) -> bool:
    """Lightweight check: every Django Model subclass in `text` has
    ``abstract = True`` in its body. Used to skip abstract-only
    models.py files (e.g. apps/core/models.py base classes) from the
    migrations_consistent dim.

    Not a real AST parse — class blocks are detected by indentation,
    and ``abstract = True`` is matched within the next 25 non-empty
    lines after each class header. Conservative: any non-abstract class
    flips the file to "concrete" and requires migrations. False-positive
    abstract → migration required (loud); false-negative abstract → no
    migration required (silent). We err loud."""
    class_pattern = re.compile(
        r"^class\s+(\w+)\s*\([^)]*\bmodels\.Model\b[^)]*\)\s*:",
        re.MULTILINE,
    )
    matches = list(class_pattern.finditer(text))
    if not matches:
        # No models.Model subclasses found — safe to treat as non-Django.
        return True
    abstract_pat = re.compile(r"^\s+abstract\s*=\s*True\b", re.MULTILINE)
    lines = text.splitlines()
    for m in matches:
        # Find class start line index
        upto = text[: m.start()]
        start_line = upto.count("\n")
        window = "\n".join(lines[start_line : start_line + 30])
        # Only count abstract markers indented relative to the class
        # (i.e. inside a Meta block). Cheap proxy: look for any line
        # matching the indented `abstract = True` pattern.
        if not abstract_pat.search(window):
            return False
    return True


def _dim_migrations_consistent(repo: Path) -> DimensionScore:
    """For every directory containing ``models.py`` with a Django model
    declaration, require a sibling ``migrations/`` dir with at least one
    migration file (other than ``__init__.py``).

    Non-Django repos (no models.py anywhere) score 1.0 — this dim is a
    no-op for them. Django repos missing migrations score continuously
    (1 - missing/total).

    Catches the failure mode observed live on author-toolkit PR #142:
    captain accepted a slice that added Plan + Subscription models but
    the migration-file check happens out-of-band. With this dim, missing
    migrations register as a rubric drop on the validate, eligible to
    trigger reject_retry."""
    apps_with_models: list[Path] = []
    for f in _walk_files(repo):
        if f.name != "models.py":
            continue
        text = _safe_read(f)
        if not text:
            continue
        if not re.search(r"\bmodels\.Model\b", text):
            continue
        # Skip abstract-only models.py (e.g. apps/core/models.py with
        # only abstract base classes). Such modules don't require
        # migrations. Heuristic: if every `class X(models.Model)` block
        # contains `abstract = True` within ~25 lines, treat as abstract.
        if _models_py_is_abstract_only(text):
            continue
        apps_with_models.append(f.parent)

    if not apps_with_models:
        return DimensionScore(
            name="migrations_consistent", score=1.0,
            rationale="no Django models.py with model classes detected",
            detail={"apps_with_models": 0},
        )

    missing: list[str] = []
    for app_dir in apps_with_models:
        mig_dir = app_dir / "migrations"
        if not mig_dir.is_dir():
            missing.append(str(app_dir.relative_to(repo)) if app_dir.is_relative_to(repo) else str(app_dir))
            continue
        migration_files = [
            p for p in mig_dir.iterdir()
            if p.is_file() and p.suffix == ".py" and p.name != "__init__.py"
        ]
        if not migration_files:
            missing.append(str(app_dir.relative_to(repo)) if app_dir.is_relative_to(repo) else str(app_dir))

    total = len(apps_with_models)
    if not missing:
        return DimensionScore(
            name="migrations_consistent", score=1.0,
            rationale=f"all {total} Django app(s) have migrations",
            detail={"apps_with_models": total, "missing": []},
        )
    score = 1.0 - len(missing) / total
    return DimensionScore(
        name="migrations_consistent", score=score,
        rationale=f"{len(missing)}/{total} Django app(s) missing migrations",
        detail={
            "apps_with_models": total,
            "missing": missing[:5],
        },
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


def _matches_any_glob(path: Path, repo: Path, globs: tuple[str, ...]) -> bool:
    rel = path.relative_to(repo).as_posix() if path.is_relative_to(repo) else path.as_posix()
    return any(Path(rel).match(g) for g in globs)


def _common_repo_root(files: list[Path]) -> Path | None:
    if not files:
        return None
    if not all(p.is_absolute() for p in files):
        return None
    try:
        return Path(os.path.commonpath([str(p) for p in files]))
    except Exception:
        return None


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

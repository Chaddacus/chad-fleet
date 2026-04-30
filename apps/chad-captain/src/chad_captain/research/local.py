"""Local repo scan — pure stdlib, no LLM, no network.

Produces a structured ``LocalProfile`` describing what an app actually is on
disk: file tree, README excerpt, package manifests, recent commits, and
language distribution. The replanner consumes this when deciding what to
research on the web side and what slices to plan next.
"""

from __future__ import annotations

import logging
import subprocess
from collections import Counter
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

README_MAX_CHARS = 4000
TOP_DIRS_DEPTH = 2
TOP_DIRS_LIMIT = 60
COMMIT_LIMIT = 30
MANIFESTS = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "requirements.txt",
    "setup.py",
)
# Skip when summing language stats / walking dirs.
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "target", ".next",
    ".turbo", "out", "coverage", ".cache", ".idea", ".vscode",
}
# Map a few extension families to language names.
EXT_LANGUAGE = {
    ".py": "Python", ".pyi": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby",
    ".swift": "Swift",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".sh": "Shell", ".bash": "Shell",
    ".md": "Markdown",
    ".html": "HTML", ".css": "CSS", ".scss": "CSS",
    ".sql": "SQL",
    ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML",
    ".json": "JSON",
}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class CommitSummary(BaseModel):
    sha: str
    date: str
    author: str
    subject: str


class LocalProfile(BaseModel):
    """Snapshot of an app's repo on disk."""

    repo_path: str
    name: str = ""
    has_readme: bool = False
    readme_excerpt: str = ""
    top_dirs: list[str] = Field(default_factory=list)
    manifests: dict[str, str] = Field(default_factory=dict)  # filename → first 1500 chars
    languages: dict[str, int] = Field(default_factory=dict)  # language → line count
    recent_commits: list[CommitSummary] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def scan_local(repo_path: str | Path) -> LocalProfile:
    """Build a ``LocalProfile`` for ``repo_path``. Always returns a profile —
    missing inputs become empty fields and a note rather than an exception.
    """
    repo = Path(repo_path).expanduser().resolve()
    profile = LocalProfile(repo_path=str(repo), name=repo.name)

    if not repo.exists():
        profile.notes.append(f"repo path does not exist: {repo}")
        return profile
    if not repo.is_dir():
        profile.notes.append(f"repo path is not a directory: {repo}")
        return profile

    profile.has_readme, profile.readme_excerpt = _read_readme(repo)
    profile.top_dirs = _list_top_entries(repo, depth=TOP_DIRS_DEPTH, limit=TOP_DIRS_LIMIT)
    profile.manifests = _read_manifests(repo)
    profile.languages = _language_stats(repo)
    profile.recent_commits = _git_log(repo, limit=COMMIT_LIMIT)
    return profile


def _read_readme(repo: Path) -> tuple[bool, str]:
    for name in ("README.md", "README.MD", "README.rst", "README.txt", "README"):
        p = repo / name
        if p.exists() and p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            return True, text[:README_MAX_CHARS]
    return False, ""


def _list_top_entries(repo: Path, *, depth: int, limit: int) -> list[str]:
    """Return up to ``limit`` paths relative to repo, breadth-first to ``depth``.

    Files surface first within a directory; SKIP_DIRS are pruned. Output is
    deterministic (sorted by path).
    """
    out: list[str] = []
    queue: list[tuple[Path, int]] = [(repo, 0)]
    while queue:
        current, d = queue.pop(0)
        try:
            entries = sorted(current.iterdir(), key=lambda p: (p.is_dir(), p.name.lower()))
        except OSError:
            continue
        for child in entries:
            if child.name.startswith(".") and child.name not in {".github", ".gitignore"}:
                continue
            if child.name in SKIP_DIRS:
                continue
            rel = child.relative_to(repo).as_posix()
            out.append(rel + ("/" if child.is_dir() else ""))
            if len(out) >= limit:
                return out
            if child.is_dir() and d + 1 < depth:
                queue.append((child, d + 1))
    return out


def _read_manifests(repo: Path) -> dict[str, str]:
    manifests: dict[str, str] = {}
    for name in MANIFESTS:
        p = repo / name
        if p.exists() and p.is_file():
            try:
                manifests[name] = p.read_text(encoding="utf-8", errors="replace")[:1500]
            except OSError:
                continue
    return manifests


def _language_stats(repo: Path) -> dict[str, int]:
    """Cheap line-count by language, walking the repo and skipping known dirs."""
    counter: Counter[str] = Counter()
    for path in _walk_files(repo):
        ext = path.suffix.lower()
        lang = EXT_LANGUAGE.get(ext)
        if not lang:
            continue
        try:
            with path.open("rb") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            continue
        counter[lang] += lines
    return dict(counter.most_common())


def _walk_files(repo: Path) -> Iterable[Path]:
    stack: list[Path] = [repo]
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


def _git_log(repo: Path, *, limit: int) -> list[CommitSummary]:
    if not (repo / ".git").exists():
        return []
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                f"-n{limit}",
                "--pretty=format:%H%x1f%cI%x1f%an%x1f%s",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("git log failed for %s: %s", repo, e)
        return []
    if proc.returncode != 0:
        return []

    out: list[CommitSummary] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f", 3)
        if len(parts) != 4:
            continue
        sha, date, author, subject = parts
        out.append(CommitSummary(sha=sha[:12], date=date, author=author, subject=subject))
    return out


__all__ = ["CommitSummary", "LocalProfile", "scan_local"]

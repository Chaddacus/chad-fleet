"""Tests for the local repo scanner — pure stdlib path."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

from chad_captain.research.local import scan_local


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    os.system(f"git -C {repo} init -q")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t add -A")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t commit -qm 'initial' --allow-empty")


def test_scan_local_returns_profile_with_name(tmp_path: Path) -> None:
    repo = tmp_path / "myapp"
    repo.mkdir()
    profile = scan_local(repo)
    assert profile.name == "myapp"
    assert profile.repo_path == str(repo.resolve())


def test_scan_local_missing_path_records_note(tmp_path: Path) -> None:
    profile = scan_local(tmp_path / "nope")
    assert profile.notes
    assert profile.has_readme is False
    assert profile.languages == {}


def test_scan_local_reads_readme_excerpt(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "README.md").write_text("# Title\n\nSome description here.\n")
    profile = scan_local(repo)
    assert profile.has_readme is True
    assert "Some description" in profile.readme_excerpt


def test_scan_local_reads_pyproject_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(textwrap.dedent("""\
        [project]
        name = "thing"
        description = "A test thing"
    """))
    profile = scan_local(repo)
    assert "pyproject.toml" in profile.manifests
    assert "A test thing" in profile.manifests["pyproject.toml"]


def test_scan_local_counts_python_lines(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
    (repo / "b.py").write_text("print('hi')\n")
    profile = scan_local(repo)
    # 3 + 1 = 4 lines of Python
    assert profile.languages.get("Python", 0) == 4


def test_scan_local_skips_node_modules_and_venv(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "node_modules" / "garbage").mkdir(parents=True)
    (repo / "node_modules" / "garbage" / "x.js").write_text("\n" * 1000)
    (repo / ".venv").mkdir()
    (repo / ".venv" / "x.py").write_text("\n" * 1000)
    (repo / "src.py").write_text("x = 1\n")
    profile = scan_local(repo)
    assert profile.languages.get("Python", 0) == 1
    assert profile.languages.get("JavaScript", 0) == 0
    # Top-dirs should not include skipped names
    assert not any("node_modules" in d for d in profile.top_dirs)
    assert not any(".venv" in d for d in profile.top_dirs)


def test_scan_local_captures_recent_commits(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _init_repo(repo)
    (repo / "x.txt").write_text("hi")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t add x.txt")
    os.system(f"git -C {repo} -c user.email=t@t -c user.name=t commit -qm 'add x'")
    profile = scan_local(repo)
    assert len(profile.recent_commits) >= 1
    subjects = [c.subject for c in profile.recent_commits]
    assert "add x" in subjects


def test_scan_local_no_git_returns_empty_commits(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "x.py").write_text("x = 1\n")
    profile = scan_local(repo)
    assert profile.recent_commits == []


def test_scan_local_top_dirs_limited(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    for i in range(120):
        (repo / f"f{i}.txt").write_text("x")
    profile = scan_local(repo)
    assert len(profile.top_dirs) <= 60

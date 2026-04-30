"""Tests for the research synthesizer — caching + TTL + web fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chad_captain.protocol import AppWorkspace
from chad_captain.research import (
    AppProfile,
    load_profile,
    profile_is_fresh,
    synthesize_profile,
)
from chad_captain.research.web import WebProfile


@pytest.fixture()
def ws(tmp_path: Path) -> AppWorkspace:
    w = AppWorkspace("test-app", base=tmp_path)
    w.ensure()
    return w


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "README.md").write_text("# RepoName\n\nThis project does X.\n")
    (r / "main.py").write_text("print('hi')\n")
    return r


def test_synthesize_writes_cache(ws: AppWorkspace, repo: Path) -> None:
    profile = synthesize_profile(ws, repo, do_web=False)
    assert profile.app_id == "test-app"
    assert profile.local.has_readme is True
    assert profile.summary
    assert ws.research_path.exists()


def test_synthesize_returns_cache_when_fresh(ws: AppWorkspace, repo: Path) -> None:
    first = synthesize_profile(ws, repo, do_web=False)
    cached = synthesize_profile(ws, repo, do_web=False)
    assert cached.generated_at == first.generated_at


def test_synthesize_refresh_overrides_cache(ws: AppWorkspace, repo: Path) -> None:
    first = synthesize_profile(ws, repo, do_web=False)
    rebuilt = synthesize_profile(ws, repo, do_web=False, refresh=True)
    assert rebuilt.generated_at >= first.generated_at


def test_synthesize_rebuilds_when_cache_stale(ws: AppWorkspace, repo: Path) -> None:
    first = synthesize_profile(ws, repo, do_web=False)
    # Forge stale generated_at by writing back
    stale_dt = datetime.now(timezone.utc) - timedelta(days=30)
    forged = first.model_copy(update={"generated_at": stale_dt.isoformat()})
    ws.research_path.write_text(forged.model_dump_json())
    rebuilt = synthesize_profile(ws, repo, do_web=False)
    assert rebuilt.generated_at > forged.generated_at


def test_load_profile_returns_none_when_missing(ws: AppWorkspace) -> None:
    assert load_profile(ws) is None


def test_load_profile_handles_corrupt_cache(ws: AppWorkspace) -> None:
    ws.research_path.write_text("not json")
    assert load_profile(ws) is None


def test_profile_is_fresh_true_when_recent(ws: AppWorkspace, repo: Path) -> None:
    profile = synthesize_profile(ws, repo, do_web=False)
    assert profile_is_fresh(profile)


def test_profile_is_fresh_false_when_old(ws: AppWorkspace, repo: Path) -> None:
    profile = synthesize_profile(ws, repo, do_web=False)
    stale = profile.model_copy(
        update={"generated_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}
    )
    assert profile_is_fresh(stale) is False


def test_summary_uses_readme_first(ws: AppWorkspace, tmp_path: Path) -> None:
    r = tmp_path / "rrr"
    r.mkdir()
    (r / "README.md").write_text("First paragraph here.\n\nSecond paragraph.")
    profile = synthesize_profile(ws, r, do_web=False)
    assert "First paragraph" in profile.summary


def test_summary_skips_heading_only_first_paragraph(ws: AppWorkspace, tmp_path: Path) -> None:
    r = tmp_path / "rrr"
    r.mkdir()
    (r / "README.md").write_text("# Title\n\nThe real description.\n")
    profile = synthesize_profile(ws, r, do_web=False)
    assert profile.summary.startswith("The real description")


def test_summary_falls_back_to_pyproject(ws: AppWorkspace, tmp_path: Path) -> None:
    r = tmp_path / "rrr"
    r.mkdir()
    (r / "pyproject.toml").write_text('[project]\nname="x"\ndescription = "An interesting tool"\n')
    profile = synthesize_profile(ws, r, do_web=False)
    assert "interesting tool" in profile.summary


def test_synthesize_skips_web_when_requested(ws: AppWorkspace, repo: Path) -> None:
    profile = synthesize_profile(ws, repo, do_web=False)
    assert profile.web.status == "skipped"


def test_synthesize_handles_web_skip(ws: AppWorkspace, repo: Path, monkeypatch) -> None:
    """If the web researcher returns skipped/error, synthesize still completes."""
    from chad_captain.research import synthesize as syn_mod

    def fake_web(**_kwargs):
        return WebProfile.skipped("test override")

    monkeypatch.setattr(syn_mod, "research_web", fake_web)
    profile = synthesize_profile(ws, repo)  # do_web=True by default
    assert profile.web.status == "skipped"
    assert profile.web.reason == "test override"
    assert profile.local.has_readme is True


def test_app_profile_round_trips(ws: AppWorkspace, repo: Path) -> None:
    written = synthesize_profile(ws, repo, do_web=False)
    re_read = AppProfile.model_validate_json(ws.research_path.read_text())
    assert re_read.app_id == written.app_id
    assert re_read.local.name == written.local.name

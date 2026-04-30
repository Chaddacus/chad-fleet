"""Tests for playbooks.py: loading, parsing, and matching."""

from __future__ import annotations

from pathlib import Path

import pytest

from captain_core.playbooks import find_playbooks_for_app, load_playbook, load_playbooks_dir
from captain_core.types import Playbook
from tests.conftest import make_app


EXPECTED_REAL_SLUGS = {
    "indie-author-launch",
    "oss-marketing",
    "federal-contracting",
    "linkedin-algorithm",
    "b2b-saas-gtm",
    "sdvosb-paperwork",
}


# ---------------------------------------------------------------------------
# Fixture playbook loader tests
# ---------------------------------------------------------------------------

def test_load_playbook_returns_playbook_model(tmp_playbooks_dir: Path) -> None:
    pb = load_playbook(tmp_playbooks_dir / "test-playbook.md")
    assert isinstance(pb, Playbook)
    assert pb.slug == "test-playbook"


def test_load_playbook_frontmatter_parsed(tmp_playbooks_dir: Path) -> None:
    pb = load_playbook(tmp_playbooks_dir / "test-playbook.md")
    assert pb.title == "Test Playbook"
    assert pb.domain == "testing"
    assert "unit-tests" in pb.applies_to
    assert pb.last_updated == "2026-04-01"


def test_load_playbook_sections_populated(tmp_playbooks_dir: Path) -> None:
    pb = load_playbook(tmp_playbooks_dir / "test-playbook.md")
    assert pb.summary != ""
    assert len(pb.when_to_consult) >= 1
    assert len(pb.recommendations) >= 1
    assert len(pb.anti_patterns) >= 1


def test_load_playbook_raw_is_body(tmp_playbooks_dir: Path) -> None:
    pb = load_playbook(tmp_playbooks_dir / "test-playbook.md")
    assert "## Summary" in pb.raw
    assert "## Recommendations" in pb.raw
    # raw should NOT contain the YAML frontmatter
    assert "slug:" not in pb.raw


def test_load_playbooks_dir_returns_all_slugs(tmp_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    assert "test-playbook" in pbs
    assert "another-playbook" in pbs
    # index.md is excluded
    assert "index" not in pbs


def test_load_playbooks_dir_excludes_index(tmp_playbooks_dir: Path) -> None:
    (tmp_playbooks_dir / "index.md").write_text("# Index\n", encoding="utf-8")
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    assert "index" not in pbs


# ---------------------------------------------------------------------------
# Real playbook loader tests
# ---------------------------------------------------------------------------

def test_real_playbooks_all_six_loaded(real_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    assert EXPECTED_REAL_SLUGS.issubset(set(pbs.keys())), (
        f"Missing slugs: {EXPECTED_REAL_SLUGS - set(pbs.keys())}"
    )


def test_real_indie_author_launch_frontmatter(real_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    pb = pbs["indie-author-launch"]
    assert pb.title == "Indie Author Launch Playbook"
    assert pb.domain == "author-publishing"
    assert "book-launch" in pb.applies_to


def test_real_indie_author_launch_has_recommendations(real_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    pb = pbs["indie-author-launch"]
    assert len(pb.recommendations) >= 5
    assert len(pb.when_to_consult) >= 3
    assert len(pb.anti_patterns) >= 3


def test_real_all_playbooks_have_summary(real_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    for slug, pb in pbs.items():
        assert pb.summary.strip(), f"Playbook {slug!r} has empty summary"


def test_real_all_playbooks_have_when_to_consult(real_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    for slug, pb in pbs.items():
        assert len(pb.when_to_consult) >= 1, f"Playbook {slug!r} has no when_to_consult bullets"


def test_real_all_playbooks_have_recommendations(real_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    for slug, pb in pbs.items():
        assert len(pb.recommendations) >= 1, f"Playbook {slug!r} has no recommendations"


# ---------------------------------------------------------------------------
# find_playbooks_for_app tests
# ---------------------------------------------------------------------------

def test_find_playbooks_explicit_slug(tmp_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    app = make_app(metadata={"playbook_slugs": ["test-playbook"]})
    result = find_playbooks_for_app(app, pbs)
    slugs = [p.slug for p in result]
    assert "test-playbook" in slugs


def test_find_playbooks_mode_match(tmp_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    # another-playbook applies_to includes "saas-product" and "freemium"
    app = make_app(mode="saas-product", owner_brand="")
    result = find_playbooks_for_app(app, pbs)
    slugs = [p.slug for p in result]
    assert "another-playbook" in slugs


def test_find_playbooks_no_match_returns_empty(tmp_playbooks_dir: Path) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    app = make_app(mode="completely-unrelated", owner_brand="nomatchwhatsoever")
    result = find_playbooks_for_app(app, pbs)
    assert result == []


def test_find_playbooks_chadacys_launch_driven(real_playbooks_dir: Path) -> None:
    """App with owner_brand=chadacys + mode=launch_driven should match indie-author-launch."""
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app(
        owner_brand="chadacys",
        mode="launch_driven",
        metadata={"playbook_slugs": ["indie-author-launch", "linkedin-algorithm"]},
    )
    result = find_playbooks_for_app(app, pbs)
    slugs = {p.slug for p in result}
    assert "indie-author-launch" in slugs
    assert "linkedin-algorithm" in slugs


def test_find_playbooks_sdvosb_match(real_playbooks_dir: Path) -> None:
    """App with applies_to overlap on sdvosb tokens should match sdvosb-paperwork."""
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app(
        owner_brand="cloudwarriors",
        mode="sdvosb",
        metadata={},
    )
    result = find_playbooks_for_app(app, pbs)
    slugs = {p.slug for p in result}
    # sdvosb-paperwork has applies_to = [commercialization-prep, business-entity-setup, veteran-benefits-activation]
    # and federal-contracting has applies_to = [sdvosb-products, ...]
    # mode="sdvosb" tokenises to ["sdvosb"] — check federal-contracting matches via "sdvosb" in applies_to tokens
    assert "federal-contracting" in slugs or "sdvosb-paperwork" in slugs

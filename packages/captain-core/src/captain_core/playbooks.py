"""Playbook loader: parse markdown files with YAML frontmatter into Playbook objects."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from captain_core.types import Playbook

# Section headings we look for (lower-cased for matching).
_SECTION_MAP = {
    "summary": "summary",
    "when to consult": "when_to_consult",
    "recommendations": "recommendations",
    "anti-patterns": "anti_patterns",
    "anti patterns": "anti_patterns",
    "decision rubric": "decision_rubric",
    "sources": "sources",
}

_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body text after the closing ---)."""
    if not text.startswith("---"):
        return {}, text
    # find second ---
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 3:].strip()
    return yaml.safe_load(fm_text) or {}, body


def _parse_bullet_list(text: str) -> list[str]:
    """Extract lines that begin with '- ' or '* ' as bullet items."""
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            items.append(stripped[2:].strip())
    return items


def _parse_numbered_paragraphs(text: str) -> list[str]:
    """
    Extract numbered recommendation paragraphs.

    Each paragraph starts with a line matching /^\\d+\\./ and may span
    multiple lines until the next numbered item or end of section.
    Returns the full paragraph text per item (stripped).
    """
    # Split on numbered item boundaries
    item_re = re.compile(r"(?m)^(\d+)\.\s+")
    parts = item_re.split(text)
    # parts structure: [pre, num, body, num, body, ...]
    items: list[str] = []
    i = 1
    while i < len(parts) - 1:
        body = parts[i + 1].strip()
        if body:
            items.append(body)
        i += 2
    return items


def _parse_sources(text: str) -> list[str]:
    """Extract source lines (markdown links or plain lines) from Sources section."""
    sources: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            sources.append(stripped)
    return sources


def _extract_sections(body: str) -> dict[str, str]:
    """Split body into dict of section_key -> raw section text."""
    positions: list[tuple[int, str]] = []
    for m in _HEADING_RE.finditer(body):
        heading_lower = m.group(1).strip().lower()
        key = _SECTION_MAP.get(heading_lower)
        if key:
            positions.append((m.end(), key))

    sections: dict[str, str] = {}
    for idx, (start, key) in enumerate(positions):
        end = positions[idx + 1][0] - len(positions[idx + 1][1]) - 10 if idx + 1 < len(positions) else len(body)
        # Find the actual next ## heading to bound this section
        next_heading = _HEADING_RE.search(body, start)
        if next_heading and idx + 1 < len(positions):
            end = next_heading.start()
        elif idx + 1 < len(positions):
            end = positions[idx + 1][0]
        else:
            end = len(body)
        sections[key] = body[start:end].strip()

    return sections


def load_playbook(path: Path) -> Playbook:
    """Parse a playbook markdown file and return a Playbook model."""
    text = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)

    # Re-extract sections using a robust scan that finds next ## boundary
    sections: dict[str, str] = {}
    # Collect all ## headings with their start/end positions in body
    heading_matches = list(_HEADING_RE.finditer(body))
    for i, m in enumerate(heading_matches):
        heading_lower = m.group(1).strip().lower()
        key = _SECTION_MAP.get(heading_lower)
        if key is None:
            continue
        content_start = m.end()
        # Content ends at the next ## heading (of any key, mapped or not)
        content_end = heading_matches[i + 1].start() if i + 1 < len(heading_matches) else len(body)
        sections[key] = body[content_start:content_end].strip()

    summary = sections.get("summary", "")
    when_to_consult = _parse_bullet_list(sections.get("when_to_consult", ""))
    recommendations = _parse_numbered_paragraphs(sections.get("recommendations", ""))
    anti_patterns = _parse_bullet_list(sections.get("anti_patterns", ""))
    decision_rubric: str | None = sections.get("decision_rubric") or None
    sources_raw = sections.get("sources", "")
    sources = _parse_sources(sources_raw) if sources_raw else []

    return Playbook(
        slug=fm.get("slug", path.stem),
        title=fm.get("title", path.stem),
        domain=fm.get("domain", ""),
        applies_to=fm.get("applies_to", []),
        last_updated=str(fm.get("last_updated", "")),
        summary=summary,
        when_to_consult=when_to_consult,
        recommendations=recommendations,
        anti_patterns=anti_patterns,
        decision_rubric=decision_rubric,
        sources=sources,
        raw=body,
    )


def load_playbooks_dir(dir_path: Path) -> dict[str, Playbook]:
    """Load all *.md files in dir_path (excluding index.md) and return slug -> Playbook."""
    playbooks: dict[str, Playbook] = {}
    for md_file in sorted(dir_path.glob("*.md")):
        if md_file.name == "index.md":
            continue
        p = load_playbook(md_file)
        playbooks[p.slug] = p
    return playbooks


def find_playbooks_for_app(
    app,  # AppSnapshot — typed loosely to avoid circular; duck-typed
    all_playbooks: dict[str, Playbook],
) -> list[Playbook]:
    """
    Return playbooks that are relevant to the given AppSnapshot.

    Matching logic (any match qualifies):
    1. Explicit metadata.playbook_slugs list on the app.
    2. applies_to tokens that overlap with app.owner_brand or app.mode.
    3. Domain keyword overlap with owner_brand/mode strings.
    """
    results: list[Playbook] = []
    explicit_slugs: list[str] = app.metadata.get("playbook_slugs", [])

    owner_tokens = set(_tokenise(app.owner_brand))
    mode_tokens = set(_tokenise(app.mode))
    app_tokens = owner_tokens | mode_tokens

    for slug, pb in all_playbooks.items():
        if slug in explicit_slugs:
            results.append(pb)
            continue
        # Check applies_to overlap
        pb_tokens: set[str] = set()
        for item in pb.applies_to:
            pb_tokens.update(_tokenise(item))
        if pb_tokens & app_tokens:
            results.append(pb)
            continue
        # Domain keyword overlap
        domain_tokens = set(_tokenise(pb.domain))
        if domain_tokens & app_tokens:
            results.append(pb)

    return results


def _tokenise(s: str) -> list[str]:
    """Split a hyphenated / spaced string into lowercase tokens."""
    return [t for t in re.split(r"[-_\s]+", s.lower()) if t]

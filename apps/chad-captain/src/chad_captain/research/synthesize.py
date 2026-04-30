"""Synthesizer: combine local + web research into a cached AppProfile.

Cache lives at ``ws.research_path``. TTL defaults to 7 days; pass
``refresh=True`` to force a rebuild. The cached profile is plain JSON so the
dashboard can read it directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field
from tracked_app_registry.storage import atomic_write

from chad_captain.protocol import AppWorkspace
from chad_captain.research.local import LocalProfile, scan_local
from chad_captain.research.web import WebProfile, research_web

logger = logging.getLogger(__name__)

DEFAULT_TTL = timedelta(days=7)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AppProfile(BaseModel):
    """Combined research artifact for one app."""

    app_id: str
    generated_at: str = Field(default_factory=_now_iso)
    local: LocalProfile
    web: WebProfile = Field(default_factory=lambda: WebProfile.skipped("not requested"))
    summary: str = ""

    @property
    def generated_dt(self) -> datetime:
        return datetime.fromisoformat(self.generated_at)


def load_profile(ws: AppWorkspace) -> AppProfile | None:
    if not ws.research_path.exists():
        return None
    try:
        return AppProfile.model_validate_json(ws.research_path.read_text())
    except Exception as e:
        logger.warning("research cache parse failed for %s: %s", ws.app_id, e)
        return None


def profile_is_fresh(profile: AppProfile, *, ttl: timedelta = DEFAULT_TTL) -> bool:
    try:
        age = datetime.now(timezone.utc) - profile.generated_dt
    except Exception:
        return False
    return age <= ttl


def synthesize_profile(
    ws: AppWorkspace,
    repo_path: str | Path,
    *,
    refresh: bool = False,
    do_web: bool = True,
    ttl: timedelta = DEFAULT_TTL,
) -> AppProfile:
    """Build (or fetch from cache) an AppProfile for ``ws``.

    - When a fresh cache exists and ``refresh=False``, return it untouched.
    - Otherwise scan the repo on disk, optionally call the web researcher,
      and write the new profile atomically.
    """
    if not refresh:
        existing = load_profile(ws)
        if existing is not None and profile_is_fresh(existing, ttl=ttl):
            return existing

    local = scan_local(repo_path)
    summary = _summary_from_local(local)

    if do_web:
        web = research_web(
            name=local.name,
            summary=summary,
            languages=local.languages,
            recent_commit_subjects=[c.subject for c in local.recent_commits],
        )
    else:
        web = WebProfile.skipped("do_web=False")

    profile = AppProfile(app_id=ws.app_id, local=local, web=web, summary=summary)
    ws.ensure()
    atomic_write(ws.research_path, profile.model_dump_json(indent=2))
    return profile


def _summary_from_local(local: LocalProfile) -> str:
    """Cheap one-paragraph summary used as the seed for web research and the
    dashboard preview. README excerpt > pyproject description > repo name."""
    if local.readme_excerpt:
        # Skip leading heading-only paragraphs (e.g. "# Title\n\nReal text.")
        paras = [p.strip() for p in local.readme_excerpt.split("\n\n") if p.strip()]
        for para in paras:
            if all(line.lstrip().startswith("#") or not line.strip() for line in para.splitlines()):
                continue
            return para[:600]
    desc = _description_from_pyproject(local.manifests.get("pyproject.toml", ""))
    if desc:
        return desc
    desc = _description_from_package_json(local.manifests.get("package.json", ""))
    if desc:
        return desc
    return f"Repository at {local.repo_path}"


def _description_from_pyproject(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("description"):
            # description = "..."
            _, _, value = s.partition("=")
            return value.strip().strip('"').strip("'")[:400]
    return ""


def _description_from_package_json(text: str) -> str:
    import json

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ""
    desc = data.get("description")
    return desc.strip()[:400] if isinstance(desc, str) else ""


__all__ = [
    "AppProfile",
    "DEFAULT_TTL",
    "load_profile",
    "profile_is_fresh",
    "synthesize_profile",
]

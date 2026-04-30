"""Web competitive landscape via the Pro/Max subscription claude CLI.

Optional and resilient: if the CLI is missing, times out, or otherwise fails,
we return a ``WebProfile`` with ``status="skipped"`` and a reason. The
synthesizer carries on with local-only data — research is augmentation, not
critical-path.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from chad_captain.llm import CLAUDE_BIN, LLMError, claude_complete

logger = logging.getLogger(__name__)


WEB_PROMPT_TEMPLATE = """You are doing market and competitive research on a software project.

Project: {name}
Top-line description (extracted from README + manifests):
{summary}

Languages: {languages}
Recent commit subjects (last few):
{recent_commits}

Produce a concise competitive-landscape brief in markdown with these sections:

## Positioning
One paragraph: who this project is for, what category it sits in.

## Comparable products
3-6 named comparable products / projects (open-source or commercial). For each:
- one-line description
- one-line differentiation vs. our project (what they do that we don't, or vice versa)

## Whitespace
2-3 specific gaps or unmet needs in the category that this project could exploit.

## Risks
2-3 named risks (incumbent reaction, dependency lock-in, regulatory, etc.) with one line each.

Be specific. Use real product names. Do NOT invent — if you don't know of a clear comparable, say so. Skip filler.
"""


class WebProfile(BaseModel):
    status: Literal["ok", "skipped", "error"] = "ok"
    reason: str = ""
    landscape_md: str = ""
    model: str = ""

    @classmethod
    def skipped(cls, reason: str) -> "WebProfile":
        return cls(status="skipped", reason=reason)

    @classmethod
    def errored(cls, reason: str) -> "WebProfile":
        return cls(status="error", reason=reason)


def research_web(
    *,
    name: str,
    summary: str,
    languages: dict[str, int] | None = None,
    recent_commit_subjects: list[str] | None = None,
    model: str = "opus",
    timeout: int = 120,
) -> WebProfile:
    """Run a single Claude call to draft a competitive-landscape brief.

    Failures (missing CLI, timeout, empty result) come back as a non-OK
    ``WebProfile`` rather than raising — research is best-effort augmentation.
    """
    bin_path = Path(CLAUDE_BIN)
    if not bin_path.exists() and not shutil.which(bin_path.name):
        return WebProfile.skipped(f"claude CLI not found at {CLAUDE_BIN}")

    languages = languages or {}
    commits = recent_commit_subjects or []

    prompt = WEB_PROMPT_TEMPLATE.format(
        name=name or "(unnamed project)",
        summary=(summary or "(no summary available)")[:2000],
        languages=", ".join(f"{k}:{v}" for k, v in list(languages.items())[:8]) or "(unknown)",
        recent_commits="\n".join(f"- {s}" for s in commits[:10]) or "(no commit history)",
    )

    try:
        text = claude_complete(prompt, model=model, timeout=timeout)
    except LLMError as e:
        logger.warning("web research errored: %s", e)
        return WebProfile.errored(str(e))

    return WebProfile(status="ok", landscape_md=text.strip(), model=model)


__all__ = ["WebProfile", "research_web"]

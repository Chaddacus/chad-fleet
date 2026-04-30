"""Voice-config-pluggable variation drafter.

Ported from chad-agent/servers/marketing/drafter.py pass-2 logic.
The cluster-picker (pass 1) is not ported — it is chad-marketing-specific.
This module handles only the variation-drafting (pass 2), but is
parameterized entirely by VoiceConfig so any persona can drive it.

Key behaviors preserved from the source:
- Deterministic provider/shape per variation (from VoiceConfig.variation_table)
- Sequential drafting: V2 sees V1's opener; V3 sees both (prior_openers token)
- Smart-quote normalization (Codex emits curly quotes)
- SKIP token detection
- Secret leak guard via _has_secret
"""

from __future__ import annotations

import logging
import re
from xml.sax.saxutils import escape as xml_escape

from voice_drafter import llm as _llm
from voice_drafter.types import Draft, SignalItem, SignalPack, VoiceConfig

logger = logging.getLogger("voice-drafter.drafter")

# Secret patterns — belt-and-suspenders guard. Mirrors anonymizer.py pattern.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ANTHROPIC_API_KEY", re.IGNORECASE),
    re.compile(r"CHAD_ZOOM_[A-Z_]+", re.IGNORECASE),
    re.compile(r"OPENAI_API_KEY", re.IGNORECASE),
    re.compile(r"[A-Z0-9_]{4,}_TOKEN", re.IGNORECASE),
    re.compile(r"[A-Z0-9_]{4,}_SECRET", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
)


def _has_secret(text: str) -> tuple[bool, str | None]:
    for pat in _SECRET_PATTERNS:
        m = pat.search(text)
        if m:
            return True, pat.pattern
    return False, None


class Drafter:
    """Stateless variation drafter parameterized by VoiceConfig.

    Usage::

        voice = load_voice_config(Path("chad-simon-ai.yaml"))
        drafter = Drafter(voice)
        drafts = drafter.draft_variations(candidate_item, signal_pack)
    """

    def __init__(self, voice: VoiceConfig) -> None:
        self._voice = voice

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def draft_variations(self, candidate: SignalItem, signal_pack: SignalPack) -> list[Draft]:
        """Sequentially produce N variations. Each variation sees prior openers.

        Each variation is driven by the corresponding VariationSpec in
        VoiceConfig.variation_table. Variations are produced in order so that
        V2's prompt contains V1's opener and V3's prompt contains both V1 and
        V2's openers. This forces opener diversity across the variation set.

        Returns exactly N Draft objects (one per variation_table entry). A
        draft with an empty body and a skip_reason indicates a SKIP or error.
        """
        shape_map = self._voice.opener_shape_map()
        results: list[Draft] = []
        prior_openers: list[str] = []

        for spec in self._voice.variation_table:
            shape_obj = shape_map.get(spec.opener_shape)
            opener_instruction = shape_obj.instruction if shape_obj else ""

            user_blob = self._build_user_blob(
                candidate=candidate,
                signal_pack=signal_pack,
                variation_index=spec.variation_index,
                opener_shape=spec.opener_shape,
                opener_instruction=opener_instruction,
                prior_openers=list(prior_openers),
                total_variations=self._voice.n,
            )

            if spec.provider == "claude":
                draft = self._call_claude(
                    user_blob=user_blob,
                    variation_index=spec.variation_index,
                    model=spec.model or "opus",
                    opener_shape=spec.opener_shape,
                )
            else:
                draft = self._call_codex(
                    user_blob=user_blob,
                    variation_index=spec.variation_index,
                    opener_shape=spec.opener_shape,
                )

            results.append(draft)

            # Thread this variation's opener into the next variation's prompt.
            if draft.body:
                first_two = "\n".join(draft.body.splitlines()[:2]).strip()
                if first_two:
                    prior_openers.append(first_two)

        return results

    # ------------------------------------------------------------------ #
    # Prompt building                                                      #
    # ------------------------------------------------------------------ #

    def _build_user_blob(
        self,
        *,
        candidate: SignalItem,
        signal_pack: SignalPack,
        variation_index: int,
        opener_shape: str,
        opener_instruction: str,
        prior_openers: list[str],
        total_variations: int,
    ) -> str:
        # Collect all items from the pack as supporting signals.
        bullets = "\n".join(
            f"- [{item.metadata.get('source', signal_pack.source)}] "
            f"{item.metadata.get('topic', item.id)}: {item.text[:1200]}"
            for item in signal_pack.items
        )

        priors = ""
        if prior_openers:
            priors = "<prior_openers do_not_repeat>\n" + xml_escape(
                "\n".join(f"- V{i + 1}: {p}" for i, p in enumerate(prior_openers))
            ) + "\n</prior_openers>"

        return (
            f"<cluster_topic>{xml_escape(candidate.metadata.get('topic', candidate.id))}</cluster_topic>\n"
            f"<rationale>{xml_escape(candidate.metadata.get('rationale', ''))}</rationale>\n"
            f"<signals>\n{xml_escape(bullets)}\n</signals>\n"
            f"<variation_index>{variation_index} of {total_variations}</variation_index>\n"
            f"<opener_shape_required>{xml_escape(opener_shape)}</opener_shape_required>\n"
            f"<opener_shape_instruction>{xml_escape(opener_instruction)}</opener_shape_instruction>\n"
            f"{priors}"
        )

    # ------------------------------------------------------------------ #
    # LLM dispatch                                                         #
    # ------------------------------------------------------------------ #

    def _call_claude(
        self,
        *,
        user_blob: str,
        variation_index: int,
        model: str,
        opener_shape: str,
    ) -> Draft:
        try:
            body = _llm.claude_complete(
                user_blob,
                model=model,
                system=self._voice.voice_prompt,
                timeout=120,
            )
        except (_llm.LLMError, RuntimeError) as exc:
            logger.warning(
                "Claude draft (V%d %s) failed: %s", variation_index, opener_shape, exc
            )
            return Draft(
                variation_index=variation_index,
                provider="claude",
                opener_shape=opener_shape,
                body="",
                skip_reason=f"claude_error: {type(exc).__name__}",
            )
        return self._post_process(body, variation_index=variation_index, provider="claude", opener_shape=opener_shape)

    def _call_codex(
        self,
        *,
        user_blob: str,
        variation_index: int,
        opener_shape: str,
    ) -> Draft:
        # Codex has no separate system prompt flag — fold voice_prompt into user message.
        full_prompt = (
            "You are following these system instructions:\n\n"
            "<system>\n"
            + self._voice.voice_prompt
            + "\n</system>\n\n"
            "Now draft the post per these inputs:\n\n"
            + user_blob
            + "\n\nReturn ONLY the post body (markdown). No commentary, no fences."
        )
        try:
            body = _llm.codex_complete(full_prompt, timeout=240)
        except _llm.LLMError as exc:
            logger.warning(
                "Codex draft (V%d %s) failed: %s", variation_index, opener_shape, exc
            )
            return Draft(
                variation_index=variation_index,
                provider="codex",
                opener_shape=opener_shape,
                body="",
                skip_reason=f"codex_error: {type(exc).__name__}",
            )
        return self._post_process(body, variation_index=variation_index, provider="codex", opener_shape=opener_shape)

    # ------------------------------------------------------------------ #
    # Post-processing                                                      #
    # ------------------------------------------------------------------ #

    def _post_process(
        self,
        body: str,
        *,
        variation_index: int,
        provider: str,
        opener_shape: str,
    ) -> Draft:
        """Normalize quotes, detect SKIP token, guard against secret leaks."""
        body = (body or "").strip()

        # Normalize fancy unicode punctuation to ASCII for paste-fidelity.
        # (Codex often emits curly quotes / smart apostrophes.)
        body = (
            body
            .replace("‘", "'").replace("’", "'")   # left/right single quotation marks
            .replace("“", '"').replace("”", '"')   # left/right double quotation marks
            .replace("–", "-")                          # en-dash -> hyphen
            # em-dash (—) is intentionally preserved
        )

        if body.startswith("SKIP"):
            return Draft(
                variation_index=variation_index,
                provider=provider,
                opener_shape=opener_shape,
                body="",
                skip_reason=body[4:].strip(" :—-") or "drafter said SKIP",
            )

        has, why = _has_secret(body)
        if has:
            return Draft(
                variation_index=variation_index,
                provider=provider,
                opener_shape=opener_shape,
                body="",
                skip_reason=f"secret leak in draft: {why}",
            )

        return Draft(
            variation_index=variation_index,
            provider=provider,
            opener_shape=opener_shape,
            body=body,
        )


__all__ = ["Drafter"]

"""Tests for Drafter.draft_variations.

All LLM calls are mocked. Verifies:
- Correct number of drafts returned
- Distinct opener shapes per variation
- Prior opener threading appears in user prompt
- Smart-quote normalization
- SKIP token handling
- Secret leak guard
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, call

import pytest

from voice_drafter.drafter import Drafter, _has_secret
from voice_drafter.types import (
    Draft,
    OpenerShape,
    SignalItem,
    SignalPack,
    VariationSpec,
    VoiceConfig,
)


# ---- Fixtures ----------------------------------------------------------------

def _make_voice(n: int = 3) -> VoiceConfig:
    shapes = [
        OpenerShape(key="analogy_metaphor", instruction="Open with analogy."),
        OpenerShape(key="stat_or_named_pattern", instruction="Open with stat."),
        OpenerShape(key="anti_thesis_contrarian", instruction="Flip conventional wisdom."),
    ]
    table = [
        VariationSpec(variation_index=1, provider="claude", model="opus", opener_shape="analogy_metaphor"),
        VariationSpec(variation_index=2, provider="claude", model="opus", opener_shape="stat_or_named_pattern"),
        VariationSpec(variation_index=3, provider="codex", model=None, opener_shape="anti_thesis_contrarian"),
    ]
    return VoiceConfig(
        name="test-voice",
        voice_prompt="Write punchy posts.",
        opener_shapes=shapes[:n],
        variation_table=table[:n],
        n=n,
    )


def _make_pack() -> SignalPack:
    return SignalPack(
        items=[
            SignalItem(
                id="item-1",
                text="We shipped two-pass safety for agents.",
                score=0.9,
                metadata={"topic": "two-pass safety", "rationale": "strong cluster"},
            )
        ],
        source="omni-mem",
        generated_at=datetime.now(timezone.utc),
    )


def _candidate() -> SignalItem:
    return SignalItem(
        id="item-1",
        text="We shipped two-pass safety for agents.",
        score=0.9,
        metadata={"topic": "two-pass safety", "rationale": "strong cluster"},
    )


# ---- Tests -------------------------------------------------------------------

class TestDraftVariations:
    def test_returns_n_drafts(self):
        voice = _make_voice(3)
        drafter = Drafter(voice)
        responses = [
            "Analogy opener here.\nBody line 1.\nMore content.",
            "Stat opener here.\nBody line 2.\nMore content.",
            "Contrarian opener here.\nBody line 3.\nMore content.",
        ]

        call_count = 0
        def fake_claude(prompt, *, model="opus", system=None, timeout=90):
            nonlocal call_count
            r = responses[call_count % len(responses)]
            call_count += 1
            return r

        def fake_codex(prompt, *, model=None, timeout=180, cwd=None):
            return responses[2]

        with patch("voice_drafter.drafter._llm.claude_complete", side_effect=fake_claude):
            with patch("voice_drafter.drafter._llm.codex_complete", side_effect=fake_codex):
                drafts = drafter.draft_variations(_candidate(), _make_pack())

        assert len(drafts) == 3

    def test_distinct_opener_shapes(self):
        voice = _make_voice(3)
        drafter = Drafter(voice)

        call_count = 0
        bodies = ["V1 opener.\nV1 body.", "V2 opener.\nV2 body.", "V3 opener.\nV3 body."]

        def fake_claude(prompt, *, model="opus", system=None, timeout=90):
            nonlocal call_count
            r = bodies[call_count % 2]
            call_count += 1
            return r

        def fake_codex(prompt, *, model=None, timeout=180, cwd=None):
            return bodies[2]

        with patch("voice_drafter.drafter._llm.claude_complete", side_effect=fake_claude):
            with patch("voice_drafter.drafter._llm.codex_complete", side_effect=fake_codex):
                drafts = drafter.draft_variations(_candidate(), _make_pack())

        shapes = [d.opener_shape for d in drafts]
        assert len(set(shapes)) == 3, f"Expected 3 distinct shapes, got {shapes}"

    def test_prior_opener_threading(self):
        """V2 user prompt must contain V1's opener in a prior_openers block."""
        voice = _make_voice(3)
        drafter = Drafter(voice)

        captured_prompts: list[str] = []

        def fake_claude(prompt, *, model="opus", system=None, timeout=90):
            captured_prompts.append(prompt)
            return "First line opener.\nSecond line body.\nMore stuff."

        def fake_codex(prompt, *, model=None, timeout=180, cwd=None):
            captured_prompts.append(prompt)
            return "Codex body line.\nMore codex stuff."

        with patch("voice_drafter.drafter._llm.claude_complete", side_effect=fake_claude):
            with patch("voice_drafter.drafter._llm.codex_complete", side_effect=fake_codex):
                drafter.draft_variations(_candidate(), _make_pack())

        # V2 prompt (index 1) should contain the prior_openers block with V1's opener
        assert len(captured_prompts) >= 2
        v2_prompt = captured_prompts[1]
        assert "prior_openers" in v2_prompt
        assert "V1:" in v2_prompt

    def test_v3_sees_both_prior_openers(self):
        """V3's prompt (codex) should contain both V1 and V2 openers."""
        voice = _make_voice(3)
        drafter = Drafter(voice)

        captured_codex_prompt: list[str] = []

        def fake_claude(prompt, *, model="opus", system=None, timeout=90):
            return "Claude opener line.\nClaude body."

        def fake_codex(prompt, *, model=None, timeout=180, cwd=None):
            captured_codex_prompt.append(prompt)
            return "Codex response."

        with patch("voice_drafter.drafter._llm.claude_complete", side_effect=fake_claude):
            with patch("voice_drafter.drafter._llm.codex_complete", side_effect=fake_codex):
                drafter.draft_variations(_candidate(), _make_pack())

        assert captured_codex_prompt
        p = captured_codex_prompt[0]
        assert "V1:" in p
        assert "V2:" in p

    def test_smart_quote_normalization(self):
        voice = _make_voice(1)
        drafter = Drafter(voice)
        # Codex/Claude may return curly quotes
        curly_body = "‘smart apostrophe’ and “double” quotes"

        with patch("voice_drafter.drafter._llm.claude_complete", return_value=curly_body):
            drafts = drafter.draft_variations(_candidate(), _make_pack())

        assert drafts[0].body == "'smart apostrophe' and \"double\" quotes"

    def test_skip_token_detected(self):
        voice = _make_voice(1)
        drafter = Drafter(voice)

        with patch("voice_drafter.drafter._llm.claude_complete", return_value="SKIP: not enough signal"):
            drafts = drafter.draft_variations(_candidate(), _make_pack())

        assert drafts[0].body == ""
        assert "not enough signal" in (drafts[0].skip_reason or "")

    def test_secret_leak_guard(self):
        voice = _make_voice(1)
        drafter = Drafter(voice)
        leaky = "Great post about sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ token usage."

        with patch("voice_drafter.drafter._llm.claude_complete", return_value=leaky):
            drafts = drafter.draft_variations(_candidate(), _make_pack())

        assert drafts[0].body == ""
        assert "secret leak" in (drafts[0].skip_reason or "")

    def test_llm_error_returns_skip_draft(self):
        from voice_drafter.llm import LLMError

        voice = _make_voice(1)
        drafter = Drafter(voice)

        with patch("voice_drafter.drafter._llm.claude_complete", side_effect=LLMError("timeout")):
            drafts = drafter.draft_variations(_candidate(), _make_pack())

        assert drafts[0].body == ""
        assert "claude_error" in (drafts[0].skip_reason or "")

    def test_provider_assigned_correctly(self):
        """V1 and V2 use claude; V3 uses codex."""
        voice = _make_voice(3)
        drafter = Drafter(voice)

        with patch("voice_drafter.drafter._llm.claude_complete", return_value="Claude body."):
            with patch("voice_drafter.drafter._llm.codex_complete", return_value="Codex body."):
                drafts = drafter.draft_variations(_candidate(), _make_pack())

        assert drafts[0].provider == "claude"
        assert drafts[1].provider == "claude"
        assert drafts[2].provider == "codex"


class TestHasSecret:
    def test_api_key_detected(self):
        has, why = _has_secret("sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ12345 is the key")
        assert has is True

    def test_clean_text_passes(self):
        has, _ = _has_secret("This is a clean LinkedIn post about agents.")
        assert has is False

    def test_token_pattern_detected(self):
        has, _ = _has_secret("export ZOOM_TOKEN=abc123")
        assert has is True

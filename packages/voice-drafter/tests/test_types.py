"""Pydantic round-trip tests for all core types."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from voice_drafter.types import (
    Draft,
    OpenerShape,
    SignalItem,
    SignalPack,
    VariationSpec,
    VoiceConfig,
)


class TestSignalItem:
    def test_round_trip(self):
        item = SignalItem(id="abc", text="some anonymized prose", score=0.8)
        d = item.model_dump()
        item2 = SignalItem(**d)
        assert item2.id == "abc"
        assert item2.score == 0.8

    def test_metadata_defaults_empty(self):
        item = SignalItem(id="x", text="y", score=0.0)
        assert item.metadata == {}

    def test_from_dict(self):
        raw = {"id": "i1", "text": "hello", "score": 0.5, "metadata": {"key": "val"}}
        item = SignalItem(**raw)
        assert item.metadata["key"] == "val"


class TestSignalPack:
    def test_round_trip(self):
        now = datetime.now(timezone.utc)
        pack = SignalPack(
            items=[SignalItem(id="s1", text="text", score=0.9)],
            source="git-log",
            generated_at=now,
        )
        d = pack.model_dump()
        pack2 = SignalPack(**d)
        assert pack2.source == "git-log"
        assert len(pack2.items) == 1

    def test_empty_items(self):
        pack = SignalPack(items=[], source="omni-mem", generated_at=datetime.now(timezone.utc))
        assert pack.items == []


class TestVoiceConfig:
    def _make_config(self, n_variations: int = 2) -> VoiceConfig:
        shapes = [
            OpenerShape(key="analogy_metaphor", instruction="Open with analogy."),
            OpenerShape(key="stat_or_named_pattern", instruction="Open with a stat."),
        ]
        table = [
            VariationSpec(variation_index=1, provider="claude", model="opus", opener_shape="analogy_metaphor"),
            VariationSpec(variation_index=2, provider="codex", model=None, opener_shape="stat_or_named_pattern"),
        ][:n_variations]
        return VoiceConfig(
            name="test-voice",
            voice_prompt="You are a test voice.",
            opener_shapes=shapes[:n_variations],
            variation_table=table,
            n=n_variations,
        )

    def test_valid_config_round_trip(self):
        vc = self._make_config(2)
        d = vc.model_dump()
        vc2 = VoiceConfig(**d)
        assert vc2.name == "test-voice"
        assert vc2.n == 2

    def test_n_mismatch_raises(self):
        with pytest.raises(Exception):  # Pydantic ValidationError
            VoiceConfig(
                name="bad",
                voice_prompt="x",
                opener_shapes=[OpenerShape(key="k", instruction="i")],
                variation_table=[
                    VariationSpec(variation_index=1, provider="claude", opener_shape="k")
                ],
                n=99,  # wrong
            )

    def test_opener_shape_map(self):
        vc = self._make_config(2)
        m = vc.opener_shape_map()
        assert "analogy_metaphor" in m
        assert m["analogy_metaphor"].instruction == "Open with analogy."

    def test_rubric_path_optional(self):
        vc = self._make_config(1)
        assert vc.rubric_path is None

    def test_rubric_path_set(self):
        shapes = [OpenerShape(key="k", instruction="i")]
        table = [VariationSpec(variation_index=1, provider="claude", opener_shape="k")]
        vc = VoiceConfig(
            name="x", voice_prompt="y", opener_shapes=shapes, variation_table=table, n=1,
            rubric_path=Path("/tmp/rubric.md"),
        )
        assert vc.rubric_path == Path("/tmp/rubric.md")


class TestDraft:
    def test_round_trip(self):
        d = Draft(variation_index=1, provider="claude", opener_shape="analogy_metaphor", body="hello")
        raw = d.model_dump()
        d2 = Draft(**raw)
        assert d2.body == "hello"
        assert d2.skip_reason is None

    def test_skip_reason(self):
        d = Draft(variation_index=2, provider="codex", opener_shape="anti_thesis_contrarian", body="", skip_reason="drafter said SKIP")
        assert d.skip_reason == "drafter said SKIP"
        assert d.body == ""

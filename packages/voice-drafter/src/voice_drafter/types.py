"""Core data types for voice-drafter.

All types are Pydantic v2 models so they round-trip cleanly through JSON/dict.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator


class SignalItem(BaseModel):
    id: str
    text: str            # anonymized prose
    score: float         # 0..1
    metadata: dict = {}  # arbitrary source-specific bag


class SignalPack(BaseModel):
    items: list[SignalItem]
    source: str          # "omni-mem", "git-log", "github-issues", "manuscript-progress"
    generated_at: datetime


class OpenerShape(BaseModel):
    key: str             # "analogy_metaphor", "stat_or_named_pattern", "anti_thesis_contrarian"
    instruction: str     # full prompt fragment


class VariationSpec(BaseModel):
    variation_index: int       # 1..N
    provider: Literal["claude", "codex"]
    model: str | None = None   # e.g. "opus", "sonnet"; None = provider default
    opener_shape: str          # references OpenerShape.key


class VoiceConfig(BaseModel):
    name: str                        # e.g. "chad-simon-ai", "chadacys-fantasy"
    voice_prompt: str                # the system-prompt prose
    opener_shapes: list[OpenerShape]
    variation_table: list[VariationSpec]  # N entries, one per variation slot
    n: int                           # number of variations per topic; usually len(variation_table)
    rubric_path: Path | None = None  # optional rubric MD for self-scoring

    @model_validator(mode="after")
    def _validate_n(self) -> "VoiceConfig":
        if self.n != len(self.variation_table):
            raise ValueError(
                f"VoiceConfig.n={self.n} does not match len(variation_table)={len(self.variation_table)}"
            )
        return self

    def opener_shape_map(self) -> dict[str, OpenerShape]:
        """Return key -> OpenerShape for fast lookup."""
        return {s.key: s for s in self.opener_shapes}


class Draft(BaseModel):
    variation_index: int
    provider: str
    opener_shape: str
    body: str            # the rendered draft text; empty string on SKIP/error
    skip_reason: str | None = None


__all__ = [
    "SignalItem",
    "SignalPack",
    "OpenerShape",
    "VariationSpec",
    "VoiceConfig",
    "Draft",
]

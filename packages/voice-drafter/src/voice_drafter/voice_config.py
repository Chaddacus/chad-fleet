"""Voice config loading and registry.

Voice configs are plain YAML files. No plugin discovery, no entry-points magic.
Configs are loaded from explicit paths supplied by the caller.

Two public functions:
- load_voice_config(path) -> VoiceConfig
- bundled_voice_configs()  -> dict[str, Path]  (empty in this slice)
"""

from __future__ import annotations

from pathlib import Path

import yaml

from voice_drafter.types import OpenerShape, VariationSpec, VoiceConfig


def load_voice_config(path: Path) -> VoiceConfig:
    """Load and validate a VoiceConfig from a YAML file.

    The YAML structure mirrors VoiceConfig's fields. Example::

        name: chad-simon-ai
        voice_prompt: |
          You are Chad Simon's voice ...
        opener_shapes:
          - key: analogy_metaphor
            instruction: "Open with a 2-line analogy..."
        variation_table:
          - variation_index: 1
            provider: claude
            model: opus
            opener_shape: analogy_metaphor
        n: 1

    Raises ValueError if the YAML is invalid or the config fails validation.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read voice config at {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in voice config {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Voice config {path} must be a YAML mapping, got {type(raw).__name__}")

    # Coerce nested structures to their typed forms so Pydantic validation
    # catches shape errors with useful messages.
    try:
        opener_shapes = [
            OpenerShape(**s) if isinstance(s, dict) else s
            for s in (raw.get("opener_shapes") or [])
        ]
        variation_table = [
            VariationSpec(**v) if isinstance(v, dict) else v
            for v in (raw.get("variation_table") or [])
        ]
    except TypeError as exc:
        raise ValueError(f"Voice config {path} has malformed opener_shapes or variation_table: {exc}") from exc

    try:
        config = VoiceConfig(
            name=raw["name"],
            voice_prompt=raw["voice_prompt"],
            opener_shapes=opener_shapes,
            variation_table=variation_table,
            n=raw["n"],
            rubric_path=Path(raw["rubric_path"]) if raw.get("rubric_path") else None,
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Voice config {path} is missing required field: {exc}") from exc

    return config


def bundled_voice_configs() -> dict[str, Path]:
    """Return name->path map for voice configs shipped with this package.

    No bundled voices in this slice. Future voices (chad-simon-ai, chadacys-fantasy)
    will live under src/voice_drafter/voices/ and be registered here.
    """
    return {}


__all__ = ["load_voice_config", "bundled_voice_configs"]

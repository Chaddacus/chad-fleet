"""Tests for load_voice_config and bundled_voice_configs."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from voice_drafter.voice_config import bundled_voice_configs, load_voice_config


VALID_YAML = textwrap.dedent("""\
    name: chad-simon-ai
    voice_prompt: |
      You are Chad Simon. Write punchy LinkedIn posts.
    opener_shapes:
      - key: analogy_metaphor
        instruction: "Open with a 2-line analogy."
      - key: stat_or_named_pattern
        instruction: "Open with a concrete stat."
      - key: anti_thesis_contrarian
        instruction: "Open with a contrarian flip."
    variation_table:
      - variation_index: 1
        provider: claude
        model: opus
        opener_shape: analogy_metaphor
      - variation_index: 2
        provider: claude
        model: opus
        opener_shape: stat_or_named_pattern
      - variation_index: 3
        provider: codex
        opener_shape: anti_thesis_contrarian
    n: 3
""")


def _write(tmp_path: Path, content: str, filename: str = "voice.yaml") -> Path:
    p = tmp_path / filename
    p.write_text(content)
    return p


class TestLoadVoiceConfig:
    def test_valid_load(self, tmp_path):
        p = _write(tmp_path, VALID_YAML)
        vc = load_voice_config(p)
        assert vc.name == "chad-simon-ai"
        assert vc.n == 3
        assert len(vc.opener_shapes) == 3
        assert len(vc.variation_table) == 3

    def test_variation_table_sums_to_n(self, tmp_path):
        p = _write(tmp_path, VALID_YAML)
        vc = load_voice_config(p)
        assert vc.n == len(vc.variation_table)

    def test_n_mismatch_raises(self, tmp_path):
        bad = VALID_YAML.replace("n: 3", "n: 99")
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError):
            load_voice_config(p)

    def test_missing_required_field_raises(self, tmp_path):
        bad = textwrap.dedent("""\
            voice_prompt: "hi"
            opener_shapes: []
            variation_table: []
            n: 0
        """)
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError):
            load_voice_config(p)

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Cannot read"):
            load_voice_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        p = _write(tmp_path, "{ not: valid: yaml: : : }")
        with pytest.raises(ValueError):
            load_voice_config(p)

    def test_rubric_path_loaded(self, tmp_path):
        yaml_with_rubric = VALID_YAML + "rubric_path: /tmp/rubric.md\n"
        p = _write(tmp_path, yaml_with_rubric)
        vc = load_voice_config(p)
        assert vc.rubric_path == Path("/tmp/rubric.md")

    def test_opener_shape_map_consistent(self, tmp_path):
        p = _write(tmp_path, VALID_YAML)
        vc = load_voice_config(p)
        m = vc.opener_shape_map()
        for spec in vc.variation_table:
            assert spec.opener_shape in m, f"{spec.opener_shape} missing from opener_shape_map"


class TestBundledVoiceConfigs:
    def test_returns_dict(self):
        result = bundled_voice_configs()
        assert isinstance(result, dict)

    def test_empty_in_this_slice(self):
        result = bundled_voice_configs()
        assert result == {}

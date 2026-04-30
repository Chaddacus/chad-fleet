"""FastMCP server exposing voice-drafter tools.

Tools:
- voice_drafter_draft       — draft variations for a single candidate
- voice_drafter_list_voices — list bundled voice config names
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

try:
    from fastmcp import FastMCP
except ImportError:
    # Fallback: mcp SDK
    from mcp.server.fastmcp import FastMCP  # type: ignore[no-reattr]

from voice_drafter.drafter import Drafter
from voice_drafter.types import SignalItem, SignalPack
from voice_drafter.voice_config import bundled_voice_configs, load_voice_config

logger = logging.getLogger("voice-drafter.server")

mcp = FastMCP("voice-drafter")


@mcp.tool()
def voice_drafter_draft(
    signal_pack: dict,
    voice_config: dict,
    candidate_id: str,
) -> list[dict]:
    """Draft N variations for a single candidate signal item.

    Args:
        signal_pack:  Dict matching SignalPack schema (items, source, generated_at).
        voice_config: Dict matching VoiceConfig schema (inline config).
        candidate_id: ID of the SignalItem in signal_pack.items to draft for.

    Returns list of Draft dicts (variation_index, provider, opener_shape, body, skip_reason).
    """
    from voice_drafter.types import OpenerShape, VariationSpec, VoiceConfig  # local to keep top-level clean

    # Parse voice_config dict into VoiceConfig, coercing nested objects.
    raw_vc = dict(voice_config)
    raw_vc["opener_shapes"] = [
        OpenerShape(**s) if isinstance(s, dict) else s
        for s in raw_vc.get("opener_shapes") or []
    ]
    raw_vc["variation_table"] = [
        VariationSpec(**v) if isinstance(v, dict) else v
        for v in raw_vc.get("variation_table") or []
    ]
    if raw_vc.get("rubric_path"):
        raw_vc["rubric_path"] = Path(raw_vc["rubric_path"])
    vc = VoiceConfig(**raw_vc)

    # Parse signal_pack into typed form.
    raw_sp = dict(signal_pack)
    raw_sp["items"] = [
        SignalItem(**it) if isinstance(it, dict) else it
        for it in raw_sp.get("items") or []
    ]
    if isinstance(raw_sp.get("generated_at"), str):
        raw_sp["generated_at"] = datetime.fromisoformat(raw_sp["generated_at"])
    sp = SignalPack(**raw_sp)

    # Find the candidate item.
    candidate = next((it for it in sp.items if it.id == candidate_id), None)
    if candidate is None:
        raise ValueError(f"candidate_id {candidate_id!r} not found in signal_pack.items")

    drafter = Drafter(vc)
    drafts = drafter.draft_variations(candidate, sp)
    return [d.model_dump() for d in drafts]


@mcp.tool()
def voice_drafter_list_voices() -> list[str]:
    """Return the names of all bundled voice configs."""
    return list(bundled_voice_configs().keys())


def create_server() -> FastMCP:
    return mcp


__all__ = ["mcp", "create_server"]

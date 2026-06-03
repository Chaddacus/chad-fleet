"""omni-mem source (stub)."""

from __future__ import annotations


class MemorySource:
    """Stub for future omni-mem integration. Returns empty list."""

    name = "omni-mem"

    def fetch(self) -> dict:
        return {"memories": []}

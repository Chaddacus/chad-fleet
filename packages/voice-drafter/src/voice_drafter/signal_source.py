"""SignalSource protocol and the OmniMem reference implementation.

Concrete signal sources (git-log, GitHub issues, manuscript progress) live in
their own packages; they must satisfy the SignalSource protocol. This module
provides:

1. The Protocol itself — the minimal contract.
2. OmniMemSignalSource — the canonical example impl that drives Docker via
   subprocess. It proves the protocol is workable without coupling the package
   to omni-mem in any mandatory way.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from voice_drafter.types import SignalItem, SignalPack

logger = logging.getLogger("voice-drafter.signal_source")


@runtime_checkable
class SignalSource(Protocol):
    name: str

    def fetch(self, since: datetime, limit: int) -> SignalPack:
        """Return a SignalPack with items scored 0..1, anonymized, ready for the drafter."""
        ...


class OmniMemSignalSource:
    """Example SignalSource that queries omni-mem via `docker exec`.

    Calls: docker exec omni-mem omni-mem search --query <query> --limit <limit>
    and coerces the JSON response into a SignalPack.

    Fail-open: if the subprocess fails, returns an empty SignalPack.
    """

    name = "omni-mem"

    def __init__(
        self,
        *,
        container: str = "omni-mem",
        query: str = "recent work signals",
        timeout: int = 30,
    ) -> None:
        self._container = container
        self._query = query
        self._timeout = timeout

    def fetch(self, since: datetime, limit: int) -> SignalPack:
        cmd = [
            "docker",
            "exec",
            self._container,
            "omni-mem",
            "search",
            "--query",
            self._query,
            "--limit",
            str(limit),
        ]
        logger.debug("OmniMemSignalSource.fetch: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("omni-mem docker exec timed out after %ds", self._timeout)
            return self._empty_pack()
        except FileNotFoundError:
            logger.warning("docker not found; skipping omni-mem fetch")
            return self._empty_pack()

        if proc.returncode != 0:
            logger.warning("omni-mem search failed (exit %d): %s", proc.returncode, (proc.stderr or "").strip()[:200])
            return self._empty_pack()

        try:
            raw = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as exc:
            logger.warning("omni-mem search returned non-JSON: %s", exc)
            return self._empty_pack()

        items: list[SignalItem] = []
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                try:
                    item = SignalItem(
                        id=str(entry.get("id") or entry.get("memory_id") or f"omni-{len(items)}"),
                        text=str(entry.get("content") or entry.get("text") or ""),
                        score=float(entry.get("score") or entry.get("relevance") or 0.5),
                        metadata={k: v for k, v in entry.items() if k not in {"id", "content", "text", "score", "relevance", "memory_id"}},
                    )
                    items.append(item)
                except (ValueError, TypeError) as exc:
                    logger.debug("skipping omni-mem entry: %s", exc)
                    continue

        return SignalPack(
            items=items,
            source=self.name,
            generated_at=datetime.now(timezone.utc),
        )

    def _empty_pack(self) -> SignalPack:
        return SignalPack(items=[], source=self.name, generated_at=datetime.now(timezone.utc))


__all__ = ["SignalSource", "OmniMemSignalSource"]

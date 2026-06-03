"""The StateSource protocol every source implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StateSource(Protocol):
    name: str

    def fetch(self) -> dict: ...

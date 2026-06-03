"""State sources — one module per source (SoC).

Re-exported here so `from state_aggregator.sources import RegistrySource, ...` keeps working
exactly as it did when this was a single module. Add a new source as its own file and export
it here; nothing else changes.
"""

from __future__ import annotations

from .base import StateSource
from .calendar import CalendarSource
from .email import EmailSource
from .inbox import InboxSource
from .memory import MemorySource
from .obsessive_loop import ObsessiveLoopSource
from .registry import RegistrySource
from .sessions import SessionsSource
from .tools import ToolsSource

__all__ = [
    "StateSource",
    "RegistrySource",
    "ObsessiveLoopSource",
    "InboxSource",
    "MemorySource",
    "SessionsSource",
    "ToolsSource",
    "EmailSource",
    "CalendarSource",
]

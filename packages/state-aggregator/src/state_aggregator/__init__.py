"""state-aggregator — unified fleet state snapshot."""

from .aggregator import Aggregator
from .types import AppSnapshot, FleetState, InboxItem

__version__ = "0.0.0"
__all__ = ["Aggregator", "FleetState", "AppSnapshot", "InboxItem"]

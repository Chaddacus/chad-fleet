"""tracked-app-registry — source-of-truth registry for Chad's fleet."""

from .models import Event, TrackedApp
from .registry import AppNotFound, Registry

__version__ = "0.0.0"
__all__ = ["Registry", "TrackedApp", "Event", "AppNotFound"]

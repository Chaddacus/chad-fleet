"""view-registry — registry of user-saved views for the genui-renderer."""

from .registry import Registry, ViewNotFound
from .types import SavedView

__version__ = "0.0.0"
__all__ = ["Registry", "SavedView", "ViewNotFound"]

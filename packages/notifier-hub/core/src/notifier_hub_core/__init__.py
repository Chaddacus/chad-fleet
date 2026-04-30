"""notifier-hub-core: routing engine and adapter protocol."""

__version__ = "0.0.0"

from notifier_hub_core.models import Action, Notification, SendResult
from notifier_hub_core.protocol import NotifierAdapter
from notifier_hub_core.hub import NotifierHub

__all__ = [
    "Action",
    "Notification",
    "SendResult",
    "NotifierAdapter",
    "NotifierHub",
]

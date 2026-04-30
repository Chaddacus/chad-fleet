"""chad-captain — runtime app wiring captain-core to the fleet."""

from chad_captain.config import CaptainConfig, load_config
from chad_captain.runner import CaptainRunner

__version__ = "0.1.0"
__all__ = ["CaptainConfig", "CaptainRunner", "load_config"]

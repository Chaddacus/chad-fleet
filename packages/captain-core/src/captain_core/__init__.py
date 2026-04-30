"""captain-core — proactive reasoning engine for Chad's Captain Agent."""

from captain_core.types import Playbook, StallAlert, NextAction, RecommendedSlice, Brief
from captain_core.playbooks import load_playbook, load_playbooks_dir, find_playbooks_for_app
from captain_core.stalls import detect_stalls
from captain_core.actions import next_actions
from captain_core.brief import compose_daily_brief

__version__ = "0.1.0"
__all__ = [
    "Playbook",
    "StallAlert",
    "NextAction",
    "RecommendedSlice",
    "Brief",
    "load_playbook",
    "load_playbooks_dir",
    "find_playbooks_for_app",
    "detect_stalls",
    "next_actions",
    "compose_daily_brief",
]

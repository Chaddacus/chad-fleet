"""Stall-detection thresholds and other captain-core defaults."""

from __future__ import annotations

# Days-since-last-progress thresholds keyed by app mode.
# Values: (warn_days, critical_days)
# Use None to indicate "never stall" for a given severity.
STALL_THRESHOLDS: dict[str, tuple[int | None, int | None]] = {
    "continuous": (3, 7),
    "event_driven": (14, 30),
    "launch_driven": (1, 2),    # interpreted relative to proximity to launch; see stalls.py
    "archived": (None, None),
    "shipped": (None, None),
}

# Default mode when app.mode is unrecognised
DEFAULT_MODE = "continuous"

# Maximum next-actions to return
NEXT_ACTIONS_CAP = 7

# Minimum keyword overlap score to consider a playbook trigger relevant
MIN_TRIGGER_SCORE = 1

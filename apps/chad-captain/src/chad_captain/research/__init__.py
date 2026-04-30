"""Research pipeline: local repo scan + web competitive landscape → app-profile.

Public surface:
    from chad_captain.research import AppProfile, synthesize_profile, load_profile

The cache lives at ``ws.research_path`` (``~/.chad/fleet/apps/<id>/research/app-profile.json``).
Default TTL is 7 days; pass ``refresh=True`` to force a rebuild.
"""

from chad_captain.research.local import LocalProfile, scan_local
from chad_captain.research.synthesize import (
    AppProfile,
    load_profile,
    profile_is_fresh,
    synthesize_profile,
)
from chad_captain.research.web import WebProfile, research_web

__all__ = [
    "AppProfile",
    "LocalProfile",
    "WebProfile",
    "load_profile",
    "profile_is_fresh",
    "research_web",
    "scan_local",
    "synthesize_profile",
]

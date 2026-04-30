"""launchd plist generator for per-app captain ticks (macOS).

Each registered app gets one plist that runs ``chad-captain tick --app <id>
--repo <path>`` daily at its configured hour. The plists live under
``~/Library/LaunchAgents/com.chadcaptain.<app_id>.plist`` so launchctl can
load/unload them without root.

This is generation-only — actual ``launchctl bootstrap`` is a manual step
the admiral runs after reviewing the plists.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from chad_captain.apps_registry import RegisteredApp

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL_PREFIX = "com.chadcaptain"


PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{captain_bin}</string>
        <string>tick</string>
        <string>--app</string>
        <string>{app_id}</string>
        <string>--repo</string>
        <string>{repo_path}</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:{user_local_bin}</string>
        <key>HOME</key>
        <string>{home}</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>{stdout_path}</string>

    <key>StandardErrorPath</key>
    <string>{stderr_path}</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def label_for(app: RegisteredApp) -> str:
    return f"{LABEL_PREFIX}.{app.app_id}"


def plist_path_for(app: RegisteredApp) -> Path:
    return LAUNCH_AGENTS_DIR / f"{label_for(app)}.plist"


def _resolve_captain_bin() -> str:
    """Find the chad-captain binary. Prefer the active venv's chad-captain,
    fall back to PATH lookup, then /usr/local/bin/chad-captain."""
    candidates = [
        Path(sys.prefix) / "bin" / "chad-captain",
        Path(sys.executable).parent / "chad-captain",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    found = shutil.which("chad-captain")
    if found:
        return found
    return "/usr/local/bin/chad-captain"


def render_plist(app: RegisteredApp, *, captain_bin: str | None = None) -> str:
    home = str(Path.home())
    bin_path = captain_bin or _resolve_captain_bin()
    log_dir = Path(home) / ".chad" / "captain" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return PLIST_TEMPLATE.format(
        label=label_for(app),
        captain_bin=bin_path,
        app_id=app.app_id,
        repo_path=app.repo_path,
        user_local_bin=str(Path(home) / ".local" / "bin"),
        home=home,
        hour=app.schedule_hour,
        stdout_path=str(log_dir / f"{app.app_id}.stdout.log"),
        stderr_path=str(log_dir / f"{app.app_id}.stderr.log"),
    )


def write_plist(app: RegisteredApp, *, captain_bin: str | None = None,
                target_dir: Path | None = None) -> Path:
    target = (target_dir or LAUNCH_AGENTS_DIR) / f"{label_for(app)}.plist"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_plist(app, captain_bin=captain_bin))
    return target


def bootstrap_command(app: RegisteredApp) -> list[str]:
    """Return the ``launchctl bootstrap`` command admin needs to run."""
    return ["launchctl", "bootstrap", "gui/$(id -u)", str(plist_path_for(app))]


def bootout_command(app: RegisteredApp) -> list[str]:
    return ["launchctl", "bootout", "gui/$(id -u)", str(plist_path_for(app))]


__all__ = [
    "LABEL_PREFIX",
    "LAUNCH_AGENTS_DIR",
    "bootstrap_command",
    "bootout_command",
    "label_for",
    "plist_path_for",
    "render_plist",
    "write_plist",
]

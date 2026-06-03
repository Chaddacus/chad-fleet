"""MCP tool-registry source.

Projects the MCP servers configured for the operator into the snapshot. Reads the two
canonical config files and exposes **names/transport/scope only** — never args, headers,
URLs-with-query, or env, because those carry tokens. This is a read-only registry view; the
agent acquires a tool via `allowed_tools`, the hub never calls the MCP itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

_DEFAULT_USER_CONFIG = Path.home() / ".claude.json"      # user-global MCP servers
_DEFAULT_PROJECT_CONFIG = Path.home() / ".mcp.json"      # project/shared MCP servers


def _transport(cfg: dict) -> str:
    if cfg.get("command"):
        return "stdio"
    t = cfg.get("type")
    if isinstance(t, str) and t:
        return t.lower()
    if cfg.get("url"):
        return "http"
    return "unknown"


def _detail(cfg: dict) -> str | None:
    """A safe, human-useful descriptor — command basename or remote host. No secrets."""
    cmd = cfg.get("command")
    if isinstance(cmd, str) and cmd:
        return os.path.basename(cmd)
    url = cfg.get("url")
    if isinstance(url, str) and url:
        host = urlparse(url).hostname  # host only — drops path, query, and any token
        return host
    return None


class ToolsSource:
    """Reads MCP server registrations from user + project config; safe-projects each."""

    name = "tools"

    def __init__(
        self,
        user_config: Path | None = None,
        project_config: Path | None = None,
    ) -> None:
        self._user_config = user_config
        self._project_config = project_config

    def _resolve(self, override: Path | None, env_var: str, default: Path) -> Path:
        if override is not None:
            return override
        env = os.environ.get(env_var)
        return Path(env) if env else default

    def _read(self, path: Path, source: str) -> list[dict]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        servers = data.get("mcpServers") or data.get("mcp_servers") or {}
        if not isinstance(servers, dict):
            return []
        out: list[dict] = []
        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue
            out.append({
                "name": name,
                "transport": _transport(cfg),
                "source": source,
                "detail": _detail(cfg),
            })
        return out

    def fetch(self) -> dict:
        """Returns {"tools": [...]}. User entries win on name collision with project."""
        user = self._read(
            self._resolve(self._user_config, "CHAD_MCP_USER_CONFIG", _DEFAULT_USER_CONFIG), "user"
        )
        project = self._read(
            self._resolve(self._project_config, "CHAD_MCP_PROJECT_CONFIG", _DEFAULT_PROJECT_CONFIG),
            "project",
        )
        by_name: dict[str, dict] = {t["name"]: t for t in project}
        for t in user:  # user overrides project on collision
            by_name[t["name"]] = t
        return {"tools": sorted(by_name.values(), key=lambda t: t["name"])}

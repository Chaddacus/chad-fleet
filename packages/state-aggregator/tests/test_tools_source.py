"""Tests for ToolsSource — the MCP registry projection. Asserts SAFE projection (no secrets)."""

from __future__ import annotations

import json

from state_aggregator.aggregator import Aggregator
from state_aggregator.sources import ToolsSource


def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def _configs(tmp_path):
    user = tmp_path / "claude.json"
    _write(user, {
        "mcpServers": {
            "dev-gateway": {"type": "http", "url": "https://gw.example.com/mcp?token=SECRET123",
                            "headers": {"Authorization": "Bearer SECRET"}},
            "omni-mem": {"command": "/opt/homebrew/bin/omni-mem", "args": ["--key", "SECRET"]},
        }
    })
    project = tmp_path / "mcp.json"
    _write(project, {
        "mcpServers": {
            "cloudwarriors": {"command": "node", "args": ["server.js", "--api-key", "SECRET"]},
        }
    })
    return user, project


def test_tools_source_projects_name_transport_source(tmp_path):
    user, project = _configs(tmp_path)
    out = ToolsSource(user_config=user, project_config=project).fetch()["tools"]
    by_name = {t["name"]: t for t in out}
    assert set(by_name) == {"dev-gateway", "omni-mem", "cloudwarriors"}

    assert by_name["dev-gateway"]["transport"] == "http"
    assert by_name["dev-gateway"]["source"] == "user"
    assert by_name["omni-mem"]["transport"] == "stdio"
    assert by_name["omni-mem"]["detail"] == "omni-mem"  # command basename
    assert by_name["cloudwarriors"]["transport"] == "stdio"
    assert by_name["cloudwarriors"]["source"] == "project"


def test_tools_source_never_leaks_secrets(tmp_path):
    user, project = _configs(tmp_path)
    out = ToolsSource(user_config=user, project_config=project).fetch()["tools"]
    blob = json.dumps(out)
    assert "SECRET" not in blob, "ToolsSource leaked a secret value"
    assert "token=" not in blob
    assert "Authorization" not in blob
    # remote detail is host-only
    dg = next(t for t in out if t["name"] == "dev-gateway")
    assert dg["detail"] == "gw.example.com"


def test_missing_configs_are_silent(tmp_path):
    out = ToolsSource(user_config=tmp_path / "nope.json", project_config=tmp_path / "no.json").fetch()
    assert out == {"tools": []}


def test_aggregator_includes_tools(tmp_path):
    user, project = _configs(tmp_path)
    agg = Aggregator(sources=[ToolsSource(user_config=user, project_config=project)])
    snap = agg.snapshot()
    assert snap.summary["tool_count"] == 3
    assert {t.name for t in snap.tools} == {"dev-gateway", "omni-mem", "cloudwarriors"}

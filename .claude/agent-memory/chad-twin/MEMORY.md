# chad-twin project memory — chad-fleet

- [hub-contracts pattern](hub-contracts-pattern.md) — pydantic-first snapshot + hand-authored admiral-chat; codegen.py + drift guard; regenerate after editing state_aggregator.types
- [chad-admiral not in uv workspace](chad-admiral-not-in-uv-workspace.md) — runs via `uv run --with`; not importable from .venv; launch.sh now starts it (:8901); Odysseus never in launch.sh
- [hub capability pattern](hub-capability-pattern.md) — capability = connector(MCP)/source/feature triple; source reads THROUGH connector (one credential home); email-mcp env + test-env gotcha
- [hub deploy packaging](hub-deploy-packaging.md) — S6b Docker: uv pip install --no-sources (boundary, no captain-core), dashboard --legacy-peer-deps + monorepo layout, genui api provider = distribution gate, compose !override ports

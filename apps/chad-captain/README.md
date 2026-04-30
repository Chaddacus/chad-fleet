# chad-captain

Captain app wiring `captain-core` to the rest of the fleet.

**Status:** scaffold

**License:** proprietary (closed source)

## What it is

The runtime app that hosts Chad Captain. Reads from `tracked-app-registry` + `state-aggregator`, composes daily briefs via `captain-core`, delivers via `notifier-hub`. Runs as an MCP server on a dedicated port.

## Contract surface

MCP tools:
- `captain.daily_brief()` — generate + emit today's brief
- `captain.next_actions()` — return current top-priority actions
- `captain.app_state(app_id)` — return registry + aggregator state for a tracked app

## Fleet context

See [../../README.md](../../README.md).

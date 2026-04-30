# notifier-hub/core

Routing engine: accepts events and fans them out to notification adapters.

**Status:** scaffold

**License:** Apache 2.0

## What it does

Single entry point for all outbound notifications in the fleet. Callers post an event; core routes it to one or more registered adapters (dashboard-inbox, ntfy, email) based on routing rules. Adapters are registered as plugins — core has no hard dependency on any adapter.

## Contract surface

HTTP API:
- `POST /notify` — accept an event, route to matching adapters
  - body: `{ "event": str, "payload": dict, "priority": "low|normal|high" }`
- `GET /adapters` — list registered adapters and their health

Library interface (for in-process adapter registration):
- `register_adapter(name, handler)` — register an adapter handler
- `emit(event, payload, priority)` — programmatic emit (bypasses HTTP)

## Fleet context

See [../../../README.md](../../../README.md) for the full fleet overview and OSS/closed split.

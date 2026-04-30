# state-aggregator

Aggregates cross-source state into a unified snapshot.

**Status:** scaffold

**License:** Apache 2.0

## What it does

Pulls state from multiple sources (Zoom presence, GitHub PRs, Linear issues, calendar, etc.) and merges them into a single structured snapshot that other fleet modules can query without touching source APIs directly.

## Contract surface

HTTP API:
- `GET /snapshot` — return current unified state snapshot
- `GET /snapshot/{source}` — return state for a single source (e.g. `zoom`, `github`, `linear`)
- `POST /refresh` — trigger a manual refresh of all sources

Events emitted (via HTTP webhook fan-out):
- `state.updated` — fired when any source state changes

## Fleet context

See [../../README.md](../../README.md) for the full fleet overview and OSS/closed split.

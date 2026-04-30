# tracked-app-registry

Registry of apps and agents Chad is monitoring or managing.

**Status:** scaffold

**License:** Apache 2.0

## What it does

Maintains a persistent catalog of every app, agent, and integration Chad has registered. Other fleet modules query the registry to discover endpoints, credentials references, and health metadata.

## Contract surface

Library functions (imported within the same Python service only):
- `register(app_id, config)` — add or update an entry
- `get(app_id)` -> `AppRecord | None` — fetch a single record
- `list_all()` -> `list[AppRecord]` — return the full catalog

HTTP API (when run as a standalone service):
- `GET /apps` — list all registered apps
- `GET /apps/{id}` — get one app record
- `POST /apps` — register a new app
- `PUT /apps/{id}` — update an existing app

## Fleet context

See [../../README.md](../../README.md) for the full fleet overview and OSS/closed split.

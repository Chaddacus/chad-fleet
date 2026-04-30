# notifier-hub/adapters/dashboard-inbox

Notifier adapter: writes events to the chad-dashboard inbox.

**Status:** scaffold

**License:** Apache 2.0

## What it does

Receives routed events from notifier-hub/core and appends them as inbox items in the chad-dashboard data store. The dashboard polls or subscribes to this store to display live inbox entries to Chad.

## Contract surface

Adapter interface (registered with notifier-hub/core):
- `handle(event, payload, priority)` — write the event as an inbox item

No standalone HTTP API — this adapter runs in-process with notifier-hub/core.

## Fleet context

See [../../../../../README.md](../../../../../README.md) for the full fleet overview and OSS/closed split.

# notifier-hub/adapters/ntfy

Notifier adapter: pushes events to ntfy.sh topics.

**Status:** scaffold

**License:** Apache 2.0

## What it does

Receives routed events from notifier-hub/core and publishes them as push notifications to configured ntfy.sh topics. Chad's devices subscribe to those topics to receive real-time alerts.

## Contract surface

Adapter interface (registered with notifier-hub/core):
- `handle(event, payload, priority)` — POST to the configured ntfy topic

Config (env vars):
- `NTFY_BASE_URL` — ntfy server base URL (default: `https://ntfy.sh`)
- `NTFY_TOPIC` — topic to publish to
- `NTFY_TOKEN` — optional auth token

No standalone HTTP API — this adapter runs in-process with notifier-hub/core.

## Fleet context

See [../../../../../README.md](../../../../../README.md) for the full fleet overview and OSS/closed split.

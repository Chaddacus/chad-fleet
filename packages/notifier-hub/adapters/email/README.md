# notifier-hub/adapters/email

Notifier adapter: sends events as email via SMTP or SES.

**Status:** scaffold

**License:** Apache 2.0

## What it does

Receives routed events from notifier-hub/core and sends them as email. Supports both SMTP and AWS SES as backends, selected by config.

## Contract surface

Adapter interface (registered with notifier-hub/core):
- `handle(event, payload, priority)` — send email to the configured recipient(s)

Config (env vars):
- `EMAIL_BACKEND` — `smtp` or `ses`
- `EMAIL_FROM` — sender address
- `EMAIL_TO` — recipient(s), comma-separated
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` — for SMTP backend
- `AWS_REGION` — for SES backend

No standalone HTTP API — this adapter runs in-process with notifier-hub/core.

## Fleet context

See [../../../../../README.md](../../../../../README.md) for the full fleet overview and OSS/closed split.

# chad-dashboard

Next.js dashboard that surfaces fleet state, the inbox, and the genui-renderer chat surface.

**Status:** active

**License:** Apache 2.0

## What it is

Single-user dashboard shell. Three pages:
- `/` — chat box + ephemeral genui-rendered views
- `/inbox` — pulled-in notifications from `notifier-hub`'s `dashboard-inbox` adapter
- `/apps` — list of tracked applications, click into per-app obsessive-loop state

## Dev

```bash
# Start dev server
npm run dev         # http://localhost:3000

# Typecheck
npm run typecheck

# Test
npm test

# Production build
npm run build
```

## Required environment variables

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_AGGREGATOR_URL` | `http://localhost:8106` | URL of the state-aggregator service (Slice F) |
| `NEXT_PUBLIC_GENUI_URL` | `http://localhost:8107` | Base URL of the genui-renderer service (Slice J) |
| `CHAD_NOTIFIER_INBOX_PATH` | `~/.chad/notifier/inbox.jsonl` | Path to the notifier inbox JSONL file |

Copy `.env.local.example` to `.env.local` and fill in values if the defaults differ from your setup.

## Required upstream services

- **state-aggregator** (Slice F) — must be running at `NEXT_PUBLIC_AGGREGATOR_URL`. The `/api/state` proxy route will return an empty fleet shape gracefully if it is down.
- **genui-renderer** (Slice J) — must be running at `NEXT_PUBLIC_GENUI_URL`. The chat panel will show an HTTP error if it is unreachable.

## Stack

Next.js 14 App Router + Tailwind CSS. All pages are React Server Components except `ChatPanel` (client component for SSE streaming).

## Hosting

Default: localhost:3000, Tailscale-accessible from phone. Single-user.

## Fleet context

See [../../README.md](../../README.md).

# claude.design handoff brief — the chad-fleet Hub

Use this in **claude.design** (web: claude.ai/design) to redesign the hub's visual layer. Point it
at `apps/chad-dashboard/` and paste the brief below. The redesign is **presentation-only** — it must
not change behavior (see "Integration boundary").

## What the product is

The Hub is a single operator console you log into and live in. One owned surface that replaces a
third-party front door. Main page is a **chat tied to an agent ("the admiral")**; the rest are tabs.

## Tabs / surfaces (what each screen shows)

| Route | Surface | Content |
|---|---|---|
| `/` | **Chat** | Streaming conversation with the admiral; user commands → dispatched work → results stream back. The primary screen. |
| `/sessions` | **Sessions** | Unified list of Claude / Codex / auto-runtime sessions. |
| `/tools` | **Tools/MCPs** | Registry of connected MCP servers (name, transport, scope) — no secrets. |
| `/email` | **Email** | Read-fast inbox list (subject, from, date, unread). Actions (reply/archive) route through chat. |
| `/login` | **Login** | Single-operator password gate. |
| `/inbox`, `/apps`, `/views`, `/captain` | existing fleet surfaces | keep. |

## Design direction (fill in your taste)

- Operator/console feel: dense but calm, dark-first, keyboard-friendly.
- Chat is the hero; tabs are a persistent left/top nav.
- Status-forward: live/healthy/stale states matter (services, sessions, unread).
- _Add: palette, type, density, references you like._

## Current stack (so the export integrates cleanly)

- Next.js 14 App Router, React 18, TypeScript. Tailwind is available (`tailwindcss`/`postcss`).
- Shell is dumb: `app/layout.tsx` (nav/chrome) + thin `app/<tab>/page.tsx` that mount feature UIs.
- Each tab's UI is `features/<tab>/ui.tsx`. Data hooks live in `features/<tab>/client.ts`.

## Integration boundary (why this is safe)

The redesign may only touch: `features/*/ui.tsx`, `app/layout.tsx`, `app/*/page.tsx` (mount points),
and global CSS. It must **not** touch `features/*/client.ts`, `features/*/server.ts`, the API
proxies (`app/api/*`), the aggregator sources, the MCPs, or `hub-contracts`. Keep the data shapes
imported from `lib/types.ts` (the generated contracts) unchanged.

## Export → integrate flow

1. claude.design exports a share URL → it's a gzipped tarball, not HTML: `curl <url> | tar -xz`.
2. Translate any `<script type="text/babel">` blocks to ESM `.tsx`.
3. Drop the visuals into `features/*/ui.tsx` + `app/layout.tsx` + global CSS only.
4. Verify post-integration: every tab still loads real data, chat still streams from the admiral,
   `npm run typecheck` + `vitest` stay green.

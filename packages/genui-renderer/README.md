# genui-renderer

Generative-UI renderer: blank canvas + LLM-rendered JSX views from JSON state.

**Status:** scaffold

**License:** Apache 2.0

## What it does

Frontend service that takes (state JSON, user prompt, primitives library) and streams JSX to the page. The frontend renders ephemeral views per question — no saved dashboards, no design work. State comes from `state-aggregator`.

## Contract surface

HTTP service:
- `POST /render` — body `{ state, request, primitives[] }` → SSE stream of JSX

Tiny React client library:
- `<GenView state={state} request={request} />` — wraps the SSE call and renders

## Stack

Next.js + Anthropic streaming + Recharts + Tailwind + a small pre-imported primitives library (`Card`, `Table`, `Chart`, `Timeline`, `Badge`).

## Fleet context

See [../../README.md](../../README.md).

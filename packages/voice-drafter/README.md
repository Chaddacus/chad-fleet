# voice-drafter

Drafts content in Chad's voice from structured inputs.

**Status:** scaffold

**License:** Apache 2.0

## What it does

Takes structured data (event summaries, deal updates, status reports) and produces natural-language drafts that match Chad's writing style. Backed by a prompt composition layer that pulls from the personality profiles in chad-agent.

## Contract surface

MCP tools (exposed via FastMCP):
- `draft_message(context, tone, length_hint)` -> `str` — produce a message draft
- `draft_update(topic, facts)` -> `str` — produce a status update draft

Library function:
- `render(template_id, vars)` -> `str` — low-level template render

## Fleet context

See [../../README.md](../../README.md) for the full fleet overview and OSS/closed split.

# captain-core

Core reasoning engine for Chad Captain.

**Status:** scaffold

**License:** proprietary (closed source)

## What it does

The proactive watcher logic that drives Chad Captain. Reads from `tracked-app-registry`, `state-aggregator`, the obsessive-loop run state, and omni-mem. Detects stalls, composes daily briefs, generates strategic prompts, orders deadline ladders. Calls `notifier-hub` to deliver outputs.

## Why closed

The reasoning logic plus the curated business playbooks (`captain-playbooks`) are the IP. Everything underneath (`tracked-app-registry`, `state-aggregator`, `notifier-hub`, etc.) is OSS plumbing. Open-core split.

## Contract surface

Library functions consumed by `apps/chad-captain`:
- `compose_daily_brief(state) -> Brief` — aggregate + narrate
- `detect_stalls(state) -> list[StallAlert]` — flag projects with no progress
- `next_actions(state, playbook) -> list[Action]` — playbook-grounded recommendations

## Fleet context

See [../../README.md](../../README.md).

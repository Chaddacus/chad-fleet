# week-intake

Thin tool layer that lets the LLM-in-chat (chad-twin / chad-agent) drive Chad's
weekly tasks through chad-fleet. Three CLIs, no new service, no new persistence
engine — just glue between Chad's brain dump and chad-captain's existing
admiral-notes protocol.

**License:** Apache 2.0

## What it does

```
weekly brain dump  ─▶  chad-week intake  ─▶  WeekItem JSONL
                                              │
                                  (LLM-in-chat clarifies in conversation)
                                              │
                                              ▼
                       chad-week route  ─▶  admiral_note | register+note
                                              │
                                              ▼
                                       chad-captain (existing)
                                              │
                                              ▼
                       chad-week status  ◀──  captain HTTP API :8109
```

The clarifier loop has no special UI. The driver is **you talking to chad-twin
in chat**. You paste a week, chad-twin runs `chad-week intake`, asks
questions per item, then runs `chad-week route` for each resolved one.

## Storage

- `~/.chad/week/<iso-week>/items.jsonl` — one `WeekItem` per line, append-only.
- All writes atomic via `tracked_app_registry.storage.atomic_write` /
  `append_jsonl`. One writer per file.
- ISO week format: `2026-W18` (Monday-anchored).

## CLI

| Command | What it does |
|---------|--------------|
| `chad-week intake [--week <iso>] [--from <path>]` | Parse stdin/file markdown into `WeekItem` rows. |
| `chad-week list [--week <iso>] [--state ...]` | Print current week's items in JSON or table. |
| `chad-week route <item_id> --app <existing\|new> [--repo <path>] [--greenfield <name>]` | Write admiral_note (and optionally register/scaffold). |
| `chad-week status [--week <iso>]` | Roll up captain state across this week's routed items. |

## Boundaries

- **No `chad_captain` Python imports.** Communication with captain is
  filesystem (admiral_notes) or HTTP (`POST /apps/register` on :8109).
- **No new persistence layer.** Per-week JSONL only.
- **No new orchestration engine.** Captain owns slice loops, validator,
  replanner, scheduler. We just file notes.

## Fleet context

See [../../README.md](../../README.md).

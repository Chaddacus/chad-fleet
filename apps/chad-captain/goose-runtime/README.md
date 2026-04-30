# Captain's goose runtime

Self-contained goose configuration for the chad-fleet captain. **Do not modify the global `~/.config/goose/`** — captain owns its own runtime so admiral-side experimentation in the global config can never destabilize the fleet.

## Layout

```
goose-runtime/
├── config/goose/config.yaml   # captain's opinionated config (provider: codex-acp, model: gpt-5.4)
├── state/                     # session DB, logs (gitignored, host-local)
├── data/                      # data home (gitignored, host-local)
├── recipes/                   # curated recipes the captain assigns to slices
└── skills/                    # curated skill set goose has access to
```

## Invocation pattern

When the captain dispatches a slice to goose, it sets the XDG env vars to point here, then shells out to `goose run`. This sandboxes goose entirely — no global config interference.

```bash
CAPTAIN=/Users/chadsimon/code/chad-fleet/apps/chad-captain
XDG_CONFIG_HOME=$CAPTAIN/goose-runtime/config \
XDG_STATE_HOME=$CAPTAIN/goose-runtime/state \
XDG_DATA_HOME=$CAPTAIN/goose-runtime/data \
goose run \
  --no-session \
  --max-turns 80 \
  --max-tool-repetitions 5 \
  --system "$slice_system_prompt" \
  --text "$slice_user_prompt"
```

## Smoke test

```bash
cd /Users/chadsimon/code/chad-fleet/apps/chad-captain
XDG_CONFIG_HOME=$PWD/goose-runtime/config goose info
# Should report:
#   Config dir:  /Users/chadsimon/code/chad-fleet/apps/chad-captain/goose-runtime/config/goose
#   Config yaml: /Users/chadsimon/code/chad-fleet/apps/chad-captain/goose-runtime/config/goose/config.yaml
```

## What's NOT here

- No agent OAuth tokens (those stay in `~/.config/goose/` or wherever codex-acp keeps them; captain inherits them via the user's environment).
- No host-specific MCP server registrations (those are admiral-level, not captain-level).
- No long-lived sessions — each slice runs `--no-session`. Session DB lives under `state/` only as a side effect.

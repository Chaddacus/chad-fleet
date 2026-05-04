# chad-fleet

Chad Simon's commercialization architecture — a fleet of independently shippable modules.

## What this is

Eight modular components that power Chad's agent-driven products. Each module has its own package manifest, tests, and license so any one can be `git subtree split` into a standalone public repo when it's ready to release.

Cross-module communication is via HTTP, MCP tools, or events. Never Python imports across module boundaries.

## OSS / closed split

Seven modules are Apache 2.0 open source. Three are proprietary:
- `captain-core` — closed source
- `captain-playbooks` — closed source
- `apps/chad-captain` — closed source

## Modules

### packages/

| Module | One-liner |
|--------|-----------|
| [tracked-app-registry](packages/tracked-app-registry/README.md) | Registry of apps and agents Chad is monitoring/managing |
| [voice-drafter](packages/voice-drafter/README.md) | Drafts content in Chad's voice from structured inputs |
| [state-aggregator](packages/state-aggregator/README.md) | Aggregates cross-source state into a unified snapshot |
| [notifier-hub/core](packages/notifier-hub/core/README.md) | Routing engine: accepts events, fans out to adapters |
| [notifier-hub/adapters/dashboard-inbox](packages/notifier-hub/adapters/dashboard-inbox/README.md) | Notifier adapter: writes to the chad-dashboard inbox |
| [notifier-hub/adapters/ntfy](packages/notifier-hub/adapters/ntfy/README.md) | Notifier adapter: pushes to ntfy.sh topics |
| [notifier-hub/adapters/email](packages/notifier-hub/adapters/email/README.md) | Notifier adapter: sends email via SMTP/SES |
| [genui-renderer](packages/genui-renderer/README.md) | React component library for rendering GenUI card specs |
| [week-intake](packages/week-intake/README.md) | Weekly task intake + router; LLM-in-chat drives captain via thin CLIs |
| [captain-core](packages/captain-core/README.md) | Core reasoning engine for Chad Captain (closed source) |
| [captain-playbooks](packages/captain-playbooks/README.md) | Playbook data for Chad Captain (closed source) |

### apps/

| App | One-liner |
|-----|-----------|
| [chad-dashboard](apps/chad-dashboard/README.md) | Next.js dashboard surfacing fleet state and inbox |
| [chad-captain](apps/chad-captain/README.md) | Captain app wiring together captain-core + fleet APIs (closed source) |

## Design rules

- Each module is independently deployable and testable.
- No cross-module Python imports. Communicate via HTTP, MCP, or events.
- Each module can be `git subtree split` into its own repo without modifications.
- Apache 2.0 for OSS modules. Proprietary modules hold their own license files.

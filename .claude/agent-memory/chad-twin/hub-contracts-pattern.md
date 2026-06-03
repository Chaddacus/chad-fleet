---
name: hub-contracts-pattern
description: How chad-fleet's cross-boundary contracts are generated and guarded (packages/hub-contracts)
metadata:
  type: project
---

`packages/hub-contracts` owns the shapes that cross hub boundaries. Two contract kinds, two patterns:

- **snapshot.schema.json** — pydantic-first. The canonical source is `state_aggregator.types`
  (`FleetState` + nested models). `codegen.py` runs `FleetState.model_json_schema()`, prunes
  pydantic's per-property `title` keys (else json2ts emits noisy `Id`/`Title1` aliases), writes
  the schema, then runs `json2ts` → `ts/snapshot.ts`. Pydantic-first was a deliberate deviation
  from the plan's "JSON-Schema-first": it removes the fragile JSON-Schema→pydantic codegen
  direction entirely (Codex review #4).
- **admiral-chat.schema.json** — hand-authored (the OpenAI chat subset is an external standard,
  not our model). codegen still generates `ts/admiral-chat.ts` from it.

**Why:** one source of truth, no hand-duplicated `FleetState` in TS.
**How to apply:** after editing `state_aggregator.types`, regenerate or the drift guard fails. System
`python3 codegen.py` FAILS (`No module named 'state_aggregator'`) — codegen imports the type source.
Run it with the source on the path + pydantic available:
`cd packages/hub-contracts && PYTHONPATH=../state-aggregator/src uv run --with pydantic python codegen.py`. Guards: `test_hub_contracts_drift.py` (pydantic validates golden fixture +
asserts checked-in schema == fresh codegen) and `hub-contracts` `npm run check` (tsc + ajv validates
fixtures against schemas, enum-aware). Generated TS/schema are **checked in**. The dashboard consumes
the generated TS via re-exports in `apps/chad-dashboard/lib/types.ts` (relative import
`../../../packages/hub-contracts/ts/...`, type-only). See [[chad-admiral-not-in-uv-workspace]].

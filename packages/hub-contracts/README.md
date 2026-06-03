# hub-contracts

Canonical cross-boundary contracts for the hub. **One source of truth, everything else generated.**

## Source of truth

The pydantic models in `state_aggregator.types` (`FleetState` and its nested models) are the
single canonical definition of the snapshot shape. Everything in this package is **generated**
from them:

- `schema/snapshot.schema.json` — JSON Schema, via `FleetState.model_json_schema()`
- `ts/snapshot.ts` — TypeScript interfaces, via `json-schema-to-typescript` over the schema

The dashboard imports `ts/snapshot.ts`; the aggregator/admiral import the pydantic models
directly. Nobody hand-maintains a second copy of `FleetState`.

> Why pydantic-first (not JSON-Schema-first as the original plan sketched): the fragile codegen
> direction is JSON Schema → idiomatic pydantic. By making pydantic canonical and generating the
> schema *from* it, that direction never exists — only the straightforward schema → TS remains.

## Regenerate after changing the models

```bash
# 1. edit state_aggregator.types
python3 packages/hub-contracts/codegen.py        # rewrites schema/ + ts/
```

Generated artifacts are **checked in**. CI guards them:

- `npm run check` (here) — tsc compiles the generated TS; ajv validates the golden fixture
  against the schema (Node half, enum-aware).
- `state-aggregator/tests/test_hub_contracts_drift.py` — pydantic validates the same golden
  fixture (Python half) **and** asserts the checked-in schema equals fresh codegen output. If you
  edit the models without re-running codegen, this test fails.

## Layout

```
schema/snapshot.schema.json   generated JSON Schema (checked in)
ts/snapshot.ts                generated TS interfaces (checked in)
ts/fixture.check.ts           TS compile smoke (typed literals; not shipped)
fixtures/snapshot.example.json  golden fixture — validated in BOTH languages
fixtures/validate.mjs         ajv validation (Node half of the cross-language guard)
codegen.py                    pydantic -> schema -> ts (one command)
```

## Adding a new contract later

`admiral-chat.schema.json` (S2) and `mcp-tool.schema.json` (S4) are authored when the slice that
needs them lands — not up front. Each follows the same pydantic-first → generate → golden-fixture
pattern.

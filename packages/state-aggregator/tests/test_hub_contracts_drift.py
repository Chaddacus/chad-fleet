"""Cross-language contract guards for packages/hub-contracts.

Two guarantees (the Python half of the guard Codex #4 asked for):

1. **Golden fixture validates against the pydantic source of truth.** The same fixture is
   validated against the generated JSON Schema by ajv on the Node side
   (`hub-contracts/fixtures/validate.mjs`). Both passing => the contract is consistent in
   both languages.
2. **Drift guard.** The checked-in `snapshot.schema.json` must equal what `codegen.py`
   produces from the current pydantic models. If someone edits `state_aggregator.types`
   without re-running codegen, this fails — keeping the checked-in artifacts honest.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from state_aggregator.types import FleetState

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HUB = _REPO_ROOT / "packages" / "hub-contracts"
_FIXTURE = _HUB / "fixtures" / "snapshot.example.json"
_SCHEMA = _HUB / "schema" / "snapshot.schema.json"


def _load_codegen():
    spec = importlib.util.spec_from_file_location("hub_contracts_codegen", _HUB / "codegen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_golden_fixture_validates_against_pydantic():
    data = json.loads(_FIXTURE.read_text())
    state = FleetState.model_validate(data)
    assert state.apps[0].id == "helm"
    assert state.inbox_recent[0].severity == "warn"
    assert state.sessions[0].source == "auto-runtime"


def test_checked_in_schema_matches_codegen():
    codegen = _load_codegen()
    expected = codegen.serialize(codegen.build_schema())
    actual = _SCHEMA.read_text()
    assert actual == expected, (
        "snapshot.schema.json is stale — pydantic models changed without re-running codegen. "
        "Run: python3 packages/hub-contracts/codegen.py"
    )

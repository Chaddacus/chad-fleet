"""Tests for the PR14 R3#7 cross-task artifact bus."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from chad_captain_scaffold.artifacts import (
    ArtifactNotFound,
    ArtifactSchemaMismatch,
    ArtifactSchemaNotRegistered,
    clear_registry,
    get,
    list_artifacts,
    put,
    register_schema,
    schema_json_schema,
)


class _DemoSchemaV1(BaseModel):
    """Test fixture — small Pydantic model used as an artifact schema."""

    item_id: str
    weight: float
    tags: list[str] = []


class _DemoSchemaV2(BaseModel):
    """Different shape for version-drift tests."""

    item_id: str
    weight: float
    flagged: bool


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Each test gets its own artifacts dir + a clean schema registry."""
    monkeypatch.setenv("CHAD_FLEET_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_registry()
    yield
    clear_registry()


def test_register_schema_idempotent_with_same_class():
    register_schema("demo.v1", _DemoSchemaV1)
    register_schema("demo.v1", _DemoSchemaV1)  # no raise


def test_register_schema_rejects_drift():
    register_schema("demo.v1", _DemoSchemaV1)
    with pytest.raises(ValueError, match="already registered"):
        register_schema("demo.v1", _DemoSchemaV2)


def test_put_then_get_roundtrip():
    register_schema("demo.v1", _DemoSchemaV1)
    payload = {"item_id": "item-1", "weight": 3.14, "tags": ["a", "b"]}
    meta = put(
        task_id="t-1", name="result", schema_id="demo.v1",
        payload=payload, produced_by_app_id="producer-app",
    )
    assert meta.name == "result"
    assert meta.payload_size_bytes > 0

    fetched = get(task_id="t-1", name="result", schema_id="demo.v1")
    assert isinstance(fetched, _DemoSchemaV1)
    assert fetched.item_id == "item-1"
    assert fetched.weight == 3.14
    assert fetched.tags == ["a", "b"]


def test_put_pydantic_instance_roundtrip():
    register_schema("demo.v1", _DemoSchemaV1)
    inst = _DemoSchemaV1(item_id="x", weight=1.0)
    put(
        task_id="t-1", name="result", schema_id="demo.v1",
        payload=inst, produced_by_app_id="producer",
    )
    fetched = get(task_id="t-1", name="result", schema_id="demo.v1")
    assert fetched.item_id == "x"


def test_put_bad_payload_raises_schema_mismatch():
    register_schema("demo.v1", _DemoSchemaV1)
    with pytest.raises(ArtifactSchemaMismatch, match="failed schema"):
        put(
            task_id="t-1", name="bad", schema_id="demo.v1",
            payload={"item_id": "x"},  # missing required 'weight'
            produced_by_app_id="p",
        )


def test_put_unregistered_schema_raises():
    with pytest.raises(ArtifactSchemaNotRegistered):
        put(
            task_id="t-1", name="x", schema_id="never.registered",
            payload={}, produced_by_app_id="p",
        )


def test_put_pydantic_wrong_class_raises():
    """Producer passes a Pydantic instance whose class doesn't match
    the registered schema → ArtifactSchemaMismatch (no silent coerce)."""
    register_schema("demo.v1", _DemoSchemaV1)
    register_schema("demo.v2", _DemoSchemaV2)
    wrong = _DemoSchemaV2(item_id="x", weight=1.0, flagged=True)
    with pytest.raises(ArtifactSchemaMismatch, match="expects"):
        put(
            task_id="t-1", name="result", schema_id="demo.v1",
            payload=wrong, produced_by_app_id="p",
        )


def test_get_missing_task_raises_not_found():
    register_schema("demo.v1", _DemoSchemaV1)
    with pytest.raises(ArtifactNotFound):
        get(task_id="never-existed", name="x", schema_id="demo.v1")


def test_get_missing_artifact_raises_not_found():
    register_schema("demo.v1", _DemoSchemaV1)
    put(
        task_id="t-1", name="result", schema_id="demo.v1",
        payload={"item_id": "x", "weight": 1.0},
        produced_by_app_id="p",
    )
    with pytest.raises(ArtifactNotFound):
        get(task_id="t-1", name="not-this-one", schema_id="demo.v1")


def test_get_with_wrong_schema_id_raises():
    """Producer wrote with demo.v1, consumer asks for demo.v2 — refuse."""
    register_schema("demo.v1", _DemoSchemaV1)
    register_schema("demo.v2", _DemoSchemaV2)
    put(
        task_id="t-1", name="result", schema_id="demo.v1",
        payload={"item_id": "x", "weight": 1.0},
        produced_by_app_id="p",
    )
    with pytest.raises(ArtifactSchemaMismatch, match="produced under"):
        get(task_id="t-1", name="result", schema_id="demo.v2")


def test_list_artifacts_returns_sorted():
    register_schema("demo.v1", _DemoSchemaV1)
    for name in ("zebra", "apple", "mango"):
        put(
            task_id="t-1", name=name, schema_id="demo.v1",
            payload={"item_id": name, "weight": 1.0},
            produced_by_app_id="p",
        )
    metas = list_artifacts("t-1")
    assert [m.name for m in metas] == ["apple", "mango", "zebra"]


def test_list_artifacts_empty_for_unknown_task():
    assert list_artifacts("never") == []


def test_schema_json_schema_returns_openapi_shape():
    register_schema("demo.v1", _DemoSchemaV1)
    sch = schema_json_schema("demo.v1")
    assert sch["type"] == "object"
    assert "item_id" in sch["properties"]
    assert "weight" in sch["properties"]


def test_schema_json_schema_unregistered_raises():
    with pytest.raises(ArtifactSchemaNotRegistered):
        schema_json_schema("nope")


def test_put_overwrites_existing_artifact():
    """Re-putting the same name updates the manifest + payload (the bus
    is last-writer-wins for now; producer is responsible for unique
    names if it cares about history)."""
    register_schema("demo.v1", _DemoSchemaV1)
    put(
        task_id="t-1", name="result", schema_id="demo.v1",
        payload={"item_id": "first", "weight": 1.0},
        produced_by_app_id="p",
    )
    put(
        task_id="t-1", name="result", schema_id="demo.v1",
        payload={"item_id": "second", "weight": 2.0},
        produced_by_app_id="p",
    )
    fetched = get(task_id="t-1", name="result", schema_id="demo.v1")
    assert fetched.item_id == "second"
    assert fetched.weight == 2.0
    # Manifest still only has one entry.
    assert len(list_artifacts("t-1")) == 1

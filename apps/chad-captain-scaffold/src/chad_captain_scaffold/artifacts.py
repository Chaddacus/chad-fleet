"""Cross-task artifact bus (PR14 — FLEET_PROCESS slice 15, R3#7 v5 §validation).

Tasks pass artifacts to each other through a JSON-typed bus:
  - producer captain writes artifact at task close
  - consumer captain reads it at slice start
  - schema validation at both put and get prevents shape drift

Storage layout (under ``~/.chad/fleet/artifacts/<task_id>/``):
  manifest.json              dict[artifact_name → ArtifactMeta]
  payloads/<artifact>.json   one file per artifact

Schemas live in a registry (Pydantic models keyed by ``schema_id``).
``put`` and ``get`` validate against the schema; mismatch raises
``ArtifactSchemaMismatch`` so a buggy producer can't poison a downstream
consumer.

Concurrency: ``put`` takes an exclusive flock on the artifact path
during write to prevent two producers from racing on the same name.
``get`` is read-only.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tracked_app_registry.storage import atomic_write


DEFAULT_ARTIFACT_BASE = Path.home() / ".chad" / "fleet" / "artifacts"


def artifact_base() -> Path:
    """Override with ``CHAD_FLEET_ARTIFACTS_DIR``."""
    raw = os.environ.get("CHAD_FLEET_ARTIFACTS_DIR")
    return Path(raw).expanduser() if raw else DEFAULT_ARTIFACT_BASE


class ArtifactMeta(BaseModel):
    """One artifact's manifest entry — what schema it claims to satisfy
    and when/by-whom it was produced."""

    name: str
    schema_id: str
    produced_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    produced_by_app_id: str
    payload_size_bytes: int = 0


class ArtifactManifest(BaseModel):
    """Per-task manifest of all produced artifacts."""

    task_id: str
    artifacts: dict[str, ArtifactMeta] = Field(default_factory=dict)


class ArtifactNotFound(LookupError):
    """Artifact (task_id, name) doesn't exist on the bus."""


class ArtifactSchemaMismatch(ValueError):
    """Payload didn't validate against the registered schema, OR the
    caller asked for a schema that doesn't match what the producer
    declared in the manifest."""


class ArtifactSchemaNotRegistered(LookupError):
    """No Pydantic model registered for the requested schema_id.
    Producer/consumer must register before put/get."""


# Schema registry: schema_id → Pydantic BaseModel subclass that validates
# the payload. Producers and consumers must agree on the schema_id and
# both must call register_schema before put/get.
_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {}


def register_schema(schema_id: str, model: type[BaseModel]) -> None:
    """Register a Pydantic model as the validator for ``schema_id``.

    Idempotent: re-registering the same id with the same class is fine;
    re-registering with a DIFFERENT class raises so version drift is
    visible at registration time, not at first validation failure.
    """
    existing = _SCHEMA_REGISTRY.get(schema_id)
    if existing is not None and existing is not model:
        raise ValueError(
            f"schema_id {schema_id!r} already registered to "
            f"{existing.__module__}.{existing.__qualname__}; refusing "
            f"to overwrite with {model.__module__}.{model.__qualname__}"
        )
    _SCHEMA_REGISTRY[schema_id] = model


def clear_registry() -> None:
    """Test helper — drop all registered schemas. Production code never
    calls this."""
    _SCHEMA_REGISTRY.clear()


def schema_json_schema(schema_id: str) -> dict[str, Any]:
    """Return the JSON Schema for a registered Pydantic model. Useful for
    cross-language consumers and for the dashboard's task-detail view."""
    model = _SCHEMA_REGISTRY.get(schema_id)
    if model is None:
        raise ArtifactSchemaNotRegistered(schema_id)
    return model.model_json_schema()


def _task_dir(task_id: str) -> Path:
    return artifact_base() / task_id


def _manifest_path(task_id: str) -> Path:
    return _task_dir(task_id) / "manifest.json"


def _payload_path(task_id: str, name: str) -> Path:
    return _task_dir(task_id) / "payloads" / f"{name}.json"


def _lock_path(task_id: str, name: str) -> Path:
    return _task_dir(task_id) / "payloads" / f".{name}.lock"


def _read_manifest(task_id: str) -> ArtifactManifest:
    p = _manifest_path(task_id)
    if not p.exists():
        return ArtifactManifest(task_id=task_id)
    try:
        return ArtifactManifest.model_validate_json(p.read_text())
    except (ValueError, OSError):
        # Corrupt manifest — return empty rather than crashing; ops can
        # inspect the file directly.
        return ArtifactManifest(task_id=task_id)


def _write_manifest(manifest: ArtifactManifest) -> None:
    _manifest_path(manifest.task_id).parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        _manifest_path(manifest.task_id),
        manifest.model_dump_json(indent=2),
    )


def put(
    *,
    task_id: str,
    name: str,
    schema_id: str,
    payload: dict | BaseModel,
    produced_by_app_id: str,
) -> ArtifactMeta:
    """Write an artifact to the bus.

    The payload is validated against ``schema_id`` before it lands; bad
    shapes raise ArtifactSchemaMismatch and nothing is written.
    """
    model = _SCHEMA_REGISTRY.get(schema_id)
    if model is None:
        raise ArtifactSchemaNotRegistered(schema_id)
    if isinstance(payload, BaseModel):
        # Already validated; serialize directly.
        if not isinstance(payload, model):
            raise ArtifactSchemaMismatch(
                f"payload is {type(payload).__name__} but schema {schema_id!r} "
                f"expects {model.__name__}"
            )
        payload_json = payload.model_dump_json(indent=2)
    else:
        try:
            validated = model.model_validate(payload)
        except Exception as e:  # pydantic.ValidationError
            raise ArtifactSchemaMismatch(
                f"artifact {name!r} (task {task_id!r}) failed schema "
                f"{schema_id!r}: {e}"
            ) from e
        payload_json = validated.model_dump_json(indent=2)

    payload_path = _payload_path(task_id, name)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(task_id, name)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            atomic_write(payload_path, payload_json)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

    meta = ArtifactMeta(
        name=name, schema_id=schema_id,
        produced_by_app_id=produced_by_app_id,
        payload_size_bytes=len(payload_json.encode("utf-8")),
    )
    manifest = _read_manifest(task_id)
    manifest.artifacts[name] = meta
    _write_manifest(manifest)
    return meta


def get(
    *,
    task_id: str,
    name: str,
    schema_id: str,
) -> BaseModel:
    """Read an artifact + validate against the caller's expected schema_id.

    Mismatched schema_id between producer and consumer raises
    ArtifactSchemaMismatch — consumers can't accidentally read an artifact
    intended for a different consumer with a different shape.
    """
    manifest = _read_manifest(task_id)
    meta = manifest.artifacts.get(name)
    if meta is None:
        raise ArtifactNotFound(f"task={task_id!r} name={name!r}")
    if meta.schema_id != schema_id:
        raise ArtifactSchemaMismatch(
            f"artifact {name!r} (task {task_id!r}) was produced under "
            f"schema {meta.schema_id!r} but consumer expected {schema_id!r}"
        )
    model = _SCHEMA_REGISTRY.get(schema_id)
    if model is None:
        raise ArtifactSchemaNotRegistered(schema_id)
    payload_path = _payload_path(task_id, name)
    if not payload_path.exists():
        # Manifest pointed at a missing payload — corrupted state.
        raise ArtifactNotFound(
            f"manifest references {name!r} but payload file is missing"
        )
    raw = payload_path.read_text()
    try:
        return model.model_validate_json(raw)
    except Exception as e:
        raise ArtifactSchemaMismatch(
            f"artifact {name!r} (task {task_id!r}) on-disk payload no "
            f"longer matches schema {schema_id!r}: {e}"
        ) from e


def list_artifacts(task_id: str) -> list[ArtifactMeta]:
    """Enumerate manifest entries for a task (sorted by name)."""
    manifest = _read_manifest(task_id)
    return sorted(manifest.artifacts.values(), key=lambda m: m.name)


__all__ = [
    "ArtifactMeta",
    "ArtifactManifest",
    "ArtifactNotFound",
    "ArtifactSchemaMismatch",
    "ArtifactSchemaNotRegistered",
    "artifact_base",
    "register_schema",
    "clear_registry",
    "schema_json_schema",
    "put",
    "get",
    "list_artifacts",
]

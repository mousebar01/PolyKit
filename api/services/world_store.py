"""Safe, strict persistence for schema-v2 world runtime documents."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, overload

from pydantic import BaseModel, ValidationError

from schemas.world import WORLD_KIND, WORLD_SCHEMA_VERSION, WorldDocument
from services.runtime_paths import runtime_paths
from services.workspace_paths import resolve_workspace_path


MAX_WORLD_BYTES = 8 * 1024 * 1024
WORLD_ROOT = "Workflows"
WORLD_SUFFIX = ".world.json"

_lock = threading.RLock()
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_PATH_KEYS = {
    "path",
    "workspace_path",
    "workspacepath",
    "mesh_path",
    "meshpath",
    "image_path",
    "imagepath",
    "concept_image",
    "conceptimage",
    "artifact_path",
    "artifactpath",
    "source_path",
    "sourcepath",
    "file_path",
    "filepath",
}
_URL_PATH_KEYS = {
    "url",
    "workspace_url",
    "workspaceurl",
    "mesh_url",
    "meshurl",
    "image_url",
    "imageurl",
}
_ARTIFACT_KEYS = {
    "artifact",
    "artifacts",
    "asset",
    "assets",
    "artifact_ref",
    "artifactref",
    "artifact_refs",
    "artifactrefs",
}
_NORMALIZED_ARTIFACT_KEYS = {re.sub(r"[^a-z0-9]", "", item) for item in _ARTIFACT_KEYS}


class WorldStoreError(ValueError):
    """Base class for invalid world documents or identifiers."""


class WorldTooLargeError(WorldStoreError):
    """Raised when a world JSON document exceeds ``MAX_WORLD_BYTES``."""


class WorldNotFoundError(FileNotFoundError):
    """Raised when a requested world has not been saved."""


def _worlds_dir() -> Path:
    workspace = runtime_paths.workspace
    directory = resolve_workspace_path(workspace, WORLD_ROOT)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def validate_world_id(world_id: str) -> str:
    if not isinstance(world_id, str):
        raise WorldStoreError("World id must be a string")
    value = world_id.strip()
    if not value:
        raise WorldStoreError("World id is required")
    if len(value) > 160:
        raise WorldStoreError("World id is too long")
    if "\x00" in value or any(ord(char) < 32 for char in value):
        raise WorldStoreError("World id contains control characters")
    if (
        value in {".", ".."}
        or "/" in value
        or "\\" in value
        or value.startswith(("/", "\\\\"))
        or _WINDOWS_DRIVE.match(value)
        or PureWindowsPath(value).is_absolute()
        or value.endswith(WORLD_SUFFIX)
    ):
        raise WorldStoreError("World id must be a safe filename (no absolute paths or '..')")
    return value


def world_path(world_id: str) -> Path:
    safe_id = validate_world_id(world_id)
    return resolve_workspace_path(runtime_paths.workspace, f"{WORLD_ROOT}/{safe_id}{WORLD_SUFFIX}")


def new_world_id(prefix: str = "scene") -> str:
    safe_prefix = re.sub(r"[^a-z0-9-]+", "-", prefix.strip().lower()).strip("-") or "scene"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    with _lock:
        for _ in range(10):
            candidate = f"{safe_prefix}-{timestamp}-{uuid.uuid4().hex[:10]}"
            if not world_path(candidate).exists():
                return candidate
    raise WorldStoreError("Could not allocate a unique world id")


def _as_mapping(world: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(world, BaseModel):
        try:
            value = world.model_dump(mode="json", exclude_unset=False)
        except (TypeError, ValueError, ValidationError) as exc:
            raise WorldStoreError(f"Invalid world document: {exc}") from exc
    elif isinstance(world, Mapping):
        value = dict(world)
    else:
        raise WorldStoreError("World document must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise WorldStoreError("World document keys must be strings")
    return value


def _is_absolute_workspace_path(raw_path: str) -> bool:
    normalized = raw_path.replace("\\", "/")
    return (
        normalized.startswith("/")
        or normalized.startswith("//")
        or normalized.startswith("web-file://")
        or bool(_WINDOWS_DRIVE.match(raw_path))
        or bool(_URI_SCHEME.match(raw_path))
        or PureWindowsPath(raw_path).is_absolute()
    )


def validate_workspace_relative_path(raw_path: str) -> str:
    if not isinstance(raw_path, str):
        raise WorldStoreError("Artifact path must be a string")
    value = raw_path.strip()
    normalized = value.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or _is_absolute_workspace_path(value)
        or any(part == ".." for part in normalized.split("/"))
    ):
        raise WorldStoreError("Artifact paths must be workspace-relative and cannot contain '..'")
    try:
        resolve_workspace_path(runtime_paths.workspace, normalized)
    except ValueError as exc:
        raise WorldStoreError("Artifact path must stay inside the workspace") from exc
    return normalized


def _looks_like_artifact_key(key: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", key.lower()) in _NORMALIZED_ARTIFACT_KEYS


def _validate_artifact_paths(value: Any, *, artifact_context: bool = False) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise WorldStoreError("World document keys must be strings")
            normalized_key = key.lower().replace("-", "_")
            child_context = artifact_context or _looks_like_artifact_key(key)
            if normalized_key in _PATH_KEYS:
                if isinstance(child, str):
                    validate_workspace_relative_path(child)
                elif artifact_context:
                    raise WorldStoreError("Artifact path must be a string")
            elif normalized_key in _URL_PATH_KEYS and artifact_context:
                validate_workspace_relative_path(child)
            elif _looks_like_artifact_key(key) and isinstance(child, str):
                validate_workspace_relative_path(child)
            _validate_artifact_paths(child, artifact_context=child_context)
    elif isinstance(value, list):
        for child in value:
            _validate_artifact_paths(child, artifact_context=artifact_context)


def _validate_world_shape(value: dict[str, Any], expected_world_id: str | None = None) -> WorldDocument:
    if value.get("schema_version") != WORLD_SCHEMA_VERSION:
        raise WorldStoreError(
            f"World schema_version must be {WORLD_SCHEMA_VERSION}; old world documents are not supported"
        )
    if value.get("kind") != WORLD_KIND:
        raise WorldStoreError(f"World kind must be {WORLD_KIND!r}")
    if expected_world_id is not None:
        body_id = value.get("id")
        if not isinstance(body_id, str) or body_id.strip() != expected_world_id:
            raise WorldStoreError("World id in the URL must match document.id")
    try:
        validated = WorldDocument.model_validate(value)
    except ValidationError as exc:
        raise WorldStoreError(f"Invalid world document: {exc}") from exc
    _validate_artifact_paths(value)
    return validated


def _encode_world(value: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorldStoreError(f"World document must contain JSON values: {exc}") from exc
    if len(encoded) + 1 > MAX_WORLD_BYTES:
        raise WorldTooLargeError(
            f"World document is larger than {MAX_WORLD_BYTES // (1024 * 1024)} MiB"
        )
    return encoded + b"\n", json.loads(encoded)


def _read_world_file(path: Path, expected_world_id: str) -> dict[str, Any]:
    try:
        if not path.is_file():
            raise WorldNotFoundError(path.name)
        if path.stat().st_size > MAX_WORLD_BYTES:
            raise WorldTooLargeError(
                f"World document is larger than {MAX_WORLD_BYTES // (1024 * 1024)} MiB"
            )
        value = json.loads(path.read_text(encoding="utf-8"))
    except (WorldStoreError, WorldNotFoundError):
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorldStoreError(f"Could not read world document: {exc}") from exc
    if not isinstance(value, dict):
        raise WorldStoreError("World document must be a JSON object")
    _validate_world_shape(value, expected_world_id)
    return value


@overload
def save_world(world_id: str, world: Mapping[str, Any] | BaseModel) -> dict[str, Any]: ...


@overload
def save_world(world: Mapping[str, Any] | BaseModel) -> dict[str, Any]: ...


def save_world(
    world_id: str | Mapping[str, Any] | BaseModel,
    world: Mapping[str, Any] | BaseModel | None = None,
) -> dict[str, Any]:
    """Validate, refresh derived gates, and atomically replace one world."""

    if world is None:
        value = _as_mapping(world_id)  # type: ignore[arg-type]
        safe_id = validate_world_id(value.get("id"))
    else:
        safe_id = validate_world_id(world_id)  # type: ignore[arg-type]
        value = _as_mapping(world)

    # Validate the authored contract first.  Derived gate refresh then operates
    # on a known schema-v2 document and its result is validated once more before
    # persistence.  The local import avoids a module-level world_store/runtime
    # cycle because world_runtime raises WorldStoreError for invalid envelopes.
    _validate_world_shape(value, expected_world_id=safe_id)
    from services.world_runtime import refresh_runtime_quality

    value = refresh_runtime_quality(value)
    _validate_world_shape(value, expected_world_id=safe_id)
    encoded, normalized = _encode_world(value)

    with _lock:
        worlds_dir = _worlds_dir()
        destination = world_path(safe_id)
        temporary = worlds_dir / f".{destination.name}.{uuid.uuid4().hex[:10]}.tmp"
        try:
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            try:
                directory_fd = os.open(worlds_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    return normalized


def load_world(world_id: str) -> dict[str, Any]:
    safe_id = validate_world_id(world_id)
    with _lock:
        return _read_world_file(world_path(safe_id), expected_world_id=safe_id)


def get_world(world_id: str) -> dict[str, Any] | None:
    try:
        return load_world(world_id)
    except WorldNotFoundError:
        return None


__all__ = [
    "MAX_WORLD_BYTES",
    "WORLD_KIND",
    "WORLD_ROOT",
    "WORLD_SCHEMA_VERSION",
    "WORLD_SUFFIX",
    "WorldNotFoundError",
    "WorldStoreError",
    "WorldTooLargeError",
    "get_world",
    "load_world",
    "new_world_id",
    "save_world",
    "validate_world_id",
    "validate_workspace_relative_path",
    "world_path",
]

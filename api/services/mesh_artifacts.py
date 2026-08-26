"""Typed mesh artifacts passed between workflow nodes.

A mesh edge in the workflow is a logical artifact, not a promise that the
upstream node has already published a user-visible asset. Phase 1 keeps the
payload file-backed for large meshes / subprocess safety while separating:

* intermediate materialisation under ``WORKSPACE_DIR/.artifacts/<run-id>``;
* persistent assets published by ``polykit.output`` into a collection.

The wrapper also gives us one place to carry the coordinate-space contract.
Model node packs are expected to emit PolyKit's canonical mesh frame.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import uuid
from typing import Any


COORDINATE_SPACE_CANONICAL = "y_up_front_pos_z"
COORDINATE_SPACE_UNKNOWN = "unknown"


def intermediate_mesh_step(step: str) -> str:
    """Rename model serialization progress so it is not confused with publication."""
    if step.startswith("Exporting textured mesh"):
        return step.replace("Exporting textured mesh", "Preparing textured mesh", 1)
    if step.startswith("Exporting mesh"):
        return step.replace("Exporting mesh", "Preparing mesh", 1)
    return step


@dataclass(frozen=True)
class MeshArtifact:
    """A mesh value travelling through the workflow DAG.

    ``path`` is intentionally the Phase-1 backing store. Keeping it behind a
    typed value lets a later implementation add an in-memory or remote backing
    without changing node wiring or sink semantics.
    """

    path: Path
    coordinate_space: str = COORDINATE_SPACE_UNKNOWN
    persistent: bool = False
    origin: str = "intermediate"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))

    @property
    def format(self) -> str:
        return self.path.suffix.lower().lstrip(".") or "unknown"

    def exists(self) -> bool:
        return self.path.is_file()


def unwrap_mesh_value(value: Any) -> Any:
    """Convert MeshArtifact values back to file paths for legacy node APIs."""
    if isinstance(value, MeshArtifact):
        return value.path
    if isinstance(value, list):
        return [unwrap_mesh_value(item) for item in value]
    return value


def wrap_mesh_value(
    value: Any,
    *,
    coordinate_space: str = COORDINATE_SPACE_UNKNOWN,
    persistent: bool = False,
    origin: str = "intermediate",
) -> Any:
    """Wrap path/list mesh outputs while leaving existing artifacts unchanged."""
    if isinstance(value, MeshArtifact):
        return value
    if isinstance(value, Path):
        return MeshArtifact(
            path=value,
            coordinate_space=coordinate_space,
            persistent=persistent,
            origin=origin,
        )
    if isinstance(value, list):
        return [
            wrap_mesh_value(
                item,
                coordinate_space=coordinate_space,
                persistent=persistent,
                origin=origin,
            )
            for item in value
        ]
    return value


def mesh_coordinate_space(value: Any) -> str:
    if isinstance(value, MeshArtifact):
        return value.coordinate_space
    if isinstance(value, list):
        for item in value:
            space = mesh_coordinate_space(item)
            if space != COORDINATE_SPACE_UNKNOWN:
                return space
    return COORDINATE_SPACE_UNKNOWN


def first_mesh_path(value: Any) -> Path | None:
    if isinstance(value, MeshArtifact):
        return value.path
    if isinstance(value, Path):
        return value
    if isinstance(value, list):
        for item in value:
            path = first_mesh_path(item)
            if path is not None:
                return path
    return None


def mesh_value_exists(value: Any) -> bool:
    if isinstance(value, MeshArtifact):
        return value.exists()
    if isinstance(value, Path):
        return value.is_file()
    if isinstance(value, list):
        return all(mesh_value_exists(item) for item in value)
    return True


def contains_nonpersistent_mesh(value: Any) -> bool:
    if isinstance(value, MeshArtifact):
        return not value.persistent
    if isinstance(value, list):
        return any(contains_nonpersistent_mesh(item) for item in value)
    return False


def _unique_destination(collection_dir: Path, source: Path) -> Path:
    candidate = collection_dir / source.name
    if not candidate.exists():
        return candidate
    return collection_dir / f"{source.stem}_{uuid.uuid4().hex[:8]}{source.suffix}"


def publish_mesh_value(value: Any, collection_dir: Path) -> Any:
    """Publish mesh artifact(s) into a workspace collection.

    Publication copies rather than moves so another downstream/preview sink can
    still read the intermediate artifact during the same DAG execution.
    """
    if isinstance(value, list):
        return [publish_mesh_value(item, collection_dir) for item in value]

    artifact = value if isinstance(value, MeshArtifact) else wrap_mesh_value(value)
    if not isinstance(artifact, MeshArtifact):
        return value
    if not artifact.exists():
        raise FileNotFoundError(f"Mesh artifact not found: {artifact.path}")

    collection_dir.mkdir(parents=True, exist_ok=True)
    try:
        artifact.path.resolve().relative_to(collection_dir.resolve())
        return MeshArtifact(
            path=artifact.path,
            coordinate_space=artifact.coordinate_space,
            persistent=True,
            origin="output",
        )
    except ValueError:
        pass

    destination = _unique_destination(collection_dir, artifact.path)
    shutil.copy2(artifact.path, destination)
    return MeshArtifact(
        path=destination,
        coordinate_space=artifact.coordinate_space,
        persistent=True,
        origin="output",
    )


def cleanup_artifact_root(root: Path) -> None:
    """Remove one run-owned artifact directory, never arbitrary workspace data."""
    if root.name and root.parent.name == ".artifacts":
        shutil.rmtree(root, ignore_errors=True)

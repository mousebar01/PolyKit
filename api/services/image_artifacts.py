"""Typed helpers for image artifacts crossing the workflow boundary.

Image model nodes use the same workspace-owned, file-backed contract as mesh
nodes, but intentionally stay separate from ``MeshArtifact`` so a downstream
consumer cannot accidentally treat a PNG as a scene asset.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import uuid
from typing import Any


@dataclass(frozen=True)
class ImageArtifact:
    """A generated or uploaded image travelling through a workflow DAG."""

    path: Path
    persistent: bool = False
    origin: str = "intermediate"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))

    @property
    def format(self) -> str:
        return self.path.suffix.lower().lstrip(".") or "unknown"

    def exists(self) -> bool:
        return self.path.is_file()


def unwrap_image_value(value: Any) -> Any:
    if isinstance(value, ImageArtifact):
        return value.path
    if isinstance(value, list):
        return [unwrap_image_value(item) for item in value]
    return value


def wrap_image_value(
    value: Any,
    *,
    persistent: bool = False,
    origin: str = "intermediate",
) -> Any:
    if isinstance(value, ImageArtifact):
        return value
    if isinstance(value, Path):
        return ImageArtifact(path=value, persistent=persistent, origin=origin)
    if isinstance(value, list):
        return [
            wrap_image_value(item, persistent=persistent, origin=origin)
            for item in value
        ]
    return value


def first_image_path(value: Any) -> Path | None:
    if isinstance(value, ImageArtifact):
        return value.path
    if isinstance(value, Path):
        return value
    if isinstance(value, list):
        for item in value:
            path = first_image_path(item)
            if path is not None:
                return path
    return None


def image_value_exists(value: Any) -> bool:
    if isinstance(value, ImageArtifact):
        return value.exists()
    if isinstance(value, Path):
        return value.is_file()
    if isinstance(value, list):
        return all(image_value_exists(item) for item in value)
    return True


def contains_nonpersistent_image(value: Any) -> bool:
    if isinstance(value, ImageArtifact):
        return not value.persistent
    if isinstance(value, list):
        return any(contains_nonpersistent_image(item) for item in value)
    return False


def _unique_destination(collection_dir: Path, source: Path) -> Path:
    candidate = collection_dir / source.name
    if not candidate.exists():
        return candidate
    return collection_dir / f"{source.stem}_{uuid.uuid4().hex[:8]}{source.suffix}"


def publish_image_value(value: Any, collection_dir: Path) -> Any:
    """Copy image artifact(s) into a workspace collection and mark persistent."""
    if isinstance(value, list):
        return [publish_image_value(item, collection_dir) for item in value]

    artifact = value if isinstance(value, ImageArtifact) else wrap_image_value(value)
    if not isinstance(artifact, ImageArtifact):
        return value
    if not artifact.exists():
        raise FileNotFoundError(f"Image artifact not found: {artifact.path}")

    collection_dir.mkdir(parents=True, exist_ok=True)
    try:
        artifact.path.resolve().relative_to(collection_dir.resolve())
        return ImageArtifact(path=artifact.path, persistent=True, origin="output")
    except ValueError:
        pass

    destination = _unique_destination(collection_dir, artifact.path)
    shutil.copy2(artifact.path, destination)
    return ImageArtifact(path=destination, persistent=True, origin="output")

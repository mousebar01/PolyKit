"""Canonical capability discovery across built-ins, model runtimes, and node packs.

This is a classification facade, not a second execution registry. Existing
runtime registries remain authoritative for loading and running implementations.
The facade gives Application Commands, Agent adapters, and Workflow tooling one
vocabulary for asking what a class_type represents.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from services.model_runtime_registry import model_runtime_registry
from services.node_catalog import process_node_pack


CapabilityKind = Literal["source", "generator", "processor", "composer", "sink"]
GeneratorKind = Literal["ai", "procedural"]

_BUILTINS: dict[str, CapabilityKind] = {
    "polykit.text": "source",
    "polykit.image": "source",
    "polykit.mesh": "source",
    "polykit.output": "sink",
    "polykit.preview": "sink",
    "polykit.image_output": "sink",
    "polykit.interrupt": "processor",
}


@dataclass(frozen=True)
class CapabilityDescriptor:
    id: str
    kind: CapabilityKind
    provider: Literal["builtin", "model", "node-pack"]
    generator_kind: GeneratorKind | None = None
    name: str | None = None
    manifest: dict[str, Any] | None = None


def _pack_kind(manifest: dict[str, Any], class_type: str) -> tuple[CapabilityKind, GeneratorKind | None]:
    declared = str(manifest.get("capability_kind") or "").strip()
    if declared in {"source", "generator", "processor", "composer", "sink"}:
        kind: CapabilityKind = declared  # type: ignore[assignment]
    elif class_type.startswith("scene-composer/"):
        kind = "composer"
    else:
        kind = "processor"

    generator_kind: GeneratorKind | None = None
    if kind == "generator":
        declared_generator = str(manifest.get("generator_kind") or "procedural").strip()
        generator_kind = "ai" if declared_generator == "ai" else "procedural"
    return kind, generator_kind


def resolve_capability(class_type: str) -> CapabilityDescriptor:
    """Resolve one executable class_type into the shared product vocabulary."""

    if class_type in _BUILTINS:
        return CapabilityDescriptor(
            id=class_type,
            kind=_BUILTINS[class_type],
            provider="builtin",
            name=class_type,
        )

    process = process_node_pack(class_type)
    if process is not None:
        _pack_dir, manifest, node = process
        raw_manifest = dict(manifest)
        kind, generator_kind = _pack_kind(raw_manifest, class_type)
        name = str(node.get("name") or manifest.get("name") or class_type)
        return CapabilityDescriptor(
            id=class_type,
            kind=kind,
            provider="node-pack",
            generator_kind=generator_kind,
            name=name,
            manifest=raw_manifest,
        )

    try:
        manifest = dict(model_runtime_registry.get_manifest(class_type))
    except (KeyError, ValueError) as exc:
        raise KeyError(f"Unknown capability '{class_type}'") from exc

    return CapabilityDescriptor(
        id=class_type,
        kind="generator",
        provider="model",
        generator_kind="ai",
        name=str(manifest.get("name") or class_type),
        manifest=manifest,
    )


def is_known_capability(class_type: str) -> bool:
    try:
        resolve_capability(class_type)
        return True
    except KeyError:
        return False


def texture_refiner_for(generator_id: str) -> str | None:
    """Return the compatible sibling image+mesh refinement capability, if any."""

    if "/" not in generator_id:
        return None
    pack_id, node_id = generator_id.rsplit("/", 1)
    if node_id != "generate":
        return None
    candidate = f"{pack_id}/refine"
    try:
        manifest = model_runtime_registry.get_manifest(candidate)
    except (KeyError, ValueError):
        return None
    inputs = manifest.get("inputs") or []
    if manifest.get("output") == "mesh" and "image" in inputs and "mesh" in inputs:
        return candidate
    return None


__all__ = [
    "CapabilityDescriptor",
    "CapabilityKind",
    "GeneratorKind",
    "is_known_capability",
    "resolve_capability",
    "texture_refiner_for",
]

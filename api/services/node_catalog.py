"""Unified node catalog — every class_type the executor can run.

Catalog metadata comes from installed manifests, not loaded model instances.
This keeps editor/schema discovery independent from whether a model environment
is currently healthy or loaded into accelerator memory. Explicit runtime-only
executors (such as the fake CPU test executor) are represented separately so
the catalog still describes everything the executor can actually run.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.node_pack_inventory import get_pack, is_official, iter_installed_packs


BUILTIN_NODES: Dict[str, Dict[str, Any]] = {
    "polykit.image": {
        "name": "Image",
        "description": "Image source; accepts base64 data or a workspace path.",
        "outputs": ["image"],
    },
    "polykit.text": {
        "name": "Text",
        "description": "Text source; emits a literal string.",
        "outputs": ["text"],
    },
    "polykit.mesh": {
        "name": "Load 3D Mesh",
        "description": "Mesh source; accepts a workspace path or base64 payload.",
        "inputs": ["mesh"],
        "outputs": ["mesh"],
    },
    "polykit.interrupt": {
        "name": "Interrupt / Approval Gate",
        "description": "Durable gate that releases execution until an external signal resumes the same WorkflowRun.",
        "inputs": ["after"],
        "outputs": ["signal"],
    },
    "polykit.output": {
        "name": "Scene Output",
        "description": "Terminal sink that pushes a mesh into the 3D viewer.",
        "inputs": ["mesh"],
    },
    "polykit.preview": {
        "name": "Preview",
        "description": "Terminal sink that collects a mesh for preview.",
        "inputs": ["mesh"],
    },
    "polykit.image_output": {
        "name": "Image Output",
        "description": "Terminal sink that publishes a generated image into the workspace.",
        "inputs": ["image"],
    },
}

CATEGORY_BUILTIN = "builtin"
CATEGORY_MODEL = "model"
CATEGORY_PROCESS = "process"


@dataclass
class NodeDefinition:
    class_type: str
    name: str
    category: str
    description: str = ""
    inputs: List[str] = field(default_factory=list)
    input_labels: Optional[List[str]] = None
    outputs: List[str] = field(default_factory=list)
    params_schema: List[Dict[str, Any]] = field(default_factory=list)
    builtin: bool = False
    i18n: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pack_i18n: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pack_name: Optional[str] = None
    pack_author: Optional[str] = None
    pack_id: Optional[str] = None
    node_id: Optional[str] = None
    pack_dir: Optional[str] = None
    entry: Optional[str] = None
    batch_input: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _builtin_definitions() -> List[NodeDefinition]:
    return [
        NodeDefinition(
            class_type=class_type,
            name=spec.get("name", class_type),
            category=CATEGORY_BUILTIN,
            description=spec.get("description", ""),
            inputs=list(spec.get("inputs", [])),
            outputs=list(spec.get("outputs", [])),
        )
        for class_type, spec in BUILTIN_NODES.items()
    ]


def _runtime_definitions() -> List[NodeDefinition]:
    """Definitions for executable nodes that do not come from Node Pack files."""
    executor = os.environ.get("POLYKIT_EXECUTOR", "cuda").strip().lower() or "cuda"
    if executor != "fake":
        return []
    return [
        NodeDefinition(
            class_type="fake",
            name="Fake CPU Executor",
            category=CATEGORY_MODEL,
            description="Deterministic CPU-only test artifact; not a model benchmark.",
            inputs=["image"],
            outputs=["mesh"],
            builtin=True,
            pack_name="PolyKit Test Runtime",
            pack_id="fake",
            node_id="fake",
        )
    ]


def _node_inputs(node: dict, manifest: dict) -> list[str]:
    values = node.get("inputs") or manifest.get("inputs")
    if values:
        return [str(value) for value in values]
    single = node.get("input", manifest.get("input"))
    return [str(single)] if single else []


def _model_definitions() -> List[NodeDefinition]:
    result: List[NodeDefinition] = []
    for pack_dir, manifest in iter_installed_packs():
        if manifest.get("type", "model") != "model":
            continue
        pack_id = str(manifest.get("id") or pack_dir.name)
        nodes = [node for node in (manifest.get("nodes") or []) if isinstance(node, dict) and node.get("id")]
        if not nodes:
            nodes = [{"id": pack_id}]

        for node in nodes:
            node_id = str(node.get("id") or pack_id)
            class_type = f"{pack_id}/{node_id}" if node_id != pack_id or manifest.get("nodes") else pack_id
            result.append(NodeDefinition(
                class_type=class_type,
                name=str(node.get("name") or manifest.get("name") or node_id),
                category=CATEGORY_MODEL,
                description=str(node.get("description") or manifest.get("description", "")),
                inputs=_node_inputs(node, manifest),
                input_labels=node.get("input_labels") or node.get("inputLabels") or manifest.get("input_labels"),
                outputs=[str(node.get("output", manifest.get("output", "mesh")))],
                params_schema=node.get("params_schema", manifest.get("params_schema", [])),
                batch_input=(str(node.get("batch_input")) if node.get("batch_input") else None),
                builtin=is_official(pack_dir),
                i18n=node.get("i18n", {}) if isinstance(node.get("i18n", {}), dict) else {},
                pack_i18n=manifest.get("i18n", {}) if isinstance(manifest.get("i18n", {}), dict) else {},
                pack_name=str(manifest.get("name") or pack_id),
                pack_author=str(manifest.get("author", "")) or None,
                pack_id=pack_id,
                node_id=node_id,
                pack_dir=str(pack_dir),
            ))
    return result


def _process_definitions() -> List[NodeDefinition]:
    result: List[NodeDefinition] = []
    for pack_dir, manifest in iter_installed_packs():
        if manifest.get("type") != "process":
            continue
        pack_id = str(manifest.get("id") or pack_dir.name)
        entry = str(manifest.get("entry", ""))
        for node in manifest.get("nodes", []) or []:
            if not isinstance(node, dict) or not node.get("id"):
                continue
            node_id = str(node["id"])
            result.append(NodeDefinition(
                class_type=f"{pack_id}/{node_id}",
                name=str(node.get("name") or node_id),
                category=CATEGORY_PROCESS,
                description=str(node.get("description") or manifest.get("description", "")),
                inputs=_node_inputs(node, manifest),
                input_labels=node.get("input_labels") or node.get("inputLabels"),
                outputs=[str(node.get("output", "mesh"))],
                params_schema=node.get("params_schema", []),
                batch_input=(str(node.get("batch_input")) if node.get("batch_input") else None),
                builtin=is_official(pack_dir),
                i18n=node.get("i18n", {}) if isinstance(node.get("i18n", {}), dict) else {},
                pack_i18n=manifest.get("i18n", {}) if isinstance(manifest.get("i18n", {}), dict) else {},
                pack_name=str(manifest.get("pack_name") or manifest.get("name") or pack_id),
                pack_author=str(manifest.get("author", "")) or None,
                pack_id=pack_id,
                node_id=node_id,
                pack_dir=str(pack_dir),
                entry=entry,
            ))
    return result


def get_node_definitions() -> List[NodeDefinition]:
    definitions = (
        _builtin_definitions()
        + _runtime_definitions()
        + _model_definitions()
        + _process_definitions()
    )
    # Keep class_type the single identity even if a runtime-only adapter and an
    # installed pack ever use the same id. The explicit runtime definition wins.
    unique: Dict[str, NodeDefinition] = {}
    runtime_types = {definition.class_type for definition in _runtime_definitions()}
    for definition in definitions:
        if definition.class_type in runtime_types and definition.class_type in unique:
            continue
        unique[definition.class_type] = definition
    return list(unique.values())


def get_node_definition(class_type: str) -> Optional[NodeDefinition]:
    for definition in get_node_definitions():
        if definition.class_type == class_type:
            return definition
    return None


def is_known(class_type: str) -> bool:
    return get_node_definition(class_type) is not None


def process_node_pack(class_type: str) -> Optional[Tuple[Path, Dict[str, Any], Dict[str, Any]]]:
    if "/" not in class_type:
        return None
    pack_id, node_id = class_type.rsplit("/", 1)
    installed = get_pack(pack_id)
    if installed is None:
        return None
    pack_dir, manifest = installed
    if manifest.get("type") != "process":
        return None
    node = next(
        (item for item in manifest.get("nodes", []) if isinstance(item, dict) and item.get("id") == node_id),
        None,
    )
    if node is None:
        return None
    return pack_dir, manifest, node

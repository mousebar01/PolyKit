"""Model-node execution helpers for typed workflow inputs.

The legacy workflow executor was originally image-first. Mesh-primary model
nodes use this compatibility boundary so workflow execution can remain typed
while model adapters keep their established generate() contract.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from schemas.workflow import WorkflowExecutionNode
from services.asset_names import output_name
from services.model_runtime_registry import model_runtime_registry
from services.model_runtime import execute_model
from services.workflow_executor import WorkflowError, _load_model, _run_model_node


def _path_from_model_result(
    result: object,
    model_id: str,
    output_dir: Path,
    output_kind: str = "mesh",
) -> Path:
    candidate: object = result
    if isinstance(result, Mapping):
        for key in ("primary_mesh", "mesh", "image", "filePath", "path", "output_path"):
            value = result.get(key)
            if value is not None:
                candidate = value
                break

    if isinstance(candidate, Path):
        path = candidate
    elif isinstance(candidate, str) and candidate.strip():
        path = Path(candidate)
    else:
        raise WorkflowError(
            f"Model node '{model_id}' did not return an {output_kind} path "
            f"(got {type(result).__name__})"
        )

    if not path.is_absolute():
        path = output_dir / path
    if not path.is_file():
        raise WorkflowError(
            f"Model node '{model_id}' returned a missing {output_kind} file: {path}"
        )
    return path


def _mesh_source_stem(
    node: WorkflowExecutionNode,
    resolved_mesh: Path,
    manifest: Mapping[str, Any] | None,
) -> str:
    raw_mesh = node.inputs.get("mesh")
    if isinstance(raw_mesh, dict) and raw_mesh.get("kind") == "workspace_path":
        raw_path = str(raw_mesh.get("path", "")).strip()
        if raw_path:
            return Path(raw_path).stem
    if resolved_mesh.stem:
        return resolved_mesh.stem
    return str((manifest or {}).get("name") or node.class_type)


async def _run_mesh_primary_model_node(
    loop: asyncio.AbstractEventLoop,
    node: WorkflowExecutionNode,
    resolve: Callable[[Any], Any],
    output_dir: Path,
    cancel_event: Optional[object],
    phase_cb,
) -> Dict[str, Any]:
    mesh_value = node.inputs.get("mesh")
    if mesh_value is None:
        raise WorkflowError(f"Model node '{node.class_type}' requires a mesh input")

    resolved_mesh = resolve(mesh_value)
    if isinstance(resolved_mesh, list):
        outputs = []
        for item in resolved_mesh:
            item_node = WorkflowExecutionNode(
                class_type=node.class_type,
                inputs={**node.inputs, "mesh": item},
            )
            out = await _run_mesh_primary_model_node(
                loop,
                item_node,
                resolve,
                output_dir,
                cancel_event,
                phase_cb,
            )
            outputs.append(out.get("mesh"))
        return {"mesh": outputs}

    if isinstance(resolved_mesh, str):
        resolved_mesh = Path(resolved_mesh)
    if not isinstance(resolved_mesh, Path):
        raise WorkflowError(
            f"Model node '{node.class_type}' mesh input must reference a mesh output"
        )
    if not resolved_mesh.is_file():
        raise WorkflowError(f"Mesh input not found: {resolved_mesh}")

    model_id = node.class_type
    try:
        model_runtime_registry.get_generator(model_id)
    except ValueError as exc:
        raise WorkflowError(f"Unknown executable node '{model_id}'") from exc
    manifest = model_runtime_registry.get_manifest(model_id)

    params_value = resolve(node.inputs.get("params", {}))
    params = dict(params_value) if isinstance(params_value, dict) else {}
    params.setdefault("remesh", "none")
    params.setdefault("enable_texture", False)
    params.setdefault("texture_resolution", 1024)
    params["mesh_path"] = str(resolved_mesh)

    model_runtime_registry.switch_model(model_id, allow_during_generation=True)
    await _load_model(loop, phase_cb, cancel_event)
    result = await loop.run_in_executor(
        None,
        lambda: execute_model(
            model_id,
            resolved_mesh,
            params,
            output_dir,
            phase_cb,
            cancel_event,
        ),
    )
    output_path = _path_from_model_result(result, model_id, output_dir)

    try:
        stem = _mesh_source_stem(node, resolved_mesh, manifest)
        output_tag = str((manifest or {}).get("output_tag") or "parts")
        renamed = output_path.with_name(
            output_name(stem, tag=output_tag, ext=output_path.suffix)
        )
        if renamed != output_path and not renamed.exists():
            output_path.rename(renamed)
            output_path = renamed
    except OSError:
        pass

    return {"mesh": output_path}


async def run_model_node(
    loop: asyncio.AbstractEventLoop,
    node: WorkflowExecutionNode,
    resolve: Callable[[Any], Any],
    output_dir: Path,
    cancel_event: Optional[object],
    phase_cb,
) -> Dict[str, Any]:
    """Execute a model node, selecting image-first or mesh-primary semantics."""
    if node.inputs.get("mesh") is not None and node.inputs.get("image") is None:
        return await _run_mesh_primary_model_node(
            loop,
            node,
            resolve,
            output_dir,
            cancel_event,
            phase_cb,
        )
    return await _run_model_node(
        loop,
        node,
        resolve,
        output_dir,
        cancel_event,
        phase_cb,
    )

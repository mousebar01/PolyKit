"""Workflow graph primitives and node-execution support helpers.

The runtime engine lives behind :mod:`services.execution_engine`. This module
contains reusable graph, cache, input-decoding, and node-adapter functions.
Legacy workflow-prefixed schema names remain during protocol migration.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.asset_names import output_name
from services.execution_references import (
    is_legacy_reference,
    iter_input_references,
    referenced_node_ids,
)
from services.model_runtime_registry import model_runtime_registry
from services.generators.base import smooth_progress
from services.model_runtime import execute_model
from services.node_catalog import get_node_definition, process_node_pack
from services.process_runner import run_processor
from services.runtime_paths import runtime_paths


IMAGE_NODE = "polykit.image"
TEXT_NODE = "polykit.text"
MESH_NODE = "polykit.mesh"
OUTPUT_NODE = "polykit.output"
PREVIEW_NODE = "polykit.preview"
IMAGE_OUTPUT_NODE = "polykit.image_output"
SOURCE_NODES = {IMAGE_NODE, TEXT_NODE, MESH_NODE}
SINK_NODES = {OUTPUT_NODE, PREVIEW_NODE, IMAGE_OUTPUT_NODE}
BUILTIN_NODES = SOURCE_NODES | SINK_NODES


class WorkflowError(ValueError):
    """User-facing execution validation/execution error.

    The compatibility name is retained while callers migrate to the generic
    execution layer.
    """


def is_reference(value: Any) -> bool:
    """Compatibility helper for the legacy ``[node_id, output_name]`` shape."""

    return is_legacy_reference(value)


def topological_order(prompt: Dict[str, WorkflowExecutionNode]) -> List[str]:
    """Return node ids in stable topological order, rejecting invalid DAGs.

    Batch-capable inputs may carry references recursively. Dependency counting
    uses unique upstream node ids so two outputs from the same node form one DAG
    edge rather than corrupting indegree accounting.
    """
    if not prompt:
        raise WorkflowError("Workflow prompt is empty")

    for node_id, node in prompt.items():
        if not node_id.strip():
            raise WorkflowError("Workflow node id cannot be empty")
        if not node.class_type.strip():
            raise WorkflowError(f"Node '{node_id}' has an empty class_type")
        for input_name, value in node.inputs.items():
            if not input_name.strip():
                raise WorkflowError(f"Node '{node_id}' has an empty input name")
            for ref_node, output_name in iter_input_references(input_name, value):
                if ref_node not in prompt:
                    raise WorkflowError(
                        f"Node '{node_id}' input '{input_name}' references missing node '{ref_node}'"
                    )
                if not output_name.strip():
                    raise WorkflowError(
                        f"Node '{node_id}' input '{input_name}' has an empty output name"
                    )

    indegree: Dict[str, int] = {node_id: 0 for node_id in prompt}
    dependents: Dict[str, List[str]] = {node_id: [] for node_id in prompt}
    for node_id, node in prompt.items():
        dependencies: list[str] = []
        for input_name, value in node.inputs.items():
            dependencies.extend(referenced_node_ids(input_name, value))
        for ref_node in dict.fromkeys(dependencies):
            indegree[node_id] += 1
            dependents[ref_node].append(node_id)

    order: List[str] = []
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    while queue:
        node_id = queue.pop(0)
        order.append(node_id)
        for dependent in dependents[node_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)

    if len(order) != len(prompt):
        raise WorkflowError("Workflow contains a cyclic node reference")
    return order


def select_execution_prompt(request: WorkflowExecutionRequest) -> Dict[str, WorkflowExecutionNode]:
    """Return the requested output branches and every node they depend on.

    ``target_node_ids`` mirrors ComfyUI's partial execution behavior: when one
    or more output/preview sinks are selected, only their upstream dependency
    closure is scheduled. A missing target or a non-sink target is rejected
    before a job is queued so the editor can surface a useful error.
    """
    targets = request.target_node_ids
    if targets is None:
        return request.prompt
    if not targets:
        raise WorkflowError("target_node_ids must contain at least one output or preview node")

    required: set[str] = set()
    pending = list(dict.fromkeys(targets))
    target_set = set(pending)
    while pending:
        node_id = pending.pop()
        if node_id in required:
            continue
        node = request.prompt.get(node_id)
        if node is None:
            raise WorkflowError(f"Execution target references missing node '{node_id}'")
        if node_id in target_set and node.class_type not in SINK_NODES:
            raise WorkflowError(
                f"Execution target '{node_id}' must be an output or preview sink"
            )
        required.add(node_id)
        for input_name, value in node.inputs.items():
            for ref_node, _output_name in iter_input_references(input_name, value):
                pending.append(ref_node)

    # Keep the original insertion order. The DAG validator will still reject
    # cycles or malformed references in the selected branch.
    return {node_id: node for node_id, node in request.prompt.items() if node_id in required}


def resolve_reference(value: Any, outputs: Dict[str, Dict[str, Any]]) -> Any:
    if not is_reference(value):
        raise WorkflowError(f"Expected a [node_id, output_name] reference, got: {value!r}")
    node_id, output_name = value
    node_outputs = outputs.get(node_id)
    if node_outputs is None:
        raise WorkflowError(
            f"Reference points to node '{node_id}' which has not produced output yet"
        )
    if output_name not in node_outputs:
        raise WorkflowError(f"Node '{node_id}' has no output named '{output_name}'")
    return node_outputs[output_name]


def validate_prompt_links(
    request: WorkflowExecutionRequest,
    prompt: Optional[Dict[str, WorkflowExecutionNode]] = None,
) -> None:
    """Validate typed reference links before any execution starts."""
    prompt = request.prompt if prompt is None else prompt
    defn_cache: Dict[str, Optional[Any]] = {}

    def _definition(class_type: str) -> Optional[Any]:
        if class_type not in defn_cache:
            defn_cache[class_type] = get_node_definition(class_type)
        return defn_cache[class_type]

    for node_id, node in prompt.items():
        for input_name, value in node.inputs.items():
            for ref_node_id, _output_name in iter_input_references(input_name, value):
                if ref_node_id not in prompt:
                    continue
                upstream_def = _definition(prompt[ref_node_id].class_type)
                upstream_outputs = upstream_def.outputs if upstream_def else []
                upstream_type = upstream_outputs[0] if upstream_outputs else None
                if input_name in {"image", "mesh", "text"} and upstream_type and upstream_type != input_name:
                    raise WorkflowError(
                        f"Node '{node_id}' input '{input_name}' expects '{input_name}' but "
                        f"node '{ref_node_id}' outputs '{upstream_type}'"
                    )


def _file_identity(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "path": str(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        return {"path": str(path), "missing": True}


def _canonical(value: Any) -> Any:
    """Canonical cache-key value including file-backed input identity."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"__bytes__": hashlib.sha256(value).hexdigest()}
    if isinstance(value, Path):
        return {"__path__": _file_identity(value)}
    if isinstance(value, dict):
        kind = value.get("kind")
        if kind == "workspace_path" and isinstance(value.get("path"), str):
            from services.workspace_paths import resolve_workspace_path

            raw_path = value["path"]
            try:
                resolved = resolve_workspace_path(runtime_paths.workspace, raw_path)
                identity = _file_identity(resolved)
            except (TypeError, ValueError):
                identity = {"path": raw_path, "invalid": True}
            return {"__workspace_path__": identity}
        if kind == "base64" and isinstance(value.get("data"), str):
            encoded = value["data"]
            return {
                "__base64__": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                "meta": {
                    str(k): _canonical(v)
                    for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
                    if k != "data"
                },
            }
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return {"__repr__": repr(value)}


def _canonical_reference_value(value: Any, ref_sigs: Dict[str, str]) -> Any:
    """Canonicalize nested batch inputs while injecting upstream signatures."""
    if is_reference(value):
        ref_node, output_name = value
        return {
            "__ref__": {
                "node": ref_node,
                "output": output_name,
                "signature": ref_sigs.get(ref_node, "?"),
            }
        }
    if isinstance(value, dict):
        # Preserve file/base64 identity handling as one literal unit.
        if value.get("kind") in {"workspace_path", "base64"}:
            return _canonical(value)
        return {
            str(k): _canonical_reference_value(v, ref_sigs)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_reference_value(item, ref_sigs) for item in value]
    return _canonical(value)


def _canonical_input_signature(input_name: str, value: Any, ref_sigs: Dict[str, str]) -> Any:
    # Preserve a direct legacy params reference, but do not recursively infer
    # references inside parameter containers because ["a", "b"] may be a
    # perfectly valid literal parameter value.
    if is_reference(value):
        return _canonical_reference_value(value, ref_sigs)
    if input_name == "params":
        return _canonical(value)
    return _canonical_reference_value(value, ref_sigs)


class NodeOutputCache:
    """Output cache keyed by transitive input signature."""

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}

    def signature(self, class_type: str, inputs: Dict[str, Any], ref_sigs: Dict[str, str]) -> str:
        parts: List[Any] = [class_type]
        for name in sorted(inputs.keys()):
            parts.append((name, _canonical_input_signature(name, inputs[name], ref_sigs)))
        raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, sig: str) -> Optional[Dict[str, Any]]:
        value = self._store.get(sig)
        if value is None:
            return None
        def _files_exist(candidate: Any) -> bool:
            if isinstance(candidate, Path):
                return candidate.is_file()
            if isinstance(candidate, (list, tuple)):
                return all(_files_exist(item) for item in candidate)
            # MeshArtifact/ImageArtifact expose exists() without making the
            # cache depend on either artifact module.
            exists = getattr(candidate, "exists", None)
            return bool(exists()) if callable(exists) else True

        if not all(_files_exist(item) for item in value.values()):
            self._store.pop(sig, None)
            return None
        return value

    def set(self, sig: str, value: Dict[str, Any]) -> None:
        self._store[sig] = value

    def clear(self) -> None:
        self._store.clear()


def _is_deterministic(params: Dict[str, Any]) -> bool:
    seed = params.get("seed")
    return seed is not None and seed != -1


def _decode_image_payload(value: object) -> bytes:
    if isinstance(value, Path):
        if not value.is_file():
            raise WorkflowError(f"Image file not found: {value}")
        data = value.read_bytes()
        if not data:
            raise WorkflowError("Image input is empty")
        return data
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        if not data:
            raise WorkflowError("Image input is empty")
        return data
    if hasattr(value, "path") and isinstance(getattr(value, "path"), Path):
        return _decode_image_payload(getattr(value, "path"))
    if not isinstance(value, dict):
        raise WorkflowError("Image input must be an object")

    kind = value.get("kind")
    if kind == "base64":
        encoded = value.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise WorkflowError("Base64 image input is empty")
        if "," in encoded and encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise WorkflowError("Invalid base64 image input") from exc
    elif kind == "workspace_path":
        from services.workspace_paths import resolve_workspace_path

        raw_path = value.get("path")
        try:
            image_path = resolve_workspace_path(runtime_paths.workspace, raw_path)
        except (TypeError, ValueError) as exc:
            raise WorkflowError(str(exc)) from exc
        if not image_path.is_file():
            raise WorkflowError(f"Image file not found: {raw_path}")
        image_bytes = image_path.read_bytes()
    else:
        raise WorkflowError("Image input kind must be 'base64' or 'workspace_path'")

    if not image_bytes:
        raise WorkflowError("Image input is empty")
    if len(image_bytes) > 50 * 1024 * 1024:
        raise WorkflowError("Image input is larger than 50 MiB")
    return image_bytes


def _decode_mesh_payload(value: object, temp_dir: Path) -> Path:
    if not isinstance(value, dict):
        raise WorkflowError("Mesh input must be an object")

    kind = value.get("kind")
    if kind == "workspace_path":
        from services.workspace_paths import resolve_workspace_path

        raw_path = value.get("path")
        try:
            mesh_path = resolve_workspace_path(runtime_paths.workspace, raw_path)
        except (TypeError, ValueError) as exc:
            raise WorkflowError(str(exc)) from exc
        if not mesh_path.is_file():
            raise WorkflowError(f"Mesh file not found: {raw_path}")
        return mesh_path
    if kind == "base64":
        encoded = value.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise WorkflowError("Base64 mesh input is empty")
        if "," in encoded and encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise WorkflowError("Invalid base64 mesh input") from exc
        if not data:
            raise WorkflowError("Base64 mesh input is empty")
        if len(data) > 1 * 1024 * 1024 * 1024:
            raise WorkflowError("Mesh input is larger than 1 GiB")
        temp_dir.mkdir(parents=True, exist_ok=True)
        dest = temp_dir / f"mesh-{uuid.uuid4().hex[:8]}.glb"
        dest.write_bytes(data)
        return dest
    raise WorkflowError("Mesh input kind must be 'workspace_path' or 'base64'")


def _phase_cb(job, start: int, end: int, persist: Callable[[], None]):
    def callback(pct: int, step: str = "") -> None:
        bounded = max(0, min(100, int(pct)))
        job.progress = max(job.progress, start + round((end - start) * bounded / 100))
        if step:
            job.step = step
        persist()
    return callback


async def _load_model(
    loop: asyncio.AbstractEventLoop,
    phase_cb,
    cancel_event: Optional[threading.Event],
):
    if not model_runtime_registry.active_status()["loaded"]:
        active = model_runtime_registry.active_status()
        model_name = active["name"]
        init_label = (
            f"Downloading {model_name}…" if not active["downloaded"] else f"Loading {model_name}…"
        )
        phase_cb(0, init_label)
        stop_load_evt = threading.Event()
        load_thread = threading.Thread(
            target=smooth_progress,
            args=(phase_cb, 0, 9, init_label, stop_load_evt, 4.0),
            daemon=True,
        )
        load_thread.start()
        try:
            return await loop.run_in_executor(None, model_runtime_registry.get_active)
        finally:
            stop_load_evt.set()
    return await loop.run_in_executor(None, model_runtime_registry.get_active)


async def _run_model_node(
    loop: asyncio.AbstractEventLoop,
    node: WorkflowExecutionNode,
    resolve: Callable[[Any], Any],
    output_dir: Path,
    cancel_event: Optional[threading.Event],
    phase_cb,
) -> Dict[str, Any]:
    image_value = node.inputs.get("image")
    text_value = node.inputs.get("text")
    if image_value is not None:
        resolved = resolve(image_value)
        if isinstance(resolved, list):
            outputs = []
            for item in resolved:
                item_node = WorkflowExecutionNode(
                    class_type=node.class_type,
                    inputs={**node.inputs, "image": item},
                )
                out = await _run_model_node(
                    loop, item_node, resolve, output_dir, cancel_event, phase_cb
                )
                outputs.append(out.get("image") or out.get("mesh"))
            output_key = "image" if manifest_output_kind(node.class_type) == "image" else "mesh"
            return {output_key: outputs}

    model_id = node.class_type
    try:
        model_runtime_registry.get_generator(model_id)
    except ValueError as exc:
        raise WorkflowError(f"Unknown executable node '{model_id}'") from exc
    manifest = model_runtime_registry.get_manifest(model_id)

    params_value = resolve(node.inputs.get("params", {}))
    params = dict(params_value) if isinstance(params_value, dict) else {}
    output_kind = manifest_output_kind(model_id)
    if output_kind != "image":
        params.setdefault("remesh", "none")
        params.setdefault("enable_texture", False)
        params.setdefault("texture_resolution", 1024)

    if text_value is not None:
        resolved_text = resolve(text_value)
        if isinstance(resolved_text, list):
            outputs = []
            for item in resolved_text:
                item_node = WorkflowExecutionNode(
                    class_type=node.class_type,
                    inputs={**node.inputs, "text": item},
                )
                out = await _run_model_node(
                    loop, item_node, resolve, output_dir, cancel_event, phase_cb
                )
                outputs.append(out.get("image") or out.get("mesh"))
            return {"image" if output_kind == "image" else "mesh": outputs}
        if output_kind != "image":
            raise WorkflowError(f"Model node '{model_id}' does not accept text input")
        params["prompt"] = str(resolved_text or "")
        model_runtime_registry.switch_model(model_id, allow_during_generation=True)
        await _load_model(loop, phase_cb, cancel_event)
        output_path = await loop.run_in_executor(
            None,
            lambda: execute_model(
                model_id,
                None,
                params,
                output_dir,
                phase_cb,
                cancel_event,
            ),
        )
        if not isinstance(output_path, Path):
            raise WorkflowError(f"Model node '{model_id}' did not return an image path")
        try:
            stem = str(params.get("filename_stem") or manifest.get("name") or model_id)
            renamed = output_path.with_name(output_name(stem, tag="image", ext=output_path.suffix))
            if renamed != output_path and not renamed.exists():
                output_path.rename(renamed)
                output_path = renamed
        except OSError:
            pass
        return {"image": output_path}

    image_bytes: bytes | None = None
    if image_value is not None:
        resolved = resolve(image_value)
        image_bytes = resolved if isinstance(resolved, bytes) else _decode_image_payload(resolved)

    mesh_value = node.inputs.get("mesh")
    if mesh_value is not None:
        mesh_path = resolve(mesh_value)
        if not isinstance(mesh_path, Path):
            raise WorkflowError(f"Model node '{model_id}' mesh input must reference a mesh output")
        params["mesh_path"] = str(mesh_path)
        params["enable_texture"] = True

    if image_bytes is None:
        raise WorkflowError(f"Model node '{model_id}' requires an image input")

    model_runtime_registry.switch_model(model_id, allow_during_generation=True)
    await _load_model(loop, phase_cb, cancel_event)
    output_path = await loop.run_in_executor(
        None,
        lambda: execute_model(
            model_id,
            image_bytes,
            params,
            output_dir,
            phase_cb,
            cancel_event,
        ),
    )
    if not isinstance(output_path, Path):
        raise WorkflowError(f"Model node '{model_id}' did not return a {output_kind} path")

    try:
        raw_image = node.inputs.get("image")
        stem = ""
        if isinstance(raw_image, dict) and raw_image.get("kind") == "workspace_path":
            stem = Path(str(raw_image.get("path", ""))).stem
        if not stem:
            stem = str((manifest or {}).get("name") or model_id)
        tag = "textured" if params.get("enable_texture") else None
        renamed = output_path.with_name(output_name(stem, tag=tag, ext=output_path.suffix))
        output_path.rename(renamed)
        output_path = renamed
    except OSError:
        pass

    return {output_kind: output_path}


def manifest_output_kind(model_id: str) -> str:
    """Return a model's declared artifact kind, defaulting to the legacy mesh."""
    try:
        value = model_runtime_registry.get_manifest(model_id).get("output", "mesh")
    except (KeyError, ValueError):
        value = "mesh"
    return str(value or "mesh")


async def _run_process_node(
    loop: asyncio.AbstractEventLoop,
    node: WorkflowExecutionNode,
    resolve: Callable[[Any], Any],
    workspace_dir: Path,
    temp_dir: Path,
    cancel_event: Optional[threading.Event],
    phase_cb,
) -> Dict[str, Any]:
    class_type = node.class_type
    mesh_value = node.inputs.get("mesh")
    process = process_node_pack(class_type)
    batch_input = str((process[2] if process is not None else {}).get("batch_input") or "")
    resolved_mesh = resolve(mesh_value) if mesh_value is not None else None
    if isinstance(resolved_mesh, list) and batch_input != "mesh":
        outputs = []
        for item in resolved_mesh:
            item_node = WorkflowExecutionNode(
                class_type=class_type,
                inputs={**node.inputs, "mesh": item},
            )
            out = await _run_process_node(
                loop,
                item_node,
                resolve,
                workspace_dir,
                temp_dir,
                cancel_event,
                phase_cb,
            )
            outputs.append(out.get("mesh"))
        return {"mesh": outputs}

    if process is None:
        raise WorkflowError(f"Unknown process node '{class_type}'")
    pack_dir, process_manifest, node_manifest = process

    input_data: Dict[str, Any] = {}
    image_value = node.inputs.get("image")
    if image_value is not None:
        resolved = resolve(image_value)
        if isinstance(resolved, Path):
            input_data["filePath"] = str(resolved)
        elif isinstance(resolved, bytes):
            img_path = temp_dir / f"proc-image-{uuid.uuid4().hex[:8]}.png"
            img_path.write_bytes(resolved)
            input_data["filePath"] = str(img_path)

    if mesh_value is not None:
        resolved = resolved_mesh
        if batch_input == "mesh" and isinstance(resolved, list):
            paths: list[str] = []
            for item in resolved:
                if isinstance(item, Path):
                    paths.append(str(item))
                elif isinstance(item, str) and item.strip():
                    paths.append(item)
                else:
                    raise WorkflowError(f"Process node '{class_type}' received a non-file mesh item")
            input_data["filePaths"] = paths
        elif isinstance(resolved, Path):
            input_data["filePath"] = str(resolved)
        elif isinstance(resolved, str):
            input_data["filePath"] = resolved

    text_value = node.inputs.get("text")
    if text_value is not None:
        input_data["text"] = str(resolve(text_value))

    params_value = resolve(node.inputs.get("params", {}))
    params = dict(params_value) if isinstance(params_value, dict) else {}
    # A process pack may expose multiple bounded nodes through one entry point.
    # Keep the node id server-owned and explicit; user params remain unchanged
    # at the API boundary while the subprocess can dispatch its manifest node.
    params["_node_id"] = str(node_manifest.get("id") or "")

    def _run() -> Dict[str, Any]:
        return run_processor(
            pack_dir,
            str(process_manifest.get("entry", "")),
            input_data,
            params,
            str(workspace_dir),
            str(temp_dir),
            progress_cb=phase_cb,
            cancel_event=cancel_event,
        )

    result = await loop.run_in_executor(None, _run)
    out_kind = node_manifest.get("output", "mesh")
    if out_kind in {"mesh", "image"} and result.get("filePath"):
        output: Dict[str, Any] = {out_kind: Path(str(result["filePath"]))}
        raw_sidecars = result.get("sidecars")
        if isinstance(raw_sidecars, list):
            output["sidecars"] = [Path(str(value)) for value in raw_sidecars if str(value or "").strip()]
        raw_metadata = result.get("metadata")
        if isinstance(raw_metadata, dict):
            output["metadata"] = dict(raw_metadata)
        return output
    if out_kind == "text" and result.get("text") is not None:
        output = {"text": str(result["text"])}
        raw_metadata = result.get("metadata")
        if isinstance(raw_metadata, dict):
            output["metadata"] = dict(raw_metadata)
        return output
    raise WorkflowError(f"Process node '{class_type}' produced no {out_kind} output")

def os_cache_enabled() -> bool:
    import os

    return os.environ.get("POLYKIT_DISABLE_NODE_CACHE", "0") != "1"
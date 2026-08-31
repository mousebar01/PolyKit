"""Artifact-aware canonical workflow engine.

Existing model/process node APIs still consume filesystem paths, while the DAG
carries typed file-backed mesh or image artifacts. Meshes publish through
``polykit.output`` and images through ``polykit.image_output`` (or an image
preview sink). Run-specific storage and cancellation live in an explicit
ExecutionContext. Deterministic node outputs are cached across runs in the
current server process using a bounded workspace-backed cache.

WorkflowRun durability is separate from that opportunistic cache: every
completed node is checkpointed into run-owned storage and may be restored after
process restart. ``polykit.interrupt`` suspends the run without occupying the
execution slot; an external signal resumes the same run id.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from schemas.workflow import WorkflowExecutionRequest
from services.execution_context import ExecutionContext
from services.model_runtime_registry import model_runtime_registry
from services.mesh_artifacts import (
    COORDINATE_SPACE_CANONICAL,
    COORDINATE_SPACE_UNKNOWN,
    MeshArtifact,
    cleanup_artifact_root,
    contains_nonpersistent_mesh,
    first_mesh_path,
    intermediate_mesh_step,
    mesh_coordinate_space,
    mesh_value_exists,
    publish_mesh_value,
    unwrap_mesh_value,
    wrap_mesh_value,
)
from services.image_artifacts import (
    ImageArtifact,
    contains_nonpersistent_image,
    first_image_path,
    publish_image_value,
    unwrap_image_value,
    wrap_image_value,
    image_value_exists,
)
from services.model_node_executor import run_model_node
from services.node_catalog import process_node_pack
from services.runtime_paths import runtime_paths
from services.workflow_execution import (
    INTERRUPT_NODE,
    consume_signal,
    initialize_workflow_execution,
    mark_step_completed,
    mark_step_started,
    mark_step_waiting,
    pending_signal,
    restore_completed_steps,
)
from services.workflow_executor import (
    IMAGE_NODE,
    IMAGE_OUTPUT_NODE,
    MESH_NODE,
    OUTPUT_NODE,
    SINK_NODES,
    TEXT_NODE,
    NodeOutputCache,
    WorkflowError,
    _decode_image_payload,
    _decode_mesh_payload,
    _is_deterministic,
    _phase_cb,
    _run_process_node,
    is_reference,
    os_cache_enabled,
    resolve_reference,
    select_execution_prompt,
    topological_order,
)


@dataclass(frozen=True)
class WorkflowWait:
    node_id: str
    signal_name: str
    prompt: str


def _stat_identity(path: Path) -> tuple[str, int, int] | tuple[str, str]:
    try:
        stat = path.stat()
        return (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    except OSError:
        return (str(path), "missing")


def _runtime_fingerprint(class_type: str) -> tuple[Any, ...]:
    """Version the cache against executable code and model-weight markers."""
    process = process_node_pack(class_type)
    if process is not None:
        pack_dir, manifest, _node = process
        entry = pack_dir / str(manifest.get("entry", ""))
        return (
            "process",
            _stat_identity(pack_dir / "manifest.json"),
            _stat_identity(entry),
        )

    try:
        manifest = model_runtime_registry.get_manifest(class_type)
        generator = model_runtime_registry.get_generator(class_type)
    except (KeyError, ValueError):
        return ("builtin", class_type)

    pack_id = str(manifest.get("pack_id") or class_type.split("/", 1)[0])
    pack_dir = runtime_paths.node_packs / pack_id
    model_dir = Path(getattr(generator, "model_dir", runtime_paths.models / class_type))
    check = str(manifest.get("download_check") or "").strip()
    check_path = model_dir / check if check else model_dir
    if check and not check_path.exists() and "/" in class_type:
        check_path = model_dir.parent / check
    return (
        "model",
        str(manifest.get("version", "")),
        _stat_identity(pack_dir / "manifest.json"),
        _stat_identity(pack_dir / "generator.py"),
        _stat_identity(check_path),
    )


def _materialize_cached_preview(value: Any, preview_dir: Path, counter: Optional[list[int]] = None) -> Any:
    """Give a preview sink a run-owned copy of cache-backed mesh values.

    Cache files are LRU-managed and may be evicted by later runs. A completed
    preview run must remain queryable for the normal run TTL, so its final mesh
    cannot point directly at ``.node-cache``.
    """
    counter = counter or [0]
    if isinstance(value, list):
        return [_materialize_cached_preview(item, preview_dir, counter) for item in value]
    if not isinstance(value, MeshArtifact) or value.origin != "cache":
        return value
    if not value.path.is_file():
        return value

    preview_dir.mkdir(parents=True, exist_ok=True)
    index = counter[0]
    counter[0] += 1
    suffix = value.path.suffix or ".bin"
    destination = preview_dir / f"mesh-{index}{suffix}"
    shutil.copy2(value.path, destination)
    return MeshArtifact(
        path=destination,
        coordinate_space=value.coordinate_space,
        persistent=False,
        origin="preview-cache",
    )


def _record_process_metadata(job: Any, node_id: str, value: Any) -> None:
    """Persist process-node evidence alongside the durable WorkflowRun.

    Process packs return small JSON metadata (for example Blender version and
    construction validation). Keeping it on the run makes that evidence
    queryable after the node checkpoint is restored, without adding a second
    task state store or copying it into the World document.
    """
    raw_metadata = value.get("metadata") if isinstance(value, dict) else None
    if not isinstance(raw_metadata, dict):
        return
    current = getattr(job, "meta", None)
    meta = dict(current) if isinstance(current, dict) else {}
    process_metadata = meta.get("process_metadata")
    process = dict(process_metadata) if isinstance(process_metadata, dict) else {}
    process[str(node_id)] = dict(raw_metadata)
    meta["process_metadata"] = process
    job.meta = meta


class ArtifactNodeOutputCache(NodeOutputCache):
    """Cross-run cache whose file-backed outputs outlive individual run dirs.

    The index is intentionally process-local. Mesh files are copied into a
    bounded workspace cache so a completed run may clean its ``.artifacts``
    tree without invalidating entries still reusable by later runs.
    """

    def __init__(self, max_entries: int = 64) -> None:
        super().__init__()
        self.max_entries = max(1, int(max_entries))
        self._cache_root = runtime_paths.workspace / ".node-cache"
        shutil.rmtree(self._cache_root, ignore_errors=True)
        self._cache_root.mkdir(parents=True, exist_ok=True)

    def _root(self) -> Path:
        current = runtime_paths.workspace / ".node-cache"
        if current != self._cache_root:
            old_root = self._cache_root
            self._store.clear()
            self._cache_root = current
            shutil.rmtree(old_root, ignore_errors=True)
            shutil.rmtree(self._cache_root, ignore_errors=True)
            self._cache_root.mkdir(parents=True, exist_ok=True)
        return self._cache_root

    def signature(self, class_type: str, inputs: Dict[str, Any], ref_sigs: Dict[str, str]) -> str:
        versioned_type = f"{class_type}|{_runtime_fingerprint(class_type)!r}"
        return super().signature(versioned_type, inputs, ref_sigs)

    def _materialize_mesh(self, value: Any, cache_dir: Path, counter: list[int]) -> Any:
        if isinstance(value, list):
            return [self._materialize_mesh(item, cache_dir, counter) for item in value]
        if isinstance(value, MeshArtifact):
            source = value.path
            if not source.is_file():
                return value
            index = counter[0]
            counter[0] += 1
            suffix = source.suffix or ".bin"
            destination = cache_dir / f"mesh-{index}{suffix}"
            shutil.copy2(source, destination)
            return MeshArtifact(
                path=destination,
                coordinate_space=value.coordinate_space,
                persistent=True,
                origin="cache",
            )
        if isinstance(value, ImageArtifact):
            source = value.path
            if not source.is_file():
                return value
            index = counter[0]
            counter[0] += 1
            suffix = source.suffix or ".bin"
            destination = cache_dir / f"image-{index}{suffix}"
            shutil.copy2(source, destination)
            return ImageArtifact(path=destination, persistent=True, origin="cache")
        if isinstance(value, Path) and value.is_file():
            index = counter[0]
            counter[0] += 1
            suffix = value.suffix or ".bin"
            destination = cache_dir / f"file-{index}{suffix}"
            shutil.copy2(value, destination)
            return destination
        return value

    def prune(self) -> None:
        """Evict old cache directories before a new serialized run begins."""
        root = self._root()
        try:
            root.mkdir(parents=True, exist_ok=True)
            dirs = [path for path in root.iterdir() if path.is_dir()]
        except FileNotFoundError:
            root.mkdir(parents=True, exist_ok=True)
            return
        if len(dirs) <= self.max_entries:
            return
        try:
            dirs.sort(key=lambda path: path.stat().st_mtime_ns)
        except FileNotFoundError:
            root.mkdir(parents=True, exist_ok=True)
            return
        for stale in dirs[: len(dirs) - self.max_entries]:
            shutil.rmtree(stale, ignore_errors=True)

    def get(self, sig: str) -> Optional[Dict[str, Any]]:
        value = self._store.get(sig)
        if value is None:
            return None

        def _artifact_exists(artifact_value: Any) -> bool:
            if isinstance(artifact_value, MeshArtifact):
                return mesh_value_exists(artifact_value)
            if isinstance(artifact_value, ImageArtifact):
                return image_value_exists(artifact_value)
            if isinstance(artifact_value, Path):
                return artifact_value.is_file()
            if isinstance(artifact_value, (list, tuple)):
                return all(_artifact_exists(item) for item in artifact_value)
            return True

        for artifact_value in value.values():
            if not _artifact_exists(artifact_value):
                self._store.pop(sig, None)
                return None
        cache_dir = self._root() / sig
        if cache_dir.is_dir():
            try:
                cache_dir.touch()
            except OSError:
                pass
        return value

    def set(self, sig: str, value: Dict[str, Any]) -> None:
        root = self._root()
        cache_dir = root / sig
        shutil.rmtree(cache_dir, ignore_errors=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        stable = dict(value)
        if "mesh" in stable:
            stable["mesh"] = self._materialize_mesh(stable["mesh"], cache_dir, [0])
        if "image" in stable:
            stable["image"] = self._materialize_mesh(stable["image"], cache_dir, [0])
        self._store[sig] = stable

    def clear(self) -> None:
        super().clear()
        root = self._root()
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)


_SHARED_NODE_CACHE = ArtifactNodeOutputCache()


def clear_workflow_cache() -> None:
    """Drop cross-run workflow cache entries and their private file copies."""
    _SHARED_NODE_CACHE.clear()


class WorkflowEngine:
    """Execute a workflow while keeping intermediate artifact files run-private."""

    def __init__(self, node_cache: Optional[NodeOutputCache] = None, cache_enabled: bool = True) -> None:
        self.node_cache = node_cache if node_cache is not None else _SHARED_NODE_CACHE
        self.cache_enabled = cache_enabled and os_cache_enabled()

    async def run(
        self,
        *,
        job_id: str,
        request: WorkflowExecutionRequest,
        job,
        persist: Callable[[], None],
        cancel_event: Optional[threading.Event],
        is_cancelled: Callable[[], bool],
    ) -> Optional[Path] | WorkflowWait:
        loop = asyncio.get_running_loop()
        context = ExecutionContext.create(
            run_id=job_id,
            collection=request.collection or "Workflows",
            cancel_event=cancel_event,
            is_cancelled=is_cancelled,
        )
        context.prepare()
        coll_dir = context.paths.collection_dir
        artifact_root = context.paths.artifact_root
        if isinstance(self.node_cache, ArtifactNodeOutputCache):
            self.node_cache.prune()

        execution_prompt = select_execution_prompt(request)
        order = topological_order(execution_prompt)
        total = max(len(order), 1)
        initialize_workflow_execution(job, request, order, workspace_root=runtime_paths.workspace)

        outputs, ref_sigs = restore_completed_steps(job, workspace_root=runtime_paths.workspace)
        cacheable: Dict[str, bool] = {}
        sink_values: Dict[str, Any] = {}
        output_sink_ids: list[str] = []
        for restored_id, restored in outputs.items():
            node = execution_prompt.get(restored_id)
            if node is None or node.class_type not in SINK_NODES:
                continue
            if "image" in restored:
                sink_values[restored_id] = restored["image"]
            elif "mesh" in restored:
                sink_values[restored_id] = restored["mesh"]
            if node.class_type == OUTPUT_NODE:
                output_sink_ids.append(restored_id)
        persist()

        def _checkpoint(node_id: str, node_outputs: Mapping[str, Any], signature: str) -> None:
            mark_step_completed(
                job,
                node_id,
                node_outputs,
                input_signature=signature,
                workspace_root=runtime_paths.workspace,
            )
            ref_sigs[node_id] = signature
            persist()

        for idx, node_id in enumerate(order):
            if context.cancelled():
                cleanup_artifact_root(artifact_root)
                return None
            node = execution_prompt[node_id]
            start = round(90 * idx / total)
            end = round(90 * (idx + 1) / total)

            if node_id in outputs:
                job.progress = max(job.progress, end)
                job.step = f"Restored {node.class_type} ({idx + 1}/{total})"
                persist()
                continue

            job.progress = max(job.progress, start)
            persist()

            def _resolve(value: Any) -> Any:
                if is_reference(value):
                    return resolve_reference(value, outputs)
                if isinstance(value, list):
                    return [_resolve(item) for item in value]
                if isinstance(value, dict):
                    return {key: _resolve(item) for key, item in value.items()}
                return value

            def _resolve_legacy(value: Any) -> Any:
                return unwrap_image_value(unwrap_mesh_value(_resolve(value)))

            if node.class_type == INTERRUPT_NODE:
                raw_params = _resolve(node.inputs.get("params", {}))
                params = raw_params if isinstance(raw_params, dict) else {}
                signal_name = str(params.get("signal_name") or params.get("signalName") or node_id).strip() or node_id
                gate_prompt = str(params.get("prompt") or params.get("message") or "Approval required to continue.")[:2000]
                signal = pending_signal(job, node_id, signal_name)
                if signal is None:
                    mark_step_waiting(job, node_id, signal_name=signal_name, prompt=gate_prompt)
                    job.status = "waiting"
                    job.step = f"Waiting for signal '{signal_name}'"
                    persist()
                    return WorkflowWait(node_id=node_id, signal_name=signal_name, prompt=gate_prompt)

                mark_step_started(job, node_id)
                outputs[node_id] = {"signal": signal.get("payload")}
                base_signature = self.node_cache.signature(node.class_type, node.inputs, ref_sigs)
                payload_json = json.dumps(signal.get("payload"), sort_keys=True, separators=(",", ":"), default=str)
                signature = hashlib.sha256(f"{base_signature}|{payload_json}".encode("utf-8")).hexdigest()
                _checkpoint(node_id, outputs[node_id], signature)
                consume_signal(job, str(signal.get("id") or ""))
                job.status = "running"
                persist()
                continue

            mark_step_started(job, node_id)
            persist()

            if node.class_type == IMAGE_NODE:
                outputs[node_id] = {"image": _decode_image_payload(_resolve(node.inputs.get("image")))}
                signature = self.node_cache.signature(node.class_type, node.inputs, ref_sigs)
                job.step = f"Loading image ({idx + 1}/{total})"
                _checkpoint(node_id, outputs[node_id], signature)
                continue
            if node.class_type == TEXT_NODE:
                outputs[node_id] = {"text": _resolve(node.inputs.get("text", ""))}
                signature = self.node_cache.signature(node.class_type, node.inputs, ref_sigs)
                job.step = f"Text input ({idx + 1}/{total})"
                _checkpoint(node_id, outputs[node_id], signature)
                continue
            if node.class_type == MESH_NODE:
                payload = _resolve(node.inputs.get("mesh"))
                mesh_path = _decode_mesh_payload(payload, context.paths.temp)
                workspace_source = isinstance(payload, dict) and payload.get("kind") == "workspace_path"
                outputs[node_id] = {
                    "mesh": MeshArtifact(
                        path=mesh_path,
                        coordinate_space=COORDINATE_SPACE_UNKNOWN,
                        persistent=workspace_source,
                        origin="workspace" if workspace_source else "upload",
                    )
                }
                signature = self.node_cache.signature(node.class_type, node.inputs, ref_sigs)
                job.step = f"Loading mesh ({idx + 1}/{total})"
                _checkpoint(node_id, outputs[node_id], signature)
                continue

            if node.class_type in SINK_NODES:
                artifact_kind = (
                    "image"
                    if node.class_type == IMAGE_OUTPUT_NODE or "image" in node.inputs
                    else "mesh"
                )
                input_name = "image" if artifact_kind == "image" else "mesh"
                artifact = _resolve(node.inputs.get(input_name))
                job.progress = max(job.progress, end)
                if node.class_type == OUTPUT_NODE:
                    job.step = f"Publishing output ({idx + 1}/{total})"
                    persist()
                    try:
                        artifact = publish_mesh_value(artifact, coll_dir)
                    except OSError as exc:
                        raise WorkflowError(f"Could not publish workflow output: {exc}") from exc
                    output_sink_ids.append(node_id)
                elif node.class_type == IMAGE_OUTPUT_NODE:
                    job.step = f"Publishing image ({idx + 1}/{total})"
                    persist()
                    try:
                        artifact = publish_image_value(artifact, coll_dir)
                    except OSError as exc:
                        raise WorkflowError(f"Could not publish workflow image: {exc}") from exc
                else:
                    job.step = f"Preparing preview ({idx + 1}/{total})"
                    persist()
                    if artifact_kind == "mesh":
                        artifact = _materialize_cached_preview(artifact, artifact_root / "preview")
                    elif artifact_kind == "image":
                        try:
                            artifact = publish_image_value(artifact, coll_dir)
                        except OSError as exc:
                            raise WorkflowError(f"Could not publish workflow image preview: {exc}") from exc
                outputs[node_id] = {input_name: artifact}
                sink_values[node_id] = artifact
                job.meta = {**(getattr(job, "meta", None) or {}), "artifact_kind": artifact_kind}
                signature = self.node_cache.signature(node.class_type, node.inputs, ref_sigs)
                _checkpoint(node_id, outputs[node_id], signature)
                continue

            cacheable_now = self.cache_enabled and _is_deterministic(
                _resolve(node.inputs.get("params", {})) or {}
            )
            if cacheable_now:
                for value in node.inputs.values():
                    if is_reference(value) and cacheable.get(value[0]) is False:
                        cacheable_now = False
                        break
            cacheable[node_id] = cacheable_now

            sig = None
            if cacheable_now:
                sig = self.node_cache.signature(node.class_type, node.inputs, ref_sigs)
                cached = self.node_cache.get(sig)
                if cached is not None:
                    outputs[node_id] = cached
                    _record_process_metadata(job, node_id, outputs[node_id])
                    job.step = f"Cached {node.class_type} ({idx + 1}/{total})"
                    _checkpoint(node_id, outputs[node_id], sig)
                    continue

            process = process_node_pack(node.class_type)
            if process is not None:
                name = (process[2].get("name") if len(process) > 2 else None) or node.class_type
                job.step = f"Running {name} ({idx + 1}/{total})"
                persist()
                phase_cb = _phase_cb(job, start, end, persist)
                inherited_space = mesh_coordinate_space(_resolve(node.inputs.get("mesh")))
                legacy_out = await _run_process_node(
                    loop,
                    node,
                    _resolve_legacy,
                    context.paths.process_workspace,
                    context.paths.temp,
                    context.cancel_event,
                    phase_cb,
                )
                outputs[node_id] = dict(legacy_out)
                raw_sidecars = legacy_out.get("sidecars")
                if isinstance(raw_sidecars, list):
                    published_sidecars: list[Path] = []
                    for raw_sidecar in raw_sidecars:
                        source = Path(str(raw_sidecar)).resolve()
                        try:
                            source.relative_to(context.paths.artifact_root.resolve())
                        except ValueError:
                            continue
                        if not source.is_file():
                            continue
                        destination = context.paths.collection_dir / source.name
                        try:
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, destination)
                        except OSError as exc:
                            raise WorkflowError(f"Could not publish process sidecar: {exc}") from exc
                        published_sidecars.append(destination)
                    outputs[node_id]["sidecars"] = published_sidecars
                if "mesh" in legacy_out:
                    outputs[node_id]["mesh"] = wrap_mesh_value(
                        legacy_out["mesh"],
                        coordinate_space=inherited_space,
                        persistent=False,
                        origin="process",
                    )
                if "image" in legacy_out:
                    outputs[node_id]["image"] = wrap_image_value(
                        legacy_out["image"],
                        persistent=False,
                        origin="process",
                    )
            else:
                manifest = model_runtime_registry.get_manifest(node.class_type)
                name = (manifest or {}).get("name") or node.class_type
                job.step = f"Running {name} ({idx + 1}/{total})"
                persist()
                base_phase_cb = _phase_cb(job, start, end, persist)

                def model_phase_cb(pct: int, step: str = "") -> None:
                    base_phase_cb(pct, intermediate_mesh_step(step))

                input_mesh_space = mesh_coordinate_space(_resolve(node.inputs.get("mesh")))
                mesh_primary = node.inputs.get("mesh") is not None and node.inputs.get("image") is None
                legacy_out = await run_model_node(
                    loop,
                    node,
                    _resolve_legacy,
                    context.paths.model_outputs,
                    context.cancel_event,
                    model_phase_cb,
                )
                outputs[node_id] = dict(legacy_out)
                if "mesh" in legacy_out:
                    outputs[node_id]["mesh"] = wrap_mesh_value(
                        legacy_out["mesh"],
                        coordinate_space=input_mesh_space if mesh_primary else COORDINATE_SPACE_CANONICAL,
                        persistent=False,
                        origin="model",
                    )
                if "image" in legacy_out:
                    outputs[node_id]["image"] = wrap_image_value(
                        legacy_out["image"],
                        persistent=False,
                        origin="model",
                    )

            _record_process_metadata(job, node_id, outputs[node_id])

            if cacheable_now and sig is not None:
                self.node_cache.set(sig, outputs[node_id])
                cached = self.node_cache.get(sig)
                if cached is not None:
                    outputs[node_id] = cached
            signature = sig if sig is not None else self.node_cache.signature(
                node.class_type, node.inputs, ref_sigs
            )
            _checkpoint(node_id, outputs[node_id], signature)

        selected: Any = None
        if request.output_node_id and request.output_node_id in sink_values:
            selected = sink_values[request.output_node_id]
        elif output_sink_ids:
            selected = sink_values[output_sink_ids[-1]]
        elif sink_values:
            selected = sink_values[next(reversed(sink_values))]

        final_artifact = first_mesh_path(selected) or first_image_path(selected)
        if final_artifact is not None:
            retained_preview = any(
                node_id not in output_sink_ids
                and (contains_nonpersistent_mesh(value) or contains_nonpersistent_image(value))
                for node_id, value in sink_values.items()
            )
            selected_is_persistent = not (
                contains_nonpersistent_mesh(selected) or contains_nonpersistent_image(selected)
            )
            if selected_is_persistent and not retained_preview:
                cleanup_artifact_root(artifact_root)

        return final_artifact


__all__ = [
    "ArtifactNodeOutputCache",
    "WorkflowEngine",
    "WorkflowWait",
    "clear_workflow_cache",
]

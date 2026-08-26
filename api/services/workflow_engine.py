"""Artifact-aware canonical workflow engine.

Existing model/process node APIs still consume filesystem paths, but the DAG
carries ``MeshArtifact`` values and only ``polykit.output`` publishes them into
a user collection. Run-specific storage and cancellation live in an explicit
ExecutionContext. Deterministic node outputs are cached across runs in the
current server process using a bounded workspace-backed cache.
"""
from __future__ import annotations

import asyncio
import shutil
import threading
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
from services.model_node_executor import run_model_node
from services.node_catalog import process_node_pack
from services.runtime_paths import runtime_paths
from services.workflow_executor import (
    IMAGE_NODE,
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
    topological_order,
)


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
        # A process-local index cannot safely reuse files left by an older
        # process because node implementations may have changed. Start clean.
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
        # The cache is disposable and may be removed by clear_workflow_cache()
        # or an external cleanup between runs. Recreate it instead of letting a
        # missing directory abort the whole workflow.
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
            # A concurrent cache clear can remove an entry after it was listed.
            # Nothing needs evicting in that case; the next run will rebuild
            # the cache root and repopulate only live entries.
            root.mkdir(parents=True, exist_ok=True)
            return
        for stale in dirs[: len(dirs) - self.max_entries]:
            shutil.rmtree(stale, ignore_errors=True)

    def get(self, sig: str) -> Optional[Dict[str, Any]]:
        value = self._store.get(sig)
        if value is None:
            return None
        if not mesh_value_exists(value.get("mesh")):
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
    """Execute a workflow while keeping intermediate mesh files private to the run."""

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
    ) -> Optional[Path]:
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

        order = topological_order(request.prompt)
        total = max(len(order), 1)

        outputs: Dict[str, Dict[str, Any]] = {}
        ref_sigs: Dict[str, str] = {}
        cacheable: Dict[str, bool] = {}
        sink_meshes: Dict[str, Any] = {}
        output_sink_ids: list[str] = []

        for idx, node_id in enumerate(order):
            if context.cancelled():
                cleanup_artifact_root(artifact_root)
                return None
            node = request.prompt[node_id]
            job.progress = max(job.progress, round(90 * idx / total))
            persist()

            def _resolve(value: Any) -> Any:
                return resolve_reference(value, outputs) if is_reference(value) else value

            def _resolve_legacy(value: Any) -> Any:
                return unwrap_mesh_value(_resolve(value))

            start = round(90 * idx / total)
            end = round(90 * (idx + 1) / total)

            if node.class_type == IMAGE_NODE:
                outputs[node_id] = {"image": _decode_image_payload(_resolve(node.inputs.get("image")))}
                ref_sigs[node_id] = self.node_cache.signature(node.class_type, node.inputs, ref_sigs)
                job.step = f"Loading image ({idx + 1}/{total})"
                continue
            if node.class_type == TEXT_NODE:
                outputs[node_id] = {"text": _resolve(node.inputs.get("text", ""))}
                ref_sigs[node_id] = self.node_cache.signature(node.class_type, node.inputs, ref_sigs)
                job.step = f"Text input ({idx + 1}/{total})"
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
                ref_sigs[node_id] = self.node_cache.signature(node.class_type, node.inputs, ref_sigs)
                job.step = f"Loading mesh ({idx + 1}/{total})"
                continue

            if node.class_type in SINK_NODES:
                mesh = _resolve(node.inputs.get("mesh"))
                job.progress = max(job.progress, end)
                if node.class_type == OUTPUT_NODE:
                    job.step = f"Publishing output ({idx + 1}/{total})"
                    persist()
                    try:
                        mesh = publish_mesh_value(mesh, coll_dir)
                    except OSError as exc:
                        raise WorkflowError(f"Could not publish workflow output: {exc}") from exc
                    output_sink_ids.append(node_id)
                else:
                    job.step = f"Preparing preview ({idx + 1}/{total})"
                    persist()
                    mesh = _materialize_cached_preview(mesh, artifact_root / "preview")
                outputs[node_id] = {"mesh": mesh}
                sink_meshes[node_id] = mesh
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
                    ref_sigs[node_id] = sig
                    job.step = f"Cached {node.class_type} ({idx + 1}/{total})"
                    persist()
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
                if "mesh" in legacy_out:
                    outputs[node_id]["mesh"] = wrap_mesh_value(
                        legacy_out["mesh"],
                        coordinate_space=inherited_space,
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

            if cacheable_now and sig is not None:
                self.node_cache.set(sig, outputs[node_id])
                cached = self.node_cache.get(sig)
                if cached is not None:
                    # Continue this DAG from the stable cache copy so the
                    # run-owned materialisation can be reclaimed normally.
                    outputs[node_id] = cached
            ref_sigs[node_id] = sig if sig is not None else self.node_cache.signature(
                node.class_type, node.inputs, ref_sigs
            )
            persist()

        selected: Any = None
        if request.output_node_id and request.output_node_id in sink_meshes:
            selected = sink_meshes[request.output_node_id]
        elif output_sink_ids:
            selected = sink_meshes[output_sink_ids[-1]]
        elif sink_meshes:
            selected = sink_meshes[next(reversed(sink_meshes))]

        final_mesh = first_mesh_path(selected)
        if final_mesh is not None and output_sink_ids:
            retained_preview = any(
                node_id not in output_sink_ids and contains_nonpersistent_mesh(mesh)
                for node_id, mesh in sink_meshes.items()
            )
            selected_is_persistent = not contains_nonpersistent_mesh(selected)
            if selected_is_persistent and not retained_preview:
                cleanup_artifact_root(artifact_root)

        return final_mesh

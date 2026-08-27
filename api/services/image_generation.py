"""Server-owned model generation jobs shared by API surfaces.

The canonical workflow API and the legacy ``/generate`` compatibility router
both enqueue work here so model lifecycle, persistence, cancellation and output
naming have one implementation.
"""
from __future__ import annotations

import asyncio
import threading
import traceback
import uuid
from pathlib import Path

from fastapi import BackgroundTasks

from schemas.generation import JobStatus
from services.asset_names import output_name
from services.model_runtime_registry import model_runtime_registry
from services.generators.base import GenerationCancelled, smooth_progress
from services.run_coordinator import run_coordinator
from services.mesh_artifacts import (
    COORDINATE_SPACE_CANONICAL,
    MeshArtifact,
    cleanup_artifact_root,
    intermediate_mesh_step,
    publish_mesh_value,
)
from services.model_runtime import execute_model
from services.runtime_paths import runtime_paths
from services.workspace_paths import normalize_collection


def workspace_url(path: Path, collection: str) -> str:
    try:
        rel = path.relative_to(runtime_paths.workspace)
        return f"/workspace/{rel.as_posix()}"
    except ValueError:
        return f"/workspace/{collection}/{path.name}"


def _executor_meta(model_id: str) -> dict | None:
    if model_id != "fake":
        return None
    return {
        "executor": "fake",
        "warning": "Synthetic CPU test artifact; not an inference result or performance benchmark.",
    }


def texture_refiner_id(model_id: str) -> str | None:
    """Return the sibling image+mesh node used to texture a generated mesh."""
    if "/" not in model_id:
        return None
    pack_id, node_id = model_id.rsplit("/", 1)
    if node_id != "generate":
        return None

    candidate = f"{pack_id}/refine"
    try:
        manifest = model_runtime_registry.get_manifest(candidate)
    except KeyError:
        return None

    inputs = manifest.get("inputs") or []
    if manifest.get("output") == "mesh" and "image" in inputs and "mesh" in inputs:
        return candidate
    return None


def enqueue_generation_job(
    background_tasks: BackgroundTasks,
    image_bytes: bytes,
    params: dict,
    collection: str,
    model_id: str,
    metadata: dict | None = None,
) -> str:
    """Validate and enqueue one model generation for any API caller."""
    model_runtime_registry.get_generator(model_id)

    run_coordinator.purge_old_jobs()
    job_id = str(uuid.uuid4())
    job_meta = dict(metadata or {})
    executor_meta = _executor_meta(model_id)
    if executor_meta:
        job_meta.update(executor_meta)
    job_meta.setdefault("collection", collection)

    job = JobStatus(job_id=job_id, status="pending", progress=0, meta=job_meta)
    run_coordinator.register(job)
    model_runtime_registry.begin_generation(job_id)
    background_tasks.add_task(run_generation, job_id, image_bytes, params, collection, model_id)
    return job_id


async def run_generation(
    job_id: str,
    image_bytes: bytes,
    params: dict,
    collection: str = "Default",
    model_id: str | None = None,
) -> None:
    job = run_coordinator.jobs[job_id]
    collection = normalize_collection(collection)
    workspace = runtime_paths.workspace
    artifact_root = workspace / ".artifacts" / job_id
    model_outputs_dir = artifact_root / "models"
    thumbnail_target: Path | None = None
    thumbnail_workspace_path: str | None = None

    def progress_cb(pct: int, step: str = "") -> None:
        if pct > job.progress:
            job.progress = pct
        if step:
            job.step = step
        run_coordinator.persist(job)

    def cancel_and_cleanup() -> bool:
        if not run_coordinator.is_cancelled(job_id):
            return False
        cleanup_artifact_root(artifact_root)
        return True

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_coordinator.generation_lock.acquire)
    run_coordinator.set_active(job_id)
    try:
        if cancel_and_cleanup():
            return

        job.status = "running"
        run_coordinator.persist(job)
        active_model_id = model_id or str(model_runtime_registry.active_status()["id"])
        model_runtime_registry.switch_model(active_model_id, allow_during_generation=True)

        refiner_id = texture_refiner_id(active_model_id) if params.get("enable_texture") else None
        phase_end = 55 if refiner_id else 95

        def phase_progress(start: int, end: int):
            def callback(pct: int, step: str = "") -> None:
                bounded = max(0, min(100, int(pct)))
                mapped = start + round((end - start) * bounded / 100)
                progress_cb(mapped, intermediate_mesh_step(step))

            return callback

        async def load_active(phase_cb) -> None:
            if not model_runtime_registry.active_status()["loaded"]:
                active = model_runtime_registry.active_status()
                model_name = active["name"]
                init_label = (
                    f"Downloading {model_name}…"
                    if not active["downloaded"]
                    else f"Loading {model_name}…"
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
                    await loop.run_in_executor(None, model_runtime_registry.get_active)
                finally:
                    stop_load_evt.set()
            else:
                await loop.run_in_executor(None, model_runtime_registry.get_active)

        primary_cb = phase_progress(0, phase_end)
        await load_active(primary_cb)
        if cancel_and_cleanup():
            return

        model_outputs_dir.mkdir(parents=True, exist_ok=True)
        cancel_event = run_coordinator.cancel_events.get(job_id)
        output_path = await loop.run_in_executor(
            None,
            lambda: execute_model(
                active_model_id,
                image_bytes,
                params,
                model_outputs_dir,
                primary_cb,
                cancel_event,
            ),
        )

        if cancel_and_cleanup():
            return

        if refiner_id:
            model_runtime_registry.switch_model(refiner_id, allow_during_generation=True)
            refine_cb = phase_progress(55, 95)
            await load_active(refine_cb)
            refine_params = {**params, "mesh_path": str(output_path)}
            output_path = await loop.run_in_executor(
                None,
                lambda: execute_model(
                    refiner_id,
                    image_bytes,
                    refine_params,
                    model_outputs_dir,
                    refine_cb,
                    cancel_event,
                ),
            )
            if cancel_and_cleanup():
                return

        try:
            image_name = (job.meta or {}).get("image_name") or ""
            stem = Path(image_name).stem if image_name else ""
            final_name = output_name(
                stem,
                tag="textured" if refiner_id else None,
                ext=".glb",
            )
            renamed = output_path.with_name(final_name)
            output_path.rename(renamed)
            output_path = renamed
        except OSError:
            pass

        progress_cb(97, "Publishing output…")
        collection_dir = workspace / collection
        published = publish_mesh_value(
            MeshArtifact(
                path=output_path,
                coordinate_space=COORDINATE_SPACE_CANONICAL,
                persistent=False,
                origin="model",
            ),
            collection_dir,
        )
        if not isinstance(published, MeshArtifact):
            raise RuntimeError("Generation output could not be published")
        output_path = published.path
        cleanup_artifact_root(artifact_root)

        job.status = "done"
        job.progress = 100
        job.step = "Generation complete"
        job.output_url = workspace_url(output_path, collection)
        run_coordinator.mark_completed(job)
        thumbnail_target = output_path
        try:
            thumbnail_workspace_path = output_path.relative_to(workspace).as_posix()
        except ValueError:
            thumbnail_target = None

    except GenerationCancelled:
        cleanup_artifact_root(artifact_root)
        job.status = "cancelled"
        run_coordinator.mark_completed(job)
    except Exception as exc:
        cleanup_artifact_root(artifact_root)
        if run_coordinator.is_cancelled(job_id):
            return
        tb = traceback.format_exc()
        msg = f"[Generation ERROR] {exc}\n{tb}"
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode("ascii", errors="replace").decode("ascii"))
        job.status = "error"
        job.error = tb.strip()
        run_coordinator.mark_completed(job)
    finally:
        run_coordinator.clear_active(job_id)
        model_runtime_registry.end_generation(job_id)
        run_coordinator.generation_lock.release()
        if job.status == "done" and thumbnail_target is not None and thumbnail_workspace_path is not None:
            # Best-effort card preview generation runs after the model lock is
            # released; the generation response never waits for thumbnail work.
            try:
                from services.asset_thumbnails import _LIBRARY_SIZE, prewarm_thumbnail
                prewarm_thumbnail(thumbnail_workspace_path, thumbnail_target, _LIBRARY_SIZE)
            except Exception as exc:
                print(f"[Thumbnails] generation prewarm could not be queued: {exc}")

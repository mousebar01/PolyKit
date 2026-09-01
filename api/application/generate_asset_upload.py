"""Materialize uploaded image inputs and prepare canonical asset-generation Runs."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from application.execution import PreparedExecution, prepare_execution_run
from application.generate_asset import GenerateAssetFromImageCommand, compile_generate_asset_from_image_plan
from schemas.execution import ExecutionInitiator
from services.capability_registry import texture_refiner_for
from services.model_runtime_registry import model_runtime_registry
from services.runtime_paths import runtime_paths
from services.workspace_paths import normalize_collection


_MAX_IMAGE_BYTES = 50 * 1024 * 1024


def _upload_suffix(content_type: str | None) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(str(content_type or "").lower(), ".img")


def prepare_uploaded_image_asset_run(
    *,
    image_bytes: bytes,
    content_type: str | None,
    image_name: str | None,
    model_id: str,
    collection: str,
    remesh: str,
    enable_texture: bool,
    texture_resolution: int,
    model_params: dict[str, Any] | None,
    initiator: ExecutionInitiator,
    workflow_id: str | None = None,
    node_id: str | None = None,
    world_id: str | None = None,
    proto_id: str | None = None,
) -> PreparedExecution:
    """Turn multipart bytes into a run-owned input and prepare one ExecutionPlan.

    The input lives inside ``.artifacts/<run_id>/inputs`` so retries can reuse it
    while the Run exists and normal artifact cleanup removes it after publishing.
    Only the relative path is stored in the durable execution snapshot.
    """

    if not content_type or not content_type.startswith("image/"):
        raise ValueError("File must be an image")
    if not image_bytes:
        raise ValueError("Image upload is empty")
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise ValueError("Image input is larger than 50 MiB")
    if remesh not in {"quad", "triangle", "none"}:
        raise ValueError("remesh must be 'quad', 'triangle', or 'none'")
    if texture_resolution < 64 or texture_resolution > 8192:
        raise ValueError("texture_resolution must be between 64 and 8192")

    collection = normalize_collection(collection)
    model_runtime_registry.get_generator(model_id)
    if enable_texture and texture_refiner_for(model_id) is None:
        raise ValueError(f"Model '{model_id}' does not provide a compatible Texture Mesh node")

    run_id = str(uuid.uuid4())
    relative_input = f".artifacts/{run_id}/inputs/source{_upload_suffix(content_type)}"
    input_path = runtime_paths.workspace / relative_input
    run_root = input_path.parents[1]
    input_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        input_path.write_bytes(image_bytes)
        mesh_params = dict(model_params or {})
        mesh_params["remesh"] = remesh
        # Refinement is an explicit capability in the canonical plan.
        mesh_params["enable_texture"] = False
        command = GenerateAssetFromImageCommand(
            image={"kind": "workspace_path", "path": relative_input},
            mesh_model_id=model_id,
            enable_texture=enable_texture,
            collection=collection,
            workflow_id=(workflow_id or "").strip() or None,
            node_id=(node_id or "").strip() or None,
            world_id=(world_id or "").strip() or None,
            proto_id=(proto_id or "").strip() or None,
            image_name=(image_name or "").strip() or None,
            mesh_params=mesh_params,
            texture_params={"texture_resolution": texture_resolution},
        )
        plan = compile_generate_asset_from_image_plan(command)
        return prepare_execution_run(plan, initiator=initiator, run_id=run_id)
    except Exception:
        # A failed validation/registration must not strand a preallocated input.
        shutil.rmtree(run_root, ignore_errors=True)
        raise


__all__ = ["prepare_uploaded_image_asset_run"]

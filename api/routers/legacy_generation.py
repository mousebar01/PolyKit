"""Legacy ``/generate`` compatibility endpoints.

New product code uses ``/workflow-runs``.  This router is intentionally named
``legacy_generation`` so it cannot be mistaken for the canonical execution API.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from services.image_generation import enqueue_generation_job, texture_refiner_id
from services.model_runtime_registry import model_runtime_registry
from services.run_coordinator import run_coordinator
from services.workspace_paths import normalize_collection

router = APIRouter(tags=["generation-legacy"])


@router.post("/from-image")
async def generate_from_image(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    model_id: str = Form(""),
    collection: str = Form("Default"),
    remesh: str = Form("quad"),
    enable_texture: bool = Form(False),
    texture_resolution: int = Form(1024),
    params: str = Form("{}"),
    workflow_id: str = Form(""),
    node_id: str = Form(""),
):
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")
    if remesh not in ("quad", "triangle", "none"):
        raise HTTPException(400, "remesh must be 'quad', 'triangle', or 'none'")

    collection = normalize_collection(collection)
    model_id = model_id or model_runtime_registry.active_status()["id"]
    try:
        model_runtime_registry.get_generator(model_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if enable_texture and texture_refiner_id(model_id) is None:
        raise HTTPException(400, f"Model '{model_id}' does not provide a compatible Texture Mesh node")

    try:
        model_params = json.loads(params)
    except (json.JSONDecodeError, TypeError):
        model_params = {}

    image_bytes = await image.read()
    full_params = {
        "remesh": remesh,
        "enable_texture": enable_texture,
        "texture_resolution": texture_resolution,
        **model_params,
    }
    metadata = {
        key: value
        for key, value in {
            "workflow_id": workflow_id.strip(),
            "node_id": node_id.strip(),
            "image_name": (image.filename or "").strip(),
        }.items()
        if value
    }
    job_id = enqueue_generation_job(
        background_tasks,
        image_bytes,
        full_params,
        collection,
        model_id,
        metadata,
    )
    return {"job_id": job_id}


@router.get("/status/{job_id}")
async def job_status(job_id: str):
    job = run_coordinator.jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


@router.post("/cancel/{job_id}")
async def cancel_job(job_id: str):
    if run_coordinator.cancel(job_id) is None:
        raise HTTPException(404, f"Job {job_id} not found")
    return {"cancelled": True}

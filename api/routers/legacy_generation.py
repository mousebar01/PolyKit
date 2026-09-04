"""Legacy ``/generate`` compatibility endpoints.

New product code uses the generic Run/Application API. This router preserves the
old transport and response shapes only; generation itself is prepared and run
through the same ExecutionPlan path as Web, Agent, Workflow, and World callers.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from application.generate_asset_upload import prepare_uploaded_image_asset_run
from application.run_control import RunNotFoundError, cancel_run as cancel_application_run
from schemas.execution import ExecutionInitiator
from services.execution_runtime import run_execution
from services.model_runtime_registry import model_runtime_registry
from services.run_coordinator import run_coordinator

router = APIRouter(tags=["generation-legacy"])


@router.post("/from-image")
async def generate_from_image(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    model_id: str = Form(""),
    collection: str = Form("Workflows"),
    remesh: str = Form("none"),
    enable_texture: bool = Form(False),
    enable_optimize: bool = Form(False),
    target_faces: int = Form(1_000_000),
    texture_resolution: int = Form(1024),
    params: str = Form("{}"),
    workflow_id: str = Form(""),
    node_id: str = Form(""),
):
    try:
        parsed_params = json.loads(params)
    except (json.JSONDecodeError, TypeError):
        parsed_params = {}
    model_params = dict(parsed_params) if isinstance(parsed_params, dict) else {}
    image_bytes = await image.read()
    selected_model_id = model_id or model_runtime_registry.active_status()["id"]

    try:
        prepared = prepare_uploaded_image_asset_run(
            image_bytes=image_bytes,
            content_type=image.content_type,
            image_name=image.filename,
            model_id=selected_model_id,
            collection=collection,
            remesh=remesh,
            enable_texture=enable_texture,
            enable_optimize=enable_optimize,
            target_faces=target_faces,
            texture_resolution=texture_resolution,
            model_params=model_params,
            workflow_id=workflow_id,
            node_id=node_id,
            initiator=ExecutionInitiator(type="user", surface="legacy.generate.image"),
        )
    except ValueError as exc:
        status = 413 if "larger than 50 MiB" in str(exc) else 400
        raise HTTPException(status, str(exc)) from exc
    except (KeyError, OSError) as exc:
        raise HTTPException(500, f"Could not prepare generation: {exc}") from exc

    background_tasks.add_task(run_execution, prepared.run_id, prepared.request)
    return {"job_id": prepared.run_id}


@router.get("/status/{job_id}")
async def job_status(job_id: str):
    job = run_coordinator.jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


@router.post("/cancel/{job_id}")
async def cancel_job(job_id: str):
    try:
        cancel_application_run(job_id)
    except RunNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"cancelled": True}

"""HTTP compiler for validator repair scopes -> ProductionRecipe v1."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.production_recipe import compile_repair_recipe
from services.run_coordinator import run_coordinator
from services.world_store import WorldStoreError, get_world
from services.world_validation import WORLD_VALIDATION_CAPABILITIES, validate_world


router = APIRouter(prefix="/workspace-library/worlds", tags=["production-recipes"])


class ProductionRecipeCompileRequest(BaseModel):
    capability: str = Field(min_length=1, max_length=160)
    repair_scope_id: str = Field(min_length=1, max_length=320)
    run_id: str | None = Field(default=None, max_length=160)
    collection: str = Field(default="Scenes", max_length=160)
    render_preview: bool = True
    allow_scope_expansion: bool = False


def _run_payload(run_id: str | None) -> dict[str, Any] | None:
    if not run_id:
        return None
    job = run_coordinator.jobs.get(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} was not found")
    return {
        "run_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "meta": job.meta or {},
    }


@router.post("/{world_id}/production-recipes/compile")
async def compile_world_repair_recipe(world_id: str, request: ProductionRecipeCompileRequest):
    """Re-run authoritative validation, select one scope, and compile without executing it."""

    try:
        if request.capability not in WORLD_VALIDATION_CAPABILITIES:
            raise HTTPException(status_code=400, detail=f"Unsupported validator capability: {request.capability}")
        world = get_world(world_id)
        if world is None:
            raise HTTPException(status_code=404, detail="World was not found")
        run = _run_payload(request.run_id)
        validation = validate_world(world_id, world, request.capability, run=run)
        return compile_repair_recipe(
            world_id=world_id,
            world=world,
            validation=validation,
            repair_scope_id=request.repair_scope_id,
            collection=request.collection,
            render_preview=request.render_preview,
            allow_scope_expansion=request.allow_scope_expansion,
        )
    except HTTPException:
        raise
    except (WorldStoreError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not compile production recipe: {exc}") from exc


__all__ = ["router"]

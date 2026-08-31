"""HTTP helpers for binding produced workspace artifacts to World documents."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.world_agent import attach_world_artifact
from services.world_store import WorldStoreError, WorldTooLargeError, get_world, save_world


router = APIRouter(prefix="/workspace-library/worlds", tags=["workspace-world-artifacts"])


class WorldArtifactAttachRequest(BaseModel):
    workspace_path: str = Field(min_length=1, max_length=1000)
    workflow_id: str | None = Field(default=None, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)
    concept_image: str | None = Field(default=None, max_length=1000)


@router.post("/{world_id}/artifacts/{proto_id}")
async def attach_world_asset(world_id: str, proto_id: str, request: WorldArtifactAttachRequest):
    """Bind a completed workspace mesh to a stable semantic world object id."""

    try:
        world = get_world(world_id)
        if world is None:
            raise HTTPException(status_code=404, detail="World was not found")
        updated = attach_world_artifact(
            world,
            proto_id=proto_id,
            workspace_path=request.workspace_path,
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            concept_image=request.concept_image,
        )
        saved = save_world(world_id, updated)
        return {"world_id": world_id, "proto_id": proto_id, "world": saved}
    except HTTPException:
        raise
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WorldStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not attach world artifact: {exc}") from exc

"""HTTP surface for server-owned world documents."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from services.world_agent import create_world_document
from services.world_store import (
    WorldNotFoundError,
    WorldStoreError,
    WorldTooLargeError,
    get_world,
    save_world,
)


router = APIRouter(prefix="/workspace-library/worlds", tags=["workspace-worlds"])


class WorldCreateRequest(BaseModel):
    """Optional metadata used when an Agent starts a new scene."""

    name: str | None = Field(default=None, max_length=240)
    prompt: str | None = Field(default=None, max_length=20_000)
    parent_world_id: str | None = Field(default=None, max_length=160)


@router.post("")
async def create_world(request: WorldCreateRequest | None = Body(default=None)):
    """Allocate and persist a new world record for one generation request."""

    try:
        payload = request or WorldCreateRequest()
        document = create_world_document(
            name=payload.name,
            prompt=payload.prompt,
            parent_world_id=payload.parent_world_id,
        )
        saved = save_world(document["id"], document)
        workspace_path = f"Workflows/{document['id']}.world.json"
        return {
            "world_id": document["id"],
            "workspace_path": workspace_path,
            "url": f"/workspace/{workspace_path}",
            "world": saved,
        }
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WorldStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create world: {exc}") from exc


@router.put("/{world_id:path}")
async def put_world(world_id: str, world: dict[str, Any] = Body(...)):
    """Create or replace one ``<world-id>.world.json`` workspace artifact."""

    try:
        save_world(world_id, world)
        workspace_path = f"Workflows/{world_id.strip()}.world.json"
        return {
            "world_id": world_id.strip(),
            "workspace_path": workspace_path,
            "url": f"/workspace/{workspace_path}",
        }
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WorldStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save world: {exc}") from exc


@router.get("/{world_id:path}")
async def read_world(world_id: str):
    """Read one saved world document."""

    try:
        world = get_world(world_id)
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WorldStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read world: {exc}") from exc
    if world is None:
        raise HTTPException(status_code=404, detail="World was not found")
    return world

"""Explicit Poly Haven provider endpoints.

These endpoints keep provider discovery/import separate from the local
workspace-library listing.  The provider service owns only network/file
normalization; Worlds and WorkflowRuns remain handled by their canonical APIs.
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.polyhaven_assets import PolyHavenError, import_model, search_models


router = APIRouter(prefix="/workspace-library/providers/polyhaven", tags=["workspace-library", "polyhaven"])


class PolyHavenSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    category: Optional[str] = Field(default=None, max_length=120)
    limit: int = Field(default=5, ge=1, le=50)
    refresh: bool = False


class PolyHavenImportRequest(BaseModel):
    asset_id: str = Field(min_length=1, max_length=120)
    resolution: Literal["1k", "2k", "4k", "8k"] = "2k"


@router.post("/search")
async def search_polyhaven(request: PolyHavenSearchRequest):
    """Read-only search against Poly Haven's public model metadata API."""

    try:
        loop = asyncio.get_running_loop()
        matches = await loop.run_in_executor(
            None,
            lambda: search_models(request.query, category=request.category, limit=request.limit, refresh=request.refresh),
        )
    except PolyHavenError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "provider": "polyhaven", "matches": matches}


@router.post("/import")
async def import_polyhaven(request: PolyHavenImportRequest):
    """Explicitly import one selected Poly Haven model into the workspace."""

    try:
        loop = asyncio.get_running_loop()
        asset = await loop.run_in_executor(
            None,
            lambda: import_model(request.asset_id, resolution=request.resolution),
        )
    except PolyHavenError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "provider": "polyhaven", "asset": asset}


__all__ = ["router"]

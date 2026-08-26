from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from services.workflow_store import delete_workflow, list_workflows, save_workflow


router = APIRouter(prefix="/workflow-definitions", tags=["workflow-definitions"])


@router.get("")
async def list_saved_workflows():
    return list_workflows()


@router.put("/{workflow_id:path}")
async def put_workflow_definition(workflow_id: str, workflow: dict[str, Any] = Body(...)):
    body_id = str(workflow.get("id") or "").strip()
    if body_id != workflow_id:
        raise HTTPException(400, "Workflow id in the URL must match the request body")
    try:
        return save_workflow(workflow)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"Could not save workflow: {exc}") from exc


@router.delete("/{workflow_id:path}")
async def remove_workflow_definition(workflow_id: str):
    try:
        deleted = delete_workflow(workflow_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"Could not delete workflow: {exc}") from exc
    return {"success": True, "deleted": deleted}

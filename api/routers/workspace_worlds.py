"""HTTP surface for server-owned world documents."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from pydantic import BaseModel, Field

from services.scene_planner import ScenePlanError, apply_scene_plan_to_world, compile_scene_plan
from services.world_agent import create_world_document
from services.world_store import (
    WorldNotFoundError,
    WorldStoreError,
    WorldTooLargeError,
    get_world,
    save_world,
)
from services.runtime_paths import runtime_paths
from services.workspace_paths import normalize_collection, resolve_workspace_path


router = APIRouter(prefix="/workspace-library/worlds", tags=["workspace-worlds"])


class WorldCreateRequest(BaseModel):
    """Optional metadata used when an Agent starts a new scene."""

    name: str | None = Field(default=None, max_length=240)
    prompt: str | None = Field(default=None, max_length=20_000)
    parent_world_id: str | None = Field(default=None, max_length=160)


class ScenePlanCompileRequest(BaseModel):
    """Agent-authored scene graph accepted by the server-side planner."""

    # ``plan`` is useful for MCP callers that already have the EmbodiedGen
    # layout shape.  The expanded fields keep the endpoint pleasant for the
    # Web client and hand-written requests.
    plan: dict[str, Any] | None = None
    prompt: str = Field(default="", max_length=20_000)
    scene_kind: str = Field(default="indoor", max_length=24)
    seed: int = 0
    bounds: dict[str, Any] | None = None
    objects: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    relations: list[dict[str, Any]] = Field(default_factory=list, max_length=512)
    solve: bool = True
    resolve_assets: bool = False


class SceneComposeRequest(BaseModel):
    """Options for composing the resolved scene objects into one GLB."""

    collection: str = Field(default="Scenes", max_length=160)
    output_name: str = Field(default="scene", min_length=1, max_length=120)
    allow_missing: bool = False


def _scene_asset_path(world: dict[str, Any], object_id: str, object_data: dict[str, Any]) -> str | None:
    asset = object_data.get("asset")
    if isinstance(asset, dict):
        value = asset.get("workspacePath") or asset.get("workspace_path")
        if isinstance(value, str) and value.strip():
            return value.strip()
    artifacts = world.get("artifacts")
    if isinstance(artifacts, dict):
        entry = artifacts.get(object_id)
        mesh = entry.get("mesh") if isinstance(entry, dict) else None
        if isinstance(mesh, dict):
            value = mesh.get("workspace_path") or mesh.get("workspacePath")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _build_scene_composition_workflow(
    world: dict[str, Any],
    *,
    world_id: str,
    collection: str,
    output_name: str,
    allow_missing: bool,
) -> WorkflowExecutionRequest:
    """Compile a saved ScenePlan into the canonical multi-mesh workflow."""

    from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest

    plan = world.get("scene_plan")
    if not isinstance(plan, dict):
        spec = world.get("spec")
        plan = spec.get("scene_plan") if isinstance(spec, dict) else None
    if not isinstance(plan, dict):
        raise ScenePlanError("World has no compiled scene_plan")

    raw_objects = plan.get("objects")
    raw_instances = plan.get("instances")
    if not isinstance(raw_objects, list) or not raw_objects:
        raise ScenePlanError("Scene plan has no objects to compose")
    objects = {
        item.get("id"): item
        for item in raw_objects
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    instances = {
        item.get("objectId") or item.get("object_id"): item
        for item in (raw_instances if isinstance(raw_instances, list) else [])
        if isinstance(item, dict) and isinstance(item.get("objectId") or item.get("object_id"), str)
    }

    nodes: dict[str, WorkflowExecutionNode] = {}
    mesh_refs: list[list[str]] = []
    placements: list[dict[str, Any]] = []
    missing: list[str] = []
    for object_id, object_data in objects.items():
        workspace_path = _scene_asset_path(world, object_id, object_data)
        if not workspace_path:
            # EmbodiedGen omits the visual background by default.  A room or
            # background node is still useful for planning/preview bounds, but
            # it must not make an otherwise complete prop composition fail.
            if str(object_data.get("role") or "").lower() in {"room", "background"}:
                continue
            missing.append(object_id)
            continue
        try:
            resolved = resolve_workspace_path(runtime_paths.workspace, workspace_path)
        except ValueError as exc:
            raise ScenePlanError(f"Invalid asset path for '{object_id}': {exc}") from exc
        if not resolved.is_file():
            missing.append(object_id)
            continue
        node_id = f"asset_{object_id}"
        nodes[node_id] = WorkflowExecutionNode(
            class_type="polykit.mesh",
            inputs={"mesh": {"kind": "workspace_path", "path": workspace_path}},
        )
        mesh_refs.append([node_id, "mesh"])
        instance = instances.get(object_id) or {}
        placements.append({
            "position": instance.get("position", [0, 0, 0]),
            "rotation": instance.get("rotation", [0, 0, 0]),
            "scale": instance.get("scale", 1),
            "size": object_data.get("size", [1, 1, 1]),
        })

    if missing and not allow_missing:
        raise ScenePlanError(
            "Scene objects are missing mesh assets: " + ", ".join(sorted(missing))
        )
    if not mesh_refs:
        raise ScenePlanError("Scene plan has no resolved mesh assets to compose")

    nodes["compose"] = WorkflowExecutionNode(
        class_type="scene-composer/compose",
        inputs={
            "mesh": mesh_refs,
            "params": {
                "output_name": output_name,
                "placements": placements,
            },
        },
    )
    nodes["output"] = WorkflowExecutionNode(
        class_type="polykit.output",
        inputs={"mesh": ["compose", "mesh"]},
    )
    return WorkflowExecutionRequest(
        prompt=nodes,
        output_node_id="output",
        collection=normalize_collection(collection),
        metadata={
            "world_id": world_id,
            "artifact_kind": "scene",
            "missing_objects": missing,
            "composition": "scene-composer",
        },
    )


@router.post("/{world_id}/scene-plan")
async def compile_world_scene_plan(world_id: str, request: ScenePlanCompileRequest):
    """Compile a semantic scene plan and persist its deterministic instances."""

    try:
        current = get_world(world_id)
        if current is None:
            raise HTTPException(status_code=404, detail="World was not found")
        payload = dict(request.plan) if isinstance(request.plan, dict) else {
            "prompt": request.prompt,
            "scene_kind": request.scene_kind,
            "seed": request.seed,
            "bounds": request.bounds or {},
            "objects": request.objects,
            "relations": request.relations,
        }
        compiled = compile_scene_plan(
            payload,
            scene_id=world_id,
            solve=request.solve,
            resolve_assets=request.resolve_assets,
        )
        updated = apply_scene_plan_to_world(current, compiled)
        saved = save_world(world_id, updated)
        return {
            "world_id": world_id,
            "scene_plan": compiled,
            "world": saved,
        }
    except HTTPException:
        raise
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (ScenePlanError, WorldStoreError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not compile scene plan: {exc}") from exc


@router.post("/{world_id}/compose")
async def compose_world_scene(
    world_id: str,
    request: SceneComposeRequest,
    background_tasks: BackgroundTasks,
):
    """Submit a saved ScenePlan to the canonical multi-mesh composition workflow."""

    try:
        world = get_world(world_id)
        if world is None:
            raise HTTPException(status_code=404, detail="World was not found")
        workflow = _build_scene_composition_workflow(
            world,
            world_id=world_id,
            collection=request.collection,
            output_name=request.output_name,
            allow_missing=request.allow_missing,
        )
        # Keep execution ownership in workflow-runs.  This route only compiles
        # the world document into a typed DAG for the existing server runtime.
        from routers.workflow_runs import execute_workflow

        return await execute_workflow(workflow, background_tasks)
    except HTTPException:
        raise
    except (ScenePlanError, WorldStoreError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not compose scene: {exc}") from exc


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

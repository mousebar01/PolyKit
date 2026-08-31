#!/usr/bin/env python3
"""Thin MCP adapter for PolyKit's canonical FastAPI control plane.

This server is intentionally stateless. It does not own workflow progression,
World mutation rules, validation logic, artifacts, or model execution. Every
MCP tool delegates to an existing PolyKit HTTP API.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

API_BASE = os.environ.get("POLYKIT_API_URL", "http://127.0.0.1:8765").rstrip("/")
DEFAULT_TIMEOUT = 60.0

WORLD_VALIDATORS = (
    "world.spec.validate",
    "world.blockout.validate",
    "world.construction.validate",
    "world.gameplay.validate",
    "world.final.validate",
)


def _object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


async def list_tools() -> list[Tool]:
    """Return the stable MCP surface exposed to external clients."""

    return [
        Tool(
            name="polykit_health",
            description="Check whether the PolyKit FastAPI server is reachable and healthy.",
            inputSchema=_object_schema({}),
        ),
        Tool(
            name="polykit_list_models",
            description="List PolyKit model/node-pack runtime entries. Optionally return only downloaded entries.",
            inputSchema=_object_schema({
                "downloaded_only": {"type": "boolean", "description": "Only include downloaded entries. Default false."},
            }),
        ),
        Tool(
            name="polykit_workflow_list",
            description="List saved editable PolyKit workflow definitions.",
            inputSchema=_object_schema({}),
        ),
        Tool(
            name="polykit_workflow_status",
            description="Read the current status of one canonical WorkflowRun.",
            inputSchema=_object_schema({"run_id": _string("WorkflowRun id.")}, ["run_id"]),
        ),
        Tool(
            name="polykit_workflow_inspect",
            description=(
                "Read the persisted node/event/artifact/evidence timeline for one WorkflowRun. "
                "This tool is strictly read-only: it never advances, retries, resumes, or mutates a run."
            ),
            inputSchema=_object_schema({"run_id": _string("WorkflowRun id.")}, ["run_id"]),
        ),
        Tool(
            name="polykit_workflow_cancel",
            description="Cancel one canonical WorkflowRun.",
            inputSchema=_object_schema({"run_id": _string("WorkflowRun id.")}, ["run_id"]),
        ),
        Tool(
            name="polykit_workflow_execute",
            description="Submit a complete WorkflowExecutionRequest JSON object to PolyKit's canonical Workflow Engine.",
            inputSchema=_object_schema({
                "request": {"type": "object", "description": "Canonical WorkflowExecutionRequest payload."},
            }, ["request"]),
        ),
        Tool(
            name="polykit_asset_from_text",
            description="Start PolyKit's canonical local text-to-asset workflow and return a WorkflowRun id.",
            inputSchema=_object_schema({
                "prompt": _string("Single-object asset description."),
                "image_model_id": _string("Text-to-image node id. Default: anima/generate."),
                "mesh_model_id": _string("Image-to-3D node id. Default: trellis2/generate."),
                "enable_texture": {"type": "boolean", "description": "Run texture refinement. Default true."},
                "enable_optimize": {"type": "boolean", "description": "Optimize the final mesh. Default true."},
                "target_faces": {"type": "integer", "minimum": 100, "description": "Target face budget. Default 100000."},
                "collection": _string("Workspace collection. Default: Workflows."),
                "workflow_id": _string("Optional workflow provenance id."),
                "world_id": _string("Optional World id for provenance."),
                "proto_id": _string("Optional semantic object id for provenance."),
                "image_params": {"type": "object", "description": "Optional image model params."},
                "mesh_params": {"type": "object", "description": "Optional mesh model params."},
                "texture_params": {"type": "object", "description": "Optional texture params."},
            }, ["prompt"]),
        ),
        Tool(
            name="polykit_mesh_decimate",
            description="Create a decimated workspace mesh through PolyKit's mesh optimization API.",
            inputSchema=_object_schema({
                "path": _string("Workspace-relative mesh path."),
                "target_faces": {"type": "integer", "minimum": 100},
            }, ["path", "target_faces"]),
        ),
        Tool(
            name="polykit_mesh_smooth",
            description="Create a smoothed workspace mesh through PolyKit's mesh optimization API.",
            inputSchema=_object_schema({
                "path": _string("Workspace-relative mesh path."),
                "iterations": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Default 1."},
            }, ["path"]),
        ),
        Tool(
            name="polykit_world_create",
            description="Create a new schema-v2 PolyKit World document. Returns its world_id and document.",
            inputSchema=_object_schema({
                "name": _string("Optional display name."),
                "prompt": _string("Optional world intent prompt."),
                "parent_world_id": _string("Optional parent World id."),
            }),
        ),
        Tool(
            name="polykit_world_get",
            description="Read one saved schema-v2 PolyKit World document.",
            inputSchema=_object_schema({"world_id": _string("World id.")}, ["world_id"]),
        ),
        Tool(
            name="polykit_world_save",
            description="Persist a complete schema-v2 World document through the canonical World API.",
            inputSchema=_object_schema({
                "world_id": _string("World id; must match the document id."),
                "document": {"type": "object", "description": "Complete schema-v2 World document."},
            }, ["world_id", "document"]),
        ),
        Tool(
            name="polykit_world_compile_scene",
            description="Compile and persist a semantic ScenePlan for a World using PolyKit's deterministic scene planner.",
            inputSchema=_object_schema({
                "world_id": _string("World id."),
                "plan": {"type": "object", "description": "Semantic ScenePlan input."},
                "solve": {"type": "boolean", "description": "Run deterministic layout solve. Default true."},
                "resolve_assets": {"type": "boolean", "description": "Resolve matching workspace assets. Default false."},
            }, ["world_id", "plan"]),
        ),
        Tool(
            name="polykit_world_find_assets",
            description="Search the PolyKit workspace for mesh assets matching a semantic query.",
            inputSchema=_object_schema({
                "query": _string("Semantic asset query."),
                "category": _string("Optional asset category."),
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Default 5."},
            }, ["query"]),
        ),
        Tool(
            name="polykit_world_build_structure",
            description="Compile a World BuildSpec building into a canonical Blender-backed WorkflowRun.",
            inputSchema=_object_schema({
                "world_id": _string("World id."),
                "building_id": _string("Optional building id. Uses the first building when omitted."),
                "collection": _string("Workspace collection. Default: Scenes."),
                "render_preview": {"type": "boolean", "description": "Render inspection previews. Default true."},
            }, ["world_id"]),
        ),
        Tool(
            name="polykit_world_validate",
            description=(
                "Run one deterministic World validator and return quality facts/evidence. "
                "Validation does not advance any task or workflow state."
            ),
            inputSchema=_object_schema({
                "world_id": _string("World id."),
                "capability": {"type": "string", "enum": list(WORLD_VALIDATORS)},
                "run_id": _string("Optional WorkflowRun id used as construction evidence."),
            }, ["world_id", "capability"]),
        ),
        Tool(
            name="polykit_world_compose",
            description="Start a canonical WorkflowRun that composes resolved World scene meshes into one scene artifact.",
            inputSchema=_object_schema({
                "world_id": _string("World id."),
                "collection": _string("Workspace collection. Default: Scenes."),
                "output_name": _string("Published scene name. Default: scene."),
                "allow_missing": {"type": "boolean", "description": "Allow unresolved non-room objects. Default false."},
            }, ["world_id"]),
        ),
        Tool(
            name="polykit_world_attach_asset",
            description="Bind an existing workspace mesh artifact to a stable semantic object in a saved World document.",
            inputSchema=_object_schema({
                "world_id": _string("World id."),
                "proto_id": _string("Semantic object/prototype id."),
                "workspace_path": _string("Workspace-relative mesh path."),
                "workflow_id": _string("Optional workflow provenance id."),
                "run_id": _string("Optional WorkflowRun provenance id."),
                "concept_image": _string("Optional workspace-relative concept image."),
            }, ["world_id", "proto_id", "workspace_path"]),
        ),
    ]


def _required_text(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _id_path(value: str) -> str:
    return quote(value.strip(), safe="")


async def _request_json(method: str, path: str, payload: Any | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE, timeout=timeout) as client:
        response = await client.request(method, path, json=payload)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "polykit_health":
        return await _request_json("GET", "/health")

    if name == "polykit_list_models":
        models = await _request_json("GET", "/model/all")
        if args.get("downloaded_only") and isinstance(models, list):
            return [item for item in models if isinstance(item, dict) and item.get("downloaded")]
        return models

    if name == "polykit_workflow_list":
        return await _request_json("GET", "/workflow-definitions")

    if name == "polykit_workflow_status":
        run_id = _id_path(_required_text(args, "run_id"))
        return await _request_json("GET", f"/workflow-runs/{run_id}")

    if name == "polykit_workflow_inspect":
        run_id = _id_path(_required_text(args, "run_id"))
        return await _request_json("GET", f"/workflow-runs/{run_id}/inspect")

    if name == "polykit_workflow_cancel":
        run_id = _id_path(_required_text(args, "run_id"))
        return await _request_json("POST", f"/workflow-runs/{run_id}/cancel")

    if name == "polykit_workflow_execute":
        request = args.get("request")
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        return await _request_json("POST", "/workflow-runs/execute", request)

    if name == "polykit_asset_from_text":
        payload = {
            "prompt": _required_text(args, "prompt"),
            "image_model_id": args.get("image_model_id") or "anima/generate",
            "mesh_model_id": args.get("mesh_model_id") or "trellis2/generate",
            "enable_texture": args.get("enable_texture", True),
            "enable_optimize": args.get("enable_optimize", True),
            "target_faces": int(args.get("target_faces", 100000)),
            "collection": args.get("collection") or "Workflows",
            "workflow_id": args.get("workflow_id") or None,
            "world_id": args.get("world_id") or None,
            "proto_id": args.get("proto_id") or None,
            "image_params": args.get("image_params") or {},
            "mesh_params": args.get("mesh_params") or {},
            "texture_params": args.get("texture_params") or {},
        }
        return await _request_json("POST", "/workflow-runs/text-to-asset", payload)

    if name == "polykit_mesh_decimate":
        return await _request_json("POST", "/optimize/mesh", {
            "path": _required_text(args, "path"),
            "target_faces": int(args["target_faces"]),
        })

    if name == "polykit_mesh_smooth":
        return await _request_json("POST", "/optimize/smooth", {
            "path": _required_text(args, "path"),
            "iterations": int(args.get("iterations", 1)),
        })

    if name == "polykit_world_create":
        payload = {
            key: value.strip()
            for key in ("name", "prompt", "parent_world_id")
            if isinstance((value := args.get(key)), str) and value.strip()
        }
        return await _request_json("POST", "/workspace-library/worlds", payload)

    if name == "polykit_world_get":
        world_id = _id_path(_required_text(args, "world_id"))
        return await _request_json("GET", f"/workspace-library/worlds/{world_id}")

    if name == "polykit_world_save":
        world_id_raw = _required_text(args, "world_id")
        document = args.get("document")
        if not isinstance(document, dict):
            raise ValueError("document must be a JSON object")
        if document.get("id") != world_id_raw:
            raise ValueError("document.id must exactly match world_id")
        return await _request_json("PUT", f"/workspace-library/worlds/{_id_path(world_id_raw)}", document)

    if name == "polykit_world_compile_scene":
        world_id = _id_path(_required_text(args, "world_id"))
        plan = args.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("plan must be a JSON object")
        return await _request_json("POST", f"/workspace-library/worlds/{world_id}/scene-plan", {
            "plan": plan,
            "solve": args.get("solve", True),
            "resolve_assets": args.get("resolve_assets", False),
        })

    if name == "polykit_world_find_assets":
        return await _request_json("POST", "/workspace-library/search", {
            "query": _required_text(args, "query"),
            "category": args.get("category") or None,
            "limit": max(1, min(int(args.get("limit", 5)), 50)),
            "meshesOnly": True,
        })

    if name == "polykit_world_build_structure":
        world_id = _id_path(_required_text(args, "world_id"))
        return await _request_json("POST", f"/workspace-library/worlds/{world_id}/build-structure", {
            "building_id": args.get("building_id") or None,
            "collection": args.get("collection") or "Scenes",
            "render_preview": args.get("render_preview", True),
        })

    if name == "polykit_world_validate":
        capability = _required_text(args, "capability")
        if capability not in WORLD_VALIDATORS:
            raise ValueError(f"Unsupported validator capability: {capability}")
        world_id = _id_path(_required_text(args, "world_id"))
        return await _request_json("POST", f"/workspace-library/worlds/{world_id}/validate", {
            "capability": capability,
            "run_id": args.get("run_id") or None,
        })

    if name == "polykit_world_compose":
        world_id = _id_path(_required_text(args, "world_id"))
        return await _request_json("POST", f"/workspace-library/worlds/{world_id}/compose", {
            "collection": args.get("collection") or "Scenes",
            "output_name": args.get("output_name") or "scene",
            "allow_missing": args.get("allow_missing", False),
        })

    if name == "polykit_world_attach_asset":
        world_id = _id_path(_required_text(args, "world_id"))
        proto_id = _id_path(_required_text(args, "proto_id"))
        return await _request_json("POST", f"/workspace-library/worlds/{world_id}/artifacts/{proto_id}", {
            "workspace_path": _required_text(args, "workspace_path"),
            "workflow_id": args.get("workflow_id") or None,
            "run_id": args.get("run_id") or None,
            "concept_image": args.get("concept_image") or None,
        })

    raise ValueError(f"Unknown MCP tool: {name}")


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Execute one MCP tool and always return machine-readable JSON text."""

    try:
        value = await _dispatch(name, arguments or {})
        payload = {"ok": True, "result": value}
    except httpx.ConnectError:
        payload = {
            "ok": False,
            "error": {
                "type": "connection",
                "message": f"Cannot connect to PolyKit API at {API_BASE}. Start FastAPI before using this MCP server.",
            },
        }
    except httpx.HTTPStatusError as exc:
        payload = {
            "ok": False,
            "error": {
                "type": "http",
                "status": exc.response.status_code,
                "message": exc.response.text[:2000],
            },
        }
    except Exception as exc:
        payload = {"ok": False, "error": {"type": "adapter", "message": str(exc)}}
    return [TextContent(type="text", text=_json_text(payload))]


async def _on_list_tools(_context: object, _params: object) -> ListToolsResult:
    return ListToolsResult(tools=await list_tools())


async def _on_call_tool(_context: object, params: object) -> CallToolResult:
    name = getattr(params, "name", "")
    arguments = getattr(params, "arguments", None) or {}
    content = await call_tool(name, arguments)
    failed = False
    if content:
        try:
            failed = not bool(json.loads(content[0].text).get("ok"))
        except Exception:
            failed = False
    return CallToolResult(content=content, isError=failed)


def _build_server() -> Server:
    """Create a server compatible with both current and older MCP Python SDKs."""

    try:
        return Server("polykit", on_list_tools=_on_list_tools, on_call_tool=_on_call_tool)
    except TypeError:
        legacy = Server("polykit")
        list_tools_decorator = getattr(legacy, "list_tools", None)
        call_tool_decorator = getattr(legacy, "call_tool", None)
        if not callable(list_tools_decorator) or not callable(call_tool_decorator):
            raise
        list_tools_decorator()(list_tools)
        call_tool_decorator()(call_tool)
        return legacy


server = _build_server()


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

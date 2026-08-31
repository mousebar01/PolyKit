"""
PolyKit MCP Server.

The world tools expose the strict schema-v2 domain runtime. Agents create and
edit world data, while durable task/stage progress lives in Agent Workflow
sessions rather than inside WorldDocument.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import Mapping
from pathlib import Path

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from services.runtime_paths import runtime_paths
from services.world_agent import attach_world_artifact
from services.world_store import WorldStoreError, validate_world_id

API_BASE = "http://localhost:8765"
server: Server | None = None


def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> Tool:
    schema: dict = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return Tool(name=name, description=description, inputSchema=schema)


async def list_tools() -> list[Tool]:
    return [
        _tool("polykit_list_models", "List downloaded 3D generation models."),
        _tool(
            "polykit_switch_model",
            "Switch the active 3D generation model.",
            {"model_id": {"type": "string"}},
            ["model_id"],
        ),
        _tool(
            "polykit_generate_from_image",
            "Generate a 3D mesh from a local image and return a workflow run id.",
            {
                "image_path": {"type": "string"},
                "model_id": {"type": "string"},
                "remesh": {"type": "string", "enum": ["quad", "triangle", "none"]},
                "collection": {"type": "string"},
                "enable_texture": {"type": "boolean"},
                "texture_resolution": {"type": "integer"},
                "params": {"type": "object"},
                "workflow_id": {"type": "string"},
            },
            ["image_path"],
        ),
        _tool(
            "polykit_remove_background",
            "Remove the background from a workspace image through the installed local process node.",
            {
                "image_path": {"type": "string"},
                "model": {"type": "string"},
                "collection": {"type": "string"},
                "workflow_id": {"type": "string"},
            },
            ["image_path"],
        ),
        _tool(
            "polykit_generate_image",
            "Generate a local illustration from text and return a workflow run id.",
            {
                "prompt": {"type": "string"},
                "model_id": {"type": "string"},
                "params": {"type": "object"},
                "collection": {"type": "string"},
                "workflow_id": {"type": "string"},
            },
            ["prompt"],
        ),
        _tool(
            "polykit_generate_text_asset",
            "Run the canonical text-to-image-to-3D asset workflow.",
            {
                "prompt": {"type": "string"},
                "image_model_id": {"type": "string"},
                "mesh_model_id": {"type": "string"},
                "enable_texture": {"type": "boolean"},
                "enable_optimize": {"type": "boolean"},
                "target_faces": {"type": "integer"},
                "collection": {"type": "string"},
                "workflow_id": {"type": "string"},
                "world_id": {"type": "string"},
                "proto_id": {"type": "string"},
                "image_params": {"type": "object"},
                "mesh_params": {"type": "object"},
                "texture_params": {"type": "object"},
            },
            ["prompt"],
        ),
        _tool(
            "polykit_get_generation_status",
            "Read a workflow run until it reaches done, cancelled, or error.",
            {"job_id": {"type": "string"}},
            ["job_id"],
        ),
        _tool(
            "polykit_decimate_mesh",
            "Reduce mesh polygon count.",
            {"path": {"type": "string"}, "target_faces": {"type": "integer"}},
            ["path", "target_faces"],
        ),
        _tool(
            "polykit_smooth_mesh",
            "Apply Laplacian smoothing to a workspace mesh.",
            {"path": {"type": "string"}, "iterations": {"type": "integer"}},
            ["path", "iterations"],
        ),
        _tool(
            "polykit_import_mesh",
            "Import a mesh file from disk into the PolyKit workspace.",
            {"path": {"type": "string"}},
            ["path"],
        ),
        _tool("polykit_unload_models", "Unload all generation models from accelerator memory."),
        _tool("polykit_get_settings", "Read configured model and workspace paths."),
        _tool(
            "polykit_world_create",
            "Create a fresh schema-v2 world domain document. Workflow progress is managed separately by Agent Workflow sessions.",
            {
                "name": {"type": "string"},
                "prompt": {"type": "string"},
                "parent_world_id": {"type": "string"},
            },
        ),
        _tool(
            "polykit_world_get",
            "Read the current strict world document before editing runtime build, scene, game, quality, or artifacts.",
            {"world_id": {"type": "string"}},
            ["world_id"],
        ),
        _tool(
            "polykit_world_save",
            "Save a complete schema-v2 world document. The document must keep its id and one runtime envelope; workflow stage state and old top-level mirrors are invalid.",
            {"world_id": {"type": "string"}, "document": {"type": "object"}},
            ["world_id", "document"],
        ),
        _tool(
            "polykit_world_compile_scene",
            "Compile an Agent-authored semantic ScenePlan and persist the result at runtime.scene. Use stable ids, measured sizes, explicit support/spatial relations, and renderer-independent transforms.",
            {
                "world_id": {"type": "string"},
                "plan": {"type": "object"},
                "solve": {"type": "boolean"},
                "resolve_assets": {"type": "boolean"},
            },
            ["world_id", "plan"],
        ),
        _tool(
            "polykit_world_find_assets",
            "Find reusable workspace assets by semantic name, aliases, and category.",
            {
                "query": {"type": "string"},
                "category": {"type": "string"},
                "limit": {"type": "integer"},
            },
            ["query"],
        ),
        _tool(
            "polykit_world_compose_scene",
            "Compose resolved runtime.scene mesh assets into one GLB through the canonical workflow runtime.",
            {
                "world_id": {"type": "string"},
                "collection": {"type": "string"},
                "output_name": {"type": "string"},
                "allow_missing": {"type": "boolean"},
            },
            ["world_id"],
        ),
        _tool("polykit_world_list_workflows", "List editable local workflows usable by world build steps."),
        _tool(
            "polykit_world_generate_asset",
            "Generate one planned world asset with the local image-to-3D workflow.",
            {
                "world_id": {"type": "string"},
                "proto_id": {"type": "string"},
                "image_path": {"type": "string"},
                "model_id": {"type": "string"},
                "workflow_id": {"type": "string"},
                "collection": {"type": "string"},
                "remesh": {"type": "string", "enum": ["quad", "triangle", "none"]},
                "enable_texture": {"type": "boolean"},
                "texture_resolution": {"type": "integer"},
                "params": {"type": "object"},
            },
            ["world_id", "proto_id", "image_path"],
        ),
        _tool(
            "polykit_world_attach_asset",
            "Attach a completed workspace mesh to a stable runtime object/prototype id.",
            {
                "world_id": {"type": "string"},
                "proto_id": {"type": "string"},
                "workspace_path": {"type": "string"},
                "workflow_id": {"type": "string"},
                "run_id": {"type": "string"},
                "concept_image": {"type": "string"},
            },
            ["world_id", "proto_id", "workspace_path"],
        ),
    ]


async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            result = await _dispatch(client, name, arguments)
        except httpx.ConnectError:
            result = "Cannot connect to PolyKit API at http://localhost:8765. Make sure PolyKit is running."
        except httpx.HTTPStatusError as exc:
            result = f"PolyKit API error {exc.response.status_code}: {exc.response.text[:300]}"
        except Exception as exc:
            result = f"Error: {exc}"
    return [TextContent(type="text", text=result)]


async def _dispatch(client: httpx.AsyncClient, name: str, args: dict) -> str:
    if name == "polykit_list_models":
        response = await client.get(f"{API_BASE}/model/all")
        response.raise_for_status()
        models = [model for model in response.json() if model.get("downloaded")]
        if not models:
            return "No models downloaded yet. Download one from the Models tab in PolyKit."
        return "\n".join(f"- {model['id']}: {model.get('name', model['id'])}" for model in models)

    if name == "polykit_switch_model":
        response = await client.post(f"{API_BASE}/model/switch", params={"model_id": args["model_id"]})
        response.raise_for_status()
        return f"Switched active model to: {args['model_id']}"

    if name == "polykit_generate_from_image":
        image_path = _required_text(args.get("image_path"), "image_path")
        with open(image_path, "rb") as image_file:
            image_bytes = image_file.read()
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        filename = image_path.replace("\\", "/").split("/")[-1]
        form_data = {
            "remesh": args.get("remesh", "quad"),
            "collection": args.get("collection", "Workflows"),
            "enable_texture": str(_as_bool(args.get("enable_texture", False))).lower(),
            "texture_resolution": str(int(args.get("texture_resolution", 1024))),
        }
        if args.get("model_id"):
            form_data["model_id"] = args["model_id"]
        if args.get("workflow_id"):
            form_data["workflow_id"] = args["workflow_id"]
        params = args.get("params")
        if isinstance(params, Mapping):
            form_data["params"] = json.dumps(dict(params), ensure_ascii=False)
        response = await client.post(
            f"{API_BASE}/workflow-runs/from-image",
            files={"image": (filename, image_bytes, mime)},
            data=form_data,
            timeout=30.0,
        )
        response.raise_for_status()
        run_id = response.json()["run_id"]
        return f"Generation started. Run ID: {run_id}\nUse polykit_get_generation_status with this ID to track progress."

    if name == "polykit_remove_background":
        image_ref = _workspace_image_reference(args.get("image_path"))
        params = {"model": args.get("model", "isnet-anime")}
        payload = {
            "schema_version": 1,
            "workflow_id": str(args.get("workflow_id") or "").strip() or None,
            "prompt": {
                "image": {"class_type": "polykit.image", "inputs": {"image": image_ref}},
                "cutout": {
                    "class_type": "image-background-remover/remove-background",
                    "inputs": {"image": ["image", "image"], "params": params},
                },
                "output": {"class_type": "polykit.image_output", "inputs": {"image": ["cutout", "image"]}},
            },
            "output_node_id": "output",
            "collection": str(args.get("collection") or "Workflows"),
        }
        response = await client.post(f"{API_BASE}/workflow-runs/execute", json=payload, timeout=30.0)
        response.raise_for_status()
        run_id = response.json()["run_id"]
        return f"Background removal started with {params['model']}. Run ID: {run_id}\nUse polykit_get_generation_status with this ID to track progress."

    if name == "polykit_generate_image":
        prompt = _required_text(args.get("prompt"), "prompt")
        model_id = _required_text(args.get("model_id") or "anima/generate", "model_id")
        params = args.get("params") if isinstance(args.get("params"), Mapping) else {}
        payload = {
            "schema_version": 1,
            "workflow_id": str(args.get("workflow_id") or "").strip() or None,
            "prompt": {
                "text": {"class_type": "polykit.text", "inputs": {"text": prompt}},
                "image": {"class_type": model_id, "inputs": {"text": ["text", "text"], "params": dict(params)}},
                "output": {"class_type": "polykit.image_output", "inputs": {"image": ["image", "image"]}},
            },
            "output_node_id": "output",
            "collection": str(args.get("collection") or "Workflows"),
        }
        response = await client.post(f"{API_BASE}/workflow-runs/execute", json=payload, timeout=30.0)
        response.raise_for_status()
        run_id = response.json()["run_id"]
        return f"Illustration generation started with {model_id}. Run ID: {run_id}\nUse polykit_get_generation_status with this ID to track progress."

    if name == "polykit_generate_text_asset":
        prompt = _required_text(args.get("prompt"), "prompt")
        payload = {
            "prompt": prompt,
            "image_model_id": str(args.get("image_model_id") or "anima/generate"),
            "mesh_model_id": str(args.get("mesh_model_id") or "trellis2/generate"),
            "enable_texture": _as_bool(args.get("enable_texture", True)),
            "enable_optimize": _as_bool(args.get("enable_optimize", True)),
            "target_faces": max(100, min(int(args.get("target_faces", 100000)), 1000000)),
            "collection": str(args.get("collection") or "Workflows"),
            "workflow_id": str(args.get("workflow_id") or "").strip() or None,
            "world_id": str(args.get("world_id") or "").strip() or None,
            "proto_id": str(args.get("proto_id") or "").strip() or None,
            "image_params": dict(args.get("image_params")) if isinstance(args.get("image_params"), Mapping) else {},
            "mesh_params": dict(args.get("mesh_params")) if isinstance(args.get("mesh_params"), Mapping) else {},
            "texture_params": dict(args.get("texture_params")) if isinstance(args.get("texture_params"), Mapping) else {},
        }
        response = await client.post(f"{API_BASE}/workflow-runs/text-to-asset", json=payload, timeout=30.0)
        response.raise_for_status()
        run_id = response.json()["run_id"]
        return f"Text-to-3D asset workflow started. Run ID: {run_id}\nUse polykit_get_generation_status with this ID to track progress."

    if name == "polykit_get_generation_status":
        run_id = _required_text(args.get("job_id"), "job_id")
        response = await client.get(f"{API_BASE}/workflow-runs/{run_id}")
        response.raise_for_status()
        status = response.json()
        parts = [f"Status: {status['status']}", f"Progress: {status.get('progress', 0)}%"]
        if status.get("step"):
            parts.append(f"Step: {status['step']}")
        if status.get("output_url"):
            parts.append(f"Output: {status['output_url']}")
        candidate = status.get("scene_candidate")
        if isinstance(candidate, Mapping) and candidate.get("workspace_path"):
            parts.append(f"Workspace path: {candidate['workspace_path']}")
        if status.get("error"):
            parts.append(f"Error: {status['error']}")
        return " | ".join(parts)

    if name == "polykit_decimate_mesh":
        response = await client.post(f"{API_BASE}/optimize/mesh", json={"path": args["path"], "target_faces": args["target_faces"]})
        response.raise_for_status()
        data = response.json()
        return f"Decimated mesh to {data.get('face_count', '?')} faces. New file: {data.get('url', '')}"

    if name == "polykit_smooth_mesh":
        response = await client.post(f"{API_BASE}/optimize/smooth", json={"path": args["path"], "iterations": args["iterations"]})
        response.raise_for_status()
        data = response.json()
        return f"Smoothed mesh ({args['iterations']} iterations). New file: {data.get('url', '')}"

    if name == "polykit_import_mesh":
        response = await client.post(f"{API_BASE}/optimize/import-by-path", json={"path": args["path"]})
        response.raise_for_status()
        data = response.json()
        return f"Mesh imported. URL: {data.get('url', '')}"

    if name == "polykit_unload_models":
        response = await client.post(f"{API_BASE}/model/unload-all")
        response.raise_for_status()
        return "All 3D generation models unloaded from VRAM."

    if name == "polykit_get_settings":
        response = await client.get(f"{API_BASE}/settings/paths")
        response.raise_for_status()
        data = response.json()
        return f"Models directory: {data.get('models_dir')}\nWorkspace directory: {data.get('workspace_dir')}"

    if name == "polykit_world_get":
        world_id = _safe_world_id(args.get("world_id"))
        response = await client.get(f"{API_BASE}/workspace-library/worlds/{world_id}")
        response.raise_for_status()
        return _json_text(response.json())

    if name == "polykit_world_create":
        payload = {
            key: value
            for key, value in {
                "name": args.get("name"),
                "prompt": args.get("prompt"),
                "parent_world_id": args.get("parent_world_id"),
            }.items()
            if isinstance(value, str) and value.strip()
        }
        response = await client.post(f"{API_BASE}/workspace-library/worlds", json=payload)
        response.raise_for_status()
        data = response.json()
        return (
            f"New scene created: {data.get('world_id', '?')}\n"
            f"{_json_text(data.get('world', data))}\n"
            "Use this world_id for domain edits and asset attachments."
        )

    if name == "polykit_world_save":
        world_id = _safe_world_id(args.get("world_id"))
        document = args.get("document")
        if not isinstance(document, Mapping):
            raise WorldStoreError("document must be a JSON object")
        payload = dict(document)
        if payload.get("id") != world_id:
            raise WorldStoreError("document.id must exactly match world_id")
        if payload.get("schema_version") != 2 or not isinstance(payload.get("runtime"), Mapping):
            raise WorldStoreError("document must be a schema-v2 world with one runtime object")
        response = await client.put(f"{API_BASE}/workspace-library/worlds/{world_id}", json=payload)
        response.raise_for_status()
        return f"World '{world_id}' saved: {_json_text(response.json())}"

    if name == "polykit_world_compile_scene":
        world_id = _safe_world_id(args.get("world_id"))
        plan = args.get("plan")
        if not isinstance(plan, Mapping):
            raise WorldStoreError("plan must be a JSON object")
        payload = {
            "plan": dict(plan),
            "solve": _as_bool(args.get("solve", True)),
            "resolve_assets": _as_bool(args.get("resolve_assets", False)),
        }
        response = await client.post(f"{API_BASE}/workspace-library/worlds/{world_id}/scene-plan", json=payload)
        response.raise_for_status()
        data = response.json()
        scene = data.get("scene", {}) if isinstance(data, Mapping) else {}
        count = len(scene.get("instances", [])) if isinstance(scene, Mapping) else 0
        return f"World '{world_id}' scene plan compiled with {count} instance(s): {_json_text(data)}"

    if name == "polykit_world_find_assets":
        query = _required_text(args.get("query"), "query")
        payload = {
            "query": query,
            "category": str(args.get("category") or "").strip() or None,
            "limit": max(1, min(int(args.get("limit", 5)), 50)),
            "meshesOnly": True,
        }
        response = await client.post(f"{API_BASE}/workspace-library/search", json=payload)
        response.raise_for_status()
        data = response.json()
        matches = data.get("matches", []) if isinstance(data, Mapping) else []
        return _json_text(matches) if matches else "No high-confidence workspace asset matches found."

    if name == "polykit_world_compose_scene":
        world_id = _safe_world_id(args.get("world_id"))
        payload = {
            "collection": str(args.get("collection") or "Scenes"),
            "output_name": str(args.get("output_name") or "scene"),
            "allow_missing": _as_bool(args.get("allow_missing", False)),
        }
        response = await client.post(f"{API_BASE}/workspace-library/worlds/{world_id}/compose", json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        run_id = data.get("run_id", "?") if isinstance(data, Mapping) else "?"
        return f"Scene composition started for world '{world_id}'. Run ID: {run_id}\nUse polykit_get_generation_status with this ID to track the GLB output."

    if name == "polykit_world_list_workflows":
        response = await client.get(f"{API_BASE}/workflow-definitions")
        response.raise_for_status()
        workflows = response.json()
        if not workflows:
            return "No editable local workflows are saved yet."
        lines = []
        for workflow in workflows:
            if not isinstance(workflow, Mapping):
                continue
            workflow_id = workflow.get("id", "?")
            name_value = workflow.get("name", workflow_id)
            node_count = len(workflow.get("nodes", [])) if isinstance(workflow.get("nodes"), list) else "?"
            lines.append(f"- {workflow_id}: {name_value} ({node_count} nodes)")
        return "\n".join(lines) if lines else "No editable local workflows are saved yet."

    if name == "polykit_world_generate_asset":
        world_id = _safe_world_id(args.get("world_id"))
        proto_id = _required_text(args.get("proto_id"), "proto_id")
        image_path = _required_text(args.get("image_path"), "image_path")
        with open(image_path, "rb") as image_file:
            image_bytes = image_file.read()
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        filename = image_path.replace("\\", "/").split("/")[-1]
        form_data = {
            "remesh": args.get("remesh", "quad"),
            "collection": args.get("collection", "Worlds"),
            "node_id": proto_id,
            "world_id": world_id,
            "proto_id": proto_id,
        }
        if args.get("enable_texture") is not None:
            form_data["enable_texture"] = str(_as_bool(args["enable_texture"])).lower()
        if args.get("texture_resolution") is not None:
            form_data["texture_resolution"] = str(int(args["texture_resolution"]))
        params = args.get("params")
        if isinstance(params, Mapping):
            form_data["params"] = json.dumps(dict(params), ensure_ascii=False)
        if args.get("model_id"):
            form_data["model_id"] = args["model_id"]
        if args.get("workflow_id"):
            form_data["workflow_id"] = args["workflow_id"]
        response = await client.post(
            f"{API_BASE}/workflow-runs/from-image",
            files={"image": (filename, image_bytes, mime)},
            data=form_data,
            timeout=30.0,
        )
        response.raise_for_status()
        run_id = response.json()["run_id"]
        return (
            f"World asset generation started for '{world_id}/{proto_id}'. Run ID: {run_id}\n"
            "Poll with polykit_get_generation_status; when done, attach scene_candidate.workspace_path using polykit_world_attach_asset."
        )

    if name == "polykit_world_attach_asset":
        world_id = _safe_world_id(args.get("world_id"))
        world = await _get_world(client, world_id)
        updated = attach_world_artifact(
            world,
            proto_id=args.get("proto_id", ""),
            workspace_path=args.get("workspace_path", ""),
            workflow_id=args.get("workflow_id"),
            run_id=args.get("run_id"),
            concept_image=args.get("concept_image"),
        )
        response = await client.put(f"{API_BASE}/workspace-library/worlds/{world_id}", json=updated)
        response.raise_for_status()
        return f"Attached asset to '{world_id}/{args['proto_id']}': {_json_text(response.json())}"

    return f"Unknown tool: {name}"


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldStoreError(f"{label} is required")
    return value.strip()


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _workspace_image_reference(image_path: object) -> dict[str, str]:
    path = Path(_required_text(image_path, "image_path")).expanduser().resolve()
    root = runtime_paths.workspace.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise WorldStoreError("image_path must point to a file inside the PolyKit workspace") from exc
    if not path.is_file():
        raise WorldStoreError(f"Image file not found: {path}")
    return {"kind": "workspace_path", "path": relative.as_posix()}


def _safe_world_id(value: object) -> str:
    return validate_world_id(_required_text(value, "world_id"))


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


async def _get_world(client: httpx.AsyncClient, world_id: str) -> dict:
    response = await client.get(f"{API_BASE}/workspace-library/worlds/{world_id}")
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise WorldStoreError("Saved world document must be a JSON object")
    if value.get("schema_version") != 2 or not isinstance(value.get("runtime"), Mapping):
        raise WorldStoreError("Saved world must use schema-v2 runtime")
    return value


async def _on_list_tools(_context: object, _params: object) -> ListToolsResult:
    return ListToolsResult(tools=await list_tools())


async def _on_call_tool(_context: object, params: object) -> CallToolResult:
    name = getattr(params, "name", "")
    arguments = getattr(params, "arguments", None) or {}
    content = await call_tool(name, arguments)
    return CallToolResult(content=content)


def _build_server() -> Server:
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

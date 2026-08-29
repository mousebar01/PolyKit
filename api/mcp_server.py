"""
PolyKit MCP Server
Exposes PolyKit's capabilities as MCP tools for external agents (Claude Desktop, Codex CLI, etc.).

Usage:
  python mcp_server.py

Requires PolyKit's FastAPI backend to be running on http://localhost:8765.
"""

import asyncio
import json
import mimetypes
from collections.abc import Mapping
from pathlib import Path

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from services.world_agent import attach_world_artifact, update_world_stage
from services.world_store import WorldStoreError, validate_world_id
from services.runtime_paths import runtime_paths

API_BASE = "http://localhost:8765"

server: Server | None = None


async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="polykit_list_models",
            description="List all 3D generation models available in PolyKit (downloaded and ready to use).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="polykit_switch_model",
            description="Switch the active 3D generation model in PolyKit.",
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {"type": "string", "description": "The model ID to activate."},
                },
                "required": ["model_id"],
            },
        ),
        Tool(
            name="polykit_generate_from_image",
            description="Generate a 3D mesh from a 2D image file. Returns a server run ID to track progress.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Absolute path to the image file on disk.",
                    },
                    "model_id": {
                        "type": "string",
                        "description": "Which model to use. If omitted, uses the currently active model.",
                    },
                    "remesh": {
                        "type": "string",
                        "enum": ["quad", "triangle", "none"],
                        "description": "Remesh strategy after generation. Default: quad.",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Workspace collection for the mesh; default: Workflows.",
                    },
                    "enable_texture": {
                        "type": "boolean",
                        "description": "Run the compatible texture-refinement node after mesh generation.",
                    },
                    "texture_resolution": {
                        "type": "integer",
                        "description": "Texture refinement resolution when enable_texture is true. Default: 1024.",
                    },
                    "params": {
                        "type": "object",
                        "description": "Optional Trellis generation/refinement parameters.",
                    },
                    "workflow_id": {
                        "type": "string",
                        "description": "Optional workflow provenance id.",
                    },
                },
                "required": ["image_path"],
            },
        ),
        Tool(
            name="polykit_remove_background",
            description=(
                "Remove the background from a local image through the installed local process node "
                "and publish a transparent PNG. Returns a server run ID to track progress."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Absolute path to an image inside the PolyKit workspace.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Background-removal model. Default: isnet-anime.",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Workspace collection for the transparent PNG; default: Workflows.",
                    },
                    "workflow_id": {
                        "type": "string",
                        "description": "Optional workflow provenance id.",
                    },
                },
                "required": ["image_path"],
            },
        ),
        Tool(
            name="polykit_generate_image",
            description=(
                "Generate an illustration from text through a local PolyKit workflow. "
                "Defaults to the official Anima Diffusers node; returns a run ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Anime/illustration prompt; Danbooru tags or natural language are both accepted.",
                    },
                    "model_id": {
                        "type": "string",
                        "description": "Text-to-image node id; default: anima/generate.",
                    },
                    "params": {
                        "type": "object",
                        "description": "Optional model parameters such as steps, guidance_scale, seed, width, and height.",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Workspace collection; default: Workflows.",
                    },
                    "workflow_id": {
                        "type": "string",
                        "description": "Optional workflow provenance id.",
                    },
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="polykit_get_generation_status",
            description="Poll a PolyKit server run. Call repeatedly until status is 'done', 'cancelled', or 'error'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Run ID returned by polykit_generate_from_image. The field name is kept for MCP client compatibility.",
                    },
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="polykit_decimate_mesh",
            description="Reduce the polygon count of a mesh using quadric edge collapse decimation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative path to the mesh (e.g. 'Workflows/mesh.glb').",
                    },
                    "target_faces": {
                        "type": "integer",
                        "description": "Target number of faces after decimation (minimum 100).",
                    },
                },
                "required": ["path", "target_faces"],
            },
        ),
        Tool(
            name="polykit_smooth_mesh",
            description="Apply Laplacian smoothing to a mesh. More iterations = smoother surface but less detail.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative path to the mesh.",
                    },
                    "iterations": {
                        "type": "integer",
                        "description": "Number of smoothing iterations (1–20).",
                    },
                },
                "required": ["path", "iterations"],
            },
        ),
        Tool(
            name="polykit_import_mesh",
            description="Import a mesh file from disk into PolyKit's workspace (.glb, .obj, .stl, .ply).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the mesh file on disk.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="polykit_unload_models",
            description="Unload all 3D generation models from GPU VRAM. Useful before running VRAM-intensive tasks.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="polykit_get_settings",
            description="Get the current PolyKit settings (models directory, workspace directory).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="polykit_world_create",
            description=(
                "Start a new scene record for one world-generation request. Call this once before "
                "polykit_world_save and the stage tools; keep the returned world_id for every "
                "subsequent stage and asset attachment. This allocates a fresh scene and never "
                "overwrites an earlier world."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Optional display name for the scene."},
                    "prompt": {"type": "string", "description": "Original scene request, if known."},
                    "parent_world_id": {
                        "type": "string",
                        "description": "Optional earlier scene id when this is a deliberate revision.",
                    },
                },
            },
        ),
        Tool(
            name="polykit_world_get",
            description=(
                "Read a server-owned world document. Use this before changing a world so the "
                "Agent preserves the existing spec, artifacts, and WorldClaw stage plan."
            ),
            inputSchema={
                "type": "object",
                "properties": {"world_id": {"type": "string", "description": "World id."}},
                "required": ["world_id"],
            },
        ),
        Tool(
            name="polykit_world_save",
            description=(
                "Save an Agent-authored world plan or manifest to the local PolyKit workspace. "
                "The Agent owns intent, regions, terrain rules, assets, spatial relations, and "
                "stage decisions; PolyKit only validates and persists the JSON document."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "string", "description": "World id."},
                    "document": {
                        "type": "object",
                        "description": (
                            "JSON world document. Put the paper-derived plan under agent_plan "
                            "and keep artifact paths workspace-relative."
                        ),
                    },
                },
                "required": ["world_id", "document"],
            },
        ),
        Tool(
            name="polykit_world_update_stage",
            description=(
                "Record progress for one WorldClaw-inspired stage without executing it. Stages "
                "are intent, plan, terrain, placement, assets, materials, and refine. Use this "
                "to make the Agent's coarse-to-fine orchestration visible and resumable."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "string", "description": "World id."},
                    "stage_id": {
                        "type": "string",
                        "enum": ["intent", "plan", "terrain", "placement", "assets", "materials", "refine"],
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "running", "done", "blocked"],
                    },
                    "note": {"type": "string", "description": "Short progress or decision note."},
                    "prompt": {"type": "string", "description": "Original world prompt, if known."},
                },
                "required": ["world_id", "stage_id", "status"],
            },
        ),
        Tool(
            name="polykit_world_list_workflows",
            description=(
                "List editable local workflow definitions that the Agent can choose for terrain, "
                "asset, material, or refinement stages."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="polykit_world_generate_asset",
            description=(
                "Run a local image-to-3D workflow for one planned world prototype. This is the "
                "paper's regional asset stage: the image and model stay on the local PolyKit "
                "server, and the returned run id can be polled before attaching the mesh."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "string", "description": "World id for orchestration context."},
                    "proto_id": {"type": "string", "description": "Prototype id from the world plan."},
                    "image_path": {"type": "string", "description": "Absolute local concept image path."},
                    "model_id": {"type": "string", "description": "Optional local model id."},
                    "workflow_id": {"type": "string", "description": "Optional workflow provenance id."},
                    "collection": {"type": "string", "description": "Workspace collection; default: Worlds."},
                    "remesh": {
                        "type": "string",
                        "enum": ["quad", "triangle", "none"],
                        "description": "Remesh strategy; default: quad.",
                    },
                    "enable_texture": {
                        "type": "boolean",
                        "description": "Run the compatible texture-refinement node after mesh generation.",
                    },
                    "texture_resolution": {
                        "type": "integer",
                        "description": "Texture refinement resolution when enable_texture is true. Default: 1024.",
                    },
                    "params": {
                        "type": "object",
                        "description": "Optional generation/refinement parameters.",
                    },
                },
                "required": ["world_id", "proto_id", "image_path"],
            },
        ),
        Tool(
            name="polykit_world_attach_asset",
            description=(
                "Attach a completed local mesh to a planned world prototype. The mesh path must "
                "be workspace-relative (for example Workflows/Worlds/observatory.glb); this "
                "updates only the world manifest and never copies binary data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "string", "description": "World id."},
                    "proto_id": {"type": "string", "description": "Prototype id from the world plan."},
                    "workspace_path": {"type": "string", "description": "Workspace-relative mesh path."},
                    "workflow_id": {"type": "string", "description": "Optional workflow provenance id."},
                    "run_id": {"type": "string", "description": "Optional local workflow run id."},
                    "concept_image": {"type": "string", "description": "Optional workspace-relative concept image."},
                },
                "required": ["world_id", "proto_id", "workspace_path"],
            },
        ),
    ]


async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            result = await _dispatch(client, name, arguments)
        except httpx.ConnectError:
            result = (
                "Cannot connect to PolyKit API at http://localhost:8765. "
                "Make sure PolyKit is running."
            )
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
        image_path: str = args["image_path"]
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
        return (
            f"Generation started. Run ID: {run_id}\n"
            f"Use polykit_get_generation_status with this ID to track progress."
        )

    if name == "polykit_remove_background":
        image_ref = _workspace_image_reference(args["image_path"])
        params = {"model": args.get("model", "isnet-anime")}
        payload = {
            "schema_version": 1,
            "workflow_id": str(args.get("workflow_id") or "").strip() or None,
            "prompt": {
                "image": {
                    "class_type": "polykit.image",
                    "inputs": {"image": image_ref},
                },
                "cutout": {
                    "class_type": "image-background-remover/remove-background",
                    "inputs": {
                        "image": ["image", "image"],
                        "params": params,
                    },
                },
                "output": {
                    "class_type": "polykit.image_output",
                    "inputs": {"image": ["cutout", "image"]},
                },
            },
            "output_node_id": "output",
            "collection": str(args.get("collection") or "Workflows"),
        }
        response = await client.post(f"{API_BASE}/workflow-runs/execute", json=payload, timeout=30.0)
        response.raise_for_status()
        run_id = response.json()["run_id"]
        return (
            f"Background removal started with {params['model']}. Run ID: {run_id}\n"
            "Use polykit_get_generation_status with this ID to track progress."
        )

    if name == "polykit_generate_image":
        prompt = _required_text(args.get("prompt"), "prompt")
        model_id = _required_text(args.get("model_id") or "anima/generate", "model_id")
        params = args.get("params")
        if not isinstance(params, Mapping):
            params = {}
        workflow_id = str(args.get("workflow_id") or "").strip() or None
        payload = {
            "schema_version": 1,
            "workflow_id": workflow_id,
            "prompt": {
                "text": {"class_type": "polykit.text", "inputs": {"text": prompt}},
                "image": {
                    "class_type": model_id,
                    "inputs": {"text": ["text", "text"], "params": dict(params)},
                },
                "output": {
                    "class_type": "polykit.image_output",
                    "inputs": {"image": ["image", "image"]},
                },
            },
            "output_node_id": "output",
            "collection": str(args.get("collection") or "Workflows"),
        }
        response = await client.post(f"{API_BASE}/workflow-runs/execute", json=payload, timeout=30.0)
        response.raise_for_status()
        run_id = response.json()["run_id"]
        return (
            f"Illustration generation started with {model_id}. Run ID: {run_id}\n"
            "Use polykit_get_generation_status with this ID to track progress."
        )

    if name == "polykit_get_generation_status":
        run_id = args["job_id"]
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
        response = await client.post(
            f"{API_BASE}/optimize/mesh",
            json={"path": args["path"], "target_faces": args["target_faces"]},
        )
        response.raise_for_status()
        data = response.json()
        return f"Decimated mesh to {data.get('face_count', '?')} faces. New file: {data.get('url', '')}"

    if name == "polykit_smooth_mesh":
        response = await client.post(
            f"{API_BASE}/optimize/smooth",
            json={"path": args["path"], "iterations": args["iterations"]},
        )
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
            "Use this world_id for the planning stages and all later asset attachments."
        )

    if name == "polykit_world_save":
        world_id = _safe_world_id(args.get("world_id"))
        document = args.get("document")
        if not isinstance(document, Mapping):
            raise WorldStoreError("document must be a JSON object")
        payload = dict(document)
        if not any(payload.get(key) for key in ("world_id", "worldId", "id")):
            payload["world_id"] = world_id
        response = await client.put(f"{API_BASE}/workspace-library/worlds/{world_id}", json=payload)
        response.raise_for_status()
        return f"World '{world_id}' saved: {_json_text(response.json())}"

    if name == "polykit_world_update_stage":
        world_id = _safe_world_id(args.get("world_id"))
        stage_id = _required_text(args.get("stage_id"), "stage_id")
        world = await _get_world_or_shell(client, world_id)
        updated = update_world_stage(
            world,
            stage_id=stage_id,
            status=args.get("status", ""),
            note=args.get("note"),
            prompt=args.get("prompt"),
        )
        if not any(updated.get(key) for key in ("world_id", "worldId", "id")):
            updated["world_id"] = world_id
        response = await client.put(f"{API_BASE}/workspace-library/worlds/{world_id}", json=updated)
        response.raise_for_status()
        stage = next(
            item
            for item in updated["agent_plan"]["stages"]
            if item["id"] == stage_id
        )
        return f"World '{world_id}' stage updated: {_json_text(stage)}"

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
            node_count = (
                len(workflow.get("nodes", []))
                if isinstance(workflow.get("nodes"), list)
                else "?"
            )
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
            "Poll with polykit_get_generation_status; when done, attach "
            "scene_candidate.workspace_path using polykit_world_attach_asset."
        )

    if name == "polykit_world_attach_asset":
        world_id = _safe_world_id(args.get("world_id"))
        world_response = await client.get(f"{API_BASE}/workspace-library/worlds/{world_id}")
        world_response.raise_for_status()
        updated = attach_world_artifact(
            world_response.json(),
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
    """Convert a trusted local image path into the workflow's safe file reference."""
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


async def _get_world_or_shell(client: httpx.AsyncClient, world_id: str) -> dict:
    response = await client.get(f"{API_BASE}/workspace-library/worlds/{world_id}")
    if response.status_code == 404:
        return {"world_id": world_id, "spec": {}, "artifacts": {}}
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise WorldStoreError("Saved world document must be a JSON object")
    return value


async def _on_list_tools(_context: object, _params: object) -> ListToolsResult:
    return ListToolsResult(tools=await list_tools())


async def _on_call_tool(_context: object, params: object) -> CallToolResult:
    name = getattr(params, "name", "")
    arguments = getattr(params, "arguments", None) or {}
    content = await call_tool(name, arguments)
    return CallToolResult(content=content)


def _build_server() -> Server:
    """Create a server for MCP 2.x, with a small MCP 1.x fallback."""

    try:
        return Server("polykit", on_list_tools=_on_list_tools, on_call_tool=_on_call_tool)
    except TypeError:
        # MCP 1.x registered low-level handlers with decorators instead of
        # constructor callbacks.  Keep the adapter usable for older installs
        # while the lockfile continues to use the current protocol package.
        legacy = Server("polykit")
        list_tools_decorator = getattr(legacy, "list_tools", None)
        call_tool_decorator = getattr(legacy, "call_tool", None)
        if not callable(list_tools_decorator) or not callable(call_tool_decorator):
            raise
        list_tools_decorator()(list_tools)
        call_tool_decorator()(call_tool)
        return legacy


server = _build_server()


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

"""
PolyKit MCP Server
Exposes PolyKit's capabilities as MCP tools for external agents (Claude Desktop, Codex CLI, etc.).

Usage:
  python mcp_server.py

Requires PolyKit's FastAPI backend to be running on http://localhost:8765.
"""

import asyncio
import mimetypes

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

API_BASE = "http://localhost:8765"

server = Server("polykit")


@server.list_tools()
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
                },
                "required": ["image_path"],
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
    ]


@server.call_tool()
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

        form_data = {"remesh": args.get("remesh", "quad")}
        if args.get("model_id"):
            form_data["model_id"] = args["model_id"]

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

    return f"Unknown tool: {name}"


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

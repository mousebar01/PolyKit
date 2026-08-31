"""PolyKit MCP entrypoint with world-domain bridge tools.

The base MCP server owns general model, workflow, mesh, and world CRUD tools.
This adapter only adds domain-level world validation and structure construction,
proxying the authoritative FastAPI control plane. No Agent-specific task runtime
or conversation state lives here.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

import mcp_server as base


API_BASE = base.API_BASE
EXTRA_TOOL_NAMES = {
    "polykit_world_validate",
    "polykit_world_build_structure",
}


def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> Tool:
    schema: dict = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return Tool(name=name, description=description, inputSchema=schema)


def extra_tools() -> list[Tool]:
    return [
        _tool(
            "polykit_world_validate",
            "Validate world domain state and optional Workflow Run evidence. Returns pass/needs_review/fail, issues, and evidence; it does not advance any Agent state machine.",
            {
                "world_id": {"type": "string"},
                "capability": {
                    "type": "string",
                    "enum": [
                        "world.spec.validate",
                        "world.blockout.validate",
                        "world.construction.validate",
                        "world.gameplay.validate",
                        "world.final.validate",
                    ],
                },
                "run_id": {"type": "string"},
            },
            ["world_id", "capability"],
        ),
        _tool(
            "polykit_world_build_structure",
            "Start the BuildSpec -> blender-scene/build -> GLB Workflow Run for one world building.",
            {
                "world_id": {"type": "string"},
                "building_id": {"type": "string"},
                "collection": {"type": "string"},
                "render_preview": {"type": "boolean"},
            },
            ["world_id"],
        ),
    ]


async def list_tools() -> list[Tool]:
    tools = await base.list_tools()
    # Keep the base server's tool implementation, but remove wording from the
    # abandoned Agent Workflow experiment before exposing it to clients.
    normalized: list[Tool] = []
    for tool in tools:
        if tool.name == "polykit_world_create":
            normalized.append(_tool(
                tool.name,
                "Create a fresh schema-v2 world domain document. Chat, UI, CLI, or any external Agent may call the same World API.",
                tool.inputSchema.get("properties", {}),
                tool.inputSchema.get("required"),
            ))
        else:
            normalized.append(tool)
    return [*normalized, *extra_tools()]


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


async def _extra_dispatch(client: httpx.AsyncClient, name: str, args: dict) -> str:
    if name == "polykit_world_validate":
        world_id = str(args.get("world_id") or "")
        response = await client.post(
            f"{API_BASE}/workspace-library/worlds/{world_id}/validate",
            json={"capability": args.get("capability"), "run_id": args.get("run_id")},
        )
    elif name == "polykit_world_build_structure":
        world_id = str(args.get("world_id") or "")
        response = await client.post(
            f"{API_BASE}/workspace-library/worlds/{world_id}/build-structure",
            json={
                "building_id": args.get("building_id"),
                "collection": str(args.get("collection") or "Scenes"),
                "render_preview": bool(args.get("render_preview", True)),
            },
            timeout=30.0,
        )
    else:
        return f"Unknown tool: {name}"
    response.raise_for_status()
    return _json_text(response.json())


async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name not in EXTRA_TOOL_NAMES:
        return await base.call_tool(name, arguments)
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            result = await _extra_dispatch(client, name, arguments)
        except httpx.ConnectError:
            result = f"Cannot connect to PolyKit API at {API_BASE}. Make sure PolyKit is running."
        except httpx.HTTPStatusError as exc:
            result = f"PolyKit API error {exc.response.status_code}: {exc.response.text[:500]}"
        except Exception as exc:
            result = f"Error: {exc}"
    return [TextContent(type="text", text=result)]


async def _on_list_tools(_context: object, _params: object) -> ListToolsResult:
    return ListToolsResult(tools=await list_tools())


async def _on_call_tool(_context: object, params: object) -> CallToolResult:
    name = getattr(params, "name", "")
    arguments = getattr(params, "arguments", None) or {}
    return CallToolResult(content=await call_tool(name, arguments))


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

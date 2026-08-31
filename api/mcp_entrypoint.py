"""Combined PolyKit MCP entrypoint.

The legacy world/model tools remain implemented by ``mcp_server``. This module
only adds Agent Workflow Protocol and world workflow bridge tools, all of which
proxy the authoritative FastAPI control plane rather than mutating state here.
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


def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> Tool:
    schema: dict = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return Tool(name=name, description=description, inputSchema=schema)


_EVIDENCE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "ref": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": ["kind", "ref"],
    },
}


EXTRA_TOOL_NAMES = {
    "polykit_agent_workflow_list",
    "polykit_agent_workflow_start",
    "polykit_agent_workflow_get",
    "polykit_agent_workflow_next",
    "polykit_agent_workflow_begin",
    "polykit_agent_workflow_complete",
    "polykit_agent_workflow_wait",
    "polykit_agent_workflow_pause",
    "polykit_agent_workflow_resume",
    "polykit_agent_workflow_cancel",
    "polykit_world_validate",
    "polykit_world_build_structure",
}


def extra_tools() -> list[Tool]:
    session = {"session_id": {"type": "string"}}
    return [
        _tool("polykit_agent_workflow_list", "List durable Agent Workflow definitions."),
        _tool(
            "polykit_agent_workflow_start",
            "Start a durable Agent Workflow session for a domain subject. This does not change normal chat mode.",
            {
                "workflow_id": {"type": "string"},
                "subject_kind": {"type": "string"},
                "subject_id": {"type": "string"},
                "metadata": {"type": "object"},
            },
            ["workflow_id", "subject_kind", "subject_id"],
        ),
        _tool("polykit_agent_workflow_get", "Read one durable Agent Workflow session.", session, ["session_id"]),
        _tool(
            "polykit_agent_workflow_next",
            "Read the next Agent Workflow action without mutating progress. The response includes subject and executor hints.",
            session,
            ["session_id"],
        ),
        _tool("polykit_agent_workflow_begin", "Explicitly begin the current workflow step.", session, ["session_id"]),
        _tool(
            "polykit_agent_workflow_complete",
            "Complete the current workflow step with an allowed outcome and required evidence.",
            {
                "session_id": {"type": "string"},
                "outcome": {"type": "string"},
                "evidence": _EVIDENCE_SCHEMA,
                "diagnostic": {"type": "string"},
            },
            ["session_id", "outcome"],
        ),
        _tool(
            "polykit_agent_workflow_wait",
            "Put the current workflow session into an explicit user/run wait state.",
            {
                "session_id": {"type": "string"},
                "kind": {"type": "string", "enum": ["user", "run"]},
                "ref": {"type": "string"},
                "reason": {"type": "string"},
            },
            ["session_id", "kind"],
        ),
        _tool("polykit_agent_workflow_pause", "Pause a durable Agent Workflow session.", session, ["session_id"]),
        _tool("polykit_agent_workflow_resume", "Resume a paused or waiting Agent Workflow session.", session, ["session_id"]),
        _tool("polykit_agent_workflow_cancel", "Cancel a durable Agent Workflow session.", session, ["session_id"]),
        _tool(
            "polykit_world_validate",
            "Run a deterministic World Builder validator and return its evidence plus allowed workflow outcome.",
            {
                "world_id": {"type": "string"},
                "capability": {
                    "type": "string",
                    "enum": [
                        "world.spec.validate",
                        "world.blockout.validate",
                        "world.construction.validate",
                        "world.gameplay.validate",
                        "world.final.validate"
                    ],
                },
                "run_id": {"type": "string"},
            },
            ["world_id", "capability"],
        ),
        _tool(
            "polykit_world_build_structure",
            "Start the real BuildSpec → blender-scene/build → GLB Workflow Run for one world building.",
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
    return [*(await base.list_tools()), *extra_tools()]


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


async def _extra_dispatch(client: httpx.AsyncClient, name: str, args: dict) -> str:
    if name == "polykit_agent_workflow_list":
        response = await client.get(f"{API_BASE}/agent-workflows/definitions")
    elif name == "polykit_agent_workflow_start":
        response = await client.post(
            f"{API_BASE}/agent-workflows/sessions",
            json={
                "workflow_id": str(args.get("workflow_id") or "world-builder"),
                "subject_kind": str(args.get("subject_kind") or "world"),
                "subject_id": str(args.get("subject_id") or ""),
                "metadata": dict(args.get("metadata")) if isinstance(args.get("metadata"), Mapping) else {},
            },
        )
    elif name == "polykit_agent_workflow_get":
        response = await client.get(f"{API_BASE}/agent-workflows/sessions/{args.get('session_id', '')}")
    elif name == "polykit_agent_workflow_next":
        response = await client.get(f"{API_BASE}/agent-workflows/sessions/{args.get('session_id', '')}/next")
    elif name in {"polykit_agent_workflow_begin", "polykit_agent_workflow_pause", "polykit_agent_workflow_resume", "polykit_agent_workflow_cancel"}:
        action = name.removeprefix("polykit_agent_workflow_")
        response = await client.post(f"{API_BASE}/agent-workflows/sessions/{args.get('session_id', '')}/{action}")
    elif name == "polykit_agent_workflow_complete":
        evidence = args.get("evidence") if isinstance(args.get("evidence"), list) else []
        response = await client.post(
            f"{API_BASE}/agent-workflows/sessions/{args.get('session_id', '')}/complete",
            json={
                "outcome": str(args.get("outcome") or ""),
                "evidence": [dict(item) for item in evidence if isinstance(item, Mapping)],
                "diagnostic": args.get("diagnostic"),
            },
        )
    elif name == "polykit_agent_workflow_wait":
        response = await client.post(
            f"{API_BASE}/agent-workflows/sessions/{args.get('session_id', '')}/wait",
            json={"kind": args.get("kind"), "ref": args.get("ref"), "reason": args.get("reason")},
        )
    elif name == "polykit_world_validate":
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

#!/usr/bin/env python3
"""Canonical, context-efficient MCP transport over PolyKit Application APIs.

Stable MCP tool names remain compatible with existing Agent clients. Submission
and Run control target the generic application API, while large read responses
use bounded projections so polling and inspection do not repeatedly consume the
Agent context window.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import urlencode

from mcp.types import Tool

import server as _base


_base_dispatch = _base._dispatch
_base_list_tools = _base.list_tools
_DEFAULT_EVENT_LIMIT = 20
_MAX_EVENT_LIMIT = 200
_DEFAULT_RESOURCE_LIMIT = 16 * 1024
_MAX_RESOURCE_LIMIT = 32 * 1024


def _agent(surface: str) -> dict[str, str]:
    return {"type": "agent", "surface": surface}


def _selected_profile() -> str:
    """Return an opt-in discovery profile; unknown values safely mean all."""

    value = os.environ.get("POLYKIT_MCP_PROFILE", "all").strip().lower()
    return value if value in {"all", "core", "asset", "world", "authoring"} else "all"


def _tool_group(name: str) -> str:
    """Classify tools by stable naming convention so profiles do not drift."""

    if name.startswith("polykit_world_"):
        return "world"
    if name.startswith(("polykit_asset_", "polykit_mesh_")):
        return "asset"
    return "core"


def _profile_allows(name: str, profile: str) -> bool:
    if profile in {"all", "authoring"}:
        return True
    group = _tool_group(name)
    if profile == "core":
        return group == "core"
    if profile == "asset":
        return group in {"core", "asset"}
    if profile == "world":
        return group in {"core", "world"}
    return True


def _tool_with_schema(tool: Tool, *, description: str, properties: dict[str, Any]) -> Tool:
    schema = dict(tool.input_schema)
    schema["properties"] = properties
    return Tool(name=tool.name, description=description, inputSchema=schema)


async def list_tools() -> list[Tool]:
    """Return stable tools with Agent-efficiency guidance and opt-in filtering."""

    projected: list[Tool] = []
    for tool in await _base_list_tools():
        properties = dict(tool.input_schema.get("properties") or {})
        if tool.name == "polykit_workflow_status":
            tool = _tool_with_schema(
                tool,
                description=(
                    "Lightweight Run polling without the large durable meta payload. "
                    "Use inspect only when detailed evidence or event history is needed."
                ),
                properties=properties,
            )
        elif tool.name == "polykit_workflow_inspect":
            properties.update({
                "since_seq": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Read events newer than this sequence; reuse next_event_seq to continue forward.",
                },
                "before_seq": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Read events older than this sequence; reuse previous_event_seq to page backward. Mutually exclusive with since_seq.",
                },
                "events_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_EVENT_LIMIT,
                    "description": f"Maximum events returned. Default {_DEFAULT_EVENT_LIMIT}.",
                },
                "include_events": {
                    "type": "boolean",
                    "description": "Include event details. Default true; false returns snapshots/evidence plus a live next_event_seq cursor.",
                },
            })
            tool = _tool_with_schema(
                tool,
                description=(
                    "Detailed read-only Run inspection; do not use it for polling. "
                    "By default only the latest event page is returned. Use since_seq for new events or before_seq for older history."
                ),
                properties=properties,
            )
        elif tool.name == "polykit_skill_read_resource":
            properties.update({
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Character offset to start reading from. Default 0.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_RESOURCE_LIMIT,
                    "description": f"Maximum characters returned. Default {_DEFAULT_RESOURCE_LIMIT}.",
                },
            })
            tool = _tool_with_schema(
                tool,
                description=(
                    "Read a bounded UTF-8 Skill resource chunk. Continue with next_offset when truncated; scripts are never executed."
                ),
                properties=properties,
            )
        projected.append(tool)

    profile = _selected_profile()
    return [tool for tool in projected if _profile_allows(tool.name, profile)]


def _bounded_int(args: dict[str, Any], key: str, default: int, maximum: int) -> int:
    try:
        value = int(args.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{key} must be positive")
    return min(value, maximum)


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    """Translate stable MCP tools onto canonical context-efficient APIs."""

    if name == "polykit_workflow_status":
        run_id = _base._id_path(_base._required_text(args, "run_id"))
        return await _base._request_json("GET", f"/runs/{run_id}?compact=true")

    if name == "polykit_workflow_inspect":
        run_id = _base._id_path(_base._required_text(args, "run_id"))
        since_seq = args.get("since_seq")
        before_seq = args.get("before_seq")
        if since_seq is not None and before_seq is not None:
            raise ValueError("since_seq and before_seq are mutually exclusive")
        params: list[tuple[str, str]] = [
            ("events_limit", str(_bounded_int(args, "events_limit", _DEFAULT_EVENT_LIMIT, _MAX_EVENT_LIMIT))),
            ("include_events", "true" if args.get("include_events", True) is not False else "false"),
        ]
        if since_seq is not None:
            if int(since_seq) < 0:
                raise ValueError("since_seq must be non-negative")
            params.append(("since_seq", str(int(since_seq))))
        if before_seq is not None:
            if int(before_seq) < 0:
                raise ValueError("before_seq must be non-negative")
            params.append(("before_seq", str(int(before_seq))))
        return await _base._request_json(
            "GET",
            f"/runs/{run_id}/inspect?{urlencode(params)}",
        )

    if name == "polykit_skill_read_resource":
        skill_name = _base._id_path(_base._required_text(args, "name"))
        resource_path = _base._relative_resource_path(_base._required_text(args, "path"))
        try:
            offset = int(args.get("offset", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("offset must be an integer") from exc
        if offset < 0:
            raise ValueError("offset must be non-negative")
        limit = _bounded_int(args, "limit", _DEFAULT_RESOURCE_LIMIT, _MAX_RESOURCE_LIMIT)
        query = urlencode({"offset": offset, "limit": limit})
        return await _base._request_json(
            "GET",
            f"/agent-skills/{skill_name}/resources/{resource_path}?{query}",
        )

    if name == "polykit_workflow_signal":
        run_id = _base._id_path(_base._required_text(args, "run_id"))
        return await _base._request_json(
            "POST",
            f"/runs/{run_id}/signals",
            {
                "name": _base._required_text(args, "name"),
                "payload": args.get("payload"),
            },
        )

    if name == "polykit_workflow_retry":
        run_id = _base._id_path(_base._required_text(args, "run_id"))
        return await _base._request_json("POST", f"/runs/{run_id}/retry")

    if name == "polykit_workflow_cancel":
        run_id = _base._id_path(_base._required_text(args, "run_id"))
        return await _base._request_json("DELETE", f"/runs/{run_id}")

    if name == "polykit_workflow_execute":
        request = args.get("request")
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        plan = dict(request)
        if not isinstance(plan.get("source"), dict):
            workflow_id = plan.get("workflow_id")
            plan["source"] = {
                "kind": "workflow",
                "id": workflow_id if isinstance(workflow_id, str) and workflow_id.strip() else None,
            }
        return await _base._request_json(
            "POST",
            "/runs",
            {
                "plan": plan,
                "initiator": _agent("mcp.workflow-execute"),
            },
        )

    if name == "polykit_asset_from_text":
        payload = {
            "prompt": _base._required_text(args, "prompt"),
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
            "initiator": _agent("mcp.asset-from-text"),
        }
        return await _base._request_json("POST", "/commands/generate-asset", payload)

    return await _base_dispatch(name, args)


def _json_text(value: Any) -> str:
    """Serialize MCP payloads without whitespace that adds no model information."""

    return _base.json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# Existing server callbacks resolve these module globals at call time. Patch the
# canonical projections and rebuild once so both current and older MCP SDK
# registration paths see the same tool catalog, dispatch, and serializer.
_base.list_tools = list_tools
_base._dispatch = _dispatch
_base._json_text = _json_text
_base.server = _base._build_server()


async def main() -> None:
    await _base.main()


if __name__ == "__main__":
    asyncio.run(main())

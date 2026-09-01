#!/usr/bin/env python3
"""Token-efficient Agent facade over PolyKit's canonical MCP adapter.

The canonical ``server.py`` remains the HTTP/MCP routing implementation. This
entry point only projects large server responses into Agent-sized payloads:
lightweight polling status, reversible cursor-based inspection events, bounded
Skill resource chunks, optional tool-discovery profiles, and compact JSON.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
from typing import Any

from mcp.types import Tool


_SERVER_PATH = Path(__file__).with_name("server.py")
_SPEC = importlib.util.spec_from_file_location("polykit_mcp_base_server", _SERVER_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load PolyKit MCP server from {_SERVER_PATH}")
_base = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_base)

_BASE_LIST_TOOLS = _base.list_tools
_BASE_DISPATCH = _base._dispatch
_STATUS_FIELDS = (
    "run_id",
    "status",
    "progress",
    "step",
    "output_url",
    "error",
    "scene_candidate",
)
_DEFAULT_EVENT_LIMIT = 20
_MAX_EVENT_LIMIT = 200
_DEFAULT_RESOURCE_LIMIT = 16 * 1024
_MAX_RESOURCE_LIMIT = 32 * 1024

_CORE_TOOLS = {
    "polykit_health",
    "polykit_list_models",
    "polykit_skill_list",
    "polykit_skill_get",
    "polykit_skill_read_resource",
    "polykit_workflow_list",
    "polykit_workflow_status",
    "polykit_workflow_inspect",
    "polykit_workflow_signal",
    "polykit_workflow_retry",
    "polykit_workflow_cancel",
    "polykit_workflow_execute",
}
_ASSET_TOOLS = {
    "polykit_asset_from_text",
    "polykit_mesh_decimate",
    "polykit_mesh_smooth",
}
_WORLD_TOOLS = {
    "polykit_world_create",
    "polykit_world_get",
    "polykit_world_save",
    "polykit_world_compile_scene",
    "polykit_world_find_assets",
    "polykit_world_build_structure",
    "polykit_world_validate",
    "polykit_world_compile_repair",
    "polykit_world_compose",
    "polykit_world_attach_asset",
}
_PROFILE_TOOLS = {
    "core": _CORE_TOOLS,
    "asset": _CORE_TOOLS | _ASSET_TOOLS,
    "world": _CORE_TOOLS | _WORLD_TOOLS,
    "authoring": _CORE_TOOLS | _ASSET_TOOLS | _WORLD_TOOLS,
}


def _selected_profile() -> str:
    """Return an opt-in discovery profile; unknown values safely fall back to all."""

    value = os.environ.get("POLYKIT_MCP_PROFILE", "all").strip().lower()
    return value if value in {"all", *_PROFILE_TOOLS} else "all"


def _tool_with_schema(tool: Tool, *, description: str, properties: dict[str, Any]) -> Tool:
    schema = dict(tool.input_schema)
    schema["properties"] = properties
    return Tool(
        name=tool.name,
        description=description,
        inputSchema=schema,
    )


async def list_tools() -> list[Tool]:
    """Return canonical tools with Agent-efficiency guidance and optional filtering."""

    tools = await _BASE_LIST_TOOLS()
    projected: list[Tool] = []
    for tool in tools:
        properties = dict(tool.input_schema.get("properties") or {})
        if tool.name == "polykit_workflow_status":
            projected.append(_tool_with_schema(
                tool,
                description=(
                    "Lightweight WorkflowRun polling without the large durable meta payload. "
                    "Use inspect only when detailed evidence is needed."
                ),
                properties=properties,
            ))
            continue
        if tool.name == "polykit_workflow_inspect":
            properties.update({
                "since_seq": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Read newer events after this sequence; reuse next_event_seq for forward continuation.",
                },
                "before_seq": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Read older events before this sequence; reuse previous_event_seq for backward paging. Mutually exclusive with since_seq.",
                },
                "events_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_EVENT_LIMIT,
                    "description": f"Maximum events returned. Default {_DEFAULT_EVENT_LIMIT}.",
                },
                "include_events": {
                    "type": "boolean",
                    "description": "Include event details. Default true; false returns only snapshots/evidence.",
                },
            })
            projected.append(_tool_with_schema(
                tool,
                description=(
                    "Detailed read-only WorkflowRun inspection; do not poll with it. "
                    "Use since_seq for new events or before_seq to recover older history."
                ),
                properties=properties,
            ))
            continue
        if tool.name == "polykit_skill_read_resource":
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
            projected.append(_tool_with_schema(
                tool,
                description=(
                    "Read a bounded UTF-8 Skill resource chunk. Continue with next_offset; scripts are never executed."
                ),
                properties=properties,
            ))
            continue
        projected.append(tool)

    profile = _selected_profile()
    if profile == "all":
        return projected
    allowed = _PROFILE_TOOLS[profile]
    return [tool for tool in projected if tool.name in allowed]


def _compact_status(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = {key: value[key] for key in _STATUS_FIELDS if key in value}
    meta = value.get("meta")
    if isinstance(meta, dict):
        for key in ("workflow_id", "collection"):
            item = meta.get(key)
            if isinstance(item, str) and item:
                result[key] = item
        execution = meta.get("execution")
        if isinstance(execution, dict):
            waiting = execution.get("waiting")
            if isinstance(waiting, dict):
                result["waiting"] = dict(waiting)
    return result


def _event_seq(event: Any) -> int | None:
    if not isinstance(event, dict):
        return None
    seq = event.get("seq")
    if isinstance(seq, bool):
        return None
    try:
        value = int(seq)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _project_inspect(value: Any, args: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    raw_events = value.get("events")
    events = list(raw_events) if isinstance(raw_events, list) else []
    sequenced = [(seq, event) for event in events if (seq := _event_seq(event)) is not None]
    latest_seq = max((seq for seq, _ in sequenced), default=0)
    earliest_seq = min((seq for seq, _ in sequenced), default=0)
    include_events = args.get("include_events", True) is not False
    limit = max(1, min(int(args.get("events_limit", _DEFAULT_EVENT_LIMIT)), _MAX_EVENT_LIMIT))
    since_provided = "since_seq" in args and args.get("since_seq") is not None
    before_provided = "before_seq" in args and args.get("before_seq") is not None
    if since_provided and before_provided:
        raise ValueError("since_seq and before_seq are mutually exclusive")
    since_seq = max(0, int(args.get("since_seq", 0) or 0))
    before_seq = max(0, int(args.get("before_seq", 0) or 0))

    if not include_events:
        selected: list[Any] = []
        next_seq = latest_seq
        previous_seq = earliest_seq
        has_more = False
        has_older = bool(sequenced)
        truncated_before = bool(events)
    elif since_provided:
        candidates = [event for seq, event in sequenced if seq > since_seq]
        selected = candidates[:limit]
        next_seq = _event_seq(selected[-1]) if selected else since_seq
        previous_seq = _event_seq(selected[0]) if selected else since_seq
        has_more = len(candidates) > len(selected)
        has_older = any(seq <= since_seq for seq, _ in sequenced)
        truncated_before = False
    elif before_provided:
        candidates = [event for seq, event in sequenced if seq < before_seq]
        selected = candidates[-limit:]
        previous_seq = _event_seq(selected[0]) if selected else before_seq
        next_seq = latest_seq
        has_more = False
        has_older = len(candidates) > len(selected)
        truncated_before = has_older
    else:
        selected = events[-limit:]
        next_seq = _event_seq(selected[-1]) if selected else latest_seq
        previous_seq = _event_seq(selected[0]) if selected else earliest_seq
        has_more = False
        has_older = len(events) > len(selected)
        truncated_before = has_older

    result["events"] = selected
    result["next_event_seq"] = int(next_seq or 0)
    result["previous_event_seq"] = int(previous_seq or 0)
    result["latest_event_seq"] = latest_seq
    result["has_more_events"] = has_more
    result["has_older_events"] = has_older
    result["events_truncated_before"] = truncated_before
    return result


def _project_skill_resource(value: Any, args: dict[str, Any]) -> Any:
    if not isinstance(value, dict) or not isinstance(value.get("content"), str):
        return value
    content = value["content"]
    offset = max(0, int(args.get("offset", 0) or 0))
    limit = max(1, min(int(args.get("limit", _DEFAULT_RESOURCE_LIMIT)), _MAX_RESOURCE_LIMIT))
    offset = min(offset, len(content))
    end = min(len(content), offset + limit)
    result = dict(value)
    result["content"] = content[offset:end]
    result["offset"] = offset
    result["next_offset"] = end
    result["total_chars"] = len(content)
    result["truncated"] = end < len(content)
    return result


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    value = await _BASE_DISPATCH(name, args)
    if name == "polykit_workflow_status":
        return _compact_status(value)
    if name == "polykit_workflow_inspect":
        return _project_inspect(value, args)
    if name == "polykit_skill_read_resource":
        return _project_skill_resource(value, args)
    return value


def _json_text(value: Any) -> str:
    """Serialize MCP payloads without whitespace that adds no model information."""

    return _base.json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# Patch only Agent-facing projection hooks, then rebuild the MCP Server so both
# modern and legacy SDK registration paths see the projected functions.
_base.list_tools = list_tools
_base._dispatch = _dispatch
_base._json_text = _json_text
_base.server = _base._build_server()


if __name__ == "__main__":
    asyncio.run(_base.main())

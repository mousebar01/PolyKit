#!/usr/bin/env python3
"""Canonical MCP transport over PolyKit Application Commands and Runs.

The stable MCP tool names remain compatible with existing Agent clients, while
submission and Run-control traffic now targets the generic application API.
World/library tools that do not create generic Runs continue to delegate to the
base stateless HTTP adapter.
"""
from __future__ import annotations

import asyncio
from typing import Any

import server as _base


_base_dispatch = _base._dispatch


def _agent(surface: str) -> dict[str, str]:
    return {"type": "agent", "surface": surface}


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    """Translate legacy MCP tool names onto canonical Application/Run routes."""

    if name == "polykit_workflow_status":
        run_id = _base._id_path(_base._required_text(args, "run_id"))
        return await _base._request_json("GET", f"/runs/{run_id}")

    if name == "polykit_workflow_inspect":
        run_id = _base._id_path(_base._required_text(args, "run_id"))
        return await _base._request_json("GET", f"/runs/{run_id}/inspect")

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


async def main() -> None:
    # Existing server handlers resolve _dispatch at call time, so replacing the
    # module global keeps one MCP implementation and one tool catalog.
    _base._dispatch = _dispatch
    await _base.main()


if __name__ == "__main__":
    asyncio.run(main())

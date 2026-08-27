"""Public FastAPI boundary for the embedded Agent conversation runtime."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from services.agent_runtime import AgentRuntimeError, agent_runtime
from services.runtime_settings import get_agent_settings


router = APIRouter(prefix="/agent", tags=["agent"])

_TOOL_NAMES_BY_PROFILE = {
    "safe": ["read"],
    "blender": ["read", "bash", "edit", "write"],
    "developer": ["bash", "read", "edit", "write", "grep", "find", "ls"],
}


def _apply_session_defaults(body: bytes) -> bytes:
    """Apply PolyKit-owned Agent defaults without overriding explicit choices."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(payload, dict):
        return body

    settings = get_agent_settings()
    if not payload.get("provider") and not payload.get("modelId"):
        if settings.default_provider and settings.default_model:
            payload["provider"] = settings.default_provider
            payload["modelId"] = settings.default_model
    payload.setdefault("thinkingLevel", settings.thinking_level)
    payload.setdefault("toolNames", _TOOL_NAMES_BY_PROFILE[settings.tool_profile])
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


async def _upstream(request: Request) -> tuple[httpx.AsyncClient, httpx.Response]:
    if not get_agent_settings().enabled:
        raise HTTPException(status_code=503, detail="Agent is disabled in Settings")
    try:
        port, token = await agent_runtime.ensure_started()
    except AgentRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    body = await request.body()
    if request.method == "POST" and request.url.path == "/agent/sessions":
        body = _apply_session_defaults(body)
    headers = {"x-polykit-agent-token": token}
    content_type = request.headers.get("content-type")
    if content_type:
        headers["content-type"] = content_type
    accept = request.headers.get("accept")
    if accept:
        headers["accept"] = accept
    client = httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        timeout=None if request.url.path.endswith("/events") else 120,
        trust_env=False,
    )
    try:
        upstream_request = client.build_request(
            request.method,
            request.url.path.removeprefix("/agent") or "/",
            params=request.query_params,
            content=body or None,
            headers=headers,
        )
        response = await client.send(
            upstream_request,
            stream=request.url.path.endswith("/events"),
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=503, detail=f"Agent runtime unavailable: {exc}") from exc
    return client, response


async def _stream_response(client: httpx.AsyncClient, response: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    finally:
        await response.aclose()
        await client.aclose()


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_agent(request: Request, path: str):
    client, upstream = await _upstream(request)
    content_type = upstream.headers.get("content-type", "application/octet-stream")
    if content_type.startswith("text/event-stream"):
        if upstream.status_code >= 400:
            content = await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            return Response(content=content, status_code=upstream.status_code, media_type="application/json")
        return StreamingResponse(
            _stream_response(client, upstream),
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    content = await upstream.aread()
    status_code = upstream.status_code
    await upstream.aclose()
    await client.aclose()
    return Response(content=content, status_code=status_code, media_type=content_type.split(";", 1)[0])

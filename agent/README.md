# PolyKit Agent Runtime

This directory contains the embedded Node/TypeScript runtime used by PolyKit's Agent feature. It is not a standalone product UI.

PolyKit keeps one public product boundary:

```text
React Web
  -> FastAPI /agent
    -> embedded Node runtime
      -> pi coding-agent SDK
```

The browser never talks to the Node sidecar directly. `api/services/agent_runtime.py` starts it on demand, and `api/routers/agent.py` proxies the public `/agent` API through FastAPI.

## Runtime entry point

```text
runtime/server.ts
```

The runtime owns model/session/tool/skill/MCP integration that must stay in Node because the pi SDK is TypeScript-based.

## Dependencies

Agent dependencies are installed lazily the first time the embedded runtime is started. Normal PolyKit Web development and Web builds do not install Agent dependencies.

The current npm workspace is retained only because `runtime/server.ts` still imports runtime helpers from `apps/web/lib/`. Those helpers are compatibility code from the former standalone Agent Web app and should be moved into runtime-owned modules incrementally.

Do not add new UI, standalone authentication, PWA/mobile behavior, or Next.js routes under this directory. Product UI belongs in `src/areas/agent/`; public server behavior belongs behind FastAPI.

## MCP

PolyKit's embedded runtime receives the repository `.mcp.json` explicitly. External coding agents can use the same local MCP server without installing a separate package:

```text
uv run python api/mcp_server.py
```

The MCP server expects the PolyKit FastAPI backend at `http://localhost:8765`.

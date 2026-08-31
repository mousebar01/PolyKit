# MCP development

PolyKit's MCP server is repository code, not a package that should be installed separately into every Agent host.

## One server implementation

The canonical server entry point is:

```bash
uv run python api/mcp_server.py
```

It uses stdio for the MCP transport and expects the PolyKit FastAPI backend at `http://127.0.0.1:8765` for tools that call product APIs.

Start the product backend separately when testing tool execution:

```bash
python api/serve.py --host 127.0.0.1 --port 8765
```

## Host configuration

PolyKit keeps host configuration in the repository:

- `.mcp.json` is the shared declaration used by the embedded PolyKit Agent runtime and hosts that understand this format.
- `.codex/config.toml` points Codex at the same local `api/mcp_server.py` entry point.

Do not add a second copy of the MCP implementation for a specific Agent host. Host files should only adapt configuration/transport syntax.

## Fast smoke test

Test the real MCP stdio handshake and `tools/list` without starting Codex or the PolyKit Agent UI:

```bash
uv run python scripts/mcp_smoke.py
```

This is the first check when MCP discovery is broken. It separates MCP server/transport failures from Agent-host configuration failures.

## Debug order

1. Run `uv run python scripts/mcp_smoke.py`.
2. Start FastAPI and verify `http://127.0.0.1:8765/health` if tool calls need the product API.
3. Check the host's active MCP list.
4. Only then debug Agent prompts or skills.

Keep MCP tools semantic and server-owned. Agent skills decide when to call them; MCP exposes stable PolyKit capabilities rather than embedding a second workflow engine.

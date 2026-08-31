# PolyKit MCP Adapter

`server.py` exposes a small MCP tool surface for external MCP clients while keeping PolyKit's FastAPI API authoritative.

The adapter is intentionally stateless:

```text
MCP client / Inspector
        ↓ stdio MCP
 tools/polykit-mcp/server.py
        ↓ HTTP
     PolyKit FastAPI
        ↓
World domain / WorkflowRun / Node Packs
```

It does **not** own task progression, World validation rules, workflow execution, artifacts, or model state. Those remain server-owned.

## Development

Start PolyKit first:

```bash
uv run python api/serve.py --host 127.0.0.1 --port 8765
```

Then launch the official MCP Inspector against the stdio server:

```bash
npm run mcp:inspect
```

This is equivalent to:

```bash
npx -y @modelcontextprotocol/inspector \
  uv run python tools/polykit-mcp/server.py
```

Use the Inspector to verify `initialize`, `tools/list`, tool schemas, and `tools/call` without installing or running a real Agent client.

Run the adapter's local contract tests with:

```bash
npm run test:mcp
```

Run the stdio server directly with:

```bash
npm run mcp:serve
```

The default API target is `http://127.0.0.1:8765`. Override it for a remote/headless PolyKit server:

```bash
POLYKIT_API_URL=http://gpu-box:8765 npm run mcp:inspect
```

## External Agent validation

The repository-level `.mcp.json` registers the same stdio adapter as `polykit`, so MCP-capable development clients can start it directly from the repository.

The intended validation order is:

```text
FastAPI/unit tests
   ↓
CLI tests
   ↓
MCP adapter tests
   ↓
MCP Inspector
   ↓
real Agent client smoke test
```

A real Agent is useful only for the last step: checking whether the model understands the descriptions and naturally chooses the right tools. Protocol and HTTP routing bugs should be debugged with the Inspector/tests first.

## Tool design rules

- Tools should map directly to existing FastAPI capabilities.
- `polykit_workflow_inspect` is read-only and must never advance, retry, or resume a run.
- World validators report quality facts/evidence; they do not return Agent transitions.
- `polykit_world_compile_repair` is a pure proxy to the ProductionRecipe compiler. It may return `ready`, `blocked`, or `no_workflow`, but it never starts the returned workflow.
- A caller that receives `ready` must inspect the result and separately call `polykit_workflow_execute` if it wants to start the returned execution request.
- Local repair scope widening remains explicit: callers must opt in with `allow_scope_expansion` before the server may compile a broader fallback.
- Long-running work returns canonical `WorkflowRun` ids.
- Do not add Agent session/task state to this adapter.
- Do not move Workflow Engine, validation, ProductionRecipe, or Node Pack logic into MCP handlers.

The current surface includes server health/model discovery, WorkflowRun list/status/inspect/cancel/execute, text-to-asset, mesh processing, schema-v2 World operations, evidence-first visual/spatial validation, and ProductionRecipe repair compilation.

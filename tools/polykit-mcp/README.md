# PolyKit MCP Adapter

`canonical_server.py` is the default Agent-facing MCP transport. It keeps PolyKit's FastAPI/Application APIs authoritative while preserving the stable MCP tool names used by existing clients.

The adapter is intentionally stateless:

```text
MCP client / Inspector
        ↓ stdio MCP
 tools/polykit-mcp/canonical_server.py
        ↓ HTTP
     PolyKit FastAPI
        ↓
Application Commands / Runs / World / Node Packs
```

It does **not** own task progression, World validation rules, execution, artifacts, or model state. Those remain server-owned. `server.py` remains the thin base HTTP adapter used by the canonical transport.

## Development

Start PolyKit first:

```bash
uv run python api/serve.py --host 127.0.0.1 --port 8765
```

Then launch the official MCP Inspector:

```bash
npm run mcp:inspect
```

Run the adapter contract tests with:

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

## Context-efficient Agent reads

The canonical transport deliberately separates lightweight polling from detailed inspection:

```text
polykit_workflow_status
        ↓
compact /runs/{id}?compact=true
        ↓
run_id / status / progress / step / output / error
        ↓
inspect only when evidence or event history is actually needed
```

`polykit_workflow_inspect` returns only the latest 20 events by default. Continue forward with `since_seq=next_event_seq`, or recover older history with `before_seq=previous_event_seq`. `events_limit` is capped at 200 and `include_events=false` can be used when only snapshots/evidence are needed.

Agent Skill discovery also follows progressive disclosure:

```text
polykit_skill_list
        ↓ metadata/frontmatter only
polykit_skill_get
        ↓ full instructions only after selection
polykit_skill_read_resource
        ↓ bounded resource chunk (default 16 KiB)
```

Continue a truncated resource with its returned `next_offset`. Skill scripts are always returned as text and are never executed by PolyKit.

MCP JSON is serialized compactly rather than pretty-printed, avoiding formatting tokens that carry no model information.

## Optional discovery profiles

`POLYKIT_MCP_PROFILE` can reduce the tools placed in the Agent discovery context:

```bash
POLYKIT_MCP_PROFILE=core npm run mcp:serve
POLYKIT_MCP_PROFILE=asset npm run mcp:serve
POLYKIT_MCP_PROFILE=world npm run mcp:serve
POLYKIT_MCP_PROFILE=authoring npm run mcp:serve
```

- `core`: Run control, Workflow definitions, Skill discovery, health/model discovery.
- `asset`: core plus `polykit_asset_*` and `polykit_mesh_*` tools.
- `world`: core plus `polykit_world_*` tools.
- `authoring`: all current authoring tools.
- unset or `all`: expose the full canonical surface.
- unknown values safely fall back to `all`.

Profiles affect **tool discovery only**. They are context controls, not authorization boundaries; the underlying MCP/API capabilities are unchanged.

## External Agent validation

The repository-level `.mcp.json` registers the same canonical stdio adapter as `polykit`, so MCP-capable development clients can start it directly from the repository.

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

- Tools map to existing FastAPI/Application capabilities.
- `polykit_workflow_status` is the lightweight polling tool; do not poll with inspect.
- `polykit_workflow_inspect` is read-only and never advances, retries, or resumes a Run.
- `polykit_workflow_signal` only delivers `{name, payload}` to a server-owned waiting interrupt. FastAPI validates the expected signal and resumes the same `run_id`; MCP does not hold or recreate execution state.
- `polykit_workflow_retry` only asks FastAPI to resume a failed/interrupted Run from durable completed-node checkpoints. It never submits a replacement Run.
- Waiting/retry lifecycle remains owned by the canonical Run/Application layer. MCP does not implement polling loops, checkpoints, retries, or a second pause/resume state machine.
- `polykit_world_validate` mirrors the server validator surface, including `world.visual.validate` and `world.spatial.validate`.
- World validators report quality facts/evidence; they do not return Agent transitions.
- `polykit_world_compile_repair` is a pure proxy to the ProductionRecipe compiler. It may return `ready`, `blocked`, or `no_workflow`, but it never starts the returned plan.
- A caller that receives `ready` inspects the result and separately calls `polykit_workflow_execute` if it wants to submit the returned execution plan.
- Local repair scope widening remains explicit: callers must opt in with `allow_scope_expansion` before the server may compile a broader fallback.
- Long-running work returns canonical Run ids.
- Do not add Agent session/task state to this adapter.
- Do not move Execution Engine, validation, ProductionRecipe, or Node Pack logic into MCP handlers.

A durable approval flow looks like:

```text
polykit_workflow_status
        ↓ waiting
polykit_workflow_inspect(include_events=false)
        ↓ evidence + expected signal
Agent / human judges evidence
        ↓
polykit_workflow_signal
        ↓
FastAPI resumes the same Run
        ↓
polykit_workflow_status
```

The current surface includes server health/model discovery, Run control through stable `polykit_workflow_*` tool names, text-to-asset generation, mesh processing, schema-v2 World operations, evidence-first visual/spatial validation, and ProductionRecipe repair compilation.

External asset discovery is intentionally a separate, two-step surface:

- `polykit_asset_search_external` performs a read-only search of Poly Haven's public model API. It does not change the workspace or create a WorkflowRun.
- `polykit_asset_import_external` downloads one selected model, verifies the declared bundle, normalizes it to a workspace GLB, and records CC0 attribution/provenance. It is an explicit side effect and requires the `asset_id` returned by search.

The local `polykit_world_find_assets` tool remains workspace-only. This keeps
provider results out of the server-owned library listing and makes the Agent's
read-before-write choice explicit.

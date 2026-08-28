# Worlds: Agent-first Three.js presentation

PolyKit's Worlds experiment is a local, server-owned adaptation of the useful
parts of `fal-worldclaw` and the [WorldClaw paper](https://arxiv.org/abs/2608.05248).
The Agent is the world director; Three.js is the presentation layer; the
FastAPI workflow runtime remains the only place that executes model/process
nodes and owns durable artifacts.

## First vertical slice

- `WorldSpec + seed` deterministically compiles to a terrain heightfield.
- Placement rules resolve into terrain-aware instances.
- Scatter prototypes use local low-poly geometry, so the preview works with no
  cloud key and no network request.
- An Agent can save a paper-derived `agent_plan`, record stage progress, submit
  local asset work, and attach the resulting workspace mesh through the MCP
  world tools described in `docs/world-agent.md`.
- A workspace concept image can still be bound to an existing editable workflow
  via `runWorldHero()` and `/workflow-runs/execute`.
- Editable world documents are stored as
  `WORKSPACE_DIR/Workflows/<id>.world.json` and can be loaded again without
  persisting derived heightfields or Three.js objects.

## Intentionally local and incremental

Text-to-image, text-to-texture, VLM critique, panorama generation, and
multi-hero orchestration are added as Agent-directed stages only when matching
local node packs/workflows exist. They should not be reintroduced as browser
calls to a hosted provider. A missing local capability is recorded as a blocked
stage and can be implemented behind the canonical workflow-run API later.

## Relevant modules

| Concern | PolyKit location |
| --- | --- |
| Seeded noise, terrain, placement, procedural geometry | `src/areas/worlds/runtime/` |
| World preview | `src/areas/worlds/components/WorldCanvas.tsx` |
| Editable world state and API adapter | `src/areas/worlds/worldStore.ts`, `worldApi.ts` |
| Existing workflow bridge | `src/areas/worlds/worldWorkflow.ts` |
| World persistence | `api/services/world_store.py`, `api/routers/workspace_worlds.py` |
| Agent planning + MCP tools | `api/services/world_agent.py`, `api/mcp_server.py`, `docs/world-agent.md` |

The external project is kept as a reference checkout outside this repository.
The PolyKit runtime reimplements the deterministic parts instead of copying
cloud-specific code or depending on its unannounced license.

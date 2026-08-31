# Worlds: Three.js presentation

PolyKit's Worlds experiment is a local, server-owned adaptation of useful ideas
from `fal-worldclaw` and the [WorldClaw paper](https://arxiv.org/abs/2608.05248).
Three.js is the presentation and interaction layer; the FastAPI workflow runtime
is the only place that executes model/process nodes and owns durable artifacts.

World building is caller-neutral: Web, CLI, automation, or another HTTP client
may author the same semantic contracts and invoke the same World APIs. There is
no embedded Agent state machine or MCP world-tool layer.

## First vertical slice

- `WorldSpec + seed` deterministically compiles to a terrain heightfield.
- Placement rules resolve into terrain-aware instances.
- Scatter prototypes use local low-poly geometry, so the preview works with no
  cloud key and no network request.
- Schema-v2 world documents keep intent, BuildSpec, ScenePlan, GameSpec, quality
  facts, and workspace artifact references.
- Asset and structure generation is submitted through the canonical
  `/workflow-runs/*` API.
- Editable world documents are stored as
  `WORKSPACE_DIR/Workflows/<id>.world.json` and can be loaded again without
  persisting derived heightfields or Three.js objects.
- Workflow execution state remains in `WorkflowRun`, not in the world document.

## Intentionally local and incremental

Text-to-image, text-to-texture, visual critique, panorama generation, and other
capabilities are added only when matching local Node Packs/workflows exist. They
must not be silently reintroduced as browser calls to hosted providers. Missing
capabilities remain explicit API/workflow gaps until implemented behind the
canonical runtime.

## Relevant modules

| Concern | PolyKit location |
| --- | --- |
| Seeded noise, terrain, placement, procedural geometry | `src/areas/worlds/runtime/` |
| Generated-scene viewer and inspector | `src/areas/worlds/components/WorldAssetViewer.tsx`, `WorldCanvas.tsx` |
| Editable scene state and API adapter | `src/areas/worlds/worldStore.ts`, `worldApi.ts` |
| World domain helpers / persistence | `api/services/world_domain.py`, `api/services/world_store.py` |
| World validation / workflow recipes | `api/services/world_validation.py`, `api/services/world_workflows.py` |
| World HTTP API | `api/routers/workspace_worlds.py`, `api/routers/world_artifacts.py` |
| Automation | `tools/polykit-cli/polykit.py` |

The asset library is the user-facing entry point for generated scenes; there is
no separate Worlds execution runtime. A saved world is inspected through the
same Three.js components and server-owned workspace contracts used elsewhere in
PolyKit.

See [`world-builder.md`](world-builder.md) for the authoritative World Builder
boundary.

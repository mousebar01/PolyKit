# Worlds: Three.js presentation and preview

PolyKit's Worlds experiment is a local, server-owned adaptation of useful ideas
from `fal-worldclaw` and the [WorldClaw paper](https://arxiv.org/abs/2608.05248).
Blender-backed process nodes are the only production modeling path. Three.js is
only a presentation/interaction client for the exported GLB; it is not a
modeling or export runtime. The FastAPI workflow runtime is the only place that
executes model/process nodes and owns durable artifacts.

There are two deliberately different viewport paths:

- `ScenePlanCanvas` loads Blender/GLB assets when they are attached. Its boxes
  are a blockout fallback for an unresolved or unavailable asset.
- `WorldCanvas` only loads attached `mode: "workspace-mesh"` artifacts. It does
  not synthesize terrain, water, grass, or asset geometry in the browser. A
  missing or unloadable GLB is rendered as incomplete/empty and must not be
  treated as a successful build.

The files under `src/areas/worlds/runtime/` still contain deterministic terrain
and placement planning plus low-poly preview/test fixtures. They are not a
production mesh path: their output is never exported, persisted as production
mesh evidence, or used to replace a Blender artifact in `WorldCanvas`.

The server-side `scene-composer` and `mesh-exporter` nodes perform GLB/export
work, including a Blender-backed FBX export when the local Blender executable
is available. They do not delegate modeling to browser Three.js code. This distinction
keeps a fast local blockout useful without allowing a browser placeholder to be
mistaken for the production Blender result.

World building is caller-neutral: Web, CLI, automation, or another HTTP client
may author the same semantic contracts and invoke the same World APIs. There is
no embedded Agent state machine or MCP world-tool layer.

## First vertical slice

- `WorldSpec + seed` deterministically compiles to a terrain heightfield.
- Placement rules resolve into terrain-aware instances.
- A world viewport loads Blender-produced workspace meshes. Missing assets stay
  visibly incomplete instead of being replaced by a browser-generated model.
  Legacy `proceduralHint` values remain schema/test data only and are not used
  to synthesize production geometry.
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
| Seeded noise, terrain and placement planning (plus test-only preview fixtures) | `src/areas/worlds/runtime/` |
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

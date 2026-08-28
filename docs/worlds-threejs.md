# Worlds: local Three.js runtime

PolyKit's Worlds experiment is a local, server-owned adaptation of the useful
parts of `fal-worldclaw`. Three.js is the presentation layer; the FastAPI
workflow runtime remains the only place that executes model/process nodes.

## First vertical slice

- `WorldSpec + seed` deterministically compiles to a terrain heightfield.
- Placement rules resolve into terrain-aware instances.
- Scatter prototypes use local low-poly geometry, so the preview works with no
  cloud key and no network request.
- A workspace concept image can be bound to an existing editable workflow via
  `runWorldHero()` and `/workflow-runs/execute`.
- Editable world documents are stored as
  `WORKSPACE_DIR/Workflows/<id>.world.json` and can be loaded again without
  persisting derived heightfields or Three.js objects.

## Deliberately deferred

Text-to-image, text-to-texture, VLM critique, panorama generation, and
multi-hero orchestration are not part of this slice. They need local node packs
or a server-owned parent run first; they should not be reintroduced as browser
calls to a hosted provider.

## Relevant modules

| Concern | PolyKit location |
| --- | --- |
| Seeded noise, terrain, placement, procedural geometry | `src/areas/worlds/runtime/` |
| World preview | `src/areas/worlds/components/WorldCanvas.tsx` |
| Editable world state and API adapter | `src/areas/worlds/worldStore.ts`, `worldApi.ts` |
| Existing workflow bridge | `src/areas/worlds/worldWorkflow.ts` |
| World persistence | `api/services/world_store.py`, `api/routers/workspace_worlds.py` |

The external project is kept as a reference checkout outside this repository.
The PolyKit runtime reimplements the deterministic parts instead of copying
cloud-specific code or depending on its unannounced license.


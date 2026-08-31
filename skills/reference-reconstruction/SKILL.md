---
name: reference-reconstruction
description: Reconstruct a 3D scene or asset from visual references using PolyKit Worlds, deterministic validation, repair scopes, and WorkflowRuns. Use when matching a reference image, rebuilding a scene from concept art, or iterating toward a visually and spatially validated result.
license: MIT
compatibility: Requires a running PolyKit FastAPI server through the PolyKit MCP adapter or HTTP API.
metadata:
  author: PolyKit
  version: "1"
---

# Reference Reconstruction

Use PolyKit as the execution runtime. This skill chooses production strategy; it does not replace WorkflowRun lifecycle, validation, or artifact state.

## Operating rules

- Keep the World document about what the scene is. Do not write workflow progress, retries, or task stages into it.
- Use PolyKit APIs or MCP tools for generation, scene planning, validation, repair compilation, and workflow execution.
- Do not bypass a blocked ProductionRecipe by directly scripting Blender when PolyKit says the installed backend cannot honor the requested repair scope.
- Treat validator evidence as authoritative. Missing evidence is not a pass.
- Prefer a bounded repair scope over rebuilding the whole scene. Allow scope expansion only when the user or calling Agent explicitly accepts it.

## Workflow

1. Read or create the World and identify the reference target, P0 subjects, camera constraints, and semantic objects.
2. Compile the ScenePlan before expensive generation. Resolve layout and relationship failures first.
3. Build or attach the minimum required assets, then execute work through canonical WorkflowRuns.
4. Validate construction, spatial, visual, and gameplay facts as applicable.
5. When validation fails, select the returned repair scope and compile a ProductionRecipe.
6. Inspect `ready`, `blocked`, or `no_workflow` before executing anything. A compiled recipe never implies permission to run it automatically.
7. Execute only an accepted WorkflowExecutionRequest, then validate the delivered artifacts again.

## Repair order

When matching a visual reference, fix high-causal-impact problems before surface polish: camera/projection, framing and P0 placement, spatial relationships and contacts, silhouette and bounds, material identity, lighting, then secondary color and atmosphere.

## Completion

A reconstruction is complete only when the required validators have acceptable evidence and the final delivered artifact is the one those validators inspected. Preserve WorkflowRun and artifact provenance so another client or Agent can reproduce the result.

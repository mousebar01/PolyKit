---
name: polykit-scene
description: "Plan and compose semantically consistent PolyKit 3D scenes from natural-language briefs."
---

# PolyKit Scene

Use this skill when an Agent must turn a scene brief into an editable PolyKit
scene, place multiple assets, or repair spatial relationships. Do not use it
for a single isolated asset or for a visual-only camera mockup.

## Contract

Produce a `polykit.scene-plan` before composing meshes. The plan is the source
of semantic truth; Blender MCP and Three.js are backends/viewers, not a second
scene runtime.

- Give every object a stable English `id`, human `name`, aliases, category,
  role, and measured size in metres. Never use a filename as an object id.
- Use `room`/`background` containers, `context` set dressing, `hero` focal
  assets, and only the distractors that support the brief.
- Describe relationships instead of guessing camera-facing coordinates. Use
  `floor`, `in_room`, `on`, `inside`, `near`, `beside`, `away_from`, and
  `overlooking`. Add `distance`, `tolerance`, `clearance`, or `side` when the
  brief makes them explicit.
- ScenePlan coordinates are metres, X-right/Y-up/Z-forward. Positions are
  ground/contact points and XYZ rotations are radians. The Blender glTF
  exporter handles its native Z-up to Three.js Y-up conversion.

## Execution

1. Extract the object inventory and a support-first relation graph from the
   brief. A `near` or `beside` edge is secondary to the object's floor/room or
   surface support edge.
2. Call `polykit_world_compile_scene` (or the equivalent
   `/workspace-library/worlds/{id}/scene-plan` route) and let FastAPI solve and
   persist the plan. Do not invent a browser-side layout algorithm.
3. Inspect `metadata.layoutQuality` and diagnostics. It must pass the
   camera-independent checks for scene bounds, support contact, containment,
   declared relations, and pairwise footprints. If it is `invalid`, fix the
   object dimensions/relations or obtain a better asset; never hide the issue
   by changing the camera.
4. Resolve or generate missing assets through the existing canonical workflow,
   write their workspace-relative references back to the matching object, and
   recompile the plan.
5. Submit `polykit_world_compose_scene` only after the plan is valid. Keep the
   resulting GLB and sidecars in the server-owned workspace and let the normal
   asset/Three.js viewers consume them.

For mesh-aware refinement, measure the imported asset bounds and contact
surface in Blender/Three.js, then update the semantic size or support relation;
do not silently alter transforms in a render-only script. See
`docs/scene-planning.md` for the API example and relation vocabulary.

## Material sourcing

Treat [Poly Haven Textures](https://polyhaven.com/textures) as an optional
material-library source during the `materials` stage, not as part of the
scene's visual prompt. Resolve a semantic material intent such as `aged pine
wood`, `painted metal`, `wool`, or `snow` to a named texture set before
applying it. Record the Poly Haven asset id, resolution, map set, UV scale,
and source URL in the scene provenance so the choice is reproducible.

Prefer a local or workspace-cached copy of the selected maps. Apply the maps
through Blender shader nodes (base color, roughness, normal, and optional
height) with a measured real-world texel scale; do not bake a website URL into
an image-generation prompt and do not let a photorealistic texture override
the scene's declared style profile. Poly Haven assets are CC0, but live API
use requires a clear Poly Haven credit and a unique User-Agent, so an online
lookup must be explicit and auditable rather than hidden in a node.

For Blender-backed presets, inspect more than the presentation camera before
accepting a scene. Render or open at least an entry/interior angle, an opposite
interior angle, and an exterior angle. If any of those views exposes an
occluding wall, floating prop, or broken support relation, repair the scene
recipe or semantic plan instead of compensating with the camera.

## Walkthrough validation

When a user asks whether a scene is usable as a game space, use the desktop
walkthrough in the ScenePlan preview as a validation pass. It is deliberately
not a second game runtime: React/Three.js reads the server-owned plan, resets
to a deterministic spawn, and uses semantic primitive AABBs for movement
collisions. Validate the following before calling the scene ready:

- the player can enter the room and move with WASD/arrow keys;
- room bounds stop the player without changing the persisted plan;
- hero/context objects remain in their declared spatial relationships;
- the same plan and seed produce the same spawn and layout.

Do not add combat, physics simulation, navigation meshes, or per-frame scene
mutation to this preview. Those are separate product capabilities and would
move execution state out of FastAPI.

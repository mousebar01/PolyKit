# Mesh production

PolyKit's `mesh-production` process pack keeps a focused set of generic, engine-facing mesh derivatives inside the server-owned workflow runtime.

| Node | Output | Behavior |
| --- | --- | --- |
| `mesh-production/collision-mesh` | GLB mesh + JSON | Builds a whole-scene convex-hull proxy, or a bounding-box proxy when a cheaper collider is preferred. |
| `mesh-production/lod-generate` | LOD0 mesh + LOD1/LOD2 GLB sidecars + JSON | Creates deterministic reduced levels and records the actual face counts and ratios. |
| `mesh-production/projection-bake` | Textured GLB mesh + JSON | Projects a reference image into mesh UVs with an explicit camera model and embeds the texture. |
| `mesh-production/uv-unwrap` | UV-enabled GLB mesh + JSON | Generates explicit seam-safe UV coordinates for downstream texturing and baking. |
| `mesh-production/surface-map-bake` | UV-enabled GLB + normal/AO PNG sidecars + JSON | Produces reviewable surface maps from the current mesh. |
| `mesh-production/geometry-integrity` | Original mesh + JSON | Audits finite geometry, topology defects, boundaries, manifoldness, winding, watertightness, and volume. |
| `mesh-production/self-intersection-audit` | Original mesh + JSON | Detects triangle-triangle self-intersections and reports participating faces without modifying the mesh. |
| `mesh-production/animation-audit` | Original mesh + JSON | Checks glTF/GLB skin bindings, animation channels, and morph-target declarations without mutating the asset. |

Collision output preserves the source world-space frame, but it is an interaction proxy rather than a render asset. LOD output keeps LOD0 as the primary workflow mesh and publishes reduced levels beside it; every level still needs silhouette, UV, material, and animation review.

Projection baking is intentionally single-view: the report exposes vertices outside the camera frustum instead of inventing unseen pixels. UV unwrapping and surface-map baking are production helpers rather than universal atlas or high-to-low baking systems.

`geometry-integrity` and `self-intersection-audit` are read-only gates to run before repair or export. They make topology failures visible while leaving repair to a later modeling step.

`animation-audit` verifies structural glTF declarations and numeric skin buffers where available. It does not claim that weights deform well or that an animation clip looks correct; runtime deformation still needs a smoke test.

Experimental reconstruction, renderer acceleration structures, character morph preparation, joint-topology heuristics, and clothing blockouts are intentionally not part of the built-in mesh-production surface. Those capabilities should return only when a complete user-facing workflow requires them.

All nodes write into the run-private process workspace. The normal workflow sink publishes the primary output and sidecars into the selected collection.

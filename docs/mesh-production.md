# Mesh production

PolyKit's `mesh-production` process pack creates engine-facing derivatives
without leaving the server-owned workflow runtime.

| Node | Output | Behavior |
| --- | --- | --- |
| `mesh-production/collision-mesh` | GLB mesh + JSON | Builds a whole-scene convex-hull proxy, or a bounding-box proxy when a cheaper broad-phase collider is preferred. |
| `mesh-production/lod-generate` | LOD0 mesh + LOD1/LOD2 GLB sidecars + JSON | Runs quadric edge-collapse reduction through the shared `pymeshlab` environment and records the actual face counts and ratios. |
| `mesh-production/projection-bake` | Textured GLB mesh + JSON | Accepts a reference image and target mesh, writes camera-projected UVs, and embeds the source image as a GLB texture. |
| `mesh-production/uv-unwrap` | UV-enabled GLB mesh + JSON | Generates explicit seam-safe UV coordinates with deterministic pymeshlab charts, duplicating vertices at wedges for GLB consumers. |
| `mesh-production/surface-map-bake` | UV-enabled GLB + normal/AO PNG sidecars + JSON | Rasterizes world-space vertex normals and a pymeshlab ambient-occlusion field into reviewable surface maps. |

Collision output preserves the source world-space frame, but it is an
interaction proxy rather than a render asset. LOD output keeps LOD0 as the
primary workflow mesh and publishes the reduced levels beside it; each level
must still be checked for silhouette, UV, material, and animation quality.

Projection baking is a real image-to-UV operation, but it is intentionally
single-view: the report counts vertices outside the camera frustum and marks
the result for visual review. It does not invent back-side pixels or claim
multi-view reconstruction.

UV unwrapping is a production derivative rather than a universal atlas
optimizer. The flat-plane and triangle-trivial methods are deterministic and
portable, but island scale, padding, and distortion still require inspection
before a high-resolution bake.

Surface-map baking produces real PNG sidecars, but its normal map is
world-space and its AO is derived from the same mesh's vertex field. It is not a
tangent-space high-to-low bake; use a dedicated baking runtime when that
production contract is required.

All nodes write into the run-private process workspace. The normal workflow
sink publishes the primary output and sidecars into the selected collection.

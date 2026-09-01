# Mesh production

PolyKit's `mesh-production` process pack creates engine-facing derivatives
without leaving the server-owned workflow runtime.

| Node | Output | Behavior |
| --- | --- | --- |
| `mesh-production/collision-mesh` | GLB mesh + JSON | Builds a whole-scene convex-hull proxy, or a bounding-box proxy when a cheaper broad-phase collider is preferred. |
| `mesh-production/lod-generate` | LOD0 mesh + LOD1/LOD2 GLB sidecars + JSON | Runs quadric edge-collapse reduction through the shared `pymeshlab` environment and records the actual face counts and ratios. |

Collision output preserves the source world-space frame, but it is an
interaction proxy rather than a render asset. LOD output keeps LOD0 as the
primary workflow mesh and publishes the reduced levels beside it; each level
must still be checked for silhouette, UV, material, and animation quality.

Both nodes write into the run-private process workspace. The normal workflow
sink publishes the primary output and sidecars into the selected collection.

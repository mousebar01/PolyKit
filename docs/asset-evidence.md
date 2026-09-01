# Asset evidence

PolyKit ships the official `asset-evidence` process pack for mesh-side checks
that can run without a Blender session. Every operation keeps the original
WorkflowRun and workspace artifact contract: a mesh is the primary output and
the measured facts are published as a JSON sidecar.

| Node | Behavior | Mesh mutation |
| --- | --- | --- |
| `asset-evidence/component-audit` | Lists scene components, world bounds, XY/XZ/YZ footprints, and overlap/near/separate relationships | None; input mesh is copied unchanged |
| `asset-evidence/material-audit` | Records declared PBR channels, source labels, confidence, and missing base-color/roughness gates | None; input mesh is copied unchanged |
| `asset-evidence/normalize-mesh` | Applies target-size scaling, explicit up-axis grounding, and optional horizontal centering | Yes; exports a new GLB and records the transform |
| `asset-evidence/turntable-evidence` | Renders a deterministic 4–12 view contact sheet with camera angles for silhouette/assembly review | No; emits an image artifact and JSON sidecar |
| `asset-evidence/component-id-sheet` | Renders stable flat colors per component across multiple views for object-ID/coverage checks | No; emits an image artifact and JSON sidecar |

The reports are evidence, not visual truth scores. For example, an AABB
overlap can be intentional in a manufactured assembly, and a declared
roughness factor does not prove that the material matches a reference. Use the
reports to decide which components or channels need a later Blender render,
reference comparison, or localized correction.

## Example mesh chain

```text
Load 3D Mesh → Component Audit → Material Audit → Normalize Mesh → Output
                                      └→ Turntable Evidence → Image Output
```

The turntable renderer is a software orthographic projection built on Pillow;
it does not require Matplotlib, a GPU, or a Blender session. Each audit can
also be connected directly to `Preview`. The server keeps
intermediate files run-private until a sink publishes them, and sidecars are
placed beside the published mesh in the selected workspace collection.

# Blender production capabilities

PolyKit now ships the official `blender-production` process pack. These are
bounded, parameterized operations executed by the existing Workflow Engine;
they do not create an Agent session or a second task runtime.

## Operations

| Node | Purpose | Input | Output |
| --- | --- | --- | --- |
| `blender-production/opening` | Exact Boolean door/window opening plus a semantic frame | none | GLB mesh |
| `blender-production/array-stairs` | Array-based stair flight with optional rails | none | GLB mesh |
| `blender-production/curve-profile` | Beveled curve for cable, trim or railing | none | GLB mesh |
| `blender-production/geometry-nodes` | Reusable instance-based Geometry Nodes strip | none | GLB mesh |
| `blender-production/assembly` | Independent parts, connector metadata, seams and gap | none | GLB mesh |
| `blender-production/surface` | Wood, metal, concrete, glass, water or fabric material | mesh | GLB mesh |
| `blender-production/lighting` | Three-point, daylight or dramatic inspection lighting | mesh | GLB mesh |
| `blender-production/deform` | Bounded Bend, Twist or Taper modifier | mesh | GLB mesh |
| `blender-production/simulation-setup` | Cloth or rigid-body setup with optional bake attempt | mesh | GLB mesh |
| `blender-production/npr` | Eevee/Cycles toon material and editable outline hull | mesh | GLB mesh |
| `blender-production/geometry-report` | Non-manifold, loose-vertex, zero-area and count facts | mesh | GLB mesh + JSON sidecar |

Mesh operations publish a GLB as the primary artifact and may publish a Blend
file and a render preview as sidecars. The output metadata records the chosen
operation and its measured/configured facts. `geometry-report` is intentionally
read-only: it republishes the input mesh with a JSON report sidecar; a caller or
World validator decides whether a repair or another WorkflowRun is appropriate.

The pack uses the same Blender bridge contract as `blender-scene/build`:
configure `POLYKIT_BLENDER_MCP_HOST`/`POLYKIT_BLENDER_MCP_PORT` (or node
parameters) and execute through `/workflow-runs/*`. MCP only proxies that API.

## Example workflow fragment

```json
{
  "id": "stairs",
  "class_type": "blender-production/array-stairs",
  "inputs": {
    "params": {
      "steps": 14,
      "run": 0.3,
      "rise": 0.18,
      "railings": true
    }
  }
}
```

For an imported asset, connect a `polykit.mesh` source to `surface`,
`lighting`, `deform`, `simulation-setup`, `npr`, or `geometry-report`. The
server owns path validation, cancellation, output naming and durable run
telemetry. When Blender is reached over EasyTier or another non-shared host,
the pack transfers connected meshes inline up to 64 MiB; larger inputs must be
available through a shared workspace path.

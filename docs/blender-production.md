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
| `blender-production/npr` | Per-material Eevee Shader-to-RGB + inverted hull, optional Freestyle structure lines, or Cycles four-ray Shader Raycast + Toon BSDF | mesh | GLB mesh |
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

## NPR rendering contract

The Eevee route uses a constant three-band `Shader to RGB` palette per authored
material color and one reusable Geometry Nodes inverted-hull group per run. The
original material slots remain intact; NPR variants are appended and assigned
to polygons so the operation can be reversed without losing material identity.
The outline isolates the extruded top faces, flips their normals, assigns a
backface-culled outline material, and joins the result with the source geometry.
Position-driven noise and normal-projected wobble are exposed as bounded node
inputs. `line_mode=structure` adds Freestyle crease and material-boundary lines;
`hybrid` combines those lines with the silhouette shell.

The Cycles route requires Blender 5.2 or newer and uses one reusable
`ShaderNodeRaycast` sample group instantiated at camera-plane offsets `+X`,
`-X`, `+Y`, and `-Y`. The offset is transformed from camera to world as a
vector, the incoming ray is reversed toward the camera, and ray length is
bounded by the node parameter. The look group multiplies the four hit masks
and mixes an unlit outline with a Cycles `Toon BSDF` fill. If the
runtime does not expose `ShaderNodeRaycast`, the node fails with an explicit
request to use Eevee; it does not silently claim a Cycles NPR result.

`replace_material` is opt-in; the default is
`preserved_with_toon_variant`.

The renderer-native NPR graph is authoritative in the `.blend` and PNG
sidecars. The preview is rendered before the export-only compatibility pass;
the GLB then restores authored material slots and flattens them to portable
Principled materials (the outline shell may remain as geometry). Consumers
that do not support Shader-to-RGB or Shader Raycast should use the base
mesh/material rather than assuming the toon look survived glTF export.

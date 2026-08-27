# WorldClaw Terrain Prototype

This experiment is intentionally Blender-first. It validates the terrain capability before any PolyKit runtime, workflow, or MCP integration is designed.

## What is implemented

- Semantic regions represented by signed-distance fields.
- Normalized soft region masks using a stable softmax.
- Per-region height functions with base elevation, FBM detail, and ridged mountain structure.
- Polyline river regions with a controllable channel cut.
- A regular Blender terrain mesh generated directly with `bpy`.
- One `mask_<region-id>` float attribute per semantic region on the terrain mesh.
- A simple material whose vertex colors are blended from the same semantic weights.
- Three deterministic diagnostic cameras: perspective, top-down, and low-angle.
- EEVEE diagnostic rendering to PNG.

The first reference world is 1024 m square and contains northern mountains, central grassland, a north-to-south river, and a southern plain.

## Blender 5.2 validation

Clone or copy the PolyKit repository to the Windows machine that runs Blender. In Blender's Python Console, add the repository root to `sys.path` and build the demo:

```python
import sys
sys.path.insert(0, r"C:\path\to\PolyKit")

from experiments.worldclaw_terrain.demo import build_demo
terrain = build_demo()
```

The viewport should now contain:

- `WorldClawTerrain`
- `WorldClawTerrain_Sun`
- three `WorldClawTerrain_Camera_*` cameras

The terrain object's Mesh Data > Attributes should include:

```text
mask_south_plain
mask_central_grassland
mask_north_mountains
mask_north_south_river
TerrainColor
```

Render the fixed diagnostic views:

```python
paths = terrain.render_diagnostics()
print(paths)
```

When no output directory is supplied, renders are written under the system temporary directory in `polykit_worldclaw_terrain`.

To choose an explicit Windows directory:

```python
paths = terrain.render_diagnostics(r"D:\worldclaw-renders", resolution=768)
```

## Iterative editing

The prototype is deliberately mutable so an agent can later issue the same edits through Blender's official MCP Python execution tool.

For example:

```python
mountain = terrain.get_region("north_mountains")
mountain.ridge_strength = 125.0
mountain.noise_amplitude = 38.0
terrain.rebuild()
terrain.render_diagnostics()
```

Widen the transition between a semantic region and its neighbors:

```python
mountain.blend_width = 110.0
terrain.rebuild()
```

Deepen or widen the river:

```python
river = terrain.get_region("north_south_river")
river.channel_depth = 20.0
river.width = 125.0
terrain.rebuild()
```

## Faster smoke test

Use a lower terrain resolution while tuning parameters:

```python
from experiments.worldclaw_terrain.demo import build_demo
terrain = build_demo(resolution=129)
```

The intended progression is `129 -> 257 -> 513` only after the terrain composition is visually stable.

## Scope

This experiment does **not** yet implement Geometry Nodes scattering, image-generated semantic layouts, PolyKit artifacts/workflows, an MCP server, or a Blender network bridge. The official Blender MCP can later execute this module in Blender; the terrain math and scene construction are kept here so the agent changes parameters instead of rewriting low-level mesh code on every iteration.

# WorldClaw Terrain Prototype

Blender-first terrain capability experiment inspired by WorldClaw. This folder is intentionally independent of PolyKit runtime integration: the goal is to validate terrain representation, geomorphic operators, procedural materials, rendering, and later MCP control directly in Blender 5.2+.

## What v2 adds

The first version proved semantic soft masks plus regional height blending. v2 keeps that representation and adds:

- replacement regions and additive geomorphic overlays;
- `VolcanoRegion` with cone, noisy rim, and caldera depression;
- `LavaFlowRegion` with additive flow thickness, incision, cooled levees, and a center-weighted heat profile;
- derived POINT attributes: `height01`, `slope01`, `lava_heat`, `ash_mask`, and `rock_mask`;
- procedural volcanic material driven by those attributes;
- basalt/ash color variation, Voronoi cracks, micro-noise bump, variable roughness, and lava emission;
- compositor glow for diagnostic renders when supported by the Blender build;
- optional lightweight linked-mesh rock scatter;
- four deterministic diagnostic cameras: perspective, top, low, detail.

The same semantic information therefore drives geometry, materials, and scatter rather than becoming a one-off vertex color.

## Blender 5.2 quick test

From Blender's Python Console, add the PolyKit repository root to `sys.path`:

```python
import sys
sys.path.insert(0, r"C:\path\to\PolyKit")
```

Build the volcanic stress-test at a quick resolution first:

```python
from experiments.worldclaw_terrain.demo import build_volcano_demo

terrain = build_volcano_demo(resolution=129)
```

Then inspect `WorldClawTerrain_DeathMountain` in Mesh Data > Attributes. It should contain:

```text
mask_volcanic_plain
mask_main_volcano
mask_lava_west
mask_lava_south
mask_lava_east
height01
slope01
lava_heat
ash_mask
rock_mask
TerrainColor
```

Render four diagnostic views:

```python
paths = terrain.render_diagnostics(
    r"D:\worldclaw-volcano-v2",
    resolution=896,
)
print(paths)
```

If 129 is stable, rebuild at 257:

```python
terrain = build_volcano_demo(resolution=257)
```

## Optional rock scatter

The scatter implementation intentionally uses ordinary linked Blender objects for easy inspection. It is a prototype, not the production million-instance solution.

```python
rocks = terrain.scatter_rocks(
    count=180,
    min_rock_mask=0.42,
    max_lava_heat=0.18,
)
print(len(rocks))
```

Or enable it while building:

```python
terrain = build_volcano_demo(
    resolution=257,
    scatter_rocks=True,
    rock_count=160,
)
```

## Useful live edits

The prototype is intentionally mutable so an MCP-driven agent can eventually perform the same edits.

Change the crater:

```python
volcano = terrain.get_region("main_volcano")
volcano.crater_depth = 120.0
volcano.rim_height = 52.0
volcano.rim_width = 24.0
terrain.rebuild()
```

Make the west flow hotter and more raised:

```python
lava = terrain.get_region("lava_west")
lava.heat_strength = 1.0
lava.flow_thickness = 12.0
lava.levee_height = 8.0
terrain.rebuild()
```

Tune the material without changing geometry:

```python
terrain.volcanic_material.emission_strength = 10.0
terrain.volcanic_material.bump_strength = 0.55
terrain.volcanic_material.crack_scale = 25.0
terrain.rebuild()
```

## Surface-field meaning

- `height01`: normalized terrain height.
- `slope01`: local slope normalized against roughly 62 degrees.
- `lava_heat`: semantic thermal field from hot regions/flow centerlines.
- `ash_mask`: ash affinity reduced by heat and steep slopes, with broad deterministic modulation.
- `rock_mask`: semantic rock affinity reinforced on steeper exposed surfaces.

These are intentionally explicit mesh attributes. They are the inspectable intermediate representation that later material, scatter, Blender MCP, and agent feedback loops can share.

## Files

```text
noise.py       deterministic dependency-free CPU noise
regions.py     semantic regions + volcano/lava geomorphic operators
surface.py     height/slope/heat/ash/rock field derivation
materials.py   generic and volcanic Blender shader graphs
scatter.py     lightweight linked rock instances
terrain.py     mesh build, attributes, material, lighting, cameras, renders
demo.py        general terrain and volcanic stress-test scenes
```

## Current limits

This is still a capability prototype. It does not yet implement hydraulic erosion, texture baking, vegetation ecology, production LOD/chunking, large-scale Geometry Nodes scattering, or PolyKit/MCP runtime integration. Those should follow only after the generated terrain/material quality is good enough in direct Blender tests.

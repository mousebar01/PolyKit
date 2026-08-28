# WorldClaw Terrain Prototype

Blender-first terrain capability experiment inspired by WorldClaw. This folder is intentionally independent of PolyKit runtime integration: the goal is to validate terrain representation, geomorphic operators, procedural materials, rendering, scatter, and later MCP control directly in Blender 5.2+.

## Target art direction

The primary target is now **stylized third-person 3D game terrain**, not photorealistic environment reconstruction. Think in terms of colorful adventure/platforming worlds: strong landmark silhouettes, readable traversal spaces, clean biome color groups, exaggerated hazards, and controlled procedural detail.

That changes several priorities:

- large landform rhythm matters more than micro-geological accuracy;
- playable shelves, paths, cliffs, rivers, lava routes, and landmarks must read from gameplay cameras;
- semantic colors should stay visible instead of being buried under dark realistic textures;
- surface noise is restrained on plains/grass and concentrated on rock/hazard surfaces;
- the same semantic fields should be reusable for gameplay-aware placement, not just rendering.

The existing detailed volcanic shader remains available as an A/B reference. The new stylized path reuses the same WorldClaw geometry and surface-field representation rather than replacing it.

## Shared terrain representation

The current terrain stack includes:

- replacement regions and additive geomorphic overlays;
- `VolcanoRegion` with cone, noisy rim, and caldera depression;
- `LavaFlowRegion` with additive flow thickness, incision, cooled levees, and a center-weighted heat profile;
- derived POINT attributes: `height01`, `slope01`, `lava_heat`, `ash_mask`, and `rock_mask`;
- optional lightweight linked-mesh rock scatter;
- four deterministic diagnostic cameras: perspective, top, low, detail.

The same semantic information therefore drives geometry, materials, scatter, and later agent/gameplay reasoning.

## Two material paths

### Detailed volcanic reference

`build_volcano_demo()` uses the existing basalt/ash/lava shader with stronger micro-noise, Voronoi cracks, variable roughness, emission, and dark volcanic lighting. Keep this as the detail-heavy comparison scene.

### Stylized game path

`build_stylized_volcano_demo()` uses `StylizedTerrain` and `StylizedMaterialSettings`:

- stepped low-frequency color bands preserve broad graphic color masses;
- semantic `TerrainColor` stays the primary palette source;
- slope + `rock_mask` expose clean rock groups on cliffs;
- `ash_mask` adds a broad matte layer without covering every surface;
- highlands receive a restrained color lift;
- Noise/Voronoi detail is mild and fractures are limited to rocky/hot surfaces;
- lava uses a dark-edge -> red -> orange -> yellow-white heat ramp with actual emission;
- brighter sun + area fill + colored world light improve third-person readability.

## Gameplay-oriented fields

`StylizedTerrain` adds two inspectable POINT attributes after the normal terrain build:

```text
hazard_mask
traversable_mask
```

They are **not** a replacement for a game-engine navmesh. They are cheap world-generation hints:

- `hazard_mask` combines thermal hazards with semantic water/river/ocean-like regions;
- `traversable_mask` prefers moderate/flat slopes and suppresses hazards while allowing broad rocky plateaus.

Later these can guide roads, platforms, props, encounters, collectible placement, camera targets, or scene-planning agents before a final navigation bake.

## Blender 5.2 quick test

From Blender's Python Console, add the PolyKit repository root to `sys.path`:

```python
import sys
sys.path.insert(0, r"C:\path\to\PolyKit")
```

### Recommended stylized stress test

Start at 129 terrain resolution:

```python
from experiments.worldclaw_terrain.demo import build_stylized_volcano_demo

terrain = build_stylized_volcano_demo(resolution=129)
```

The main object should be:

```text
WorldClawTerrain_StylizedVolcano
```

Expected companion objects include the Sun, Fill light, and four diagnostic cameras.

Inspect Mesh Data > Attributes. Besides `mask_<region-id>`, it should contain:

```text
height01
slope01
lava_heat
ash_mask
rock_mask
hazard_mask
traversable_mask
TerrainColor
```

Render the diagnostic views:

```python
paths = terrain.render_diagnostics(
    r"D:\worldclaw-stylized-volcano",
    resolution=896,
)
print(paths)
```

If 129 is stable and the composition is good, rebuild at 257:

```python
terrain = build_stylized_volcano_demo(resolution=257)
```

Only move to 513 after the art direction and landform composition are stable.

### Detailed volcanic comparison

```python
from experiments.worldclaw_terrain.demo import build_volcano_demo
reference = build_volcano_demo(resolution=129)
```

This lets you compare stylized readability against the existing detail-heavy volcanic material without changing the terrain framework.

## What to judge in the stylized test

Do **not** judge it primarily on realism. Check these instead:

1. The volcano reads as one dominant landmark from the perspective/low cameras.
2. The crater is immediately legible as a bowl with a rim, not just a noisy peak.
3. There are broad shelves/shoulders that look usable for third-person movement.
4. Lava follows the terrain and reads as a hazard route rather than a flat orange stripe.
5. Lava has a bright core and cooler/darker edges.
6. Plains and broad playable surfaces stay visually clean instead of receiving dense cracks everywhere.
7. Cliff faces move toward a coherent rock color group.
8. The top view makes semantic regions and hazard routes understandable at a glance.
9. `traversable_mask` broadly corresponds to places you would plausibly route a player through.
10. Micro detail supports the silhouette instead of competing with it.

## Optional rock scatter

The current scatter intentionally uses ordinary linked Blender objects for easy inspection. It is a prototype, not the production million-instance solution.

For the stylized scene:

```python
terrain = build_stylized_volcano_demo(
    resolution=257,
    scatter_rocks=True,
    rock_count=80,
)
```

Or after building:

```python
rocks = terrain.scatter_rocks(
    count=90,
    min_rock_mask=0.46,
    min_scale=2.0,
    max_scale=7.5,
)
print(len(rocks))
```

## Useful live edits

The prototype is mutable so an MCP-driven agent can eventually perform the same changes.

Change the stylized volcano landmark:

```python
volcano = terrain.get_region("main_volcano")
volcano.cone_height = 270.0
volcano.crater_depth = 118.0
volcano.rim_height = 38.0
volcano.terrace_strength = 0.14
terrain.rebuild()
```

Change a lava route:

```python
lava = terrain.get_region("lava_south")
lava.width = 80.0
lava.heat_strength = 1.0
lava.incision_depth = 5.2
lava.levee_height = 5.0
terrain.rebuild()
```

Change the stylized material without touching geometry:

```python
terrain.style.bump_strength = 0.18
terrain.style.lava_emission_strength = 7.8
terrain.style.rock_mix_strength = 0.90
terrain.style.macro_scale = 3.0
terrain.rebuild()
```

## Surface-field meaning

- `height01`: normalized terrain height.
- `slope01`: local slope normalized against roughly 62 degrees.
- `lava_heat`: semantic thermal field from hot regions/flow centerlines.
- `ash_mask`: ash affinity reduced by heat and steep slopes, with broad deterministic modulation.
- `rock_mask`: semantic rock affinity reinforced on steeper exposed surfaces.
- `hazard_mask`: stylized-generation hazard hint from lava/water-like semantics.
- `traversable_mask`: stylized-generation walkability preference from slope + hazards.

These explicit mesh attributes are the inspectable intermediate representation that later material, scatter, Blender MCP, and agent feedback loops can share.

## Files

```text
noise.py                deterministic dependency-free CPU noise
regions.py              semantic regions + volcano/lava geomorphic operators
surface.py              height/slope/heat/ash/rock field derivation
materials.py            generic and detailed volcanic Blender shader graphs
stylized_materials.py   game-oriented semantic stylized shader
stylized.py             StylizedTerrain, gameplay masks, stylized volcano operator
scatter.py              lightweight linked rock instances
terrain.py              shared mesh build, attributes, lighting, cameras, renders
demo.py                 general, detailed volcanic, and stylized volcanic scenes
```

## Current limits

This is still a capability prototype. It does not yet implement a true navigation mesh, authored road/path solver, vegetation ecology, production LOD/chunking, texture baking, large-scale Geometry Nodes scattering, game-engine export rules, or PolyKit/MCP runtime integration. Those should follow after the stylized terrain quality and gameplay readability are good enough in direct Blender tests.

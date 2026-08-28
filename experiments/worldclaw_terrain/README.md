# WorldClaw Terrain Prototype

This experiment is intentionally Blender-first. It validates terrain-generation capability before PolyKit runtime, workflow, or MCP integration is designed.

## Art direction

The target is **stylized third-person 3D game terrain**, not photorealism. The system should support colorful adventure/platforming worlds with:

- strong landmark silhouettes and large readable landforms;
- broad semantic color groups rather than scanned PBR realism;
- clear traversable flats, slopes, cliffs, rivers, lava, and biome transitions;
- moderate graphic surface detail that does not destroy silhouette readability;
- reusable semantic fields for later gameplay-aware scattering and object placement.

The implementation therefore keeps the WorldClaw idea of shared semantic masks, while deliberately using stylized color banding, slope-based rock exposure, controlled bump, and exaggerated lava emission.

## What is implemented

### Terrain representation

- Semantic regions represented by signed-distance fields.
- Normalized soft region masks using a stable softmax.
- Base-region height composition with base elevation, FBM, ridged structure, and optional terraces.
- Overlay height operators for rivers/lava, so a narrow channel modifies the existing mountain instead of flattening it toward another absolute height.
- `VolcanoRegion` with a readable cone, crater bowl, raised rim, ridged detail, and terraces.
- `LavaSplineRegion` with a shallow channel cut and raised cooling levees.

### Mesh attributes

The generated Blender mesh stores:

```text
mask_<region-id>   semantic soft mask
height01           normalized terrain height
slope01            normalized finite-difference slope (60 deg -> 1)
lava_heat          continuous hot-core / cooling-edge signal
ash_mask           broad volcanic ash/dust accumulation signal
TerrainColor       blended semantic base color
```

These fields are intended to be reused later for materials, Geometry Nodes scattering, gameplay/navigation constraints, and MCP inspection.

### Stylized material

The procedural shader is built directly in Blender and uses:

- semantic `TerrainColor` as the main art-directed color source;
- stepped low-frequency Noise for broad graphic color variation;
- `slope01` for clean rock exposure on steep faces;
- `ash_mask` for matte dust/ash layering;
- Noise + Voronoi for restrained rock/fracture bump;
- `lava_heat` for a dark-edge -> red -> orange -> yellow-white lava temperature ramp;
- hot fissure emphasis and actual Principled emission, not only orange base color;
- variable roughness with hot lava smoother and ash more matte.

The material aims for game readability rather than physically accurate volcanic rock.

## Blender 5.2 validation

Clone/copy PolyKit to the Windows machine running Blender. In Blender's Python Console:

```python
import sys
sys.path.insert(0, r"C:\path\to\PolyKit")
```

### General stylized terrain

```python
from experiments.worldclaw_terrain.demo import build_demo
terrain = build_demo(resolution=129)
```

### Stylized volcanic stress test

This is the main material/terrain validation scene:

```python
from experiments.worldclaw_terrain.demo import build_stylized_volcano_demo
terrain = build_stylized_volcano_demo(resolution=129)
```

Expected scene objects include:

```text
WorldClawTerrain_StylizedVolcano
WorldClawTerrain_StylizedVolcano_Sun
WorldClawTerrain_StylizedVolcano_Fill
WorldClawTerrain_StylizedVolcano_Camera_Perspective
WorldClawTerrain_StylizedVolcano_Camera_Top
WorldClawTerrain_StylizedVolcano_Camera_Low
```

Select the terrain and inspect **Mesh Data > Attributes**. In addition to the region masks, confirm these exist:

```text
height01
slope01
lava_heat
ash_mask
TerrainColor
```

Render the fixed diagnostic views:

```python
paths = terrain.render_diagnostics(
    r"D:\worldclaw-stylized-volcano",
    resolution=768,
)
print(paths)
```

Use `129` terrain resolution for fast iteration, then `257`, and only move to `513` after the composition/material behavior is stable.

## What to look for in the volcanic test

The first pass should be judged on game-world readability rather than realism:

1. The volcano should read as one dominant landmark from the perspective camera.
2. The crater should be visibly lower than its rim instead of looking like a noisy mountain peak.
3. Lava should follow the mountain surface and cut shallow channels instead of creating giant absolute-height trenches.
4. Lava should have a brighter center and darker cooling edges, with emission visible in rendered views.
5. Steep mountain faces should shift toward a coherent dark-rock group.
6. Upper/flatter volcanic surfaces should receive a softer ash layer.
7. Noise/Voronoi detail should add surface language without making the silhouette look photogrammetric or overly noisy.

## Iterative editing

The prototype remains mutable so a future agent can issue the same edits through Blender's official MCP Python execution tool.

Change the volcano silhouette:

```python
volcano = terrain.get_region("main_volcano")
volcano.cone_height = 270.0
volcano.crater_depth = 115.0
volcano.rim_height = 34.0
terrain.rebuild()
```

Make the main lava channel broader/deeper:

```python
lava = terrain.get_region("lava_south")
lava.width = 82.0
lava.channel_depth = 8.0
lava.levee_height = 5.5
terrain.rebuild()
```

Tune the art style without changing terrain semantics:

```python
terrain.style.bump_strength = 0.18
terrain.style.lava_emission_strength = 7.5
terrain.style.slope_rock_strength = 0.88
terrain.rebuild()
```

`StylizedTerrainStyle` is deliberately separate from region geometry so future grassland, desert, snow, floating-island, or fantasy-biome presets can reuse the same terrain representation with different visual language.

## Scope

This experiment still does **not** implement Geometry Nodes asset scattering, image-generated semantic layouts, PolyKit artifacts/workflows, an MCP server, or a Blender network bridge. Those should come after the Blender-side terrain capability is visually stable.

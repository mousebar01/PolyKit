# WorldClaw Grassland Experiment

This branch tests a lightweight natural-environment layer inspired by projects such as `three-stylized`: readable terrain, large-scale instanced grass, wind, sparse flowers, rocks, shrubs, and clean lighting.

It intentionally does **not** try to solve caves, cities, hero assets, photorealism, or the full PolyKit runtime. The benchmark question is simpler:

> Does a modest terrain feel like a place a third-person character could run through once functional vegetation and environmental scale cues are present?

## What it builds

- 700–1000 m rolling terrain with a broad traversal valley, meadow and landmark ridges.
- `grass_mask`, `rock_mask`, `valley_mask`, `TerrainColor` mesh attributes.
- Geometry Nodes environment modifier.
- Three ultra-light grass blade variants, instanced across `grass_mask`.
- Scene-time-driven spatial wind field.
- Sparse flowers, low-poly rocks and shrubs using separate scatter chains.
- Terrain material with clean macro variation and restrained bump.
- Hero, low, detail and orthographic top cameras.

The grass is intentionally simple. The target is game-world readability and environmental completeness, not botanical realism.

## Blender 5.2 validation

Checkout the branch:

```bash
git fetch origin
git switch worldclaw-grassland
```

Restart Blender before the first test so old imported Python modules are not cached. In the Python Console:

```python
import sys
sys.path.insert(0, r"C:\path\to\PolyKit")

from experiments.grassland import build_grassland_demo

result = build_grassland_demo(quality="preview")
```

Expected main object:

```text
GrasslandTerrain
```

Expected modifier:

```text
GrasslandEnvironment
```

Expected mesh attributes:

```text
grass_mask
rock_mask
valley_mask
TerrainColor
```

The source asset collections are kept around z=-10000 so Geometry Nodes can evaluate them without placing the originals in the visible world.

## Diagnostic renders

```python
from experiments.grassland.demo import render_diagnostics

paths = render_diagnostics(
    result,
    r"D:\grassland-preview",
)
print(paths)
```

This renders:

```text
grassland_hero.png
grassland_low.png
grassland_detail.png
grassland_top.png
```

The **low** and **detail** frames matter most. The top view is only for checking biome/scatter distribution.

## Wind check

The grass and flowers use Scene Time, so changing the frame should change the wind phase.

```python
from experiments.grassland.demo import render_wind_preview

render_wind_preview(
    result,
    r"D:\grassland-wind",
    frames=(1, 35, 70),
)
```

If the three detail frames are identical, report that as a Geometry Nodes wind bug.

## Higher density pass

Once preview works:

```python
result = build_grassland_demo(quality="quality")
```

The quality preset uses a 1000 m terrain, 321x321 terrain vertices and roughly 1.55 grass points per square meter before mask filtering. Since grass is instanced, this is intentionally much denser than the old object-scatter prototypes.

## Useful direct tuning

```python
from experiments.grassland.config import GrasslandConfig
from experiments.grassland import build_grassland_demo

cfg = GrasslandConfig.preview(seed=81)
cfg.grass_density = 1.1
cfg.grass_height = 1.2
cfg.wind_strength = 0.25
cfg.flower_density = 0.018
cfg.shrub_density = 0.0025

result = build_grassland_demo(cfg)
```

## Validation criteria

Do not judge this as a photoreal landscape. Judge these questions:

1. From the low camera, does the world stop feeling empty?
2. Does grass density communicate traversable ground without hiding the terrain shape?
3. Are steep/high areas naturally less grassy and more rocky?
4. Do flowers and shrubs appear as accents rather than uniform noise?
5. Does wind make the field feel alive without bending grass absurdly?
6. Can you imagine placing a controllable character in the meadow immediately?

If these fail, fix this layer before adding forests, snow, roads or more complicated terrain backends.

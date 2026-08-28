# Infinigen Terrain Branch

This branch evaluates **real Infinigen terrain generation** as an alternative terrain backend for the WorldClaw reproduction work.

It intentionally does **not** copy Infinigen source code into PolyKit. Infinigen stays an external pinned BSD-3-Clause dependency. The bridge only calls its public terrain APIs and exports a small portable terrain package.

Pinned upstream revision: `3f58bb886bb1bda681d41240344fe3126ac0e9bd`.

## Why Linux generation + Windows Blender import

Current upstream Infinigen documentation marks:

- Linux x86_64 Terrain CPU: supported;
- Windows x86_64 Terrain CPU: unsupported;
- Windows WSL2 Terrain CPU: experimental.

That matches the PolyKit setup well:

```text
Linux
  Infinigen terrain generation
  ANT Landscape / MultiMountains / erosion
        |
        | .npz + .json terrain package
        v
Windows
  Blender 5.2
  import / render / inspect / later MCP refinement
```

The Windows machine therefore does not need to compile Infinigen terrain.

## What is being tested

This is deliberately more demanding than the earlier single-volcano prototype. The Infinigen land-tile generator exposes real upstream terrain presets including:

- `multi_mountains`
- `canyon`
- `canyons`
- `cliff`
- `mesa`
- `mountain`
- `river`
- `volcano`
- `coast`

The recommended first benchmark is `multi_mountains` at 1024 source resolution. The benchmark suite additionally generates `canyons`, `cliff`, and `river` so we can judge whether the backend has enough terrain vocabulary for a large adventure-game world.

## 1. Linux: prepare Infinigen

First switch to this branch:

```bash
git fetch origin
git switch worldclaw-infinigen-terrain
```

Clone the pinned Infinigen source:

```bash
bash experiments/infinigen_terrain/bootstrap_linux.sh --clone-only
```

Infinigen terrain has native Linux dependencies. Install the dependencies listed by upstream Infinigen, then let the helper create/install the dedicated conda environment:

```bash
bash experiments/infinigen_terrain/bootstrap_linux.sh --install
conda activate infinigen-polykit
```

The helper intentionally does not run `sudo apt install` automatically.

## 2. Linux: generate the first terrain

From the PolyKit repository root:

```bash
PYTHONPATH="$PWD" python -m experiments.infinigen_terrain.generate \
  --preset multi_mountains \
  --resolution 1024 \
  --seed 73
```

Default output:

```text
.artifacts/infinigen-terrain/
  infinigen_multi_mountains_seed73_r1024.npz
  infinigen_multi_mountains_seed73_r1024.json
```

The package stores:

- raw Infinigen height field;
- eroded height field when upstream emits one;
- erosion mask when available;
- tile size and source resolution;
- source metadata.

Generate the harder comparison suite:

```bash
PYTHONPATH="$PWD" python -m experiments.infinigen_terrain.generate \
  --benchmark \
  --resolution 1024 \
  --seed 73
```

This generates four real upstream terrain families with successive seeds:

```text
multi_mountains
canyons
cliff
river
```

Use `--force` to rebuild Infinigen's cached source fields.

## 3. Move the package to Windows

Copy the generated `.npz` and matching `.json` files to the Windows machine running Blender 5.2. A shared folder is fine.

## 4. Windows Blender 5.2: import

Update the same PolyKit branch on Windows, then open Blender's Python Console:

```python
import sys
sys.path.insert(0, r"C:\path\to\PolyKit")

from experiments.infinigen_terrain.blender_import import import_terrain_package

result = import_terrain_package(
    r"D:\terrain\infinigen_multi_mountains_seed73_r1024.npz",
    target_resolution=513,
    prefer_eroded=True,
)
```

`target_resolution=513` gives roughly 263k vertices and is the recommended visual-quality test. Use 257 first if Blender becomes slow.

The imported mesh receives evaluation attributes:

```text
height01
slope01
curvature01
rock_mask
traversable_mask
erosion_mask       # when upstream emitted it
TerrainColor
```

The material is intentionally simple. We want to judge the terrain geometry, not hide weak geometry behind a complicated shader.

## 5. Render fixed diagnostics

```python
from experiments.infinigen_terrain.blender_import import render_diagnostics

paths = render_diagnostics(
    result,
    r"D:\terrain\infinigen-renders",
    resolution=1024,
)
print(paths)
```

Views:

```text
perspective
top
low
detail
```

## 6. Import a benchmark directory side by side

After generating the full Linux benchmark and copying all `.npz` files to Windows:

```python
from experiments.infinigen_terrain.blender_import import import_directory

results = import_directory(
    r"D:\terrain\benchmark",
    target_resolution=257,
)
```

This is useful for quickly comparing Infinigen's mountain/canyon/cliff/river vocabulary in one Blender file.

## Evaluation criteria

For this branch, do **not** mainly judge material realism. Judge whether the geometry has enough structure for a Breath-of-the-Wild-like adventure world:

1. coherent ridge chains rather than isolated noise bumps;
2. readable valleys and drainage structure;
3. convincing cliffs / mesas / canyon walls;
4. multi-scale detail: macro silhouette plus mid-scale landform detail;
5. enough broad traversable surfaces between steep areas;
6. terrain that remains interesting from top, low, and player-like perspective views;
7. erosion that improves landform structure instead of only adding noisy grooves.

If Infinigen wins these tests, the next step is not to keep its full scene runtime. We would extract the useful terrain operators/fields behind a smaller Blender/PolyKit-facing interface.

## Upstream facts relevant to this experiment

Infinigen's current land-tile implementation uses several terrain presets and can produce raw and erosion-processed height fields. Its larger terrain system also contains SDF-based terrain elements and separate meshing backends. This branch starts with the land-tile path because it gives us a reproducible, inspectable quality test before integrating the much heavier full-scene terrain system.

## Files

```text
INFINIGEN_PIN.json   pinned upstream revision and license
bootstrap_linux.sh   clone/install helper for Linux
package.py           portable .npz/.json exchange format
generate.py          real Infinigen land-tile generation on Linux
blender_import.py    Blender 5.2 importer and diagnostic renders
```

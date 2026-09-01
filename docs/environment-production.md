# Environment production

`environment-production/terrain-mesh` is the server-side production counterpart
to the World runtime's planning heightfield. It accepts a JSON text descriptor,
generates a seeded terrain surface, carves declared rivers, and optionally adds
a separate water slab at `seaLevel`. The output is a real GLB mesh plus a JSON
report with bounds, face counts, source regions, and a deterministic vertex hash.

Example descriptor:

```json
{
  "seed": 42,
  "size": 24,
  "seaLevel": 0,
  "regions": [
    {"id": "ridge", "kind": "mountain", "center": [0.48, 0.42], "radius": 0.42, "amplitude": 5, "roughness": 0.7},
    {"id": "valley", "kind": "plains", "center": [0.55, 0.68], "radius": 0.3, "amplitude": 1.2, "roughness": 0.35}
  ],
  "rivers": [
    {"id": "main", "path": [[0.08, 0.2], [0.45, 0.55], [0.92, 0.82]], "width": 0.35, "depth": 1.1}
  ]
}
```

`resolution` and `include_water` are exposed as node parameters. The generated
terrain is intentionally geometry-first: biome textures, erosion simulation,
and vegetation placement remain downstream scene work. The browser World
runtime can still use its own deterministic heightfield for planning, but it no
longer needs to pretend that preview geometry is production output.

`environment-production/city-blockout` builds a second composable environment
artifact from `width`, `depth`, `rows`, `columns`, `roadWidth`, `setback`, and a
building-height range. It emits separate road and building masses with stable
names and a layout hash. This is a flat camera/layout blockout, not an inferred
city GIS model: doors, windows, interiors, traffic, zoning, and terrain fitting
must be authored or validated in later scene passes.

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

`environment-production/vegetation-scatter` emits deterministic low-poly
`tree`, `pine`, `rock`, `boulder`, `grass`, and `cactus` instances over the same
world-size convention. Its JSON sidecar records every instance origin, scale,
part name, and layout hash. It is intended to be composed over `terrain-mesh`;
species identity, collision, seasonal changes, and image-grounded density still
need an explicit scene review.

`environment-production/room-blockout` emits a room shell with a floor, four
wall planes, optional ceiling, and explicit door/window voids. Openings are
specified per wall with a meter offset from the wall's negative edge, width,
height, and (for windows) sill height:

```json
{
  "width": 6,
  "depth": 5,
  "height": 3,
  "wallThickness": 0.2,
  "doors": [{"id": "entry", "wall": "front", "offset": 2, "width": 1, "height": 2.1}],
  "windows": [{"id": "view", "wall": "back", "offset": 1.3, "width": 1.6, "height": 1.1, "sill": 1}]
}
```

The node writes actual segmented wall geometry and simple trim around each
opening, plus a report with room bounds and a stable layout hash. It is a
camera/composition blockout, not an image-inferred architectural model: it does
not add fixtures, materials, furniture, structural framing, or hidden rooms.

`environment-production/multi-room-blockout` composes the same shell contract
for a list of rooms. Each entry supplies an `id` and `[x, z]` `position`; room
dimensions and door/window lists use the `room-blockout` schema. The output
prefixes every mesh name with its room ID and records per-room hashes, so a
single room can be regenerated without losing identity in a later composition.
Adjacent rooms remain separate shells by design; shared-wall booleans,
corridors, navigation, and inferred floor plans still require a downstream
scene or Blender pass.

# Blender reference scene workflow

The `blender-cabin-reference` template is the first end-to-end Blender-backed
scene example. It follows the staged shape of the reference production skills
project without introducing a second runtime:

```text
Text brief → blender-scene/build → polykit.output → workspace GLB
                                      ├─ .blend sidecar
                                      └─ production/gray multi-angle PNG sidecars
```

## Subway reference regression case

The same `blender-scene/build` node also includes the deterministic
`subway_station` preset used by the reference regression test. The canonical
brief is:

> Reconstruct a cinematic 16:9 night subway platform from a low eye-level view: the camera is tucked behind a large tiled foreground column on the right and looks diagonally down twin rails into a deep shadowed tunnel; both left and right platform edges carry matching proportionally inset, thin yellow textured tactile strips flush with the platform slabs, with raised dots and dark safety edges, repeating square tiled columns show visible grout and microtexture, open platform edges remain unobstructed as the station recedes into the distance, the left side is one continuous platform running flush from the tiled wall to the track with no side corridor or railing, the ceiling has long linear recessed grooves with dark metal housings, evenly spaced LED beads, and flush transparent glass diffuser panels, with cool white light and a restrained ceiling wash keeping the top panels readable without crushed black, blue-gray porcelain and concrete, slightly reflective floor, high contrast, no people, no train, no readable signage.

It builds real platform, track-bed, rails, sleepers, tactile strips, tiled walls,
repeating columns, one continuous left platform that touches both the tiled wall
and track bed, open platform edges, tunnel depth, and recessed ceiling fixtures.
Columns, sleepers, and ceiling bars are native Array dependents rather than
hand-duplicated fragments. The workflow compiler passes the brief through the
`polykit.text → blender-scene/build` graph and records the preset in BuildSpec.
The **Night Subway Station Reference** workflow template preloads this prompt,
the `subway_station` preset, and a 768×432 (16:9) preview.
The station has thin tactile strips on both platform edges, proportionally inset
from the drop edge (default width 16% of platform width and inset 4%), with the
strip bottom flush to the platform slab, raised tactile-dot Array rows, and mapped
tile/grout materials on the column families. Render evidence
now reports `lightingValidation` and `surfaceValidation` separately from the
construction status. Each ceiling light family is assembled as an EXACT Boolean
recess in the soffit, a dark metal housing, an Array of LED beads, and a
transmission-enabled glass diffuser; the validation report requires two such
assemblies and records the glass transmission weight. Preview evidence also
includes a dedicated `ceiling-fixture` inspection view so the recessed stack can
be checked independently of the wide station shot. Export evidence records that
the Boolean recesses were applied and the construction cutters were removed.
The left platform is a single real slab solved from the wall's inner face to the
track-bed edge. The spatial validation report checks both contacts and the full
platform length, so a floor-colored gap or a disconnected second slab cannot
pass as a platform.

Run the focused real-Blender regression test with:

```bash
python3 -m unittest api.tests.test_blender_scene_builtin -v
```

The test skips only when the `blender` executable is unavailable; it validates
the generated GLB, semantic attachment report, 16:9 render dimensions, lighting
evidence, surface evidence, scene object count, and the export-only removal of
Boolean construction cutters.

A checked-in example of the same output is available under
`examples/blender-subway-reference/`: `entry.png` is the production view and
`subway_station_reference.blend` is the editable Blender sidecar.

## Runtime file safety

Development may change the source, tests, and bundled pack. A formal workflow run
does not write into the repository: FastAPI passes each process node a private
`.artifacts/<run_id>/process-workspace` directory, then publishes approved
artifacts into the configured workspace collection. The Blender process refuses
to run if `workspaceDir` is omitted instead of falling back to its current
directory, so a direct invocation cannot silently create `./Workflows` beside
the project source.

`blender-scene/build` sends a fixed, reviewed scene recipe (winter cabin or
night subway station) to the Blender backend used by the process node. The text
input is recorded as scene metadata (`polyKitBrief`) for provenance; presets
are deterministic so layout and Three.js loading can be verified before adding
more presets. Arbitrary Blender Python is not accepted as a node parameter.

## Running it

1. Make the configured Blender backend available for the `blender-scene` process
   node in the target environment.
2. Build the bundled process packs:

   ```bash
   node scripts/build-builtins.mjs
   ```

3. Restart/reload the FastAPI server so the bundled pack is synchronised into
   `~/.polykit/node-packs`.
4. In Workflows, choose **Blender Winter Cabin** or **Night Subway Station**,
   or submit the compiled graph to `POST /workflow-runs/execute`.

The world-domain shortcut `POST /workspace-library/worlds/{world_id}/build-structure`
accepts the optional `render_profile` values `production`, `gray`, and `toon`;
the default remains `production`.

The final mesh is served at `/workspace/Workflows/<name>.glb`, so the existing
Three.js `Viewer3D` can load it with the normal workspace URL. The `.blend` and
PNG files are published beside it for Blender inspection and visual checks;
they are auxiliary outputs, not additional graph types. When preview rendering
is enabled, the node publishes three production views and three neutral review
views: `<name>_view_entry.png`, `<name>_view_hearth.png`,
`<name>_view_exterior.png`, plus `<name>_view_gray.png`,
`<name>_view_top.png`, and `<name>_view_side.png`. A
`<name>.render-evidence.json` sidecar records the camera, lighting roles,
color-management profile, and measured luminance/non-blank checks for every
pass. The inspection cameras are also stored in the `.blend`, so the same
scene can be checked from inside the entry, from the hearth side, and from
outside/above the cabin instead of trusting one carefully framed screenshot.

## Spatial contract

The recipe uses Blender's native right-handed, Z-up world in meters. The glTF
exporter writes the standard right-handed, Y-up representation consumed by
Three.js `GLTFLoader`; the viewer does not need a second hand-written axis
conversion. Every generated mesh is tagged with `polyKitZone`
(`architecture`, `interior`, or `exterior`) and `polyKitRole`.

Before export, the Blender script checks the world-space bounding boxes of the
semantic zones. Interior props must stay inside the cabin floor plan and above
the floor, and a failed attachment or containment check stops the node instead
of publishing a misleading GLB. Generated objects carry `polyKitZone` and
`polyKitRole` custom properties, and the result metadata contains the
construction report for downstream checks.

The saved `.blend` stores the presentation and inspection cameras. This is only
an inspection convenience—the active render camera is still stored on the scene
and Three.js receives the regular GLB. The cabin recipe derives roof/wall
placement from shared width, depth, pitch, thickness, and overhang parameters,
adds a physically connected porch threshold, and uses separate wood/fabric
materials for structural and interior parts. Openings, Arrays, curves, and
other finishing operations are available as separate Blender production nodes;
they are not silently claimed by the base cabin recipe. The builder clears old
objects before rebuilding and validates its declared relationships before
publishing.
Existing `.blend` files are snapshots; rerun the template to regenerate them
with the current recipe.

The presentation camera is not the only acceptance check. A scene is
considered visually plausible only after the entry, hearth, and exterior
renders all show the expected support and containment relationships; a
good-looking single camera angle cannot hide a wall, roof, or prop that is
misplaced on the other side of the set.

Render evidence separates render health from visual approval. Nonblank
luminance and unique camera paths are recorded as health checks; the builder
reports `visualValidation: not_evaluated` until a downstream visual report
checks framing, material identity, line quality, and reference fidelity.

For a stylized 3D-to-2D pass, set the builder's `render_profile` to `toon`.
This preserves the authored wood, metal, fabric, fire, and snow colors through
per-material Eevee Shader-to-RGB three-band variants, adds a shared Geometry
Nodes inverted-hull silhouette, and enables a Freestyle structure-line pass.
The presentation camera frames the cabin body rather than the large snow
receiver. For an existing GLB, use `blender-production/npr`; its default
material policy is also non-destructive, and its `line_mode` can select
silhouette, structure, or hybrid lines.

## Material source policy

The scene brief should describe material intent and art direction, not embed a
texture website URL. A caller or future material workflow may resolve that
intent through [Poly Haven Textures](https://polyhaven.com/textures), then
record the selected asset id, map set, resolution, and real-world UV scale in
scene provenance. Blender applies the result through shader nodes; the source
is therefore replaceable without changing object placement or the Three.js
viewer contract.

Poly Haven's assets are CC0. If PolyKit uses the live Poly Haven API rather than
a workspace-cached asset, the UI or asset metadata must credit Poly Haven and
requests must use a unique User-Agent. This keeps online lookup explicit and
avoids coupling a generated scene to an unavailable remote URL.

## What this proves

- FastAPI remains the owner of execution, cancellation, output naming and
  workspace persistence.
- Blender is an external authoring/render backend, not a browser-side runtime.
- The exported GLB contains named semantic objects and material assignments;
  Three.js parses it as a regular glTF 2.0 scene.
- More advanced scene composition can add reviewed presets or explicit asset
  inputs without changing the viewer contract.

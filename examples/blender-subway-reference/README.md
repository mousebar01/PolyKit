# Blender subway reference example

This directory contains the checked-in visual example produced by the
`blender-subway-reference` workflow template:

- `entry.png` — the 16:9 production camera view.
- `subway_station_reference.blend` — the editable Blender scene sidecar.

The scene includes the continuous left platform, proportionally inset tactile
strips, textured tiled columns, and recessed ceiling light assemblies. The
files are example artifacts only. Formal workflow runs continue to write into
their private `.artifacts/<run_id>/process-workspace` directory and do not
modify the project tree.

To regenerate the example, run the real-Blender regression test from the
repository root:

```bash
python3 -m unittest api.tests.test_blender_scene_builtin -v
```

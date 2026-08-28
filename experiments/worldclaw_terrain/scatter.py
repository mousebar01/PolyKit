"""Lightweight Blender rock scatter for terrain capability validation.

This intentionally uses linked mesh instances instead of Geometry Nodes so the
prototype remains easy to inspect and debug. It is not intended for million-
instance production scattering.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import TYPE_CHECKING

try:
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:  # pragma: no cover
    bpy = None
    Vector = None

if TYPE_CHECKING:
    from .terrain import Terrain


@dataclass(kw_only=True)
class RockScatterSettings:
    count: int = 140
    seed_offset: int = 7_901
    min_rock_mask: float = 0.38
    max_lava_heat: float = 0.20
    min_scale: float = 1.3
    max_scale: float = 5.8
    flatten_min: float = 0.58
    flatten_max: float = 1.25
    max_attempt_factor: int = 20


def _require_blender() -> None:
    if bpy is None or Vector is None:
        raise RuntimeError("rock scattering must run inside Blender")


def _remove_collection(name: str) -> None:
    collection = bpy.data.collections.get(name)
    if collection is None:
        return
    for obj in list(collection.objects):
        data = obj.data if obj.type == "MESH" else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)
    bpy.data.collections.remove(collection)


def _ensure_rock_material(name: str):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.diffuse_color = (0.045, 0.038, 0.033, 1.0)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        if bsdf.inputs.get("Base Color") is not None:
            bsdf.inputs["Base Color"].default_value = (0.035, 0.029, 0.025, 1.0)
        if bsdf.inputs.get("Roughness") is not None:
            bsdf.inputs["Roughness"].default_value = 0.91
    return material


def _create_rock_mesh(name: str):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.0, location=(0, 0, 0))
    source = bpy.context.active_object
    source.name = f"{name}_Source"
    mesh = source.data
    mesh.name = f"{name}_Mesh"
    for index, vertex in enumerate(mesh.vertices):
        phase = index * 1.61803398875
        radial = 0.82 + 0.16 * math.sin(phase * 3.1) + 0.08 * math.cos(phase * 5.7)
        vertex.co.x *= radial * 1.08
        vertex.co.y *= radial * 0.94
        vertex.co.z *= radial * 0.88
    mesh.update()
    bpy.data.objects.remove(source, do_unlink=True)
    return mesh


def scatter_rocks(terrain: "Terrain", settings: RockScatterSettings | None = None) -> list[object]:
    _require_blender()
    settings = settings or RockScatterSettings()
    if not terrain.surface_samples:
        raise RuntimeError("build terrain before scattering rocks")

    collection_name = f"{terrain.name}_RockScatter"
    _remove_collection(collection_name)
    collection = bpy.data.collections.new(collection_name)
    bpy.context.scene.collection.children.link(collection)

    mesh = _create_rock_mesh(f"{terrain.name}_Rock")
    material = _ensure_rock_material(f"{terrain.name}_RockMaterial")
    mesh.materials.append(material)

    candidates = [
        sample
        for sample in terrain.surface_samples
        if sample.rock_mask >= settings.min_rock_mask
        and sample.lava_heat <= settings.max_lava_heat
    ]
    if not candidates:
        return []

    rng = random.Random(terrain.seed + settings.seed_offset)
    instances: list[object] = []
    attempts = 0
    max_attempts = max(settings.count, settings.count * settings.max_attempt_factor)
    while len(instances) < settings.count and attempts < max_attempts:
        attempts += 1
        sample = candidates[rng.randrange(len(candidates))]
        if rng.random() > sample.rock_mask:
            continue

        obj = bpy.data.objects.new(f"{terrain.name}_Rock_{len(instances):04d}", mesh)
        collection.objects.link(obj)
        scale = rng.uniform(settings.min_scale, settings.max_scale)
        obj.scale = (
            scale * rng.uniform(0.72, 1.28),
            scale * rng.uniform(0.72, 1.28),
            scale * rng.uniform(settings.flatten_min, settings.flatten_max),
        )
        obj.location = (sample.x, sample.y, sample.z + obj.scale.z * 0.28)

        normal = Vector(sample.normal)
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = normal.to_track_quat("Z", "Y")
        obj.rotation_mode = "XYZ"
        obj.rotation_euler.rotate_axis("Z", rng.uniform(0.0, math.tau))
        instances.append(obj)

    return instances

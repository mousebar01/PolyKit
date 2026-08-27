"""Blender-side WorldClaw terrain prototype.

This module intentionally targets capability validation rather than PolyKit
runtime integration. It builds a regular Blender mesh directly from semantic
regions, stores the normalized soft masks as point attributes, derives a simple
vertex-color material from the same weights, and creates fixed diagnostic
cameras for iterative agent inspection.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import tempfile
from typing import Iterable

from .regions import TerrainRegion

try:  # Keep the math layer importable outside Blender.
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:  # pragma: no cover - exercised in Blender.
    bpy = None
    Vector = None


@dataclass(frozen=True)
class BuildStats:
    vertices: int
    faces: int
    min_height: float
    max_height: float


def _require_blender() -> None:
    if bpy is None or Vector is None:
        raise RuntimeError(
            "worldclaw_terrain.terrain must run inside Blender's Python environment"
        )


def _softmax(values: Iterable[float]) -> list[float]:
    values = list(values)
    if not values:
        return []
    maximum = max(values)
    exps = [math.exp(max(-60.0, min(60.0, value - maximum))) for value in values]
    total = sum(exps)
    if total <= 1e-12:
        return [1.0 / len(exps)] * len(exps)
    return [value / total for value in exps]


def _mix_colors(regions: list[TerrainRegion], weights: list[float]) -> tuple[float, float, float, float]:
    color = [0.0, 0.0, 0.0, 0.0]
    for region, weight in zip(regions, weights):
        for channel in range(4):
            color[channel] += region.color[channel] * weight
    color[3] = max(color[3], 1.0)
    return tuple(color)  # type: ignore[return-value]


def _look_at(obj, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


class Terrain:
    """Mutable Blender terrain scene driven by semantic regions."""

    def __init__(
        self,
        *,
        size: float = 1024.0,
        resolution: int = 257,
        seed: int = 42,
        name: str = "WorldClawTerrain",
    ) -> None:
        _require_blender()
        if size <= 0:
            raise ValueError("size must be positive")
        if resolution < 3:
            raise ValueError("resolution must be at least 3")
        self.size = float(size)
        self.resolution = int(resolution)
        self.seed = int(seed)
        self.name = name
        self.regions: list[TerrainRegion] = []
        self.object = None
        self.last_stats: BuildStats | None = None

    def add_region(self, region: TerrainRegion) -> TerrainRegion:
        if any(existing.id == region.id for existing in self.regions):
            raise ValueError(f"duplicate terrain region id: {region.id}")
        self.regions.append(region)
        return region

    def get_region(self, region_id: str) -> TerrainRegion:
        for region in self.regions:
            if region.id == region_id:
                return region
        raise KeyError(region_id)

    def weights_at(self, x: float, y: float) -> list[float]:
        if not self.regions:
            raise RuntimeError("terrain has no semantic regions")
        return _softmax(region.mask_logit(x, y) for region in self.regions)

    def sample(self, x: float, y: float) -> tuple[float, list[float]]:
        weights = self.weights_at(x, y)
        height = 0.0
        for index, (region, weight) in enumerate(zip(self.regions, weights)):
            if weight <= 1e-6:
                continue
            # Offset each region's seed so two regions with identical noise
            # settings do not accidentally share the same field.
            region_seed = self.seed + index * 100_003
            height += weight * region.local_height(x, y, seed=region_seed)
        return height, weights

    def _remove_previous_mesh(self) -> None:
        existing = bpy.data.objects.get(self.name)
        if existing is None:
            return
        old_mesh = existing.data if existing.type == "MESH" else None
        bpy.data.objects.remove(existing, do_unlink=True)
        if old_mesh is not None and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)

    def build(self) -> BuildStats:
        """Build or rebuild the terrain mesh and semantic mask attributes."""
        if not self.regions:
            raise RuntimeError("add at least one region before building terrain")

        self._remove_previous_mesh()
        n = self.resolution
        half = self.size * 0.5
        step = self.size / (n - 1)

        vertices: list[tuple[float, float, float]] = []
        vertex_weights: list[list[float]] = []
        vertex_colors: list[tuple[float, float, float, float]] = []
        minimum = math.inf
        maximum = -math.inf

        for row in range(n):
            y = -half + row * step
            for column in range(n):
                x = -half + column * step
                height, weights = self.sample(x, y)
                vertices.append((x, y, height))
                vertex_weights.append(weights)
                vertex_colors.append(_mix_colors(self.regions, weights))
                minimum = min(minimum, height)
                maximum = max(maximum, height)

        faces: list[tuple[int, int, int, int]] = []
        for row in range(n - 1):
            base = row * n
            next_base = (row + 1) * n
            for column in range(n - 1):
                a = base + column
                b = a + 1
                d = next_base + column
                c = d + 1
                faces.append((a, b, c, d))

        mesh = bpy.data.meshes.new(f"{self.name}_Mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()

        for polygon in mesh.polygons:
            polygon.use_smooth = True

        # Persist each soft semantic mask as a POINT-domain float attribute. This
        # makes the WorldClaw intermediate representation inspectable in Blender
        # and reusable later by materials or Geometry Nodes without recomputing.
        for region_index, region in enumerate(self.regions):
            attribute = mesh.attributes.new(
                name=f"mask_{region.id}",
                type="FLOAT",
                domain="POINT",
            )
            for vertex_index, sample_weights in enumerate(vertex_weights):
                attribute.data[vertex_index].value = sample_weights[region_index]

        color_layer = mesh.color_attributes.new(
            name="TerrainColor",
            type="FLOAT_COLOR",
            domain="CORNER",
        )
        for loop in mesh.loops:
            color_layer.data[loop.index].color = vertex_colors[loop.vertex_index]

        obj = bpy.data.objects.new(self.name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj["worldclaw_seed"] = self.seed
        obj["worldclaw_size"] = self.size
        obj["worldclaw_resolution"] = self.resolution
        obj["worldclaw_regions"] = ",".join(region.id for region in self.regions)
        self.object = obj

        material = self._ensure_material()
        obj.data.materials.append(material)

        self.last_stats = BuildStats(
            vertices=len(vertices),
            faces=len(faces),
            min_height=minimum,
            max_height=maximum,
        )
        return self.last_stats

    def rebuild(self) -> BuildStats:
        return self.build()

    def _ensure_material(self):
        material_name = f"{self.name}_Material"
        material = bpy.data.materials.get(material_name)
        if material is None:
            material = bpy.data.materials.new(material_name)
        material.use_nodes = True

        nodes = material.node_tree.nodes
        links = material.node_tree.links
        nodes.clear()

        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (420.0, 0.0)
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (120.0, 0.0)
        if bsdf.inputs.get("Roughness") is not None:
            bsdf.inputs["Roughness"].default_value = 0.82

        try:
            color_node = nodes.new("ShaderNodeVertexColor")
            color_node.layer_name = "TerrainColor"
        except RuntimeError:
            # Fallback for Blender builds that expose color attributes through
            # the generic Attribute node instead of the dedicated vertex node.
            color_node = nodes.new("ShaderNodeAttribute")
            color_node.attribute_name = "TerrainColor"
        color_node.location = (-180.0, 0.0)

        links.new(color_node.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
        return material

    def _ensure_light(self) -> None:
        name = f"{self.name}_Sun"
        existing = bpy.data.objects.get(name)
        if existing is not None:
            bpy.data.objects.remove(existing, do_unlink=True)
        light_data = bpy.data.lights.new(name=name, type="SUN")
        light_data.energy = 3.0
        light_data.angle = math.radians(8.0)
        light = bpy.data.objects.new(name, light_data)
        bpy.context.scene.collection.objects.link(light)
        light.rotation_euler = (
            math.radians(32.0),
            math.radians(-18.0),
            math.radians(-35.0),
        )

        world = bpy.context.scene.world
        if world is None:
            world = bpy.data.worlds.new("World")
            bpy.context.scene.world = world
        world.use_nodes = True
        background = world.node_tree.nodes.get("Background")
        if background is not None:
            background.inputs["Color"].default_value = (0.055, 0.075, 0.11, 1.0)
            background.inputs["Strength"].default_value = 0.45

    def _make_camera(
        self,
        suffix: str,
        *,
        location: tuple[float, float, float],
        target: tuple[float, float, float],
        lens: float,
    ):
        name = f"{self.name}_Camera_{suffix}"
        existing = bpy.data.objects.get(name)
        if existing is not None:
            bpy.data.objects.remove(existing, do_unlink=True)
        data = bpy.data.cameras.new(name)
        data.lens = lens
        camera = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(camera)
        camera.location = location
        _look_at(camera, target)
        return camera

    def setup_diagnostics(self) -> dict[str, object]:
        """Create deterministic perspective, top and low-angle cameras."""
        if self.object is None:
            raise RuntimeError("build terrain before creating diagnostic cameras")
        self._ensure_light()

        stats = self.last_stats or BuildStats(0, 0, 0.0, 0.0)
        vertical_span = max(20.0, stats.max_height - stats.min_height)
        target_z = stats.min_height + vertical_span * 0.35
        target = (0.0, 0.0, target_z)
        size = self.size
        high_z = stats.max_height + size * 0.85

        return {
            "perspective": self._make_camera(
                "Perspective",
                location=(size * 0.55, -size * 0.88, stats.max_height + size * 0.42),
                target=target,
                lens=48.0,
            ),
            "top": self._make_camera(
                "Top",
                location=(0.0, 0.0, high_z),
                target=(0.0, 0.0, stats.min_height),
                lens=50.0,
            ),
            "low": self._make_camera(
                "Low",
                location=(-size * 0.78, -size * 0.72, stats.max_height + size * 0.18),
                target=target,
                lens=56.0,
            ),
        }

    def render_diagnostics(
        self,
        output_dir: str | Path | None = None,
        *,
        resolution: int = 768,
    ) -> dict[str, str]:
        """Render all diagnostic cameras and return PNG paths."""
        cameras = self.setup_diagnostics()
        if output_dir is None:
            output_dir = Path(tempfile.gettempdir()) / "polykit_worldclaw_terrain"
        else:
            output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        scene = bpy.context.scene
        try:
            scene.render.engine = "BLENDER_EEVEE_NEXT"
        except (TypeError, ValueError):
            # Preserve the user's currently selected engine if this identifier
            # changes in a future Blender build.
            pass
        scene.render.resolution_x = int(resolution)
        scene.render.resolution_y = int(resolution)
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.film_transparent = False

        paths: dict[str, str] = {}
        for label, camera in cameras.items():
            path = output_dir / f"{self.name.lower()}_{label}.png"
            scene.camera = camera
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            paths[label] = str(path)
        return paths

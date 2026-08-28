"""Blender-side WorldClaw terrain capability prototype.

The terrain keeps WorldClaw's useful intermediate representation visible:
semantic region masks, derived height/slope/heat/ash/rock fields, geometry, and
procedural material all live in Blender and can be inspected or modified by an
agent later through MCP.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import tempfile
from typing import Iterable

from .materials import (
    VolcanicMaterialSettings,
    build_generic_material,
    build_volcanic_material,
)
from .regions import TerrainRegion, clamp01
from .surface import SurfaceSample, derive_surface_samples

try:
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:  # pragma: no cover
    bpy = None
    Vector = None


@dataclass(frozen=True)
class BuildStats:
    vertices: int
    faces: int
    min_height: float
    max_height: float
    max_slope: float
    max_lava_heat: float


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


def _look_at(obj, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _blend_color(
    regions: list[TerrainRegion],
    masks: list[float],
) -> tuple[float, float, float, float]:
    base = [0.0, 0.0, 0.0, 1.0]
    replace_total = 0.0
    for region, mask in zip(regions, masks):
        if region.blend_mode != "replace":
            continue
        replace_total += mask
        for channel in range(3):
            base[channel] += region.color[channel] * mask
    if replace_total <= 1e-8:
        base[:3] = [0.25, 0.25, 0.25]
    elif abs(replace_total - 1.0) > 1e-5:
        for channel in range(3):
            base[channel] /= replace_total

    for region, mask in zip(regions, masks):
        if region.blend_mode != "add" or mask <= 1e-5:
            continue
        factor = clamp01(mask * 0.88)
        for channel in range(3):
            base[channel] = base[channel] * (1.0 - factor) + region.color[channel] * factor
    return (base[0], base[1], base[2], 1.0)


class Terrain:
    """Mutable Blender terrain scene driven by semantic regions."""

    def __init__(
        self,
        *,
        size: float = 1024.0,
        resolution: int = 257,
        seed: int = 42,
        name: str = "WorldClawTerrain",
        material_profile: str = "generic",
        volcanic_material: VolcanicMaterialSettings | None = None,
        compositor_glow: bool = True,
    ) -> None:
        _require_blender()
        if size <= 0:
            raise ValueError("size must be positive")
        if resolution < 3:
            raise ValueError("resolution must be at least 3")
        if material_profile not in {"generic", "volcanic"}:
            raise ValueError("material_profile must be 'generic' or 'volcanic'")
        self.size = float(size)
        self.resolution = int(resolution)
        self.seed = int(seed)
        self.name = name
        self.material_profile = material_profile
        self.volcanic_material = volcanic_material or VolcanicMaterialSettings()
        self.compositor_glow = bool(compositor_glow)
        self.regions: list[TerrainRegion] = []
        self.object = None
        self.last_stats: BuildStats | None = None
        self.surface_samples: list[SurfaceSample] = []
        self._heights: list[float] = []
        self._masks: list[list[float]] = []

    @property
    def replacement_regions(self) -> list[TerrainRegion]:
        return [region for region in self.regions if region.blend_mode == "replace"]

    @property
    def additive_regions(self) -> list[TerrainRegion]:
        return [region for region in self.regions if region.blend_mode == "add"]

    def add_region(self, region: TerrainRegion) -> TerrainRegion:
        if any(existing.id == region.id for existing in self.regions):
            raise ValueError(f"duplicate terrain region id: {region.id}")
        if region.blend_mode not in {"replace", "add"}:
            raise ValueError(f"unsupported blend mode for {region.id}: {region.blend_mode}")
        self.regions.append(region)
        return region

    def get_region(self, region_id: str) -> TerrainRegion:
        for region in self.regions:
            if region.id == region_id:
                return region
        raise KeyError(region_id)

    def masks_at(self, x: float, y: float) -> list[float]:
        """Return one inspectable semantic/overlay mask per region."""
        if not self.regions:
            raise RuntimeError("terrain has no semantic regions")
        replacements = self.replacement_regions
        if not replacements:
            raise RuntimeError("terrain needs at least one replacement/base region")

        base_weights = _softmax(region.mask_logit(x, y) for region in replacements)
        by_id = {region.id: weight for region, weight in zip(replacements, base_weights)}
        result: list[float] = []
        for region in self.regions:
            if region.blend_mode == "replace":
                result.append(by_id[region.id])
            else:
                result.append(region.additive_mask(x, y))
        return result

    def sample(self, x: float, y: float) -> tuple[float, list[float]]:
        masks = self.masks_at(x, y)
        height = 0.0
        for index, (region, mask) in enumerate(zip(self.regions, masks)):
            if mask <= 1e-7:
                continue
            region_seed = self.seed + index * 100_003
            if region.blend_mode == "replace":
                height += mask * region.local_height(x, y, seed=region_seed)
            else:
                height += mask * region.height_modifier(x, y, seed=region_seed)
        return height, masks

    def _remove_previous_mesh(self) -> None:
        existing = bpy.data.objects.get(self.name)
        if existing is None:
            return
        old_mesh = existing.data if existing.type == "MESH" else None
        bpy.data.objects.remove(existing, do_unlink=True)
        if old_mesh is not None and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)

    @staticmethod
    def _write_float_attribute(mesh, name: str, values: list[float]) -> None:
        attribute = mesh.attributes.get(name)
        if attribute is not None:
            mesh.attributes.remove(attribute)
        attribute = mesh.attributes.new(name=name, type="FLOAT", domain="POINT")
        try:
            attribute.data.foreach_set("value", values)
        except (AttributeError, TypeError, ValueError):
            for index, value in enumerate(values):
                attribute.data[index].value = value

    @staticmethod
    def _write_color_attribute(mesh, name: str, colors: list[tuple[float, float, float, float]]) -> None:
        existing = mesh.color_attributes.get(name)
        if existing is not None:
            mesh.color_attributes.remove(existing)
        layer = mesh.color_attributes.new(name=name, type="FLOAT_COLOR", domain="CORNER")
        flat: list[float] = []
        for loop in mesh.loops:
            flat.extend(colors[loop.vertex_index])
        try:
            layer.data.foreach_set("color", flat)
        except (AttributeError, TypeError, ValueError):
            for loop in mesh.loops:
                layer.data[loop.index].color = colors[loop.vertex_index]

    def build(self) -> BuildStats:
        """Build/rebuild geometry, semantic masks, and derived material fields."""
        if not self.regions:
            raise RuntimeError("add at least one region before building terrain")
        if not self.replacement_regions:
            raise RuntimeError("add at least one replacement/base region before building terrain")

        self._remove_previous_mesh()
        n = self.resolution
        half = self.size * 0.5
        step = self.size / (n - 1)

        vertices: list[tuple[float, float, float]] = []
        heights: list[float] = []
        masks: list[list[float]] = []
        colors: list[tuple[float, float, float, float]] = []

        for row in range(n):
            y = -half + row * step
            for column in range(n):
                x = -half + column * step
                height, sample_masks = self.sample(x, y)
                vertices.append((x, y, height))
                heights.append(height)
                masks.append(sample_masks)
                colors.append(_blend_color(self.regions, sample_masks))

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

        surface_samples = derive_surface_samples(
            heights=heights,
            masks=masks,
            regions=self.regions,
            resolution=n,
            step=step,
            half_size=half,
            seed=self.seed,
        )

        mesh = bpy.data.meshes.new(f"{self.name}_Mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        for polygon in mesh.polygons:
            polygon.use_smooth = True

        for region_index, region in enumerate(self.regions):
            self._write_float_attribute(
                mesh,
                f"mask_{region.id}",
                [sample_masks[region_index] for sample_masks in masks],
            )

        self._write_float_attribute(mesh, "height01", [sample.height01 for sample in surface_samples])
        self._write_float_attribute(mesh, "slope01", [sample.slope01 for sample in surface_samples])
        self._write_float_attribute(mesh, "lava_heat", [sample.lava_heat for sample in surface_samples])
        self._write_float_attribute(mesh, "ash_mask", [sample.ash_mask for sample in surface_samples])
        self._write_float_attribute(mesh, "rock_mask", [sample.rock_mask for sample in surface_samples])
        self._write_color_attribute(mesh, "TerrainColor", colors)

        obj = bpy.data.objects.new(self.name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj["worldclaw_seed"] = self.seed
        obj["worldclaw_size"] = self.size
        obj["worldclaw_resolution"] = self.resolution
        obj["worldclaw_regions"] = ",".join(region.id for region in self.regions)
        obj["worldclaw_material_profile"] = self.material_profile
        self.object = obj

        material = self._ensure_material()
        obj.data.materials.append(material)

        self._heights = heights
        self._masks = masks
        self.surface_samples = surface_samples
        minimum = min(heights)
        maximum = max(heights)
        self.last_stats = BuildStats(
            vertices=len(vertices),
            faces=len(faces),
            min_height=minimum,
            max_height=maximum,
            max_slope=max((sample.slope01 for sample in surface_samples), default=0.0),
            max_lava_heat=max((sample.lava_heat for sample in surface_samples), default=0.0),
        )
        return self.last_stats

    def rebuild(self) -> BuildStats:
        return self.build()

    def _ensure_material(self):
        material_name = f"{self.name}_Material"
        material = bpy.data.materials.get(material_name)
        if material is None:
            material = bpy.data.materials.new(material_name)
        if self.material_profile == "volcanic":
            return build_volcanic_material(material, self.volcanic_material)
        return build_generic_material(material)

    def scatter_rocks(self, **kwargs):
        """Create inspectable linked rock instances using the derived rock mask."""
        from .scatter import RockScatterSettings, scatter_rocks

        settings = RockScatterSettings(**kwargs)
        return scatter_rocks(self, settings)

    def clear_rock_scatter(self) -> None:
        name = f"{self.name}_RockScatter"
        collection = bpy.data.collections.get(name)
        if collection is None:
            return
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)

    def _ensure_light(self) -> None:
        name = f"{self.name}_Sun"
        existing = bpy.data.objects.get(name)
        if existing is not None:
            old_data = existing.data
            bpy.data.objects.remove(existing, do_unlink=True)
            if old_data is not None and old_data.users == 0:
                bpy.data.lights.remove(old_data)

        light_data = bpy.data.lights.new(name=name, type="SUN")
        light_data.energy = 2.7 if self.material_profile == "volcanic" else 3.0
        light_data.angle = math.radians(7.0)
        light = bpy.data.objects.new(name, light_data)
        bpy.context.scene.collection.objects.link(light)
        light.rotation_euler = (
            math.radians(38.0),
            math.radians(-22.0),
            math.radians(-42.0),
        )

        world = bpy.context.scene.world
        if world is None:
            world = bpy.data.worlds.new("World")
            bpy.context.scene.world = world
        world.use_nodes = True
        background = world.node_tree.nodes.get("Background")
        if background is not None:
            if self.material_profile == "volcanic":
                background.inputs["Color"].default_value = (0.018, 0.022, 0.028, 1.0)
                background.inputs["Strength"].default_value = 0.24
            else:
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
            old_data = existing.data
            bpy.data.objects.remove(existing, do_unlink=True)
            if old_data is not None and old_data.users == 0:
                bpy.data.cameras.remove(old_data)

        data = bpy.data.cameras.new(name)
        data.lens = lens
        data.dof.use_dof = False
        camera = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(camera)
        camera.location = location
        _look_at(camera, target)
        return camera

    def setup_diagnostics(self) -> dict[str, object]:
        """Create deterministic perspective, top, low, and detail cameras."""
        if self.object is None:
            raise RuntimeError("build terrain before creating diagnostic cameras")
        self._ensure_light()

        stats = self.last_stats or BuildStats(0, 0, 0.0, 0.0, 0.0, 0.0)
        vertical_span = max(20.0, stats.max_height - stats.min_height)
        target_z = stats.min_height + vertical_span * 0.38
        target = (0.0, 0.0, target_z)
        size = self.size
        high_z = stats.max_height + size * 0.86

        return {
            "perspective": self._make_camera(
                "Perspective",
                location=(size * 0.58, -size * 0.90, stats.max_height + size * 0.40),
                target=target,
                lens=50.0,
            ),
            "top": self._make_camera(
                "Top",
                location=(0.0, 0.0, high_z),
                target=(0.0, 0.0, stats.min_height),
                lens=52.0,
            ),
            "low": self._make_camera(
                "Low",
                location=(-size * 0.80, -size * 0.70, stats.max_height + size * 0.17),
                target=target,
                lens=58.0,
            ),
            "detail": self._make_camera(
                "Detail",
                location=(size * 0.18, -size * 0.48, stats.max_height + size * 0.15),
                target=(0.0, size * 0.10, target_z + vertical_span * 0.35),
                lens=72.0,
            ),
        }

    def _ensure_compositor_glow(self) -> None:
        if not self.compositor_glow or self.material_profile != "volcanic":
            return
        scene = bpy.context.scene
        try:
            scene.use_nodes = True
            tree = scene.node_tree
            if tree is None:
                return
            nodes = tree.nodes
            links = tree.links
            nodes.clear()
            render_layers = nodes.new("CompositorNodeRLayers")
            render_layers.location = (-420, 0)
            glare = nodes.new("CompositorNodeGlare")
            glare.location = (-100, 0)
            for glare_type in ("BLOOM", "FOG_GLOW"):
                try:
                    glare.glare_type = glare_type
                    break
                except (TypeError, ValueError):
                    continue
            try:
                glare.quality = "HIGH"
            except (TypeError, ValueError):
                pass
            glare.threshold = 0.8
            try:
                glare.size = 7
            except (TypeError, ValueError):
                pass
            composite = nodes.new("CompositorNodeComposite")
            composite.location = (220, 0)
            links.new(render_layers.outputs["Image"], glare.inputs["Image"])
            links.new(glare.outputs["Image"], composite.inputs["Image"])
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return

    def configure_render(self, *, resolution: int = 768) -> None:
        scene = bpy.context.scene
        try:
            scene.render.engine = "BLENDER_EEVEE"
        except (TypeError, ValueError):
            pass
        scene.render.resolution_x = int(resolution)
        scene.render.resolution_y = int(resolution)
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.film_transparent = False
        try:
            scene.render.image_settings.color_mode = "RGBA"
        except (TypeError, ValueError):
            pass
        for look in ("AgX - Medium High Contrast", "AgX - Medium High Contrast Look"):
            try:
                scene.view_settings.look = look
                break
            except (TypeError, ValueError):
                continue
        if self.material_profile == "volcanic":
            scene.view_settings.exposure = -0.35
        self._ensure_compositor_glow()

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

        self.configure_render(resolution=resolution)
        scene = bpy.context.scene
        paths: dict[str, str] = {}
        for label, camera in cameras.items():
            path = output_dir / f"{self.name.lower()}_{label}.png"
            scene.camera = camera
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            paths[label] = str(path)
        return paths

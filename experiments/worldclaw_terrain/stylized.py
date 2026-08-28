"""Game-oriented terrain wrapper on top of the shared WorldClaw prototype.

The base :class:`Terrain` owns semantic masks, height composition, derived
surface fields, mesh construction, and scatter inputs.  This module adds an art
direction layer for stylized third-person worlds plus two gameplay-oriented
fields: ``hazard_mask`` and ``traversable_mask``.

Those fields are not a navmesh. They are deliberately cheap semantic hints that
an agent can later use when deciding where to place paths, props, encounters, or
landmarks before a real game-engine navigation bake exists.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .regions import VolcanoRegion, clamp01
from .stylized_materials import StylizedMaterialSettings, build_stylized_material
from .terrain import Terrain

try:
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:  # pragma: no cover
    bpy = None
    Vector = None


HAZARD_WEIGHTS: dict[str, float] = {
    "lava": 1.0,
    "magma": 1.0,
    "ocean": 1.0,
    "water": 0.82,
    "river": 0.72,
    "swamp": 0.58,
}


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge1 <= edge0:
        return 1.0 if value >= edge1 else 0.0
    t = clamp01((value - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _look_at(obj, target: tuple[float, float, float]) -> None:
    if Vector is None:
        return
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


@dataclass(kw_only=True)
class StylizedVolcanoRegion(VolcanoRegion):
    """Volcano variant that introduces mild broad terraces for game readability.

    The terrace blend is intentionally subtle: it helps create readable ledges
    and large shape rhythm without turning the mountain into visible stairs.
    """

    terrace_step: float = 16.0
    terrace_strength: float = 0.10

    def local_height(self, x: float, y: float, *, seed: int) -> float:
        height = super().local_height(x, y, seed=seed)
        if self.terrace_step <= 1e-6 or self.terrace_strength <= 1e-6:
            return height
        step = float(self.terrace_step)
        strength = clamp01(self.terrace_strength)
        terraced = round(height / step) * step
        return height * (1.0 - strength) + terraced * strength


class StylizedTerrain(Terrain):
    """Terrain presentation tuned for colorful third-person game worlds."""

    def __init__(
        self,
        *,
        style: StylizedMaterialSettings | None = None,
        walkable_slope_start: float = 0.36,
        walkable_slope_end: float = 0.68,
        **kwargs,
    ) -> None:
        # Copy the style so mutating one terrain during agent iteration does not
        # silently alter every scene that reused a module-level preset instance.
        self.style = replace(style) if style is not None else StylizedMaterialSettings()
        if walkable_slope_start < 0.0 or walkable_slope_end <= walkable_slope_start:
            raise ValueError("walkable slope range must be increasing and non-negative")
        self.walkable_slope_start = float(walkable_slope_start)
        self.walkable_slope_end = float(walkable_slope_end)

        # Reuse the base compositor glow path only when the style actually has
        # emissive lava. _ensure_material/_ensure_light are overridden below.
        kwargs["material_profile"] = "volcanic" if self.style.enable_lava else "generic"
        kwargs.setdefault("compositor_glow", self.style.enable_lava)
        super().__init__(**kwargs)

    def _ensure_material(self):
        material_name = f"{self.name}_Material"
        material = bpy.data.materials.get(material_name)
        if material is None:
            material = bpy.data.materials.new(material_name)
        return build_stylized_material(material, self.style)

    def _remove_light(self, name: str) -> None:
        existing = bpy.data.objects.get(name)
        if existing is None:
            return
        old_data = existing.data
        bpy.data.objects.remove(existing, do_unlink=True)
        if old_data is not None and old_data.users == 0:
            bpy.data.lights.remove(old_data)

    def _ensure_light(self) -> None:
        """Use bright key/fill separation instead of dark realistic lighting."""
        sun_name = f"{self.name}_Sun"
        self._remove_light(sun_name)
        sun_data = bpy.data.lights.new(name=sun_name, type="SUN")
        sun_data.energy = self.style.sun_energy
        sun_data.angle = math.radians(self.style.sun_angle_degrees)
        sun = bpy.data.objects.new(sun_name, sun_data)
        bpy.context.scene.collection.objects.link(sun)
        sun.rotation_euler = (
            math.radians(37.0),
            math.radians(-20.0),
            math.radians(-40.0),
        )

        fill_name = f"{self.name}_Fill"
        self._remove_light(fill_name)
        fill_data = bpy.data.lights.new(name=fill_name, type="AREA")
        fill_data.energy = self.style.fill_energy
        fill_data.shape = "DISK"
        fill_data.size = self.size * 0.58
        fill = bpy.data.objects.new(fill_name, fill_data)
        bpy.context.scene.collection.objects.link(fill)
        maximum = self.last_stats.max_height if self.last_stats else self.size * 0.2
        fill.location = (
            -self.size * 0.42,
            -self.size * 0.30,
            maximum + self.size * 0.52,
        )
        _look_at(fill, (0.0, 0.0, maximum * 0.30))

        world = bpy.context.scene.world
        if world is None:
            world = bpy.data.worlds.new("World")
            bpy.context.scene.world = world
        world.use_nodes = True
        background = world.node_tree.nodes.get("Background")
        if background is not None:
            background.inputs["Color"].default_value = self.style.world_color
            background.inputs["Strength"].default_value = self.style.world_strength

    def _gameplay_fields(self) -> tuple[list[float], list[float]]:
        hazards: list[float] = []
        traversable: list[float] = []
        for sample, masks in zip(self.surface_samples, self._masks):
            hazard = sample.lava_heat
            for region, mask in zip(self.regions, masks):
                weight = HAZARD_WEIGHTS.get(region.kind.lower())
                if weight is not None:
                    hazard = max(hazard, mask * weight)
            hazard = clamp01(hazard)

            slope_block = _smoothstep(
                self.walkable_slope_start,
                self.walkable_slope_end,
                sample.slope01,
            )
            slope_ok = 1.0 - slope_block
            # Rock itself is not automatically unwalkable. Only extreme rock
            # exposure gently reduces preference so broad rocky plateaus remain
            # usable in stylized adventure worlds.
            rock_penalty = max(0.0, sample.rock_mask - 0.82) * 0.35
            walk = clamp01((slope_ok - rock_penalty) * (1.0 - hazard))
            hazards.append(hazard)
            traversable.append(walk)
        return hazards, traversable

    def build(self):
        stats = super().build()
        hazard, traversable = self._gameplay_fields()
        mesh = self.object.data
        self._write_float_attribute(mesh, "hazard_mask", hazard)
        self._write_float_attribute(mesh, "traversable_mask", traversable)
        self.object["worldclaw_material_profile"] = "stylized"
        self.object["worldclaw_style"] = self.style.name
        return stats

    def configure_render(self, *, resolution: int = 768) -> None:
        super().configure_render(resolution=resolution)
        scene = bpy.context.scene
        scene.view_settings.exposure = self.style.exposure
        # Keep the existing AgX setup but favor a pleasant medium contrast if
        # Blender exposes that look under a version-specific label.
        for look in (
            "AgX - Medium High Contrast",
            "AgX - Medium High Contrast Look",
            "AgX - Medium Low Contrast",
        ):
            try:
                scene.view_settings.look = look
                break
            except (TypeError, ValueError):
                continue

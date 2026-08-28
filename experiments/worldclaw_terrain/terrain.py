"""Blender-side WorldClaw terrain prototype.

The prototype remains Blender-first and capability-focused.  It now keeps the
WorldClaw-style semantic representation while producing game-oriented material
signals (height, slope, lava heat, ash) and a stylized procedural material.

Base semantic regions are softly blended into an absolute height field.  Overlay
regions such as rivers and lava flows then modify that height locally, which
keeps channels attached to the underlying mountain instead of flattening it to a
second absolute terrain function.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import tempfile
from typing import Iterable

from .materials import build_stylized_terrain_material
from .regions import SplineRegion, TerrainRegion
from .styles import DEFAULT_STYLIZED_STYLE, StylizedTerrainStyle

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
    max_slope_degrees: float = 0.0


def _require_blender() -> None:
    if bpy is None or Vector is None:
        raise RuntimeError(
            "worldclaw_terrain.terrain must run inside Blender's Python environment"
        )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


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


def _mix_colors(
    regions: list[TerrainRegion],
    weights: list[float],
) -> tuple[float, float, float, float]:
    """Blend semantic colors while leaving lava to the dedicated hot shader."""
    usable = [
        (region, weight)
        for region, weight in zip(regions, weights)
        if region.kind.lower() not in {"lava", "magma"}
    ]
    total = sum(weight for _, weight in usable)
    if total <= 1e-8:
        usable = list(zip(regions, weights))
        total = max(1e-8, sum(weight for _, weight in usable))

    color = [0.0, 0.0, 0.0, 0.0]
    for region, weight in usable:
        normalized = weight / total
        for channel in range(4):
            color[channel] += region.color[channel] * normalized
    color[3] = 1.0
    return tuple(color)  # type: ignore[return-value]


def _look_at(obj, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _write_float_attribute(mesh, name: str, values: list[float]) -> None:
    attribute = mesh.attributes.new(name=name, type="FLOAT", domain="POINT")
    try:
        attribute.data.foreach_set("value", values)
    except (AttributeError, TypeError):
        for index, value in enumerate(values):
            attribute.data[index].value = value


class Terrain:
    """Mutable Blender terrain scene driven by semantic regions."""

    def __init__(
        self,
        *,
        size: float = 1024.0,
        resolution: int = 257,
        seed: int = 42,
        name: str = "WorldClawTerrain",
        style: StylizedTerrainStyle | None = None,
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
        self.style = style or DEFAULT_STYLIZED_STYLE
        self.regions: list[TerrainRegion] = []
        self.object = None
        self.last_stats: BuildStats | None = None

    def add_region(self, region: TerrainRegion) -> TerrainRegion:
        if any(existing.id == region.id for existing in self.regions):
            raise ValueError(f"duplicate terrain region id: {region.id}")
        if region.height_mode not in {"blend", "overlay"}:
            raise ValueError(
                f"terrain region '{region.id}' has unsupported height_mode "
                f"{region.height_mode!r}"
            )
        self.regions.append(region)
        return region

    def get_region(self, region_id: str) -> TerrainRegion:
        for region in self.regions:
            if region.id == region_id:
                return region
        raise KeyError(region_id)

    def _logits_at(self, x: float, y: float) -> list[float]:
        return [region.mask_logit(x, y) for region in self.regions]

    def weights_at(self, x: float, y: float) -> list[float]:
        if not self.regions:
            raise RuntimeError("terrain has no semantic regions")
        return _softmax(self._logits_at(x, y))

    def sample(self, x: float, y: float) -> tuple[float, list[float]]:
        """Sample final terrain height plus normalized semantic weights."""
        if not self.regions:
            raise RuntimeError("terrain has no semantic regions")

        logits = self._logits_at(x, y)
        semantic_weights = _softmax(logits)

        blend_indices = [
            index for index, region in enumerate(self.regions)
            if region.height_mode == "blend"
        ]
        if not blend_indices:
            raise RuntimeError("terrain requires at least one height_mode='blend' region")
        base_weights = _softmax(logits[index] for index in blend_indices)

        height = 0.0
        for local_index, region_index in enumerate(blend_indices):
            region = self.regions[region_index]
            region_seed = self.seed + region_index * 100_003
            height += base_weights[local_index] * region.local_height(
                x,
                y,
                seed=region_seed,
            )

        for region_index, region in enumerate(self.regions):
            if region.height_mode != "overlay":
                continue
            coverage = region.coverage(x, y)
            if coverage <= 1e-5:
                continue
            region_seed = self.seed + region_index * 100_003
            height += coverage * region.height_offset(x, y, seed=region_seed)

        return height, semantic_weights

    def _remove_previous_mesh(self) -> None:
        existing = bpy.data.objects.get(self.name)
        if existing is None:
            return
        old_mesh = existing.data if existing.type == "MESH" else None
        bpy.data.objects.remove(existing, do_unlink=True)
        if old_mesh is not None and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)

    def _compute_slope_fields(
        self,
        heights: list[float],
        *,
        step: float,
    ) -> tuple[list[float], float]:
        """Finite-difference slope normalized so 60 degrees maps to 1."""
        n = self.resolution
        slope_values: list[float] = [0.0] * len(heights)
        maximum_angle = 0.0

        def height_at(row: int, column: int) -> float:
            row = max(0, min(n - 1, row))
            column = max(0, min(n - 1, column))
            return heights[row * n + column]

        normalizer = math.radians(60.0)
        for row in range(n):
            for column in range(n):
                left = height_at(row, column - 1)
                right = height_at(row, column + 1)
                down = height_at(row - 1, column)
                up = height_at(row + 1, column)
                x_span = step * (1.0 if column in {0, n - 1} else 2.0)
                y_span = step * (1.0 if row in {0, n - 1} else 2.0)
                dzdx = (right - left) / max(1e-6, x_span)
                dzdy = (up - down) / max(1e-6, y_span)
                angle = math.atan(math.hypot(dzdx, dzdy))
                maximum_angle = max(maximum_angle, angle)
                slope_values[row * n + column] = _clamp01(angle / normalizer)
        return slope_values, math.degrees(maximum_angle)

    def _lava_heat_at(
        self,
        x: float,
        y: float,
        semantic_weights: list[float],
    ) -> float:
        heat = 0.0
        for index, region in enumerate(self.regions):
            if region.kind.lower() not in {"lava", "magma"}:
                continue
            coverage = region.coverage(x, y)
            if isinstance(region, SplineRegion):
                core = region.center_weight(x, y)
            else:
                core = coverage
            # Coverage defines the cooling edge; the center term keeps a bright
            # readable core. Semantic weight helps when several hot overlays meet.
            candidate = max(semantic_weights[index], coverage * 0.82)
            candidate *= 0.24 + 0.76 * (core ** 0.72)
            heat = max(heat, candidate)
        return _clamp01(heat)

    def _ash_mask_at(
        self,
        semantic_weights: list[float],
        *,
        height01: float,
        slope01: float,
        lava_heat: float,
    ) -> float:
        volcanic = 0.0
        for region, weight in zip(self.regions, semantic_weights):
            if region.kind.lower() in {"volcano", "ash", "badlands"}:
                volcanic += weight
        if volcanic <= 1e-6:
            return 0.0
        # Broad readable accumulation: upper/flat volcanic surfaces carry more
        # ash while steep faces and hot lava expose darker rock beneath.
        height_term = 0.34 + 0.66 * height01
        flat_term = 1.0 - 0.62 * slope01
        cool_term = 1.0 - 0.92 * lava_heat
        return _clamp01(volcanic * height_term * flat_term * cool_term)

    def build(self) -> BuildStats:
        """Build/rebuild mesh, semantic masks, style fields, and material."""
        if not self.regions:
            raise RuntimeError("add at least one region before building terrain")

        self._remove_previous_mesh()
        n = self.resolution
        half = self.size * 0.5
        step = self.size / (n - 1)

        vertices: list[tuple[float, float, float]] = []
        coordinates: list[tuple[float, float]] = []
        heights: list[float] = []
        vertex_weights: list[list[float]] = []
        minimum = math.inf
        maximum = -math.inf

        for row in range(n):
            y = -half + row * step
            for column in range(n):
                x = -half + column * step
                height, weights = self.sample(x, y)
                vertices.append((x, y, height))
                coordinates.append((x, y))
                heights.append(height)
                vertex_weights.append(weights)
                minimum = min(minimum, height)
                maximum = max(maximum, height)

        span = max(1e-6, maximum - minimum)
        height01 = [_clamp01((height - minimum) / span) for height in heights]
        slope01, max_slope_degrees = self._compute_slope_fields(heights, step=step)

        lava_heat: list[float] = []
        ash_mask: list[float] = []
        vertex_colors: list[tuple[float, float, float, float]] = []
        for index, ((x, y), weights) in enumerate(zip(coordinates, vertex_weights)):
            heat = self._lava_heat_at(x, y, weights)
            lava_heat.append(heat)
            ash_mask.append(
                self._ash_mask_at(
                    weights,
                    height01=height01[index],
                    slope01=slope01[index],
                    lava_heat=heat,
                )
            )
            vertex_colors.append(_mix_colors(self.regions, weights))

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

        # Persist semantic masks for material authoring, future scattering, and MCP
        # inspection. These are the most important intermediate representation.
        for region_index, region in enumerate(self.regions):
            _write_float_attribute(
                mesh,
                f"mask_{region.id}",
                [weights[region_index] for weights in vertex_weights],
            )

        _write_float_attribute(mesh, "height01", height01)
        _write_float_attribute(mesh, "slope01", slope01)
        _write_float_attribute(mesh, "lava_heat", lava_heat)
        _write_float_attribute(mesh, "ash_mask", ash_mask)

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
        obj["worldclaw_style"] = self.style.name
        self.object = obj

        material = build_stylized_terrain_material(
            f"{self.name}_Material",
            self.style,
        )
        obj.data.materials.append(material)

        self.last_stats = BuildStats(
            vertices=len(vertices),
            faces=len(faces),
            min_height=minimum,
            max_height=maximum,
            max_slope_degrees=max_slope_degrees,
        )
        return self.last_stats

    def rebuild(self) -> BuildStats:
        return self.build()

    def _remove_object_and_data(self, name: str, data_collection) -> None:
        existing = bpy.data.objects.get(name)
        if existing is None:
            return
        old_data = existing.data
        bpy.data.objects.remove(existing, do_unlink=True)
        if old_data is not None and old_data.users == 0:
            data_collection.remove(old_data)

    def _ensure_light(self) -> None:
        sun_name = f"{self.name}_Sun"
        self._remove_object_and_data(sun_name, bpy.data.lights)
        light_data = bpy.data.lights.new(name=sun_name, type="SUN")
        light_data.energy = self.style.sun_energy
        light_data.angle = math.radians(self.style.sun_angle_degrees)
        light = bpy.data.objects.new(sun_name, light_data)
        bpy.context.scene.collection.objects.link(light)
        light.rotation_euler = (
            math.radians(32.0),
            math.radians(-18.0),
            math.radians(-35.0),
        )

        fill_name = f"{self.name}_Fill"
        self._remove_object_and_data(fill_name, bpy.data.lights)
        fill_data = bpy.data.lights.new(name=fill_name, type="AREA")
        fill_data.energy = self.style.fill_energy
        fill_data.shape = "DISK"
        fill_data.size = self.size * 0.55
        fill = bpy.data.objects.new(fill_name, fill_data)
        bpy.context.scene.collection.objects.link(fill)
        max_height = self.last_stats.max_height if self.last_stats else self.size * 0.2
        fill.location = (-self.size * 0.45, -self.size * 0.30, max_height + self.size * 0.55)
        _look_at(fill, (0.0, 0.0, max_height * 0.25))

        world = bpy.context.scene.world
        if world is None:
            world = bpy.data.worlds.new("World")
            bpy.context.scene.world = world
        world.use_nodes = True
        background = world.node_tree.nodes.get("Background")
        if background is not None:
            background.inputs["Color"].default_value = self.style.world_color
            background.inputs["Strength"].default_value = self.style.world_strength

    def _make_camera(
        self,
        suffix: str,
        *,
        location: tuple[float, float, float],
        target: tuple[float, float, float],
        lens: float,
    ):
        name = f"{self.name}_Camera_{suffix}"
        self._remove_object_and_data(name, bpy.data.cameras)
        data = bpy.data.cameras.new(name)
        data.lens = lens
        camera = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(camera)
        camera.location = location
        _look_at(camera, target)
        return camera

    def setup_diagnostics(self) -> dict[str, object]:
        """Create deterministic perspective, top, and low-angle cameras."""
        if self.object is None:
            raise RuntimeError("build terrain before creating diagnostic cameras")
        self._ensure_light()

        stats = self.last_stats or BuildStats(0, 0, 0.0, 0.0)
        vertical_span = max(20.0, stats.max_height - stats.min_height)
        target_z = stats.min_height + vertical_span * 0.38
        target = (0.0, 0.0, target_z)
        size = self.size
        high_z = stats.max_height + size * 0.90

        return {
            "perspective": self._make_camera(
                "Perspective",
                location=(size * 0.58, -size * 0.86, stats.max_height + size * 0.40),
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
                location=(-size * 0.76, -size * 0.70, stats.max_height + size * 0.17),
                target=target,
                lens=58.0,
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
        scene.render.engine = "BLENDER_EEVEE"
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

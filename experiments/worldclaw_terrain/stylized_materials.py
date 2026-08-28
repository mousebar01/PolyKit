"""Stylized game-oriented Blender terrain materials.

This path intentionally prioritizes readable large color masses, landmark
silhouettes, and controllable semantic layers over photorealistic surface
reconstruction. It consumes the same WorldClaw-derived mesh attributes as the
existing volcanic shader, so geometry generation stays shared.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

try:
    import bpy  # type: ignore
except ImportError:  # pragma: no cover
    bpy = None


Color = tuple[float, float, float, float]


@dataclass(kw_only=True)
class StylizedMaterialSettings:
    name: str = "stylized_adventure"
    enable_lava: bool = True

    macro_scale: float = 3.4
    macro_detail: float = 2.0
    macro_roughness: float = 0.52
    macro_shadow: float = 0.84
    macro_mid: float = 0.99
    macro_highlight: float = 1.10

    rock_tint: Color = (0.16, 0.145, 0.18, 1.0)
    rock_mix_strength: float = 0.68
    ash_tint: Color = (0.34, 0.30, 0.30, 1.0)
    ash_mix_strength: float = 0.72
    highland_tint: Color = (0.72, 0.69, 0.62, 1.0)
    highland_mix_strength: float = 0.10

    micro_scale: float = 22.0
    micro_detail: float = 2.4
    micro_roughness: float = 0.58
    crack_scale: float = 18.0
    crack_width: float = 0.055
    crack_strength: float = 0.34
    bump_strength: float = 0.22
    bump_distance: float = 0.55

    base_roughness: float = 0.56
    roughness_noise_strength: float = 0.12
    ash_roughness_boost: float = 0.18
    lava_roughness_drop: float = 0.27
    min_roughness: float = 0.27
    max_roughness: float = 0.90

    lava_mix_strength: float = 0.96
    lava_emission_strength: float = 6.0
    lava_cool: Color = (0.16, 0.008, 0.002, 1.0)
    lava_red: Color = (0.95, 0.035, 0.004, 1.0)
    lava_orange: Color = (1.0, 0.22, 0.010, 1.0)
    lava_hot: Color = (1.0, 0.78, 0.16, 1.0)
    lava_core: Color = (1.0, 0.98, 0.64, 1.0)

    sun_energy: float = 2.35
    sun_angle_degrees: float = 11.0
    fill_energy: float = 850.0
    world_color: Color = (0.055, 0.085, 0.16, 1.0)
    world_strength: float = 0.58
    exposure: float = 0.10


STYLIZED_VOLCANIC_SETTINGS = StylizedMaterialSettings(
    name="stylized_volcanic",
    enable_lava=True,
    macro_scale=3.8,
    macro_shadow=0.77,
    macro_mid=0.95,
    macro_highlight=1.08,
    rock_tint=(0.105, 0.095, 0.13, 1.0),
    rock_mix_strength=0.82,
    ash_tint=(0.30, 0.27, 0.29, 1.0),
    ash_mix_strength=0.78,
    highland_tint=(0.48, 0.40, 0.39, 1.0),
    highland_mix_strength=0.08,
    micro_scale=25.0,
    crack_scale=20.0,
    crack_width=0.048,
    crack_strength=0.42,
    bump_strength=0.27,
    bump_distance=0.68,
    base_roughness=0.60,
    lava_emission_strength=6.8,
    world_color=(0.045, 0.055, 0.11, 1.0),
    world_strength=0.50,
)


def _require_blender() -> None:
    if bpy is None:
        raise RuntimeError("stylized material construction must run inside Blender")


def _socket(collection, *names: str):
    for name in names:
        socket = collection.get(name)
        if socket is not None:
            return socket
    raise KeyError(f"none of these sockets exist: {', '.join(names)}")


def _maybe_socket(collection, *names: str):
    for name in names:
        socket = collection.get(name)
        if socket is not None:
            return socket
    return None


def _set_input(node, value, *names: str) -> None:
    socket = _maybe_socket(node.inputs, *names)
    if socket is not None:
        socket.default_value = value


def _attribute(nodes, name: str, *, x: float, y: float):
    node = nodes.new("ShaderNodeAttribute")
    node.attribute_type = "GEOMETRY"
    node.attribute_name = name
    node.label = name
    node.location = (x, y)
    return node


def _math(nodes, operation: str, *, x: float, y: float, clamp: bool = False):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.use_clamp = clamp
    node.location = (x, y)
    return node


def _mix(nodes, *, x: float, y: float, blend_type: str = "MIX"):
    node = nodes.new("ShaderNodeMixRGB")
    node.blend_type = blend_type
    node.use_clamp = True
    node.location = (x, y)
    return node


def _set_ramp(
    ramp_node,
    stops: Iterable[tuple[float, Color]],
    *,
    interpolation: str = "LINEAR",
) -> None:
    values = sorted(stops, key=lambda item: item[0])
    if len(values) < 2:
        raise ValueError("color ramp requires at least two stops")
    ramp = ramp_node.color_ramp
    ramp.interpolation = interpolation
    while len(ramp.elements) > 2:
        ramp.elements.remove(ramp.elements[-1])
    ramp.elements[0].position = values[0][0]
    ramp.elements[0].color = values[0][1]
    ramp.elements[1].position = values[-1][0]
    ramp.elements[1].color = values[-1][1]
    for position, color in values[1:-1]:
        element = ramp.elements.new(position)
        element.color = color


def build_stylized_material(material, settings: StylizedMaterialSettings | None = None):
    """Build a stylized terrain material from shared semantic surface fields."""
    _require_blender()
    settings = settings or StylizedMaterialSettings()
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (1320, 100)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (1040, 100)
    _set_input(bsdf, 0.0, "Metallic")
    _set_input(bsdf, 1.42, "IOR")

    terrain_color = _attribute(nodes, "TerrainColor", x=-1460, y=610)
    height = _attribute(nodes, "height01", x=-1460, y=420)
    slope = _attribute(nodes, "slope01", x=-1460, y=250)
    rock = _attribute(nodes, "rock_mask", x=-1460, y=80)
    ash = _attribute(nodes, "ash_mask", x=-1460, y=-90)
    heat = _attribute(nodes, "lava_heat", x=-1460, y=-260)

    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.location = (-1460, -560)

    # Broad, stepped color variation is a graphic art-direction layer, not a
    # realistic albedo reconstruction.
    macro = nodes.new("ShaderNodeTexNoise")
    macro.noise_dimensions = "3D"
    macro.location = (-1200, 650)
    _set_input(macro, settings.macro_scale, "Scale")
    _set_input(macro, settings.macro_detail, "Detail")
    _set_input(macro, settings.macro_roughness, "Roughness")
    _set_input(macro, 0.12, "Distortion")
    links.new(_socket(texcoord.outputs, "Generated"), _socket(macro.inputs, "Vector"))

    bands = nodes.new("ShaderNodeValToRGB")
    bands.location = (-940, 650)
    _set_ramp(
        bands,
        [
            (0.00, (settings.macro_shadow,) * 3 + (1.0,)),
            (0.42, (settings.macro_shadow,) * 3 + (1.0,)),
            (0.43, (settings.macro_mid,) * 3 + (1.0,)),
            (0.69, (settings.macro_mid,) * 3 + (1.0,)),
            (0.70, (settings.macro_highlight,) * 3 + (1.0,)),
            (1.00, (settings.macro_highlight,) * 3 + (1.0,)),
        ],
        interpolation="CONSTANT",
    )
    links.new(_socket(macro.outputs, "Fac"), _socket(bands.inputs, "Fac"))

    color_bands = _mix(nodes, x=-680, y=560, blend_type="MULTIPLY")
    color_bands.inputs[0].default_value = 1.0
    links.new(_socket(terrain_color.outputs, "Color"), color_bands.inputs[1])
    links.new(_socket(bands.outputs, "Color"), color_bands.inputs[2])

    # Rock exposure is driven by both semantic rock intent and actual slope.
    rock_slope = _math(nodes, "MULTIPLY", x=-1120, y=160, clamp=True)
    links.new(_socket(rock.outputs, "Fac"), rock_slope.inputs[0])
    links.new(_socket(slope.outputs, "Fac"), rock_slope.inputs[1])
    rock_gain = _math(nodes, "MULTIPLY", x=-900, y=180, clamp=True)
    rock_gain.inputs[1].default_value = settings.rock_mix_strength
    links.new(rock_slope.outputs[0], rock_gain.inputs[0])

    rock_mix = _mix(nodes, x=-430, y=460)
    rock_mix.inputs[2].default_value = settings.rock_tint
    links.new(rock_gain.outputs[0], _socket(rock_mix.inputs, "Fac"))
    links.new(_socket(color_bands.outputs, "Color"), rock_mix.inputs[1])

    ash_gain = _math(nodes, "MULTIPLY", x=-900, y=-40, clamp=True)
    ash_gain.inputs[1].default_value = settings.ash_mix_strength
    links.new(_socket(ash.outputs, "Fac"), ash_gain.inputs[0])
    ash_mix = _mix(nodes, x=-190, y=390)
    ash_mix.inputs[2].default_value = settings.ash_tint
    links.new(ash_gain.outputs[0], _socket(ash_mix.inputs, "Fac"))
    links.new(_socket(rock_mix.outputs, "Color"), ash_mix.inputs[1])

    highland_map = nodes.new("ShaderNodeMapRange")
    highland_map.location = (-900, 360)
    highland_map.clamp = True
    highland_map.interpolation_type = "SMOOTHSTEP"
    highland_map.inputs["From Min"].default_value = 0.58
    highland_map.inputs["From Max"].default_value = 0.96
    highland_map.inputs["To Min"].default_value = 0.0
    highland_map.inputs["To Max"].default_value = settings.highland_mix_strength
    links.new(_socket(height.outputs, "Fac"), highland_map.inputs["Value"])
    highland_mix = _mix(nodes, x=40, y=360)
    highland_mix.inputs[2].default_value = settings.highland_tint
    links.new(_socket(highland_map.outputs, "Result"), _socket(highland_mix.inputs, "Fac"))
    links.new(_socket(ash_mix.outputs, "Color"), highland_mix.inputs[1])

    # Lava is optional. Heat remains zero in ordinary scenes, so the same graph
    # can be reused across biomes without a separate material implementation.
    lava_ramp = nodes.new("ShaderNodeValToRGB")
    lava_ramp.location = (-620, -180)
    _set_ramp(
        lava_ramp,
        [
            (0.00, settings.lava_cool),
            (0.24, settings.lava_cool),
            (0.40, settings.lava_red),
            (0.62, settings.lava_orange),
            (0.82, settings.lava_hot),
            (1.00, settings.lava_core),
        ],
        interpolation="EASE",
    )
    links.new(_socket(heat.outputs, "Fac"), _socket(lava_ramp.inputs, "Fac"))

    lava_factor = _math(nodes, "MULTIPLY", x=-380, y=-170, clamp=True)
    lava_factor.inputs[1].default_value = settings.lava_mix_strength if settings.enable_lava else 0.0
    links.new(_socket(heat.outputs, "Fac"), lava_factor.inputs[0])
    lava_mix = _mix(nodes, x=300, y=290)
    links.new(lava_factor.outputs[0], _socket(lava_mix.inputs, "Fac"))
    links.new(_socket(highland_mix.outputs, "Color"), lava_mix.inputs[1])
    links.new(_socket(lava_ramp.outputs, "Color"), lava_mix.inputs[2])
    links.new(_socket(lava_mix.outputs, "Color"), _socket(bsdf.inputs, "Base Color"))

    # Micro detail is intentionally mild. Voronoi fractures are confined to
    # rocky/hot surfaces so grass and playable flats remain visually clean.
    micro = nodes.new("ShaderNodeTexNoise")
    micro.noise_dimensions = "3D"
    micro.location = (-1180, -520)
    _set_input(micro, settings.micro_scale, "Scale")
    _set_input(micro, settings.micro_detail, "Detail")
    _set_input(micro, settings.micro_roughness, "Roughness")
    _set_input(micro, 0.08, "Distortion")
    links.new(_socket(texcoord.outputs, "Generated"), _socket(micro.inputs, "Vector"))

    voronoi = nodes.new("ShaderNodeTexVoronoi")
    voronoi.voronoi_dimensions = "3D"
    voronoi.feature = "DISTANCE_TO_EDGE"
    voronoi.location = (-1180, -760)
    _set_input(voronoi, settings.crack_scale, "Scale")
    links.new(_socket(texcoord.outputs, "Generated"), _socket(voronoi.inputs, "Vector"))

    crack = nodes.new("ShaderNodeMapRange")
    crack.location = (-920, -740)
    crack.clamp = True
    crack.interpolation_type = "SMOOTHERSTEP"
    crack.inputs["From Min"].default_value = 0.0
    crack.inputs["From Max"].default_value = settings.crack_width
    crack.inputs["To Min"].default_value = 1.0
    crack.inputs["To Max"].default_value = 0.0
    links.new(_socket(voronoi.outputs, "Distance"), crack.inputs["Value"])

    fracture_surface = _math(nodes, "MAXIMUM", x=-650, y=-670, clamp=True)
    links.new(_socket(rock.outputs, "Fac"), fracture_surface.inputs[0])
    links.new(_socket(heat.outputs, "Fac"), fracture_surface.inputs[1])
    crack_depth = _math(nodes, "MULTIPLY", x=-440, y=-690)
    crack_depth.inputs[1].default_value = settings.crack_strength
    links.new(_socket(crack.outputs, "Result"), crack_depth.inputs[0])
    masked_crack = _math(nodes, "MULTIPLY", x=-210, y=-680)
    links.new(crack_depth.outputs[0], masked_crack.inputs[0])
    links.new(fracture_surface.outputs[0], masked_crack.inputs[1])

    micro_height = _math(nodes, "MULTIPLY", x=-650, y=-480)
    micro_height.inputs[1].default_value = 0.52
    links.new(_socket(micro.outputs, "Fac"), micro_height.inputs[0])
    bump_height = _math(nodes, "SUBTRACT", x=20, y=-520)
    links.new(micro_height.outputs[0], bump_height.inputs[0])
    links.new(masked_crack.outputs[0], bump_height.inputs[1])

    bump = nodes.new("ShaderNodeBump")
    bump.location = (670, -420)
    _set_input(bump, settings.bump_strength, "Strength")
    _set_input(bump, settings.bump_distance, "Distance")
    links.new(bump_height.outputs[0], _socket(bump.inputs, "Height"))
    links.new(_socket(bump.outputs, "Normal"), _socket(bsdf.inputs, "Normal"))

    rough_noise = _math(nodes, "MULTIPLY", x=-430, y=-370)
    rough_noise.inputs[1].default_value = settings.roughness_noise_strength
    links.new(_socket(micro.outputs, "Fac"), rough_noise.inputs[0])
    rough_base = _math(nodes, "ADD", x=-200, y=-340)
    rough_base.inputs[0].default_value = settings.base_roughness
    links.new(rough_noise.outputs[0], rough_base.inputs[1])
    ash_rough = _math(nodes, "MULTIPLY", x=-420, y=-270)
    ash_rough.inputs[1].default_value = settings.ash_roughness_boost
    links.new(_socket(ash.outputs, "Fac"), ash_rough.inputs[0])
    rough_ash = _math(nodes, "ADD", x=30, y=-330)
    links.new(rough_base.outputs[0], rough_ash.inputs[0])
    links.new(ash_rough.outputs[0], rough_ash.inputs[1])
    heat_rough = _math(nodes, "MULTIPLY", x=-190, y=-220)
    heat_rough.inputs[1].default_value = settings.lava_roughness_drop
    links.new(_socket(heat.outputs, "Fac"), heat_rough.inputs[0])
    rough_sub = _math(nodes, "SUBTRACT", x=260, y=-310)
    links.new(rough_ash.outputs[0], rough_sub.inputs[0])
    links.new(heat_rough.outputs[0], rough_sub.inputs[1])
    rough_min = _math(nodes, "MAXIMUM", x=470, y=-300)
    rough_min.inputs[1].default_value = settings.min_roughness
    links.new(rough_sub.outputs[0], rough_min.inputs[0])
    rough_max = _math(nodes, "MINIMUM", x=670, y=-280)
    rough_max.inputs[1].default_value = settings.max_roughness
    links.new(rough_min.outputs[0], rough_max.inputs[0])
    links.new(rough_max.outputs[0], _socket(bsdf.inputs, "Roughness"))

    if settings.enable_lava:
        hot_cracks = _math(nodes, "MULTIPLY", x=-190, y=-850, clamp=True)
        links.new(_socket(crack.outputs, "Result"), hot_cracks.inputs[0])
        links.new(_socket(heat.outputs, "Fac"), hot_cracks.inputs[1])
        heat_power = _math(nodes, "POWER", x=20, y=-830, clamp=True)
        heat_power.inputs[1].default_value = 1.55
        links.new(_socket(heat.outputs, "Fac"), heat_power.inputs[0])
        crack_boost = _math(nodes, "MULTIPLY", x=20, y=-940)
        crack_boost.inputs[1].default_value = 0.72
        links.new(hot_cracks.outputs[0], crack_boost.inputs[0])
        emission_mask = _math(nodes, "ADD", x=250, y=-850, clamp=True)
        links.new(heat_power.outputs[0], emission_mask.inputs[0])
        links.new(crack_boost.outputs[0], emission_mask.inputs[1])
        emission_strength = _math(nodes, "MULTIPLY", x=470, y=-840)
        emission_strength.inputs[1].default_value = settings.lava_emission_strength
        links.new(emission_mask.outputs[0], emission_strength.inputs[0])

        emission_color = _maybe_socket(bsdf.inputs, "Emission Color", "Emission")
        emission_strength_socket = _maybe_socket(bsdf.inputs, "Emission Strength")
        if emission_color is not None:
            links.new(_socket(lava_ramp.outputs, "Color"), emission_color)
        if emission_strength_socket is not None:
            links.new(emission_strength.outputs[0], emission_strength_socket)

    links.new(_socket(bsdf.outputs, "BSDF"), _socket(output.inputs, "Surface"))
    material["worldclaw_style"] = settings.name
    return material

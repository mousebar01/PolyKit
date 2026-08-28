"""Blender shader builders for WorldClaw terrain experiments.

The volcanic profile deliberately consumes semantic mesh attributes rather than
hard-coding one texture. The same height/slope/heat/ash fields can later drive
scatter or agent diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import bpy  # type: ignore
except ImportError:  # pragma: no cover
    bpy = None


@dataclass(kw_only=True)
class VolcanicMaterialSettings:
    basalt_dark: tuple[float, float, float, float] = (0.012, 0.014, 0.017, 1.0)
    basalt_light: tuple[float, float, float, float] = (0.095, 0.078, 0.060, 1.0)
    ash_dark: tuple[float, float, float, float] = (0.095, 0.087, 0.082, 1.0)
    ash_light: tuple[float, float, float, float] = (0.28, 0.245, 0.205, 1.0)
    lava_cool: tuple[float, float, float, float] = (0.10, 0.003, 0.001, 1.0)
    lava_red: tuple[float, float, float, float] = (0.75, 0.025, 0.002, 1.0)
    lava_orange: tuple[float, float, float, float] = (1.0, 0.18, 0.006, 1.0)
    lava_hot: tuple[float, float, float, float] = (1.0, 0.72, 0.12, 1.0)
    lava_white: tuple[float, float, float, float] = (1.0, 0.96, 0.68, 1.0)
    emission_strength: float = 7.5
    basalt_roughness: float = 0.78
    ash_roughness: float = 0.96
    hot_roughness: float = 0.34
    bump_strength: float = 0.42
    bump_distance: float = 0.24
    macro_scale: float = 3.2
    detail_scale: float = 31.0
    crack_scale: float = 18.0


def _require_blender() -> None:
    if bpy is None:
        raise RuntimeError("material construction must run inside Blender")


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
    node.location = (x, y)
    return node


def _set_ramp(ramp_node, stops: list[tuple[float, tuple[float, float, float, float]]]) -> None:
    ramp = ramp_node.color_ramp
    while len(ramp.elements) > 2:
        ramp.elements.remove(ramp.elements[-1])
    stops = sorted(stops, key=lambda item: item[0])
    ramp.elements[0].position = stops[0][0]
    ramp.elements[0].color = stops[0][1]
    ramp.elements[1].position = stops[-1][0]
    ramp.elements[1].color = stops[-1][1]
    for position, color in stops[1:-1]:
        element = ramp.elements.new(position)
        element.color = color


def _terrain_color_node(nodes):
    try:
        node = nodes.new("ShaderNodeVertexColor")
        node.layer_name = "TerrainColor"
    except RuntimeError:
        node = nodes.new("ShaderNodeAttribute")
        node.attribute_name = "TerrainColor"
    return node


def build_generic_material(material, *, detail_bump: bool = True):
    """Region-color material with cheap micro detail for non-volcanic scenes."""
    _require_blender()
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (760, 20)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (480, 20)
    _set_input(bsdf, 0.82, "Roughness")

    terrain_color = _terrain_color_node(nodes)
    terrain_color.location = (-560, 80)
    links.new(_socket(terrain_color.outputs, "Color"), _socket(bsdf.inputs, "Base Color"))

    if detail_bump:
        texcoord = nodes.new("ShaderNodeTexCoord")
        texcoord.location = (-780, -180)
        noise = nodes.new("ShaderNodeTexNoise")
        noise.noise_dimensions = "3D"
        noise.location = (-520, -180)
        _set_input(noise, 22.0, "Scale")
        _set_input(noise, 6.0, "Detail")
        _set_input(noise, 0.65, "Roughness")
        bump = nodes.new("ShaderNodeBump")
        bump.location = (220, -160)
        _set_input(bump, 0.24, "Strength")
        _set_input(bump, 0.18, "Distance")
        links.new(_socket(texcoord.outputs, "Generated"), _socket(noise.inputs, "Vector"))
        links.new(_socket(noise.outputs, "Fac"), _socket(bump.inputs, "Height"))
        links.new(_socket(bump.outputs, "Normal"), _socket(bsdf.inputs, "Normal"))

    links.new(_socket(bsdf.outputs, "BSDF"), _socket(output.inputs, "Surface"))
    return material


def build_volcanic_material(material, settings: VolcanicMaterialSettings | None = None):
    """Procedural basalt/ash/lava shader driven by terrain attributes."""
    _require_blender()
    settings = settings or VolcanicMaterialSettings()
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (1360, 80)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (1080, 80)
    _set_input(bsdf, 0.02, "Metallic")
    _set_input(bsdf, 1.46, "IOR")

    heat = _attribute(nodes, "lava_heat", x=-1100, y=520)
    ash = _attribute(nodes, "ash_mask", x=-1100, y=350)
    slope = _attribute(nodes, "slope01", x=-1100, y=180)
    rock = _attribute(nodes, "rock_mask", x=-1100, y=10)

    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.location = (-1120, -320)

    macro_noise = nodes.new("ShaderNodeTexNoise")
    macro_noise.noise_dimensions = "3D"
    try:
        macro_noise.noise_type = "MULTIFRACTAL"
    except (AttributeError, TypeError, ValueError):
        pass
    macro_noise.location = (-850, -260)
    _set_input(macro_noise, settings.macro_scale, "Scale")
    _set_input(macro_noise, 5.0, "Detail")
    _set_input(macro_noise, 0.62, "Roughness")
    _set_input(macro_noise, 0.18, "Distortion")

    detail_noise = nodes.new("ShaderNodeTexNoise")
    detail_noise.noise_dimensions = "3D"
    try:
        detail_noise.noise_type = "RIDGED_MULTIFRACTAL"
    except (AttributeError, TypeError, ValueError):
        pass
    detail_noise.location = (-850, -500)
    _set_input(detail_noise, settings.detail_scale, "Scale")
    _set_input(detail_noise, 7.0, "Detail")
    _set_input(detail_noise, 0.7, "Roughness")

    voronoi = nodes.new("ShaderNodeTexVoronoi")
    voronoi.voronoi_dimensions = "3D"
    voronoi.feature = "DISTANCE_TO_EDGE"
    voronoi.distance = "EUCLIDEAN"
    voronoi.location = (-850, -720)
    _set_input(voronoi, settings.crack_scale, "Scale")
    _set_input(voronoi, 0.9, "Randomness")

    generated = _socket(texcoord.outputs, "Generated")
    links.new(generated, _socket(macro_noise.inputs, "Vector"))
    links.new(generated, _socket(detail_noise.inputs, "Vector"))
    links.new(generated, _socket(voronoi.inputs, "Vector"))

    basalt_ramp = nodes.new("ShaderNodeValToRGB")
    basalt_ramp.location = (-500, -220)
    _set_ramp(basalt_ramp, [(0.15, settings.basalt_dark), (0.50, (0.035, 0.032, 0.029, 1.0)), (0.86, settings.basalt_light)])
    links.new(_socket(macro_noise.outputs, "Fac"), _socket(basalt_ramp.inputs, "Fac"))

    ash_ramp = nodes.new("ShaderNodeValToRGB")
    ash_ramp.location = (-500, -20)
    _set_ramp(ash_ramp, [(0.12, settings.ash_dark), (0.52, (0.16, 0.145, 0.13, 1.0)), (0.88, settings.ash_light)])
    links.new(_socket(macro_noise.outputs, "Fac"), _socket(ash_ramp.inputs, "Fac"))

    ash_mix = _mix(nodes, x=-180, y=-80)
    links.new(_socket(ash.outputs, "Fac"), _socket(ash_mix.inputs, "Fac"))
    links.new(_socket(basalt_ramp.outputs, "Color"), ash_mix.inputs[1])
    links.new(_socket(ash_ramp.outputs, "Color"), ash_mix.inputs[2])

    crack_ramp = nodes.new("ShaderNodeValToRGB")
    crack_ramp.location = (-500, -690)
    _set_ramp(crack_ramp, [(0.0, (1.0, 1.0, 1.0, 1.0)), (0.025, (0.92, 0.92, 0.92, 1.0)), (0.075, (0.0, 0.0, 0.0, 1.0)), (0.18, (0.0, 0.0, 0.0, 1.0))])
    links.new(_socket(voronoi.outputs, "Distance"), _socket(crack_ramp.inputs, "Fac"))

    lava_ramp = nodes.new("ShaderNodeValToRGB")
    lava_ramp.location = (-470, 510)
    _set_ramp(lava_ramp, [(0.0, settings.lava_cool), (0.22, settings.lava_red), (0.52, settings.lava_orange), (0.80, settings.lava_hot), (1.0, settings.lava_white)])
    links.new(_socket(heat.outputs, "Fac"), _socket(lava_ramp.inputs, "Fac"))

    heat_noise_mul = _math(nodes, "MULTIPLY", x=-190, y=430, clamp=True)
    heat_noise_mul.inputs[1].default_value = 1.25
    links.new(_socket(heat.outputs, "Fac"), heat_noise_mul.inputs[0])
    detail_center = _math(nodes, "SUBTRACT", x=-470, y=300)
    detail_center.inputs[1].default_value = 0.35
    links.new(_socket(detail_noise.outputs, "Fac"), detail_center.inputs[0])
    detail_gain = _math(nodes, "MULTIPLY", x=-270, y=300)
    detail_gain.inputs[1].default_value = 0.35
    links.new(detail_center.outputs[0], detail_gain.inputs[0])
    heat_mod = _math(nodes, "ADD", x=20, y=390, clamp=True)
    links.new(heat_noise_mul.outputs[0], heat_mod.inputs[0])
    links.new(detail_gain.outputs[0], heat_mod.inputs[1])

    base_lava_mix = _mix(nodes, x=320, y=120)
    links.new(heat_mod.outputs[0], _socket(base_lava_mix.inputs, "Fac"))
    links.new(_socket(ash_mix.outputs, "Color"), base_lava_mix.inputs[1])
    links.new(_socket(lava_ramp.outputs, "Color"), base_lava_mix.inputs[2])
    links.new(_socket(base_lava_mix.outputs, "Color"), _socket(bsdf.inputs, "Base Color"))

    crack_heat = _math(nodes, "MULTIPLY", x=-50, y=620, clamp=True)
    links.new(_socket(heat.outputs, "Fac"), crack_heat.inputs[0])
    links.new(_socket(crack_ramp.outputs, "Color"), crack_heat.inputs[1])
    crack_boost = _math(nodes, "MULTIPLY", x=170, y=620)
    crack_boost.inputs[1].default_value = 2.2
    links.new(crack_heat.outputs[0], crack_boost.inputs[0])
    emission_factor = _math(nodes, "ADD", x=380, y=600, clamp=False)
    links.new(heat_mod.outputs[0], emission_factor.inputs[0])
    links.new(crack_boost.outputs[0], emission_factor.inputs[1])
    emission_strength = _math(nodes, "MULTIPLY", x=600, y=600, clamp=False)
    emission_strength.inputs[1].default_value = settings.emission_strength
    links.new(emission_factor.outputs[0], emission_strength.inputs[0])

    emission_color_socket = _maybe_socket(bsdf.inputs, "Emission Color", "Emission")
    emission_strength_socket = _maybe_socket(bsdf.inputs, "Emission Strength")
    if emission_color_socket is not None:
        links.new(_socket(lava_ramp.outputs, "Color"), emission_color_socket)
    if emission_strength_socket is not None:
        links.new(emission_strength.outputs[0], emission_strength_socket)

    ash_rough = _math(nodes, "MULTIPLY", x=210, y=-170)
    ash_rough.inputs[1].default_value = settings.ash_roughness - settings.basalt_roughness
    links.new(_socket(ash.outputs, "Fac"), ash_rough.inputs[0])
    rough_base = _math(nodes, "ADD", x=410, y=-170)
    rough_base.inputs[0].default_value = settings.basalt_roughness
    links.new(ash_rough.outputs[0], rough_base.inputs[1])
    heat_rough = _math(nodes, "MULTIPLY", x=410, y=-310)
    heat_rough.inputs[1].default_value = max(0.0, settings.ash_roughness - settings.hot_roughness)
    links.new(_socket(heat.outputs, "Fac"), heat_rough.inputs[0])
    roughness = _math(nodes, "SUBTRACT", x=650, y=-190, clamp=True)
    links.new(rough_base.outputs[0], roughness.inputs[0])
    links.new(heat_rough.outputs[0], roughness.inputs[1])
    links.new(roughness.outputs[0], _socket(bsdf.inputs, "Roughness"))

    crack_invert = _math(nodes, "MULTIPLY", x=-270, y=-620)
    crack_invert.inputs[1].default_value = -0.55
    links.new(_socket(crack_ramp.outputs, "Color"), crack_invert.inputs[0])
    bump_height = _math(nodes, "ADD", x=-60, y=-510)
    links.new(_socket(detail_noise.outputs, "Fac"), bump_height.inputs[0])
    links.new(crack_invert.outputs[0], bump_height.inputs[1])
    rock_slope = _math(nodes, "MULTIPLY", x=-60, y=-320, clamp=True)
    links.new(_socket(rock.outputs, "Fac"), rock_slope.inputs[0])
    links.new(_socket(slope.outputs, "Fac"), rock_slope.inputs[1])
    bump_strength = _math(nodes, "ADD", x=170, y=-390, clamp=True)
    bump_strength.inputs[0].default_value = settings.bump_strength * 0.45
    links.new(rock_slope.outputs[0], bump_strength.inputs[1])

    bump = nodes.new("ShaderNodeBump")
    bump.location = (700, -470)
    _set_input(bump, settings.bump_distance, "Distance")
    links.new(bump_strength.outputs[0], _socket(bump.inputs, "Strength"))
    links.new(bump_height.outputs[0], _socket(bump.inputs, "Height"))
    links.new(_socket(bump.outputs, "Normal"), _socket(bsdf.inputs, "Normal"))

    links.new(_socket(bsdf.outputs, "BSDF"), _socket(output.inputs, "Surface"))
    return material

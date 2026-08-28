"""Procedural Blender materials for the WorldClaw terrain experiment.

The material deliberately targets stylized game readability: large semantic
color groups, stepped macro variation, clean slope rock exposure, restrained
micro bump, and optional volcanic ash/lava layers.  All semantic controls come
from named mesh attributes written by :mod:`terrain`.
"""
from __future__ import annotations

from typing import Iterable

from .styles import StylizedTerrainStyle

try:  # Importable outside Blender for static checks.
    import bpy  # type: ignore
except ImportError:  # pragma: no cover - exercised in Blender.
    bpy = None


def _require_blender() -> None:
    if bpy is None:
        raise RuntimeError("worldclaw_terrain.materials must run inside Blender")


def _socket(node, *names: str):
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    raise KeyError(f"{node.bl_idname} has none of the sockets {names!r}")


def _attribute(nodes, name: str, x: float, y: float):
    node = nodes.new("ShaderNodeAttribute")
    node.attribute_type = "GEOMETRY"
    node.attribute_name = name
    node.label = name
    node.location = (x, y)
    return node


def _math(nodes, operation: str, x: float, y: float, *, label: str = ""):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.location = (x, y)
    node.label = label or operation.title()
    return node


def _mix(nodes, x: float, y: float, *, label: str = ""):
    node = nodes.new("ShaderNodeMixRGB")
    node.blend_type = "MIX"
    node.use_clamp = True
    node.location = (x, y)
    node.label = label
    return node


def _set_ramp(node, stops: Iterable[tuple[float, tuple[float, float, float, float]]], *, interpolation: str = "LINEAR") -> None:
    values = list(stops)
    if len(values) < 2:
        raise ValueError("a color ramp requires at least two stops")
    ramp = node.color_ramp
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


def build_stylized_terrain_material(name: str, style: StylizedTerrainStyle):
    """Create/update the stylized terrain material used by the prototype.

    Required mesh attributes:
      - ``TerrainColor`` (color)
      - ``height01`` (float)
      - ``slope01`` (float)
      - ``lava_heat`` (float)
      - ``ash_mask`` (float)

    ``height01`` is currently retained as an authoring/debug signal and for
    future biome rules; slope, ash, and lava are already consumed here.
    """
    _require_blender()

    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.use_nodes = True

    tree = material.node_tree
    nodes = tree.nodes
    links = tree.links
    nodes.clear()

    # ------------------------------------------------------------------
    # Inputs / semantic fields
    # ------------------------------------------------------------------
    terrain_color = _attribute(nodes, "TerrainColor", -1500.0, 520.0)
    slope = _attribute(nodes, "slope01", -1500.0, 260.0)
    ash = _attribute(nodes, "ash_mask", -1500.0, 60.0)
    heat = _attribute(nodes, "lava_heat", -1500.0, -180.0)
    _attribute(nodes, "height01", -1500.0, -400.0)  # Visible for inspection.

    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.location = (-1500.0, -720.0)

    # ------------------------------------------------------------------
    # Broad, deliberately stepped color breakup.
    # ------------------------------------------------------------------
    macro_noise = nodes.new("ShaderNodeTexNoise")
    macro_noise.location = (-1230.0, 700.0)
    macro_noise.noise_dimensions = "3D"
    _socket(macro_noise, "Scale").default_value = style.macro_noise_scale
    _socket(macro_noise, "Detail").default_value = style.macro_noise_detail
    _socket(macro_noise, "Roughness").default_value = style.macro_noise_roughness
    if macro_noise.inputs.get("Distortion") is not None:
        macro_noise.inputs["Distortion"].default_value = 0.16
    links.new(texcoord.outputs["Generated"], macro_noise.inputs["Vector"])

    macro_bands = nodes.new("ShaderNodeValToRGB")
    macro_bands.location = (-990.0, 700.0)
    macro_bands.label = "Graphic macro bands"
    _set_ramp(
        macro_bands,
        (
            (0.0, (style.macro_shadow,) * 3 + (1.0,)),
            (0.43, (style.macro_shadow,) * 3 + (1.0,)),
            (0.44, (style.macro_mid,) * 3 + (1.0,)),
            (0.68, (style.macro_mid,) * 3 + (1.0,)),
            (0.69, (style.macro_highlight,) * 3 + (1.0,)),
            (1.0, (style.macro_highlight,) * 3 + (1.0,)),
        ),
        interpolation="CONSTANT",
    )
    links.new(macro_noise.outputs["Fac"], macro_bands.inputs["Fac"])

    macro_multiply = nodes.new("ShaderNodeMixRGB")
    macro_multiply.blend_type = "MULTIPLY"
    macro_multiply.inputs[0].default_value = 1.0
    macro_multiply.location = (-720.0, 540.0)
    macro_multiply.label = "Semantic color × graphic bands"
    links.new(terrain_color.outputs["Color"], macro_multiply.inputs[1])
    links.new(macro_bands.outputs["Color"], macro_multiply.inputs[2])

    # ------------------------------------------------------------------
    # Slope-based rock grouping: broad and readable rather than realistic.
    # ------------------------------------------------------------------
    slope_range = nodes.new("ShaderNodeMapRange")
    slope_range.location = (-980.0, 290.0)
    slope_range.clamp = True
    slope_range.interpolation_type = "SMOOTHSTEP"
    slope_range.inputs["From Min"].default_value = style.slope_rock_start
    slope_range.inputs["From Max"].default_value = style.slope_rock_end
    slope_range.inputs["To Min"].default_value = 0.0
    slope_range.inputs["To Max"].default_value = style.slope_rock_strength
    links.new(slope.outputs["Fac"], slope_range.inputs["Value"])

    rock_mix = _mix(nodes, -450.0, 420.0, label="Stylized rock exposure")
    rock_mix.inputs[2].default_value = style.rock_tint
    links.new(slope_range.outputs["Result"], rock_mix.inputs[0])
    links.new(macro_multiply.outputs["Color"], rock_mix.inputs[1])

    ash_strength = _math(nodes, "MULTIPLY", -970.0, 60.0, label="Ash strength")
    ash_strength.inputs[1].default_value = style.ash_strength
    links.new(ash.outputs["Fac"], ash_strength.inputs[0])

    ash_mix = _mix(nodes, -190.0, 360.0, label="Ash / dust overlay")
    ash_mix.inputs[2].default_value = style.ash_tint
    links.new(ash_strength.outputs[0], ash_mix.inputs[0])
    links.new(rock_mix.outputs["Color"], ash_mix.inputs[1])

    # ------------------------------------------------------------------
    # Lava: hot center, dark cooling edges, with graphic fissure emphasis.
    # ------------------------------------------------------------------
    heat_power = _math(nodes, "POWER", -970.0, -180.0, label="Lava core falloff")
    heat_power.inputs[1].default_value = 1.55
    links.new(heat.outputs["Fac"], heat_power.inputs[0])

    lava_ramp = nodes.new("ShaderNodeValToRGB")
    lava_ramp.location = (-700.0, -110.0)
    lava_ramp.label = "Stylized lava temperature"
    _set_ramp(
        lava_ramp,
        (
            (0.00, style.lava_cool),
            (0.22, style.lava_cool),
            (0.38, style.lava_red),
            (0.60, style.lava_orange),
            (0.82, style.lava_hot),
            (1.00, style.lava_core),
        ),
        interpolation="EASE",
    )
    links.new(heat.outputs["Fac"], lava_ramp.inputs["Fac"])

    lava_mix_strength = _math(nodes, "MULTIPLY", -430.0, -80.0, label="Lava surface mix")
    lava_mix_strength.inputs[1].default_value = style.lava_surface_mix
    links.new(heat.outputs["Fac"], lava_mix_strength.inputs[0])

    lava_surface_mix = _mix(nodes, 70.0, 300.0, label="Terrain / lava surface")
    links.new(lava_mix_strength.outputs[0], lava_surface_mix.inputs[0])
    links.new(ash_mix.outputs["Color"], lava_surface_mix.inputs[1])
    links.new(lava_ramp.outputs["Color"], lava_surface_mix.inputs[2])

    # ------------------------------------------------------------------
    # Micro surface and graphic fractures.
    # ------------------------------------------------------------------
    micro_noise = nodes.new("ShaderNodeTexNoise")
    micro_noise.location = (-1230.0, -610.0)
    micro_noise.noise_dimensions = "3D"
    _socket(micro_noise, "Scale").default_value = style.micro_noise_scale
    _socket(micro_noise, "Detail").default_value = style.micro_noise_detail
    _socket(micro_noise, "Roughness").default_value = style.micro_noise_roughness
    if micro_noise.inputs.get("Distortion") is not None:
        micro_noise.inputs["Distortion"].default_value = 0.10
    links.new(texcoord.outputs["Generated"], micro_noise.inputs["Vector"])

    voronoi = nodes.new("ShaderNodeTexVoronoi")
    voronoi.location = (-1230.0, -890.0)
    voronoi.voronoi_dimensions = "3D"
    voronoi.feature = "DISTANCE_TO_EDGE"
    voronoi.inputs["Scale"].default_value = style.crack_scale
    links.new(texcoord.outputs["Generated"], voronoi.inputs["Vector"])

    crack_range = nodes.new("ShaderNodeMapRange")
    crack_range.location = (-960.0, -870.0)
    crack_range.clamp = True
    crack_range.interpolation_type = "SMOOTHERSTEP"
    crack_range.inputs["From Min"].default_value = 0.0
    crack_range.inputs["From Max"].default_value = style.crack_width
    crack_range.inputs["To Min"].default_value = 1.0
    crack_range.inputs["To Max"].default_value = 0.0
    links.new(voronoi.outputs["Distance"], crack_range.inputs["Value"])

    crack_bump = _math(nodes, "MULTIPLY", -690.0, -800.0, label="Fracture depth")
    crack_bump.inputs[1].default_value = style.crack_bump_strength
    links.new(crack_range.outputs["Result"], crack_bump.inputs[0])

    bump_height = _math(nodes, "SUBTRACT", -450.0, -660.0, label="Rock surface height")
    links.new(micro_noise.outputs["Fac"], bump_height.inputs[0])
    links.new(crack_bump.outputs[0], bump_height.inputs[1])

    heat_smooth = _math(nodes, "MULTIPLY", -450.0, -870.0, label="Molten smoothing")
    heat_smooth.inputs[1].default_value = 0.72
    links.new(heat.outputs["Fac"], heat_smooth.inputs[0])
    one_minus_heat = _math(nodes, "SUBTRACT", -240.0, -870.0, label="Cooling crust")
    one_minus_heat.inputs[0].default_value = 1.0
    links.new(heat_smooth.outputs[0], one_minus_heat.inputs[1])

    final_bump_height = _math(nodes, "MULTIPLY", -210.0, -660.0, label="Stylized bump mask")
    links.new(bump_height.outputs[0], final_bump_height.inputs[0])
    links.new(one_minus_heat.outputs[0], final_bump_height.inputs[1])

    bump = nodes.new("ShaderNodeBump")
    bump.location = (120.0, -520.0)
    bump.inputs["Strength"].default_value = style.bump_strength
    bump.inputs["Distance"].default_value = style.bump_distance
    links.new(final_bump_height.outputs[0], bump.inputs["Height"])

    # ------------------------------------------------------------------
    # Roughness: narrow range, ash matte, hot lava smoother.
    # ------------------------------------------------------------------
    rough_noise = _math(nodes, "MULTIPLY", -690.0, -420.0, label="Roughness noise")
    rough_noise.inputs[1].default_value = style.roughness_noise_strength
    links.new(micro_noise.outputs["Fac"], rough_noise.inputs[0])

    rough_base = _math(nodes, "ADD", -470.0, -410.0, label="Base roughness")
    rough_base.inputs[0].default_value = style.base_roughness
    links.new(rough_noise.outputs[0], rough_base.inputs[1])

    ash_rough = _math(nodes, "MULTIPLY", -700.0, -300.0, label="Ash roughness")
    ash_rough.inputs[1].default_value = style.ash_roughness_boost
    links.new(ash.outputs["Fac"], ash_rough.inputs[0])
    rough_with_ash = _math(nodes, "ADD", -260.0, -360.0, label="Roughness + ash")
    links.new(rough_base.outputs[0], rough_with_ash.inputs[0])
    links.new(ash_rough.outputs[0], rough_with_ash.inputs[1])

    heat_rough = _math(nodes, "MULTIPLY", -480.0, -250.0, label="Hot lava smoothness")
    heat_rough.inputs[1].default_value = style.lava_roughness_drop
    links.new(heat.outputs["Fac"], heat_rough.inputs[0])
    rough_sub = _math(nodes, "SUBTRACT", -40.0, -340.0, label="Final roughness raw")
    links.new(rough_with_ash.outputs[0], rough_sub.inputs[0])
    links.new(heat_rough.outputs[0], rough_sub.inputs[1])

    rough_min = _math(nodes, "MAXIMUM", 170.0, -320.0, label="Roughness minimum")
    rough_min.inputs[1].default_value = style.min_roughness
    links.new(rough_sub.outputs[0], rough_min.inputs[0])
    rough_max = _math(nodes, "MINIMUM", 390.0, -320.0, label="Roughness maximum")
    rough_max.inputs[1].default_value = style.max_roughness
    links.new(rough_min.outputs[0], rough_max.inputs[0])

    # ------------------------------------------------------------------
    # Emission: broad core plus brighter hot cracks. This produces a readable
    # game lava language instead of a flat orange painted stripe.
    # ------------------------------------------------------------------
    hot_cracks = _math(nodes, "MULTIPLY", -690.0, -1040.0, label="Hot fissures")
    links.new(crack_range.outputs["Result"], hot_cracks.inputs[0])
    links.new(heat.outputs["Fac"], hot_cracks.inputs[1])

    core_emission = _math(nodes, "MULTIPLY", -440.0, -1010.0, label="Core emission")
    core_emission.inputs[1].default_value = 0.48
    links.new(heat_power.outputs[0], core_emission.inputs[0])
    crack_emission = _math(nodes, "MULTIPLY", -440.0, -1110.0, label="Crack emission")
    crack_emission.inputs[1].default_value = 0.78
    links.new(hot_cracks.outputs[0], crack_emission.inputs[0])

    emission_sum = _math(nodes, "ADD", -190.0, -1030.0, label="Emission mask")
    links.new(core_emission.outputs[0], emission_sum.inputs[0])
    links.new(crack_emission.outputs[0], emission_sum.inputs[1])
    emission_clamp = _math(nodes, "MINIMUM", 30.0, -1030.0, label="Emission clamp")
    emission_clamp.inputs[1].default_value = 1.0
    links.new(emission_sum.outputs[0], emission_clamp.inputs[0])
    emission_strength = _math(nodes, "MULTIPLY", 250.0, -990.0, label="Emission strength")
    emission_strength.inputs[1].default_value = style.lava_emission_strength
    links.new(emission_clamp.outputs[0], emission_strength.inputs[0])

    # ------------------------------------------------------------------
    # Principled output.
    # ------------------------------------------------------------------
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (650.0, 140.0)
    bsdf.label = f"{style.name} terrain"
    links.new(lava_surface_mix.outputs["Color"], _socket(bsdf, "Base Color"))
    links.new(rough_max.outputs[0], _socket(bsdf, "Roughness"))
    links.new(bump.outputs["Normal"], _socket(bsdf, "Normal"))
    links.new(lava_ramp.outputs["Color"], _socket(bsdf, "Emission Color", "Emission"))
    links.new(emission_strength.outputs[0], _socket(bsdf, "Emission Strength"))

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (940.0, 140.0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    material["worldclaw_style"] = style.name
    return material

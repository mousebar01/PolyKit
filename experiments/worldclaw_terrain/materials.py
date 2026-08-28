"""Procedural Blender materials for the WorldClaw terrain experiment.

The material targets stylized game readability: large semantic color groups,
stepped macro variation, clean slope rock exposure, restrained micro bump, and
optional volcanic ash/lava layers. All controls come from named mesh attributes
written by :mod:`terrain`.
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


def _set_ramp(
    node,
    stops: Iterable[tuple[float, tuple[float, float, float, float]]],
    *,
    interpolation: str = "LINEAR",
) -> None:
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
    """Create/update a game-oriented stylized terrain material.

    Required mesh attributes:
      - ``TerrainColor`` (color)
      - ``height01`` (float; authoring/debug and future biome rules)
      - ``slope01`` (float)
      - ``lava_heat`` (float)
      - ``ash_mask`` (float)
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

    terrain_color = _attribute(nodes, "TerrainColor", -1560.0, 540.0)
    slope = _attribute(nodes, "slope01", -1560.0, 280.0)
    ash = _attribute(nodes, "ash_mask", -1560.0, 80.0)
    heat = _attribute(nodes, "lava_heat", -1560.0, -160.0)
    _attribute(nodes, "height01", -1560.0, -390.0)

    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.location = (-1560.0, -720.0)

    # Broad color breakup is deliberately stepped so the terrain reads as
    # authored color masses instead of scanned surface variation.
    macro_noise = nodes.new("ShaderNodeTexNoise")
    macro_noise.location = (-1280.0, 720.0)
    macro_noise.noise_dimensions = "3D"
    _socket(macro_noise, "Scale").default_value = style.macro_noise_scale
    _socket(macro_noise, "Detail").default_value = style.macro_noise_detail
    _socket(macro_noise, "Roughness").default_value = style.macro_noise_roughness
    if macro_noise.inputs.get("Distortion") is not None:
        macro_noise.inputs["Distortion"].default_value = 0.16
    links.new(texcoord.outputs["Generated"], macro_noise.inputs["Vector"])

    macro_bands = nodes.new("ShaderNodeValToRGB")
    macro_bands.location = (-1020.0, 720.0)
    macro_bands.label = "Graphic macro bands"
    _set_ramp(
        macro_bands,
        (
            (0.00, (style.macro_shadow,) * 3 + (1.0,)),
            (0.43, (style.macro_shadow,) * 3 + (1.0,)),
            (0.44, (style.macro_mid,) * 3 + (1.0,)),
            (0.68, (style.macro_mid,) * 3 + (1.0,)),
            (0.69, (style.macro_highlight,) * 3 + (1.0,)),
            (1.00, (style.macro_highlight,) * 3 + (1.0,)),
        ),
        interpolation="CONSTANT",
    )
    links.new(macro_noise.outputs["Fac"], macro_bands.inputs["Fac"])

    macro_multiply = nodes.new("ShaderNodeMixRGB")
    macro_multiply.blend_type = "MULTIPLY"
    macro_multiply.inputs[0].default_value = 1.0
    macro_multiply.location = (-760.0, 560.0)
    macro_multiply.label = "Semantic color × graphic bands"
    links.new(terrain_color.outputs["Color"], macro_multiply.inputs[1])
    links.new(macro_bands.outputs["Color"], macro_multiply.inputs[2])

    # Broad slope grouping creates readable cliff/rock faces without using a
    # realistic multi-material scan blend.
    slope_range = nodes.new("ShaderNodeMapRange")
    slope_range.location = (-1030.0, 300.0)
    slope_range.clamp = True
    slope_range.interpolation_type = "SMOOTHSTEP"
    slope_range.inputs["From Min"].default_value = style.slope_rock_start
    slope_range.inputs["From Max"].default_value = style.slope_rock_end
    slope_range.inputs["To Min"].default_value = 0.0
    slope_range.inputs["To Max"].default_value = style.slope_rock_strength
    links.new(slope.outputs["Fac"], slope_range.inputs["Value"])

    rock_mix = _mix(nodes, -500.0, 440.0, label="Stylized rock exposure")
    rock_mix.inputs[2].default_value = style.rock_tint
    links.new(slope_range.outputs["Result"], rock_mix.inputs[0])
    links.new(macro_multiply.outputs["Color"], rock_mix.inputs[1])

    ash_strength = _math(nodes, "MULTIPLY", -1020.0, 80.0, label="Ash strength")
    ash_strength.inputs[1].default_value = style.ash_strength
    links.new(ash.outputs["Fac"], ash_strength.inputs[0])

    ash_mix = _mix(nodes, -230.0, 380.0, label="Ash / dust overlay")
    ash_mix.inputs[2].default_value = style.ash_tint
    links.new(ash_strength.outputs[0], ash_mix.inputs[0])
    links.new(rock_mix.outputs["Color"], ash_mix.inputs[1])

    # Lava temperature and surface blend.
    heat_power = _math(nodes, "POWER", -1020.0, -170.0, label="Lava core falloff")
    heat_power.inputs[1].default_value = 1.55
    links.new(heat.outputs["Fac"], heat_power.inputs[0])

    lava_ramp = nodes.new("ShaderNodeValToRGB")
    lava_ramp.location = (-740.0, -80.0)
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

    lava_mix_strength = _math(nodes, "MULTIPLY", -470.0, -40.0, label="Lava surface mix")
    lava_mix_strength.inputs[1].default_value = style.lava_surface_mix
    links.new(heat.outputs["Fac"], lava_mix_strength.inputs[0])

    lava_surface_mix = _mix(nodes, 30.0, 320.0, label="Terrain / lava surface")
    links.new(lava_mix_strength.outputs[0], lava_surface_mix.inputs[0])
    links.new(ash_mix.outputs["Color"], lava_surface_mix.inputs[1])
    links.new(lava_ramp.outputs["Color"], lava_surface_mix.inputs[2])

    # Micro noise remains subtle everywhere; graphic fractures are masked to
    # steep rock, volcanic ash terrain, or hot lava so grass/plains stay clean.
    micro_noise = nodes.new("ShaderNodeTexNoise")
    micro_noise.location = (-1280.0, -600.0)
    micro_noise.noise_dimensions = "3D"
    _socket(micro_noise, "Scale").default_value = style.micro_noise_scale
    _socket(micro_noise, "Detail").default_value = style.micro_noise_detail
    _socket(micro_noise, "Roughness").default_value = style.micro_noise_roughness
    if micro_noise.inputs.get("Distortion") is not None:
        micro_noise.inputs["Distortion"].default_value = 0.10
    links.new(texcoord.outputs["Generated"], micro_noise.inputs["Vector"])

    voronoi = nodes.new("ShaderNodeTexVoronoi")
    voronoi.location = (-1280.0, -880.0)
    voronoi.voronoi_dimensions = "3D"
    voronoi.feature = "DISTANCE_TO_EDGE"
    voronoi.inputs["Scale"].default_value = style.crack_scale
    links.new(texcoord.outputs["Generated"], voronoi.inputs["Vector"])

    crack_range = nodes.new("ShaderNodeMapRange")
    crack_range.location = (-1030.0, -850.0)
    crack_range.clamp = True
    crack_range.interpolation_type = "SMOOTHERSTEP"
    crack_range.inputs["From Min"].default_value = 0.0
    crack_range.inputs["From Max"].default_value = style.crack_width
    crack_range.inputs["To Min"].default_value = 1.0
    crack_range.inputs["To Max"].default_value = 0.0
    links.new(voronoi.outputs["Distance"], crack_range.inputs["Value"])

    ash_fracture = _math(nodes, "MULTIPLY", -820.0, -760.0, label="Ash fracture mask")
    ash_fracture.inputs[1].default_value = 0.48
    links.new(ash.outputs["Fac"], ash_fracture.inputs[0])
    heat_fracture = _math(nodes, "MULTIPLY", -820.0, -930.0, label="Lava fracture mask")
    heat_fracture.inputs[1].default_value = 0.88
    links.new(heat.outputs["Fac"], heat_fracture.inputs[0])
    fracture_add = _math(nodes, "ADD", -600.0, -790.0, label="Rock + ash fractures")
    links.new(slope_range.outputs["Result"], fracture_add.inputs[0])
    links.new(ash_fracture.outputs[0], fracture_add.inputs[1])
    fracture_add_heat = _math(nodes, "ADD", -390.0, -800.0, label="Fracture surface mask")
    links.new(fracture_add.outputs[0], fracture_add_heat.inputs[0])
    links.new(heat_fracture.outputs[0], fracture_add_heat.inputs[1])
    fracture_clamp = _math(nodes, "MINIMUM", -180.0, -800.0, label="Fracture clamp")
    fracture_clamp.inputs[1].default_value = 1.0
    links.new(fracture_add_heat.outputs[0], fracture_clamp.inputs[0])

    crack_bump = _math(nodes, "MULTIPLY", -790.0, -1030.0, label="Raw fracture depth")
    crack_bump.inputs[1].default_value = style.crack_bump_strength
    links.new(crack_range.outputs["Result"], crack_bump.inputs[0])
    masked_crack_bump = _math(nodes, "MULTIPLY", -560.0, -1020.0, label="Masked fracture depth")
    links.new(crack_bump.outputs[0], masked_crack_bump.inputs[0])
    links.new(fracture_clamp.outputs[0], masked_crack_bump.inputs[1])

    micro_height = _math(nodes, "MULTIPLY", -760.0, -570.0, label="Soft micro height")
    micro_height.inputs[1].default_value = 0.56
    links.new(micro_noise.outputs["Fac"], micro_height.inputs[0])
    bump_height = _math(nodes, "SUBTRACT", -320.0, -610.0, label="Stylized rock surface")
    links.new(micro_height.outputs[0], bump_height.inputs[0])
    links.new(masked_crack_bump.outputs[0], bump_height.inputs[1])

    heat_smooth = _math(nodes, "MULTIPLY", -340.0, -940.0, label="Molten smoothing")
    heat_smooth.inputs[1].default_value = 0.72
    links.new(heat.outputs["Fac"], heat_smooth.inputs[0])
    one_minus_heat = _math(nodes, "SUBTRACT", -120.0, -940.0, label="Cooling crust")
    one_minus_heat.inputs[0].default_value = 1.0
    links.new(heat_smooth.outputs[0], one_minus_heat.inputs[1])

    final_bump_height = _math(nodes, "MULTIPLY", -70.0, -610.0, label="Final bump height")
    links.new(bump_height.outputs[0], final_bump_height.inputs[0])
    links.new(one_minus_heat.outputs[0], final_bump_height.inputs[1])

    bump = nodes.new("ShaderNodeBump")
    bump.location = (180.0, -500.0)
    bump.inputs["Strength"].default_value = style.bump_strength
    bump.inputs["Distance"].default_value = style.bump_distance
    links.new(final_bump_height.outputs[0], bump.inputs["Height"])

    # Roughness stays in a compressed game-art range: ash matte, lava smoother.
    rough_noise = _math(nodes, "MULTIPLY", -720.0, -370.0, label="Roughness noise")
    rough_noise.inputs[1].default_value = style.roughness_noise_strength
    links.new(micro_noise.outputs["Fac"], rough_noise.inputs[0])
    rough_base = _math(nodes, "ADD", -500.0, -360.0, label="Base roughness")
    rough_base.inputs[0].default_value = style.base_roughness
    links.new(rough_noise.outputs[0], rough_base.inputs[1])

    ash_rough = _math(nodes, "MULTIPLY", -720.0, -260.0, label="Ash roughness")
    ash_rough.inputs[1].default_value = style.ash_roughness_boost
    links.new(ash.outputs["Fac"], ash_rough.inputs[0])
    rough_with_ash = _math(nodes, "ADD", -280.0, -330.0, label="Roughness + ash")
    links.new(rough_base.outputs[0], rough_with_ash.inputs[0])
    links.new(ash_rough.outputs[0], rough_with_ash.inputs[1])

    heat_rough = _math(nodes, "MULTIPLY", -500.0, -220.0, label="Hot lava smoothness")
    heat_rough.inputs[1].default_value = style.lava_roughness_drop
    links.new(heat.outputs["Fac"], heat_rough.inputs[0])
    rough_sub = _math(nodes, "SUBTRACT", -60.0, -310.0, label="Final roughness raw")
    links.new(rough_with_ash.outputs[0], rough_sub.inputs[0])
    links.new(heat_rough.outputs[0], rough_sub.inputs[1])

    rough_min = _math(nodes, "MAXIMUM", 160.0, -300.0, label="Roughness minimum")
    rough_min.inputs[1].default_value = style.min_roughness
    links.new(rough_sub.outputs[0], rough_min.inputs[0])
    rough_max = _math(nodes, "MINIMUM", 380.0, -300.0, label="Roughness maximum")
    rough_max.inputs[1].default_value = style.max_roughness
    links.new(rough_min.outputs[0], rough_max.inputs[0])

    # Emission: broad hot core plus brighter fissures. This avoids the previous
    # flat-orange-stripe look while remaining intentionally graphic.
    hot_cracks = _math(nodes, "MULTIPLY", -340.0, -1110.0, label="Hot fissures")
    links.new(crack_range.outputs["Result"], hot_cracks.inputs[0])
    links.new(heat.outputs["Fac"], hot_cracks.inputs[1])
    core_emission = _math(nodes, "MULTIPLY", -100.0, -1040.0, label="Core emission")
    core_emission.inputs[1].default_value = 0.48
    links.new(heat_power.outputs[0], core_emission.inputs[0])
    crack_emission = _math(nodes, "MULTIPLY", -100.0, -1140.0, label="Crack emission")
    crack_emission.inputs[1].default_value = 0.78
    links.new(hot_cracks.outputs[0], crack_emission.inputs[0])
    emission_sum = _math(nodes, "ADD", 130.0, -1060.0, label="Emission mask")
    links.new(core_emission.outputs[0], emission_sum.inputs[0])
    links.new(crack_emission.outputs[0], emission_sum.inputs[1])
    emission_clamp = _math(nodes, "MINIMUM", 340.0, -1060.0, label="Emission clamp")
    emission_clamp.inputs[1].default_value = 1.0
    links.new(emission_sum.outputs[0], emission_clamp.inputs[0])
    emission_strength = _math(nodes, "MULTIPLY", 540.0, -1010.0, label="Emission strength")
    emission_strength.inputs[1].default_value = style.lava_emission_strength
    links.new(emission_clamp.outputs[0], emission_strength.inputs[0])

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (700.0, 150.0)
    bsdf.label = f"{style.name} terrain"
    links.new(lava_surface_mix.outputs["Color"], _socket(bsdf, "Base Color"))
    links.new(rough_max.outputs[0], _socket(bsdf, "Roughness"))
    links.new(bump.outputs["Normal"], _socket(bsdf, "Normal"))
    links.new(lava_ramp.outputs["Color"], _socket(bsdf, "Emission Color", "Emission"))
    links.new(emission_strength.outputs[0], _socket(bsdf, "Emission Strength"))

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (990.0, 150.0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    material["worldclaw_style"] = style.name
    return material

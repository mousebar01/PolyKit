from __future__ import annotations

try:
    import bpy  # type: ignore
except Exception:  # pragma: no cover
    bpy = None

from .config import GrasslandConfig


def _socket(group, *, name: str, in_out: str):
    return group.interface.new_socket(name=name, in_out=in_out, socket_type="NodeSocketGeometry")


def _math(nodes, operation: str, value: float | None = None):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    if value is not None:
        node.inputs[1].default_value = value
    return node


def _named_attr(nodes, name: str):
    node = nodes.new("GeometryNodeInputNamedAttribute")
    node.data_type = "FLOAT"
    node.inputs["Name"].default_value = name
    return node


def _collection_info(nodes, collection):
    node = nodes.new("GeometryNodeCollectionInfo")
    node.inputs["Collection"].default_value = collection
    if "Separate Children" in node.inputs:
        node.inputs["Separate Children"].default_value = True
    if "Reset Children" in node.inputs:
        node.inputs["Reset Children"].default_value = True
    return node


def _random_vector(nodes, minimum, maximum):
    node = nodes.new("FunctionNodeRandomValue")
    node.data_type = "FLOAT_VECTOR"
    node.inputs["Min"].default_value = minimum
    node.inputs["Max"].default_value = maximum
    return node


def _scatter_chain(
    *,
    nodes,
    links,
    mesh_socket,
    mask_name: str,
    density: float,
    collection,
    scale_min: tuple[float, float, float],
    scale_max: tuple[float, float, float],
    seed: int,
    wind: bool,
    wind_strength: float,
    wind_speed: float,
    wind_scale: float,
):
    attr = _named_attr(nodes, mask_name)
    mul = _math(nodes, "MULTIPLY", density)
    links.new(attr.outputs["Attribute"], mul.inputs[0])

    distribute = nodes.new("GeometryNodeDistributePointsOnFaces")
    distribute.distribute_method = "RANDOM"
    links.new(mesh_socket, distribute.inputs["Mesh"])
    links.new(mul.outputs[0], distribute.inputs["Density"])

    collection_node = _collection_info(nodes, collection)
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    links.new(distribute.outputs["Points"], instance.inputs["Points"])
    links.new(collection_node.outputs["Instances"], instance.inputs["Instance"])
    if "Pick Instance" in instance.inputs:
        instance.inputs["Pick Instance"].default_value = True

    index_random = nodes.new("FunctionNodeRandomValue")
    index_random.data_type = "INT"
    index_random.inputs["Min"].default_value = 0
    index_random.inputs["Max"].default_value = max(0, len(collection.objects) - 1)
    if "Seed" in index_random.inputs:
        index_random.inputs["Seed"].default_value = seed
    links.new(index_random.outputs["Value"], instance.inputs["Instance Index"])

    scale = _random_vector(nodes, scale_min, scale_max)
    if "Seed" in scale.inputs:
        scale.inputs["Seed"].default_value = seed + 13
    links.new(scale.outputs["Value"], instance.inputs["Scale"])

    rotate_random = _random_vector(nodes, (0.0, 0.0, -3.14159), (0.0, 0.0, 3.14159))
    if "Seed" in rotate_random.inputs:
        rotate_random.inputs["Seed"].default_value = seed + 29
    links.new(rotate_random.outputs["Value"], instance.inputs["Rotation"])

    if not wind:
        return instance.outputs["Instances"]

    # Spatial sine wave + scene time. This deliberately uses only cheap fields.
    position = nodes.new("GeometryNodeInputPosition")
    separate = nodes.new("ShaderNodeSeparateXYZ")
    links.new(position.outputs["Position"], separate.inputs["Vector"])

    xscale = _math(nodes, "MULTIPLY", wind_scale)
    yscale = _math(nodes, "MULTIPLY", wind_scale * 0.63)
    links.new(separate.outputs["X"], xscale.inputs[0])
    links.new(separate.outputs["Y"], yscale.inputs[0])
    add_xy = _math(nodes, "ADD")
    links.new(xscale.outputs[0], add_xy.inputs[0])
    links.new(yscale.outputs[0], add_xy.inputs[1])

    scene_time = nodes.new("GeometryNodeInputSceneTime")
    tscale = _math(nodes, "MULTIPLY", wind_speed)
    links.new(scene_time.outputs["Seconds"], tscale.inputs[0])
    phase = _math(nodes, "ADD")
    links.new(add_xy.outputs[0], phase.inputs[0])
    links.new(tscale.outputs[0], phase.inputs[1])
    sine = _math(nodes, "SINE")
    links.new(phase.outputs[0], sine.inputs[0])
    strength = _math(nodes, "MULTIPLY", wind_strength)
    links.new(sine.outputs[0], strength.inputs[0])

    combine = nodes.new("ShaderNodeCombineXYZ")
    links.new(strength.outputs[0], combine.inputs["X"])
    # Small cross-wind component keeps the field from reading like one hinge.
    cross = _math(nodes, "MULTIPLY", 0.42)
    links.new(strength.outputs[0], cross.inputs[0])
    links.new(cross.outputs[0], combine.inputs["Y"])

    rotate = nodes.new("GeometryNodeRotateInstances")
    links.new(instance.outputs["Instances"], rotate.inputs["Instances"])
    links.new(combine.outputs["Vector"], rotate.inputs["Rotation"])
    return rotate.outputs["Instances"]


def attach_grassland_nodes(terrain_obj, assets: dict[str, object], config: GrasslandConfig):
    if bpy is None:
        raise RuntimeError("Geometry Nodes setup must run inside Blender")

    modifier_name = "GrasslandEnvironment"
    old = terrain_obj.modifiers.get(modifier_name)
    if old:
        terrain_obj.modifiers.remove(old)

    group_name = f"{terrain_obj.name}_GrasslandNodes"
    old_group = bpy.data.node_groups.get(group_name)
    if old_group:
        bpy.data.node_groups.remove(old_group, do_unlink=True)

    group = bpy.data.node_groups.new(group_name, "GeometryNodeTree")
    _socket(group, name="Geometry", in_out="INPUT")
    _socket(group, name="Geometry", in_out="OUTPUT")

    nodes = group.nodes
    links = group.links
    group_in = nodes.new("NodeGroupInput")
    group_out = nodes.new("NodeGroupOutput")
    group_in.location = (-950, 120)
    group_out.location = (850, 120)

    grass = _scatter_chain(
        nodes=nodes, links=links, mesh_socket=group_in.outputs["Geometry"],
        mask_name="grass_mask", density=config.grass_density,
        collection=assets["grass"],
        scale_min=(0.72, 0.72, 0.72),
        scale_max=(1.0 + config.grass_scale_variation,) * 3,
        seed=config.seed + 1,
        wind=True,
        wind_strength=config.wind_strength,
        wind_speed=config.wind_speed,
        wind_scale=config.wind_scale,
    )

    flowers = _scatter_chain(
        nodes=nodes, links=links, mesh_socket=group_in.outputs["Geometry"],
        mask_name="grass_mask", density=config.flower_density,
        collection=assets["flowers"],
        scale_min=(0.75, 0.75, 0.75), scale_max=(1.35, 1.35, 1.35),
        seed=config.seed + 101,
        wind=True,
        wind_strength=config.wind_strength * 0.48,
        wind_speed=config.wind_speed,
        wind_scale=config.wind_scale * 0.91,
    )

    rocks = _scatter_chain(
        nodes=nodes, links=links, mesh_socket=group_in.outputs["Geometry"],
        mask_name="rock_mask", density=config.rock_density,
        collection=assets["rocks"],
        scale_min=(0.55, 0.55, 0.55), scale_max=(2.0, 2.0, 1.45),
        seed=config.seed + 211,
        wind=False,
        wind_strength=0.0, wind_speed=0.0, wind_scale=0.0,
    )

    shrubs = _scatter_chain(
        nodes=nodes, links=links, mesh_socket=group_in.outputs["Geometry"],
        mask_name="grass_mask", density=config.shrub_density,
        collection=assets["shrubs"],
        scale_min=(0.65, 0.65, 0.65), scale_max=(1.55, 1.55, 1.35),
        seed=config.seed + 307,
        wind=False,
        wind_strength=0.0, wind_speed=0.0, wind_scale=0.0,
    )

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (560, 120)
    links.new(group_in.outputs["Geometry"], join.inputs["Geometry"])
    links.new(grass, join.inputs["Geometry"])
    links.new(flowers, join.inputs["Geometry"])
    links.new(rocks, join.inputs["Geometry"])
    links.new(shrubs, join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], group_out.inputs["Geometry"])

    modifier = terrain_obj.modifiers.new(name=modifier_name, type="NODES")
    modifier.node_group = group
    return modifier

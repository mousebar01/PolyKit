from __future__ import annotations

try:
    import bpy  # type: ignore
except Exception:  # pragma: no cover
    bpy = None

_SOURCE_Z = -10000.0


def _ensure_material(name: str, color: tuple[float, float, float, float], roughness: float = 0.8):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
    mat.diffuse_color = color
    return mat


def _collection(name: str):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    # Keep source collections evaluated. The objects themselves live far below
    # the benchmark terrain, while Collection Info resets their transforms.
    col.hide_render = False
    col.hide_viewport = False
    return col


def _link_only(obj, collection):
    for col in tuple(obj.users_collection):
        col.objects.unlink(obj)
    collection.objects.link(obj)
    obj.location.z = _SOURCE_Z


def _blade_mesh(name: str, *, height: float, width: float, bend: float):
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    z1 = height * 0.48
    verts = [
        (-width * 0.50, 0.0, 0.0),
        ( width * 0.50, 0.0, 0.0),
        ( width * 0.30, bend * 0.45, z1),
        (-width * 0.30, bend * 0.45, z1),
        (0.0, bend, height),
    ]
    faces = [(0, 1, 2, 3), (3, 2, 4)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return bpy.data.objects.new(name, mesh)


def build_grass_assets(*, base_height: float = 1.0):
    if bpy is None:
        raise RuntimeError("asset generation must run inside Blender")
    col = _collection("Grassland_GrassAssets")
    mat_a = _ensure_material("Grassland_GrassA", (0.14, 0.34, 0.045, 1.0), 0.72)
    mat_b = _ensure_material("Grassland_GrassB", (0.23, 0.44, 0.075, 1.0), 0.70)
    specs = [
        ("GrassBlade_A", base_height * 0.82, 0.095, 0.10, mat_a),
        ("GrassBlade_B", base_height * 1.04, 0.082, -0.08, mat_b),
        ("GrassBlade_C", base_height * 1.18, 0.070, 0.16, mat_a),
    ]
    for name, height, width, bend, mat in specs:
        old = bpy.data.objects.get(name)
        if old:
            bpy.data.objects.remove(old, do_unlink=True)
        obj = _blade_mesh(name, height=height, width=width, bend=bend)
        obj.data.materials.append(mat)
        _link_only(obj, col)
    return col


def _create_cross_card(name: str, *, width: float, height: float, material):
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    w = width * 0.5
    verts = [
        (-w, 0.0, 0.0), (w, 0.0, 0.0), (w, 0.0, height), (-w, 0.0, height),
        (0.0, -w, 0.0), (0.0, w, 0.0), (0.0, w, height), (0.0, -w, height),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(material)
    return obj


def build_flower_assets():
    if bpy is None:
        raise RuntimeError("asset generation must run inside Blender")
    col = _collection("Grassland_FlowerAssets")
    colors = [
        (0.92, 0.76, 0.18, 1.0),
        (0.86, 0.42, 0.62, 1.0),
        (0.72, 0.78, 0.96, 1.0),
    ]
    for i, color in enumerate(colors):
        name = f"Grassland_Flower_{i}"
        old = bpy.data.objects.get(name)
        if old:
            bpy.data.objects.remove(old, do_unlink=True)
        mat = _ensure_material(f"Grassland_FlowerMat_{i}", color, 0.65)
        obj = _create_cross_card(name, width=0.20 + i * 0.025, height=0.42 + i * 0.05, material=mat)
        _link_only(obj, col)
    return col


def build_rock_assets():
    if bpy is None:
        raise RuntimeError("asset generation must run inside Blender")
    col = _collection("Grassland_RockAssets")
    mat = _ensure_material("Grassland_Rock", (0.31, 0.30, 0.25, 1.0), 0.88)
    for i, scale in enumerate((0.55, 0.82, 1.15)):
        name = f"Grassland_Rock_{i}"
        old = bpy.data.objects.get(name)
        if old:
            bpy.data.objects.remove(old, do_unlink=True)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0)
        obj = bpy.context.object
        obj.name = name
        obj.scale = (scale * 1.25, scale * (0.82 + 0.13 * i), scale * 0.62)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.data.materials.append(mat)
        _link_only(obj, col)
    return col


def build_shrub_assets():
    if bpy is None:
        raise RuntimeError("asset generation must run inside Blender")
    col = _collection("Grassland_ShrubAssets")
    leaf_mat = _ensure_material("Grassland_ShrubLeaf", (0.12, 0.29, 0.055, 1.0), 0.78)
    for i, radius in enumerate((0.70, 0.95)):
        name = f"Grassland_Shrub_{i}"
        old = bpy.data.objects.get(name)
        if old:
            bpy.data.objects.remove(old, do_unlink=True)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=radius)
        obj = bpy.context.object
        obj.name = name
        obj.scale = (1.25, 0.95, 0.72)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.data.materials.append(leaf_mat)
        _link_only(obj, col)
    return col


def build_all_assets(*, grass_height: float = 1.0):
    return {
        "grass": build_grass_assets(base_height=grass_height),
        "flowers": build_flower_assets(),
        "rocks": build_rock_assets(),
        "shrubs": build_shrub_assets(),
    }

import asyncio
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trimesh
from PIL import Image

from schemas.workflow import WorkflowExecutionNode
from services.process_runner import run_processor
from services.workflow_executor import _run_process_node


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/mesh-production"


class MeshProductionProcessorTests(unittest.TestCase):
    def test_executor_preserves_named_image_and_mesh_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            image = root / "reference.png"
            mesh = root / "model.glb"
            output = root / "result.glb"
            image.write_bytes(b"image")
            mesh.write_bytes(b"mesh")
            output.write_bytes(b"result")
            process_tuple = (
                PACK_DIR,
                {"entry": "processor.py"},
                {"id": "projection-bake", "inputs": ["image", "mesh"], "output": "mesh"},
            )

            with patch("services.workflow_executor.process_node_pack", return_value=process_tuple), patch(
                "services.workflow_executor.run_processor",
                return_value={"filePath": str(output)},
            ) as run:
                async def execute():
                    return await _run_process_node(
                        asyncio.get_running_loop(),
                        WorkflowExecutionNode(
                            class_type="mesh-production/projection-bake",
                            inputs={"image": image, "mesh": mesh, "params": {}},
                        ),
                        lambda value: value,
                        root,
                        root,
                        None,
                        lambda *_args: None,
                    )

                result = asyncio.run(execute())

            input_data = run.call_args.args[2]
            self.assertEqual(input_data["imagePath"], str(image))
            self.assertEqual(input_data["meshPath"], str(mesh))
            self.assertEqual(result["mesh"], output)

    def test_executor_preserves_batched_image_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reference = root / "reference.png"
            candidate = root / "candidate.png"
            output = root / "comparison.png"
            reference.write_bytes(b"reference")
            candidate.write_bytes(b"candidate")
            output.write_bytes(b"comparison")
            process_tuple = (
                PACK_DIR,
                {"entry": "processor.py"},
                {"id": "reference-compare", "input": "image", "batch_input": "image", "output": "image"},
            )

            with patch("services.workflow_executor.process_node_pack", return_value=process_tuple), patch(
                "services.workflow_executor.run_processor",
                return_value={"filePath": str(output)},
            ) as run:
                async def execute():
                    return await _run_process_node(
                        asyncio.get_running_loop(),
                        WorkflowExecutionNode(
                            class_type="reference-evidence/reference-compare",
                            inputs={"image": [reference, candidate], "params": {}},
                        ),
                        lambda value: value,
                        root,
                        root,
                        None,
                        lambda *_args: None,
                    )

                result = asyncio.run(execute())

            input_data = run.call_args.args[2]
            self.assertEqual(input_data["filePaths"], [str(reference), str(candidate)])
            self.assertEqual(result["image"], output)

    def test_projection_bake_embeds_reference_texture_and_uvs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            image = root / "reference.png"
            mesh = root / "model.glb"
            Image.new("RGBA", (32, 24), (220, 80, 60, 255)).save(image)
            trimesh.creation.box(extents=(1, 1, 1)).export(mesh)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"imagePath": str(image), "meshPath": str(mesh)},
                {
                    "_node_id": "projection-bake",
                    "projection_mode": "orthographic-front-projection",
                    "texture_size": 64,
                    "unseen_strategy": "leave-unprojected",
                },
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            baked = trimesh.load(output, force="mesh", process=False)
            self.assertTrue(output.is_file())
            self.assertIsInstance(baked, trimesh.Trimesh)
            self.assertIsNotNone(getattr(baked.visual, "uv", None))
            texture = getattr(baked.visual.material, "baseColorTexture", None)
            self.assertIsNotNone(texture)
            self.assertEqual(texture.size, (32, 24))
            self.assertEqual(report["kind"], "polykit.projection-bake")
            self.assertTrue(report["texture"]["embedded"])
            self.assertEqual(report["texture"]["actualSize"], [32, 24])
            self.assertEqual(result["metadata"]["evidence_kind"], "projection-bake")

    def test_uv_unwrap_exports_explicit_seam_safe_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.glb"
            trimesh.creation.box(extents=(2, 1, 1)).export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "uv-unwrap", "method": "flat-plane"},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            unwrapped = trimesh.load(output, force="mesh", process=False)
            self.assertTrue(output.is_file())
            self.assertIsInstance(unwrapped, trimesh.Trimesh)
            self.assertIsNotNone(getattr(unwrapped.visual, "uv", None))
            self.assertEqual(unwrapped.visual.uv.shape, (len(unwrapped.vertices), 2))
            self.assertEqual(report["kind"], "polykit.uv-unwrap")
            self.assertTrue(report["uv"]["hasWedgeCoordinates"])
            self.assertEqual(result["metadata"]["evidence_kind"], "uv-unwrap")

    def test_surface_map_bake_writes_normal_and_ao_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.glb"
            trimesh.creation.icosphere(subdivisions=1, radius=1.0).export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "surface-map-bake", "resolution": 64},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report_path = next(Path(str(path)) for path in result["sidecars"] if str(path).endswith(".json"))
            normal_path = next(Path(str(path)) for path in result["sidecars"] if str(path).endswith("_normal.png"))
            ao_path = next(Path(str(path)) for path in result["sidecars"] if str(path).endswith("_ao.png"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            baked = trimesh.load(output, force="mesh", process=False)
            self.assertTrue(output.is_file())
            self.assertIsNotNone(getattr(baked.visual, "uv", None))
            with Image.open(normal_path) as normal, Image.open(ao_path) as ao:
                self.assertEqual(normal.size, (64, 64))
                self.assertEqual(normal.mode, "RGB")
                self.assertEqual(ao.size, (64, 64))
                self.assertEqual(ao.mode, "L")
            self.assertEqual(report["kind"], "polykit.surface-map-bake")
            self.assertEqual(report["maps"]["normal"]["space"], "world")
            self.assertEqual(result["metadata"]["evidence_kind"], "surface-map-bake")

    def test_geometry_integrity_distinguishes_closed_and_open_meshes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            closed = root / "closed.glb"
            trimesh.creation.box().export(closed)
            closed_result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(closed)},
                {"_node_id": "geometry-integrity", "require_watertight": True},
                str(workspace),
                str(temp),
            )
            closed_report = json.loads(Path(str(closed_result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(closed_report["status"], "pass")
            self.assertTrue(closed_report["checks"]["watertight"])
            self.assertEqual(closed_report["counts"]["boundaryEdges"], 0)

            open_mesh = root / "open.glb"
            # A single triangle is unambiguously open and keeps this test
            # independent of trimesh's face-removal mutation semantics.
            trimesh.Trimesh(
                vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                faces=[[0, 1, 2]],
                process=False,
            ).export(open_mesh)
            open_result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(open_mesh)},
                {"_node_id": "geometry-integrity", "require_watertight": True},
                str(workspace),
                str(temp),
            )
            open_report = json.loads(Path(str(open_result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(open_report["status"], "needs_review")
            self.assertFalse(open_report["checks"]["watertight"])
            self.assertEqual(open_report["counts"]["boundaryEdges"], 3)

    def test_bvh_build_covers_every_triangle_in_stable_leaf_order(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.glb"
            trimesh.creation.icosphere(subdivisions=2).export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "bvh-build", "leaf_triangles": 4, "max_depth": 16},
                str(workspace),
                str(temp),
            )

            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            tree = report["tree"]
            self.assertEqual(report["kind"], "polykit.bvh")
            self.assertTrue(tree["complete"])
            self.assertEqual(tree["triangleCount"], tree["indexedTriangleCount"])
            self.assertEqual(sorted(tree["triangleOrder"]), list(range(tree["triangleCount"])))
            self.assertGreater(tree["nodeCount"], 1)
            self.assertEqual(result["metadata"]["evidence_kind"], "bvh")

    def test_self_intersection_audit_flags_crossing_triangles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            clean = root / "clean.glb"
            trimesh.creation.box().export(clean)
            clean_result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(clean)},
                {"_node_id": "self-intersection-audit"},
                str(workspace),
                str(temp),
            )
            clean_report = json.loads(Path(str(clean_result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(clean_report["status"], "pass")
            self.assertEqual(clean_report["check"]["intersectingFaceCount"], 0)

            crossing = root / "crossing.glb"
            trimesh.Trimesh(
                vertices=[
                    [0.0, 0.0, 0.0],
                    [2.0, 0.0, 0.0],
                    [0.0, 2.0, 0.0],
                    [0.5, -0.5, -1.0],
                    [0.5, 1.5, -1.0],
                    [0.5, 0.5, 1.0],
                ],
                faces=[[0, 1, 2], [3, 4, 5]],
                process=False,
            ).export(crossing)
            crossing_result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(crossing)},
                {"_node_id": "self-intersection-audit", "max_reported_faces": 1},
                str(workspace),
                str(temp),
            )
            crossing_report = json.loads(Path(str(crossing_result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(crossing_report["status"], "fail")
            self.assertEqual(crossing_report["check"]["intersectingFaceCount"], 2)
            self.assertEqual(len(crossing_report["check"]["reportedFaceIndices"]), 1)
            self.assertTrue(crossing_report["check"]["truncated"])
            self.assertEqual(crossing_result["metadata"]["evidence_kind"], "self-intersection-audit")

    def test_visual_hull_carves_closed_mesh_from_silhouettes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()
            mask = ["11111111"] * 8
            descriptor = {
                "name": "cube-hull",
                "bounds": {"min": [-1, -1, -1], "max": [1, 1, 1]},
                "resolution": 8,
                "triangleBudget": 10000,
                "views": [
                    {"axis": "front", "confidence": 1.0, "mask": mask},
                    {"axis": "side", "confidence": 1.0, "mask": mask},
                    {"axis": "top", "confidence": 1.0, "mask": mask},
                ],
            }
            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"text": json.dumps(descriptor)},
                {"_node_id": "visual-hull"},
                str(workspace),
                str(temp),
            )
            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            hull = trimesh.load(output, force="mesh", process=False)
            self.assertTrue(output.is_file())
            self.assertIsInstance(hull, trimesh.Trimesh)
            self.assertGreater(len(hull.faces), 0)
            self.assertTrue(hull.is_watertight)
            self.assertEqual(report["kind"], "polykit.visual-hull")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["occupiedVoxelCount"], 8**3)
            self.assertEqual(result["metadata"]["evidence_kind"], "visual-hull")

    def test_morph_target_bake_emits_relative_deltas_and_flags_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()
            source = root / "base.glb"
            trimesh.creation.box().export(source)
            base = trimesh.load(source, force="mesh", process=False)
            target_vertices = base.vertices.tolist()
            target_vertices[0][1] += 0.25
            descriptor = {"targets": [{"name": "raise", "vertices": target_vertices}]}
            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source), "text": json.dumps(descriptor)},
                {"_node_id": "morph-target-bake"},
                str(workspace),
                str(temp),
            )
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(report["kind"], "polykit.morph-target-bake")
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["morphTargetsRelative"])
            self.assertEqual(report["targets"][0]["movedVertexCount"], 1)
            self.assertEqual(report["targets"][0]["deltas"][0][1], 0.25)
            self.assertEqual(result["metadata"]["evidence_kind"], "morph-target-bake")

            noop = root / "noop.json"
            noop.write_text(json.dumps({"vertices": base.vertices.tolist()}), encoding="utf-8")
            noop_result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source), "text": noop.read_text(encoding="utf-8")},
                {"_node_id": "morph-target-bake"},
                str(workspace),
                str(temp),
            )
            noop_report = json.loads(Path(str(noop_result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(noop_report["status"], "needs_review")
            self.assertEqual(noop_report["noOpTargets"], ["morph-1"])

    def test_joint_loop_audit_counts_axial_vertex_bands(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()
            rings = [-0.4, -0.2, 0.0, 0.2, 0.4]
            vertices = []
            for z in rings:
                for index in range(8):
                    angle = (2.0 * 3.141592653589793 * index) / 8.0
                    vertices.append([0.1 * math.cos(angle), 0.1 * math.sin(angle), z])
            faces = []
            for ring in range(len(rings) - 1):
                for index in range(8):
                    a = ring * 8 + index
                    b = ring * 8 + (index + 1) % 8
                    c = (ring + 1) * 8 + (index + 1) % 8
                    d = (ring + 1) * 8 + index
                    faces.extend([[a, b, c], [a, c, d]])
            tube = root / "tube.glb"
            trimesh.Trimesh(vertices=vertices, faces=faces, process=False).export(tube)
            descriptor = {"bones": [{"id": "elbow", "jointPos": [0, 0, 0], "tipPos": [0, 0, 1]}]}
            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(tube), "text": json.dumps(descriptor)},
                {"_node_id": "joint-loop-audit", "min_loops": 3, "radius_scale": 0.35},
                str(workspace),
                str(temp),
            )
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["joints"][0]["loops"], 3)
            self.assertEqual(result["metadata"]["evidence_kind"], "joint-loop-audit")

            sparse = root / "sparse.glb"
            trimesh.creation.box(extents=(0.2, 0.2, 0.2)).export(sparse)
            sparse_result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(sparse), "text": json.dumps(descriptor)},
                {"_node_id": "joint-loop-audit", "min_loops": 3, "radius_scale": 0.35},
                str(workspace),
                str(temp),
            )
            sparse_report = json.loads(Path(str(sparse_result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(sparse_report["status"], "fail")
            self.assertEqual(sparse_report["checks"]["failingJointCount"], 1)

    def test_animation_audit_checks_skin_clips_and_morph_targets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()
            rigged = root / "rigged.gltf"
            rigged.write_text(
                json.dumps(
                    {
                        "asset": {"version": "2.0"},
                        "nodes": [{"name": "root"}, {"name": "joint"}],
                        "skins": [{"joints": [1], "skeleton": 0}],
                        "meshes": [
                            {
                                "primitives": [
                                    {
                                        "attributes": {"POSITION": 0, "JOINTS_0": 1, "WEIGHTS_0": 2},
                                        "targets": [{"POSITION": 3}],
                                    }
                                ]
                            }
                        ],
                        "animations": [{"name": "idle", "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}]}],
                    }
                ),
                encoding="utf-8",
            )
            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(rigged)},
                {"_node_id": "animation-audit", "require_animation": True},
                str(workspace),
                str(temp),
            )
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["rig"]["bound"])
            self.assertEqual(report["animation"]["clipCount"], 1)
            self.assertTrue(report["morphTargets"]["present"])

            plain = root / "plain.gltf"
            plain.write_text(json.dumps({"asset": {"version": "2.0"}, "nodes": [{}], "meshes": []}), encoding="utf-8")
            plain_result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(plain)},
                {"_node_id": "animation-audit", "require_animation": True},
                str(workspace),
                str(temp),
            )
            plain_report = json.loads(Path(str(plain_result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(plain_report["status"], "needs_review")
            self.assertFalse(plain_report["rig"]["bound"])

    def test_collision_mesh_builds_convex_proxy_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "assembly.glb"
            scene = trimesh.Scene()
            scene.add_geometry(trimesh.creation.box(extents=(2, 1, 1)), geom_name="body", node_name="body")
            scene.add_geometry(
                trimesh.creation.icosphere(subdivisions=1, radius=0.4),
                geom_name="cap",
                node_name="cap",
                transform=trimesh.transformations.translation_matrix((1.1, 0.0, 0.0)),
            )
            scene.export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "collision-mesh", "method": "convex_hull"},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            collider = trimesh.load(output, force="mesh", process=False)
            self.assertTrue(output.is_file())
            self.assertIsInstance(collider, trimesh.Trimesh)
            self.assertGreater(len(collider.faces), 0)
            self.assertEqual(report["kind"], "polykit.collision-mesh")
            self.assertEqual(report["method"]["used"], "convex_hull")
            self.assertEqual(report["source"]["componentCount"], 2)
            self.assertEqual(result["metadata"]["evidence_kind"], "collision-mesh")

    def test_lod_generate_writes_three_levels_with_reduced_faces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "dense.glb"
            scene = trimesh.Scene()
            scene.add_geometry(
                trimesh.creation.icosphere(subdivisions=3, radius=1.0),
                geom_name="body",
                node_name="body",
            )
            scene.export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "lod-generate", "lod1_ratio": 0.5, "lod2_ratio": 0.2, "min_faces": 20},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            self.assertEqual(report["kind"], "polykit.lod-generation")
            self.assertEqual([level["level"] for level in report["levels"]], ["LOD0", "LOD1", "LOD2"])
            self.assertGreater(report["levels"][0]["faces"], report["levels"][1]["faces"])
            self.assertGreater(report["levels"][1]["faces"], report["levels"][2]["faces"])
            self.assertEqual(len(result["sidecars"]), 3)
            for path in result["sidecars"][1:]:
                self.assertTrue(Path(str(path)).is_file())
                self.assertGreater(len(trimesh.load(path, force="mesh", process=False).faces), 0)
            self.assertEqual(result["metadata"]["level_count"], 3)


if __name__ == "__main__":
    unittest.main()

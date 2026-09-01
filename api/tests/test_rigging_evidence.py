import json
import tempfile
import unittest
from pathlib import Path

from services.process_runner import run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/rigging-evidence"


class RiggingEvidenceProcessorTests(unittest.TestCase):
    def _run(self, descriptor: dict, root: Path, node_id: str = "attachment-anchor-audit") -> dict:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        temp = root / "tmp"
        temp.mkdir(exist_ok=True)
        result = run_processor(
            PACK_DIR,
            "processor.py",
            {"text": json.dumps(descriptor)},
            {"_node_id": node_id},
            str(workspace),
            str(temp),
        )
        return json.loads(str(result["text"]))

    def test_attachment_anchor_audit_checks_declaration_and_proximity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            valid = {
                "componentTree": [
                    {"id": "body"},
                    {"id": "head", "parent": "body", "dimensions": {"radius": 0.4}},
                    {"id": "hat", "parent": "body", "role": "worn", "attachment": {"anchor": "head"}},
                ],
                "measured": {"head": [0, 1.8, 0], "hat": [0, 1.85, 0]},
            }
            report = self._run(valid, root)
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["passed"])
            self.assertEqual(report["attachments"][0]["anchorKind"], "component")
            self.assertTrue(report["attachments"][0]["withinLimit"])

            missing = {
                "componentTree": [
                    {"id": "body"},
                    {"id": "hat", "role": "worn", "parent": "body"},
                ]
            }
            missing_report = self._run(missing, root)
            self.assertEqual(missing_report["status"], "fail")
            self.assertIn("ANCHOR_DECLARED", missing_report["errors"][0])

            far = {
                "componentTree": [
                    {"id": "body"},
                    {"id": "head", "parent": "body", "dimensions": {"radius": 0.4}},
                    {"id": "hat", "parent": "body", "role": "worn", "attachment": {"anchor": "head"}},
                ],
                "measured": {"head": [0, 1.8, 0], "hat": [0, 3.0, 0]},
            }
            far_report = self._run(far, root)
            self.assertEqual(far_report["status"], "fail")
            self.assertIn("ANCHOR_PROXIMITY", far_report["errors"][0])

    def test_rig_payload_audit_checks_hierarchy_matrices_and_weights(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            valid = {
                "schemaVersion": 1,
                "coordinateSystem": {"up": "Y", "handedness": "right", "unit": "meter"},
                "joints": [[0, 0, 0], [0, 1, 0]],
                "parents": [None, 0],
                "names": ["root", "elbow"],
                "matrix_local": [identity, identity],
                "skinIndex": [[0, 1, 0, 0], [1, 0, 0, 0]],
                "skinWeight": [[1, 0, 0, 0], [1, 0, 0, 0]],
            }
            report = self._run(valid, root, "rig-payload-audit")
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["passed"])
            self.assertEqual(report["summary"]["jointCount"], 2)
            self.assertEqual(report["summary"]["activeVertexCountByJoint"], [1, 1])

            invalid = dict(valid)
            invalid["skinWeight"] = [[0.8, 0, 0, 0], [1, 0, 0, 0]]
            invalid_report = self._run(invalid, root, "rig-payload-audit")
            self.assertEqual(invalid_report["status"], "fail")
            self.assertFalse(invalid_report["passed"])
            self.assertIn("must sum to 1", invalid_report["errors"][0])

    def test_chirality_audit_accepts_reflection_and_rejects_vertical_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            valid = {
                "pairs": [
                    {"stem": "hand", "right": [0.6, 1.2, 0.28], "left": [-0.6, 1.2, 0.28]},
                    {"stem": "foot", "right": [0.4, 0.1, -0.2], "left": [-0.4, 0.1, -0.2]},
                ],
                "points": [[0.5, 1.0, 0.2], [-0.5, 1.0, 0.2], [0, 0, 0]],
            }
            report = self._run(valid, root, "chirality-audit")
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["passed"])
            self.assertEqual(report["pairCount"], 2)
            self.assertEqual(report["pairs"][0]["relation"], "reflection")
            self.assertEqual(report["summary"]["symmetryError"], 0.0)

            rotated = {
                "pairs": [{"stem": "hand", "right": [0.6, 1.2, 0.28], "left": [-0.6, 1.2, -0.28]}]
            }
            rotated_report = self._run(rotated, root, "chirality-audit")
            self.assertEqual(rotated_report["status"], "fail")
            self.assertFalse(rotated_report["passed"])
            self.assertEqual(rotated_report["pairs"][0]["relation"], "rotation")
            self.assertIn("negate lateral X only", rotated_report["errors"][0])

    def test_geodesic_bind_routes_weights_through_solid_and_normalizes_four_slots(self) -> None:
        def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> tuple[list[list[float]], list[int]]:
            vertices = [
                [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
            ]
            quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 4, 7, 3)]
            indices: list[int] = []
            for first, second, third, fourth in quads:
                indices.extend([first, second, third, first, third, fourth])
            return vertices, indices

        def merge(*meshes: tuple[list[list[float]], list[int]]) -> tuple[list[list[float]], list[int]]:
            vertices: list[list[float]] = []
            indices: list[int] = []
            for mesh_vertices, mesh_indices in meshes:
                offset = len(vertices)
                vertices.extend(mesh_vertices)
                indices.extend(index + offset for index in mesh_indices)
            return vertices, indices

        torso = box(0.0, 2.0, 0.0, 4.0, 0.0, 1.0)
        arm = box(2.4, 3.4, 0.0, 3.4, 0.0, 1.0)
        shoulder = box(1.8, 2.6, 3.4, 4.0, 0.0, 1.0)
        vertices, indices = merge(torso, arm, shoulder)
        descriptor = {
            "mesh": {"vertices": vertices, "indices": indices},
            "bones": [
                {"id": "spine", "jointPos": [1.0, 0.5, 0.5], "tipPos": [1.0, 3.5, 0.5]},
                {"id": "arm", "jointPos": [2.9, 3.2, 0.5], "tipPos": [2.9, 0.4, 0.5]},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            report = self._run(descriptor, Path(td), "geodesic-bind")
        self.assertEqual(report["kind"], "polykit.geodesic-bind")
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["passed"])
        self.assertEqual(report["summary"]["vertexCount"], len(vertices))
        self.assertEqual(report["summary"]["jointCount"], 2)
        self.assertEqual(report["summary"]["unreachableVertexCount"], 0)
        self.assertLess(report["summary"]["maxWeightError"], 1e-9)
        self.assertTrue(all(len(row) == 4 for row in report["skinIndices"]))
        self.assertTrue(all(len(row) == 4 for row in report["skinWeights"]))
        chest_index = 1
        arm_slot = report["boneOrder"].index("arm")
        chest_arm_weight = sum(weight for slot, weight in zip(report["skinIndices"][chest_index], report["skinWeights"][chest_index]) if slot == arm_slot)
        arm_corner_index = 8 + 1
        arm_arm_weight = sum(weight for slot, weight in zip(report["skinIndices"][arm_corner_index], report["skinWeights"][arm_corner_index]) if slot == arm_slot)
        self.assertLess(chest_arm_weight, 0.2)
        self.assertGreater(arm_arm_weight, 0.8)


if __name__ == "__main__":
    unittest.main()

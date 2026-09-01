import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from services.process_runner import run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/reference-evidence"


class ReferenceEvidenceProcessorTests(unittest.TestCase):
    def test_detail_inventory_writes_overlay_report_and_crops(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "chair.png"
            Image.new("RGB", (11, 7), (30, 60, 90)).save(source)
            workspace = root / "process-workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"grid_size": 2, "subject": "Test chair", "target_min_details": 5},
                str(workspace),
                str(temp),
            )

            overlay = Path(str(result["filePath"]))
            sidecars = [Path(str(path)) for path in result["sidecars"]]
            report_path = next(path for path in sidecars if path.name.endswith("detail-inventory.json"))
            self.assertTrue(overlay.is_file())
            self.assertEqual(len(sidecars), 5)  # report plus 2 × 2 region crops
            self.assertTrue(all(path.is_file() for path in sidecars))
            with Image.open(overlay) as image:
                self.assertEqual(image.size, (11, 7))

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["kind"], "polykit.detail-inventory")
            self.assertEqual(report["status"], "needs_visual_review")
            self.assertEqual(report["subject"], "Test chair")
            self.assertEqual(report["sourceImage"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(len(report["scan"]["regions"]), 4)
            self.assertEqual(len(report["detailChecklist"]), 15)
            self.assertTrue(all(item["present"] is None for item in report["detailChecklist"]))
            self.assertEqual(result["metadata"]["region_count"], 4)

    def test_include_crops_false_keeps_report_references_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "reference.png"
            Image.new("RGB", (4, 4), (255, 255, 255)).save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"grid_size": 3, "include_crops": False},
                str(workspace),
                str(temp),
            )

            self.assertEqual(len(result["sidecars"]), 1)
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(all(region["crop"] is None for region in report["scan"]["regions"]))
            self.assertEqual(result["metadata"]["region_count"], 9)

    def test_workflow_publishes_sidecars_with_image_output(self) -> None:
        import asyncio
        import threading
        from types import SimpleNamespace
        from unittest import mock

        from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
        from services.runtime_paths import runtime_paths
        from services.workflow_engine import WorkflowEngine

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_paths = runtime_paths.snapshot()
            runtime_paths.update(workspace_dir=root)
            source = root / "source.png"
            Image.new("RGB", (8, 8), (80, 120, 160)).save(source)
            payload = base64.b64encode(source.read_bytes()).decode("ascii")
            manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
            node_manifest = manifest["nodes"][0]
            process_tuple = (PACK_DIR, manifest, node_manifest)
            request = WorkflowExecutionRequest(
                collection="Evidence",
                prompt={
                    "source": WorkflowExecutionNode(
                        class_type="polykit.image",
                        inputs={"image": {"kind": "base64", "data": payload}},
                    ),
                    "inventory": WorkflowExecutionNode(
                        class_type="reference-evidence/detail-inventory",
                        inputs={"image": ["source", "image"], "params": {"grid_size": 2}},
                    ),
                    "output": WorkflowExecutionNode(
                        class_type="polykit.image_output",
                        inputs={"image": ["inventory", "image"]},
                    ),
                },
                output_node_id="output",
            )
            job = SimpleNamespace(job_id="reference-evidence-workflow", progress=0, step="", meta=None)
            loop = asyncio.new_event_loop()
            try:
                with mock.patch("services.workflow_engine.process_node_pack", return_value=process_tuple), mock.patch(
                    "services.workflow_executor.process_node_pack", return_value=process_tuple
                ):
                    final = loop.run_until_complete(
                        WorkflowEngine(cache_enabled=False).run(
                            job_id=job.job_id,
                            request=request,
                            job=job,
                            persist=lambda: None,
                            cancel_event=threading.Event(),
                            is_cancelled=lambda: False,
                        )
                    )
            finally:
                loop.close()
                runtime_paths.update(
                    models_dir=old_paths.models,
                    workspace_dir=old_paths.workspace,
                    workflows_dir=old_paths.workflows,
                    node_packs_dir=old_paths.node_packs,
                )

            self.assertIsNotNone(final)
            assert final is not None
            self.assertTrue(final.is_relative_to(root / "Evidence"))
            reports = list((root / "Evidence").glob("*_detail-inventory.json"))
            crops = list((root / "Evidence").glob("*_r*.png"))
            self.assertEqual(len(reports), 1)
            self.assertEqual(len(crops), 4)
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(report["scan"]["method"], "grid-2x2")
            self.assertEqual(job.meta["process_metadata"]["inventory"]["evidence_kind"], "detail-inventory")


if __name__ == "__main__":
    unittest.main()

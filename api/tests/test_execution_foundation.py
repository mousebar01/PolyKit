from __future__ import annotations

import unittest

from application.generate_asset import (
    GenerateAssetCommand,
    GenerateAssetFromImageCommand,
    compile_generate_asset_from_image_plan,
    compile_generate_asset_plan,
)
from schemas.execution import ExecutionInitiator, ExecutionNode, ExecutionPlan, ExecutionSource
from schemas.workflow import WorkflowExecutionRequest
from services.capability_registry import resolve_capability
from services.execution_engine import ExecutionEngine, ExecutionWait
from services.workflow_engine import WorkflowEngine, WorkflowWait
from services.world_workflows import build_structure_workflow, compile_structure_plan


class ExecutionFoundationTests(unittest.TestCase):
    def test_execution_plan_supports_direct_non_workflow_source(self) -> None:
        plan = ExecutionPlan(
            source=ExecutionSource(kind="direct", id="assets.generate"),
            prompt={
                "input": ExecutionNode(
                    class_type="polykit.text",
                    inputs={"text": "chair"},
                )
            },
            collection="Generated",
        )

        self.assertEqual(plan.source.kind, "direct")
        self.assertIsNone(plan.workflow_id)
        self.assertEqual(plan.prompt["input"].class_type, "polykit.text")

    def test_execution_initiator_is_separate_from_plan_source(self) -> None:
        initiator = ExecutionInitiator(type="agent", surface="mcp")
        source = ExecutionSource(kind="direct", id="assets.generate.text")

        self.assertEqual(initiator.type, "agent")
        self.assertEqual(source.kind, "direct")

    def test_legacy_workflow_request_remains_wire_compatible(self) -> None:
        payload = {
            "schema_version": 1,
            "workflow_id": "legacy-workflow",
            "prompt": {
                "input": {
                    "class_type": "polykit.text",
                    "inputs": {"text": "chair"},
                }
            },
            "collection": "Workflows",
            "metadata": {"test": True},
        }

        request = WorkflowExecutionRequest.model_validate(payload)

        self.assertEqual(request.workflow_id, "legacy-workflow")
        self.assertEqual(request.prompt["input"].inputs["text"], "chair")
        self.assertEqual(request.metadata, {"test": True})

    def test_execution_engine_is_compatible_with_existing_engine(self) -> None:
        self.assertTrue(issubclass(ExecutionEngine, WorkflowEngine))
        self.assertIs(ExecutionWait, WorkflowWait)

    def test_builtin_capabilities_have_product_level_kinds(self) -> None:
        self.assertEqual(resolve_capability("polykit.text").kind, "source")
        self.assertEqual(resolve_capability("polykit.output").kind, "sink")
        self.assertEqual(resolve_capability("polykit.interrupt").kind, "processor")

    def test_generate_asset_compiles_direct_ai_plan(self) -> None:
        plan = compile_generate_asset_plan(
            GenerateAssetCommand(
                prompt="wooden chair",
                world_id="world-a",
                proto_id="chair",
            )
        )

        self.assertEqual(plan.source, ExecutionSource(kind="direct", id="assets.generate.text"))
        self.assertIsNone(plan.workflow_id)
        self.assertEqual(plan.metadata["generation_kind"], "ai")
        self.assertEqual(plan.metadata["generation_mode"], "text")
        self.assertEqual(plan.metadata["world_id"], "world-a")
        self.assertEqual(plan.metadata["proto_id"], "chair")
        self.assertEqual(plan.prompt["image"].inputs["text"], ["text", "text"])
        self.assertEqual(plan.prompt["mesh"].inputs["image"], ["cutout", "image"])
        self.assertEqual(plan.prompt["texture"].inputs["mesh"], ["mesh", "mesh"])
        self.assertEqual(plan.prompt["output"].inputs["mesh"], ["optimize", "mesh"])

    def test_generate_asset_can_be_minimal_without_cutout_texture_or_optimize(self) -> None:
        plan = compile_generate_asset_plan(
            GenerateAssetCommand(
                prompt="simple prop",
                enable_cutout=False,
                enable_texture=False,
                enable_optimize=False,
            )
        )

        self.assertEqual(set(plan.prompt), {"text", "image", "mesh", "output"})
        self.assertEqual(plan.prompt["mesh"].inputs["image"], ["image", "image"])
        self.assertEqual(plan.prompt["output"].inputs["mesh"], ["mesh", "mesh"])

    def test_image_generation_compiles_to_same_execution_protocol(self) -> None:
        plan = compile_generate_asset_from_image_plan(
            GenerateAssetFromImageCommand(
                image={"kind": "workspace_path", "path": "References/chair.png"},
                enable_texture=True,
            )
        )

        self.assertEqual(plan.source, ExecutionSource(kind="direct", id="assets.generate.image"))
        self.assertEqual(plan.metadata["generation_kind"], "ai")
        self.assertEqual(plan.metadata["generation_mode"], "image")
        self.assertEqual(plan.prompt["mesh"].inputs["image"], ["image", "image"])
        self.assertEqual(plan.prompt["texture"].class_type, "trellis2/refine")

    def test_world_compiler_produces_generic_plan_and_legacy_wrapper(self) -> None:
        world = {
            "runtime": {
                "version": 1,
                "intent": {"prompt": "small wooden cabin"},
                "build": {
                    "buildings": [
                        {
                            "id": "cabin-a",
                            "generator": "blender-parametric",
                            "parameters": {"width": 4.0, "depth": 6.0},
                        }
                    ]
                },
            }
        }

        plan = compile_structure_plan(world, world_id="world-a")
        legacy = build_structure_workflow(world, world_id="world-a")

        self.assertIsInstance(plan, ExecutionPlan)
        self.assertEqual(plan.source, ExecutionSource(kind="world", id="world-a"))
        self.assertEqual(plan.prompt["build"].class_type, "blender-scene/build")
        self.assertEqual(plan.prompt["build"].inputs["params"]["render_profile"], "production")
        self.assertIsInstance(legacy, WorkflowExecutionRequest)
        self.assertEqual(legacy.source, plan.source)
        self.assertEqual(legacy.model_dump(mode="json"), plan.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from schemas.execution import ExecutionNode, ExecutionPlan, ExecutionSource
from schemas.workflow import WorkflowExecutionRequest
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
        self.assertIsInstance(legacy, WorkflowExecutionRequest)
        self.assertEqual(legacy.source, plan.source)
        self.assertEqual(legacy.model_dump(mode="json"), plan.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()

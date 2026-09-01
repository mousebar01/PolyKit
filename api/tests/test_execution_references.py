from __future__ import annotations

import unittest

from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.workflow_executor import (
    NodeOutputCache,
    WorkflowError,
    select_execution_prompt,
    topological_order,
)


class ExecutionReferenceTests(unittest.TestCase):
    def test_batch_references_create_real_dag_dependencies(self) -> None:
        prompt = {
            # Deliberately insert the consumer first. Correct ordering must come
            # from the nested references rather than dictionary insertion order.
            "compose": WorkflowExecutionNode(
                class_type="scene-composer/compose",
                inputs={"mesh": [["lamp", "mesh"], ["table", "mesh"]]},
            ),
            "lamp": WorkflowExecutionNode(
                class_type="polykit.mesh",
                inputs={"mesh": {"kind": "workspace_path", "path": "Assets/lamp.glb"}},
            ),
            "table": WorkflowExecutionNode(
                class_type="polykit.mesh",
                inputs={"mesh": {"kind": "workspace_path", "path": "Assets/table.glb"}},
            ),
            "output": WorkflowExecutionNode(
                class_type="polykit.output",
                inputs={"mesh": ["compose", "mesh"]},
            ),
        }

        order = topological_order(prompt)

        self.assertLess(order.index("lamp"), order.index("compose"))
        self.assertLess(order.index("table"), order.index("compose"))
        self.assertLess(order.index("compose"), order.index("output"))

    def test_missing_nested_reference_is_rejected(self) -> None:
        prompt = {
            "compose": WorkflowExecutionNode(
                class_type="scene-composer/compose",
                inputs={"mesh": [["missing", "mesh"]]},
            )
        }

        with self.assertRaisesRegex(WorkflowError, "missing node 'missing'"):
            topological_order(prompt)

    def test_partial_execution_includes_nested_upstream_nodes(self) -> None:
        request = WorkflowExecutionRequest(
            prompt={
                "compose": WorkflowExecutionNode(
                    class_type="scene-composer/compose",
                    inputs={"mesh": [["lamp", "mesh"], ["table", "mesh"]]},
                ),
                "lamp": WorkflowExecutionNode(
                    class_type="polykit.mesh",
                    inputs={"mesh": {"kind": "workspace_path", "path": "Assets/lamp.glb"}},
                ),
                "table": WorkflowExecutionNode(
                    class_type="polykit.mesh",
                    inputs={"mesh": {"kind": "workspace_path", "path": "Assets/table.glb"}},
                ),
                "unused": WorkflowExecutionNode(
                    class_type="polykit.text",
                    inputs={"text": "not selected"},
                ),
                "output": WorkflowExecutionNode(
                    class_type="polykit.output",
                    inputs={"mesh": ["compose", "mesh"]},
                ),
            },
            target_node_ids=["output"],
        )

        selected = select_execution_prompt(request)

        self.assertEqual(set(selected), {"lamp", "table", "compose", "output"})

    def test_nested_reference_signature_tracks_upstream_content(self) -> None:
        cache = NodeOutputCache()
        inputs = {"mesh": [["lamp", "mesh"], ["table", "mesh"]]}

        first = cache.signature(
            "scene-composer/compose",
            inputs,
            {"lamp": "lamp-v1", "table": "table-v1"},
        )
        second = cache.signature(
            "scene-composer/compose",
            inputs,
            {"lamp": "lamp-v2", "table": "table-v1"},
        )

        self.assertNotEqual(first, second)

    def test_reference_output_name_is_part_of_cache_signature(self) -> None:
        cache = NodeOutputCache()
        ref_sigs = {"source": "same-node-signature"}

        mesh = cache.signature("consumer", {"mesh": ["source", "mesh"]}, ref_sigs)
        preview = cache.signature("consumer", {"mesh": ["source", "preview"]}, ref_sigs)

        self.assertNotEqual(mesh, preview)

    def test_two_string_parameter_list_remains_literal(self) -> None:
        prompt = {
            "node": WorkflowExecutionNode(
                class_type="polykit.text",
                inputs={
                    "text": "hello",
                    "params": {"tags": ["indoor", "wood"]},
                },
            )
        }

        self.assertEqual(topological_order(prompt), ["node"])

        cache = NodeOutputCache()
        first = cache.signature("polykit.text", prompt["node"].inputs, {})
        second = cache.signature(
            "polykit.text",
            {"text": "hello", "params": {"tags": ["indoor", "metal"]}},
            {},
        )
        self.assertNotEqual(first, second)

    def test_direct_params_reference_remains_legacy_compatible(self) -> None:
        prompt = {
            "consumer": WorkflowExecutionNode(
                class_type="polykit.text",
                inputs={"text": "hello", "params": ["config", "text"]},
            ),
            "config": WorkflowExecutionNode(
                class_type="polykit.text",
                inputs={"text": "config"},
            ),
        }

        order = topological_order(prompt)
        self.assertLess(order.index("config"), order.index("consumer"))


if __name__ == "__main__":
    unittest.main()

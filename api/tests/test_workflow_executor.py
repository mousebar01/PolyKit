import unittest

from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.workflow_executor import (
    WorkflowError,
    is_reference,
    resolve_reference,
    select_execution_prompt,
    topological_order,
)


def _prompt(classes: dict[str, dict]) -> dict[str, WorkflowExecutionNode]:
    return {
        node_id: WorkflowExecutionNode(class_type=spec["class_type"], inputs=spec.get("inputs", {}))
        for node_id, spec in classes.items()
    }


class ReferenceTests(unittest.TestCase):
    def test_is_reference_accepts_two_strings(self) -> None:
        self.assertTrue(is_reference(["a", "image"]))
        self.assertFalse(is_reference("a"))
        self.assertFalse(is_reference(["a"]))
        self.assertFalse(is_reference(["a", 1]))

    def test_resolve_reference_reads_named_output(self) -> None:
        outputs = {"gen": {"mesh": "/tmp/m.glb"}}
        self.assertEqual(resolve_reference(["gen", "mesh"], outputs), "/tmp/m.glb")

    def test_resolve_reference_rejects_missing_node_or_output(self) -> None:
        outputs = {"gen": {"mesh": "/tmp/m.glb"}}
        with self.assertRaises(WorkflowError):
            resolve_reference(["nope", "mesh"], outputs)
        with self.assertRaises(WorkflowError):
            resolve_reference(["gen", "text"], outputs)


class TopologicalOrderTests(unittest.TestCase):
    def test_linear_chain_orders_sources_first(self) -> None:
        request = WorkflowExecutionRequest(
            prompt=_prompt({
                "image": {"class_type": "polykit.image", "inputs": {"image": {"kind": "base64", "data": "eA=="}}},
                "gen": {"class_type": "m/gen", "inputs": {"image": ["image", "image"]}},
                "out": {"class_type": "polykit.output", "inputs": {"mesh": ["gen", "mesh"]}},
            })
        )
        order = topological_order(request.prompt)
        self.assertEqual(order, ["image", "gen", "out"])

    def test_diamond_orders_sources_and_sink(self) -> None:
        request = WorkflowExecutionRequest(
            prompt=_prompt({
                "image": {"class_type": "polykit.image", "inputs": {"image": {"kind": "base64", "data": "eA=="}}},
                "a": {"class_type": "m/a", "inputs": {"image": ["image", "image"]}},
                "b": {"class_type": "m/b", "inputs": {"image": ["image", "image"]}},
                "merge": {"class_type": "m/merge", "inputs": {"mesh": ["a", "mesh"], "image": ["image", "image"]}},
                "out": {"class_type": "polykit.output", "inputs": {"mesh": ["merge", "mesh"]}},
            })
        )
        order = topological_order(request.prompt)
        self.assertEqual(set(order), set(request.prompt))
        self.assertEqual(order[0], "image")
        self.assertEqual(order[-1], "out")
        self.assertLess(order.index("a"), order.index("merge"))
        self.assertLess(order.index("b"), order.index("merge"))

    def test_rejects_empty_prompt(self) -> None:
        with self.assertRaises(WorkflowError):
            topological_order({})

    def test_rejects_empty_node_id(self) -> None:
        request = WorkflowExecutionRequest(prompt={"": {"class_type": "polykit.text", "inputs": {}}})
        with self.assertRaises(WorkflowError):
            topological_order(request.prompt)

    def test_rejects_missing_reference(self) -> None:
        request = WorkflowExecutionRequest(
            prompt=_prompt({
                "a": {"class_type": "polykit.text", "inputs": {"text": "x"}},
                "b": {"class_type": "polykit.text", "inputs": {"text": ["missing", "text"]}},
            })
        )
        with self.assertRaises(WorkflowError):
            topological_order(request.prompt)

    def test_rejects_cycle(self) -> None:
        request = WorkflowExecutionRequest(
            prompt=_prompt({
                "a": {"class_type": "polykit.text", "inputs": {"text": "x"}},
                "b": {"class_type": "polykit.text", "inputs": {"text": ["a", "text"]}},
            })
        )
        request.prompt["a"].inputs["text"] = ["b", "text"]
        with self.assertRaises(WorkflowError):
            topological_order(request.prompt)


class PartialExecutionTests(unittest.TestCase):
    def test_selects_only_target_sink_and_its_upstream_branch(self) -> None:
        request = WorkflowExecutionRequest(
            target_node_ids=["preview-a"],
            prompt=_prompt({
                "image": {"class_type": "polykit.image", "inputs": {"image": {"kind": "base64", "data": "eA=="}}},
                "a": {"class_type": "m/a", "inputs": {"image": ["image", "image"]}},
                "preview-a": {"class_type": "polykit.preview", "inputs": {"image": ["a", "image"]}},
                "b": {"class_type": "m/b", "inputs": {"image": ["image", "image"]}},
                "preview-b": {"class_type": "polykit.preview", "inputs": {"image": ["b", "image"]}},
            }),
        )

        selected = select_execution_prompt(request)

        self.assertEqual(list(selected), ["image", "a", "preview-a"])
        self.assertNotIn("b", selected)
        self.assertNotIn("preview-b", selected)

    def test_requires_sink_targets(self) -> None:
        request = WorkflowExecutionRequest(
            target_node_ids=["model"],
            prompt=_prompt({
                "model": {"class_type": "m/model", "inputs": {}},
            }),
        )
        with self.assertRaisesRegex(WorkflowError, "output or preview"):
            select_execution_prompt(request)

    def test_rejects_missing_target(self) -> None:
        request = WorkflowExecutionRequest(
            target_node_ids=["missing"],
            prompt=_prompt({"out": {"class_type": "polykit.output", "inputs": {}}}),
        )
        with self.assertRaisesRegex(WorkflowError, "missing node"):
            select_execution_prompt(request)


if __name__ == "__main__":
    unittest.main()

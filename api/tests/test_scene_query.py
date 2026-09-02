import importlib.util
from pathlib import Path
import tempfile
import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from application.world import compile_world_blender_projection, query_world_scene
from routers.workspace_worlds import (
    SceneQueryRequest,
    project_world_scene_to_blender,
    query_world_scene_route,
)
from services.blender_scene_bridge import compile_blender_scene_projection
from services.runtime_paths import runtime_paths
from services.scene_planner import ScenePlanError, normalize_scene_plan
from services.scene_query import SceneQuery, resolve_scene_query
from services.world_domain import create_world_document
from services.world_store import save_world


REPO_ROOT = Path(__file__).resolve().parents[2]
BLENDER_BRIDGE_PATH = REPO_ROOT / "src/areas/workflows/nodes/blender-scene/semantic_bridge.py"


def _load_blender_bridge():
    spec = importlib.util.spec_from_file_location("polykit_blender_semantic_bridge", BLENDER_BRIDGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {BLENDER_BRIDGE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scene():
    return normalize_scene_plan({
        "schema_version": 1,
        "kind": "polykit.scene-plan",
        "sceneId": "query-demo",
        "sceneKind": "outdoor",
        "bounds": {"width": 40, "depth": 40, "height": 20},
        "objects": [
            {
                "id": "lake",
                "name": "Mirror Lake",
                "role": "background",
                "category": "water",
                "aliases": ["湖", "湖面"],
                "collections": ["Lake"],
                "tags": ["water"],
                "size": [8, 0.2, 8],
            },
            {
                "id": "pine-a",
                "name": "Short Pine",
                "role": "context",
                "category": "vegetation",
                "aliases": ["松树", "pine"],
                "collections": ["NorthForest", "LakeShore"],
                "tags": ["pine", "tree"],
                "size": [1.5, 3.0, 1.5],
                "asset": {"assetId": "pine-alpine-01", "source": "generated"},
            },
            {
                "id": "pine-b",
                "name": "Tall Pine",
                "role": "context",
                "category": "vegetation",
                "aliases": ["松树", "pine"],
                "collections": ["NorthForest"],
                "tags": ["pine", "tree"],
                "size": [1.5, 5.0, 1.5],
            },
            {
                "id": "rock-a",
                "name": "Granite Boulder",
                "role": "context",
                "category": "rock",
                "collections": ["NorthForest", "LakeShore"],
                "tags": ["rock"],
                "size": [2, 1.2, 2],
            },
        ],
        "relations": [
            {"subject": "pine-a", "type": "near", "object": "lake", "distance": 3},
            {"subject": "rock-a", "type": "near", "object": "lake", "distance": 2},
        ],
        "instances": [
            {"id": "lake-i", "objectId": "lake", "position": [0, 0, 0]},
            {"id": "pine-a-i", "objectId": "pine-a", "position": [2, 0, 0]},
            {"id": "pine-b-i", "objectId": "pine-b", "position": [9, 0, 0]},
            {"id": "rock-a-i", "objectId": "rock-a", "position": [1, 0, 0]},
        ],
    })


def _world():
    return {"runtime": {"version": 1, "scene": _scene().model_dump(mode="json", by_alias=True)}}


class SceneQueryTests(unittest.TestCase):
    def test_semantic_spatial_query_resolves_nearest_matching_instance(self) -> None:
        result = resolve_scene_query(_scene(), {
            "category": "vegetation",
            "terms": ["pine"],
            "collectionsAny": ["LakeShore"],
            "nearObjectId": "lake",
            "maxDistance": 5,
            "sort": "distance",
            "limit": 1,
        })
        self.assertEqual(result.total, 1)
        self.assertEqual([item.instance_id for item in result.matches], ["pine-a-i"])
        self.assertAlmostEqual(result.matches[0].distance or 0, 2.0)

    def test_relation_query_uses_scene_graph_before_blender(self) -> None:
        result = resolve_scene_query(_scene(), {
            "target": "object",
            "relation": {"type": "near", "objectId": "lake"},
            "category": "vegetation",
        })
        self.assertEqual([item.object_id for item in result.matches], ["pine-a"])

    def test_ids_accept_instance_identity(self) -> None:
        result = resolve_scene_query(_scene(), SceneQuery(ids=["pine-b-i"]))
        self.assertEqual([item.instance_id for item in result.matches], ["pine-b-i"])

    def test_object_identity_resolves_multiple_declared_instances(self) -> None:
        payload = _scene().model_dump(mode="json", by_alias=True)
        payload["instances"].append({
            "id": "pine-a-i-2",
            "objectId": "pine-a",
            "position": [4, 0, 0],
        })
        plan = normalize_scene_plan(payload)
        result = resolve_scene_query(plan, {"ids": ["pine-a"]})
        projection = compile_blender_scene_projection(plan)
        self.assertEqual([item.instance_id for item in result.matches], ["pine-a-i", "pine-a-i-2"])
        self.assertEqual(
            {item["instanceId"] for item in projection["instances"] if item["objectId"] == "pine-a"},
            {"pine-a-i", "pine-a-i-2"},
        )

    def test_malformed_list_filter_is_rejected_instead_of_becoming_unfiltered(self) -> None:
        for field, value in (
            ("ids", 123),
            ("ids", None),
            ("terms", {"pine": True}),
            ("tagsAny", ["pine", 7]),
            ("collectionsAny", ["LakeShore", ""]),
            ("ids", ["x" * 121]),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    SceneQuery.model_validate({field: value})

    def test_unknown_relation_type_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SceneQuery.model_validate({"relation": {"type": "around", "objectId": "lake"}})

    def test_unknown_relation_anchor_is_rejected(self) -> None:
        with self.assertRaises(ScenePlanError):
            resolve_scene_query(_scene(), {"relation": {"type": "near", "objectId": "missing"}})

    def test_duplicate_scene_instance_ids_are_rejected(self) -> None:
        payload = _scene().model_dump(mode="json", by_alias=True)
        payload["instances"][1]["id"] = payload["instances"][0]["id"]
        with self.assertRaises(ScenePlanError):
            normalize_scene_plan(payload)

    def test_world_application_exposes_query_and_blender_projection(self) -> None:
        result = query_world_scene(_world(), {"ids": ["pine-a-i"]}, world_id="world-1")
        projection = compile_world_blender_projection(_world(), world_id="world-1")
        self.assertEqual([item.instance_id for item in result.matches], ["pine-a-i"])
        self.assertEqual(projection["sceneId"], "query-demo")
        self.assertEqual(projection["kind"], "polykit.blender-scene-projection")

    def test_blender_projection_keeps_semantic_identity_out_of_filenames(self) -> None:
        projection = compile_blender_scene_projection(_scene())
        objects = {item["objectId"]: item for item in projection["objects"]}
        instances = {item["instanceId"]: item for item in projection["instances"]}
        pine = objects["pine-a"]
        pine_instance = instances["pine-a-i"]

        self.assertEqual(projection["rootCollection"], "PolyKit")
        self.assertIn("NorthForest", projection["collections"])
        self.assertIn("LakeShore", projection["collections"])
        self.assertEqual(pine["customProperties"]["polykit_object_id"], "pine-a")
        self.assertEqual(pine["customProperties"]["polykit_asset_id"], "pine-alpine-01")
        self.assertIn("pine", pine["customProperties"]["polykit_tags"])
        self.assertEqual(pine_instance["customProperties"]["polykit_instance_id"], "pine-a-i")
        self.assertEqual(pine_instance["objectId"], "pine-a")

    def test_blender_adapter_resolves_only_stable_ids(self) -> None:
        bridge = _load_blender_bridge()

        class FakeObject(dict):
            pass

        object_a = FakeObject()
        object_a_copy = FakeObject()
        object_b = FakeObject()
        bridge.apply_custom_properties(object_a, {
            "polykit_object_id": "pine-a",
            "polykit_instance_id": "pine-a-i",
            "polykit_tags": ["pine", "tree"],
        })
        bridge.apply_custom_properties(object_a_copy, {
            "polykit_object_id": "pine-a",
            "polykit_instance_id": "pine-a-i-2",
        })
        bridge.apply_custom_properties(object_b, {
            "polykit_object_id": "rock-a",
            "polykit_instance_id": "rock-a-i",
        })

        class FakeData:
            objects = [object_a, object_a_copy, object_b]

        class FakeBpy:
            data = FakeData()

        index = bridge.semantic_index(FakeBpy())
        self.assertIs(index["instances"]["pine-a-i"], object_a)
        self.assertEqual(index["objects"]["pine-a"], [object_a, object_a_copy])
        self.assertEqual(
            bridge.resolve_semantic_objects(FakeBpy(), instance_ids=["rock-a-i"], object_ids=["pine-a"]),
            [object_b, object_a, object_a_copy],
        )
        self.assertNotIn("pine", index["objects"])

    def test_blender_adapter_rejects_ambiguous_instance_identity(self) -> None:
        bridge = _load_blender_bridge()

        class FakeObject(dict):
            pass

        object_a = FakeObject(name="A")
        object_b = FakeObject(name="B")
        for obj in (object_a, object_b):
            obj["polykit_object_id"] = "pine"
            obj["polykit_instance_id"] = "duplicate"

        class FakeData:
            objects = [object_a, object_b]

        class FakeBpy:
            data = FakeData()

        with self.assertRaisesRegex(ValueError, "Duplicate PolyKit instance id"):
            bridge.semantic_index(FakeBpy())

    def test_blender_projection_disambiguates_sanitised_names(self) -> None:
        plan = normalize_scene_plan({
            "objects": [
                {"id": "a/b", "name": "A", "role": "context"},
                {"id": "a?b", "name": "B", "role": "context"},
            ],
        })
        projection = compile_blender_scene_projection(plan)
        names = [item["blenderName"] for item in projection["objects"]]
        self.assertEqual(len(names), len(set(names)))

    def test_blender_adapter_rejects_non_semantic_property_keys(self) -> None:
        bridge = _load_blender_bridge()
        with self.assertRaisesRegex(ValueError, "custom property keys"):
            bridge.apply_custom_properties({}, {"tags": ["pine"]})

    def test_blender_adapter_rejects_non_string_identity_properties(self) -> None:
        bridge = _load_blender_bridge()

        class FakeData:
            objects = [{"polykit_object_id": 123}]

        class FakeBpy:
            data = FakeData()

        with self.assertRaisesRegex(ValueError, "must be a non-empty string"):
            bridge.semantic_index(FakeBpy())


class SceneQueryRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_paths = runtime_paths.snapshot()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="polykit-scene-query-")
        runtime_paths.update(workspace_dir=Path(self.temp_dir.name))
        world = create_world_document(name="Query world")
        world["id"] = "query-world"
        world["runtime"]["scene"] = _scene().model_dump(mode="json", by_alias=True, exclude_none=True)
        save_world("query-world", world)

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original_paths.models,
            workspace_dir=self.original_paths.workspace,
            workflows_dir=self.original_paths.workflows,
            node_packs_dir=self.original_paths.node_packs,
        )
        self.temp_dir.cleanup()

    async def test_world_routes_expose_query_and_projection(self) -> None:
        query_result = await query_world_scene_route(
            "query-world",
            SceneQueryRequest(query={"ids": ["pine-a-i"]}),
        )
        projection = await project_world_scene_to_blender("query-world")
        self.assertEqual(query_result["matches"][0]["instanceId"], "pine-a-i")
        self.assertEqual(projection["kind"], "polykit.blender-scene-projection")

    async def test_world_query_route_returns_client_error_for_malformed_filter(self) -> None:
        with self.assertRaises(HTTPException) as context:
            await query_world_scene_route(
                "query-world",
                SceneQueryRequest(query={"ids": {"pine": True}}),
            )
        self.assertEqual(context.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()

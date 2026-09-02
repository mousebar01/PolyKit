import importlib.util
from pathlib import Path
import unittest

from application.world import compile_world_blender_projection, query_world_scene
from services.blender_scene_bridge import compile_blender_scene_projection
from services.scene_planner import normalize_scene_plan
from services.scene_query import SceneQuery, resolve_scene_query


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
            "tags": ["pine", "tree"],
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


if __name__ == "__main__":
    unittest.main()

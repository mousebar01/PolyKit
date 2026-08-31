import unittest

from services.production_recipe import PRODUCTION_RECIPE_KIND, compile_repair_recipe
from services.world_domain import create_world_document


class ProductionRecipeTests(unittest.TestCase):
    def _world(self) -> dict:
        world = create_world_document(name="Cabin", prompt="Repair a small cabin")
        world["id"] = "world-1"
        world["runtime"]["build"]["buildings"] = [
            {
                "id": "cabin",
                "name": "Cabin",
                "generator": "blender-parametric",
                "parameters": {
                    "width": 8.0,
                    "depth": 7.0,
                    "wallHeight": 4.2,
                    "contactTolerance": 0.03,
                },
                "anchors": [],
                "attachments": [],
            }
        ]
        return world

    def _scope(
        self,
        *,
        scope_id: str = "repair:world.spatial.validate:wall-floor",
        locality: str = "relationship",
        causal_system: str = "construction_geometry",
        safe_to_localize: bool = True,
    ) -> dict:
        return {
            "schema_version": 1,
            "kind": "polykit.repair-scope",
            "id": scope_id,
            "source": {
                "capability": "world.spatial.validate",
                "check_id": "spatial.attachment.cabin.wall-floor",
            },
            "source_status": "fail",
            "locality": locality,
            "causal_system": causal_system,
            "affected_object_ids": ["floor", "left-wall"] if safe_to_localize else [],
            "affected_relationship_ids": ["wall-floor"] if locality == "relationship" else [],
            "affected_subject_ids": ["cabin", "wall-floor"] if locality == "relationship" else [],
            "action_hint": "repair_relationship_geometry",
            "safe_to_localize": safe_to_localize,
            "reason": "Attachment failed.",
        }

    def _validation(self, scope: dict, *, capability: str = "world.spatial.validate") -> dict:
        return {
            "world_id": "world-1",
            "capability": capability,
            "status": "fail",
            "repair_scopes": [scope],
        }

    def test_local_relationship_does_not_silently_expand_to_building(self) -> None:
        scope = self._scope()
        recipe = compile_repair_recipe(
            world_id="world-1",
            world=self._world(),
            validation=self._validation(scope),
            repair_scope_id=scope["id"],
        )

        self.assertEqual(recipe["kind"], PRODUCTION_RECIPE_KIND)
        self.assertEqual(recipe["status"], "blocked")
        self.assertEqual(recipe["blockers"][0]["code"], "repair-scope-expansion-required")
        self.assertEqual(recipe["blockers"][0]["required_capability"], "blender-scene/repair-parts")
        self.assertTrue(recipe["available_fallback"]["scope_expanded"])
        self.assertIsNone(recipe["workflow_definition"])
        self.assertIsNone(recipe["execution_request"])

    def test_explicit_scope_expansion_compiles_existing_building_workflow(self) -> None:
        scope = self._scope()
        recipe = compile_repair_recipe(
            world_id="world-1",
            world=self._world(),
            validation=self._validation(scope),
            repair_scope_id=scope["id"],
            allow_scope_expansion=True,
        )

        self.assertEqual(recipe["status"], "ready")
        self.assertTrue(recipe["scope_expanded"])
        self.assertEqual(recipe["compiled_scope"], {"locality": "building", "building_id": "cabin"})
        execution = recipe["execution_request"]
        self.assertEqual(execution["workflow_id"], "building-construction")
        self.assertEqual(execution["prompt"]["build"]["class_type"], "blender-scene/build")
        self.assertEqual(execution["metadata"]["repair_scope_id"], scope["id"])
        self.assertTrue(execution["metadata"]["scope_expanded"])
        definition = recipe["workflow_definition"]
        self.assertEqual(definition["metadata"]["repair_scope_id"], scope["id"])
        self.assertEqual(definition["nodes"][1]["data"]["nodePackId"], "blender-scene/build")
        self.assertEqual(definition["edges"][0]["targetHandle"], "input-0")

    def test_scene_level_construction_can_compile_without_scope_expansion(self) -> None:
        scope = self._scope(
            scope_id="repair:world.construction.validate:construction",
            locality="scene",
            causal_system="construction",
            safe_to_localize=False,
        )
        scope["source"] = {
            "capability": "world.construction.validate",
            "issue_code": "construction-invalid",
        }
        validation = self._validation(scope, capability="world.construction.validate")
        recipe = compile_repair_recipe(
            world_id="world-1",
            world=self._world(),
            validation=validation,
            repair_scope_id=scope["id"],
        )

        self.assertEqual(recipe["status"], "ready")
        self.assertFalse(recipe["scope_expanded"])
        self.assertEqual(recipe["compiled_scope"]["building_id"], "cabin")

    def test_evidence_scope_returns_no_workflow(self) -> None:
        scope = self._scope(
            scope_id="repair:world.visual.validate:visual-report-missing",
            locality="evidence",
            causal_system="evidence_pipeline",
            safe_to_localize=False,
        )
        scope["source"] = {
            "capability": "world.visual.validate",
            "issue_code": "visual-report-missing",
        }
        validation = self._validation(scope, capability="world.visual.validate")
        recipe = compile_repair_recipe(
            world_id="world-1",
            world=self._world(),
            validation=validation,
            repair_scope_id=scope["id"],
        )

        self.assertEqual(recipe["status"], "no_workflow")
        self.assertEqual(recipe["next_action"], scope["action_hint"])
        self.assertIsNone(recipe["workflow_definition"])

    def test_camera_scope_names_missing_backend_capability(self) -> None:
        scope = self._scope(
            scope_id="repair:world.visual.validate:camera",
            locality="object",
            causal_system="camera_composition",
            safe_to_localize=True,
        )
        scope["source"] = {
            "capability": "world.visual.validate",
            "check_id": "spatial.p0.hero.frustum",
        }
        validation = self._validation(scope, capability="world.visual.validate")
        recipe = compile_repair_recipe(
            world_id="world-1",
            world=self._world(),
            validation=validation,
            repair_scope_id=scope["id"],
        )

        self.assertEqual(recipe["status"], "blocked")
        self.assertEqual(recipe["blockers"][0]["required_capability"], "blender-scene/repair-camera")

    def test_unknown_repair_scope_is_rejected(self) -> None:
        scope = self._scope()
        with self.assertRaisesRegex(ValueError, "Repair scope was not found"):
            compile_repair_recipe(
                world_id="world-1",
                world=self._world(),
                validation=self._validation(scope),
                repair_scope_id="repair:missing",
            )


if __name__ == "__main__":
    unittest.main()

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
                "anchors": [
                    {"id": "floor-left", "partId": "floor", "position": [0.0, 0.0, 0.0]},
                    {"id": "wall-bottom", "partId": "left-wall", "position": [0.0, 0.0, 0.0]},
                ],
                "attachments": [
                    {
                        "id": "wall-floor",
                        "from": "floor-left",
                        "to": "wall-bottom",
                        "mode": "support",
                        "tolerance": 0.03,
                    }
                ],
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

    def _validation(
        self,
        scope: dict,
        *,
        capability: str = "world.spatial.validate",
        source_mesh: str | None = None,
    ) -> dict:
        details: dict = {}
        if source_mesh:
            details = {
                "checks": [
                    {
                        "id": "spatial.final-mesh",
                        "status": "pass",
                        "metrics": {"workspace_path": source_mesh},
                    }
                ]
            }
        return {
            "world_id": "world-1",
            "capability": capability,
            "status": "fail",
            "repair_scopes": [scope],
            "details": details,
        }

    def test_local_relationship_requires_authoritative_source_mesh(self) -> None:
        scope = self._scope()
        recipe = compile_repair_recipe(
            world_id="world-1",
            world=self._world(),
            validation=self._validation(scope),
            repair_scope_id=scope["id"],
        )

        self.assertEqual(recipe["kind"], PRODUCTION_RECIPE_KIND)
        self.assertEqual(recipe["status"], "blocked")
        self.assertEqual(recipe["blockers"][0]["code"], "repair-source-mesh-missing")
        self.assertEqual(recipe["blockers"][0]["required_capability"], "spatial.final-mesh")
        self.assertTrue(recipe["available_fallback"]["scope_expanded"])
        self.assertIsNone(recipe["workflow_definition"])
        self.assertIsNone(recipe["execution_request"])

    def test_authoritative_source_mesh_compiles_exact_part_repair(self) -> None:
        scope = self._scope()
        recipe = compile_repair_recipe(
            world_id="world-1",
            world=self._world(),
            validation=self._validation(scope, source_mesh="Scenes/cabin.glb"),
            repair_scope_id=scope["id"],
        )

        self.assertEqual(recipe["status"], "ready")
        self.assertFalse(recipe["scope_expanded"])
        self.assertEqual(
            recipe["compiled_scope"],
            {
                "locality": "relationship",
                "building_id": "cabin",
                "part_ids": ["floor", "left-wall"],
                "relationship_ids": ["wall-floor"],
            },
        )
        execution = recipe["execution_request"]
        self.assertEqual(execution["workflow_id"], "building-part-repair")
        self.assertEqual(execution["prompt"]["source"]["class_type"], "polykit.mesh")
        self.assertEqual(
            execution["prompt"]["source"]["inputs"]["mesh"],
            {"kind": "workspace_path", "path": "Scenes/cabin.glb"},
        )
        self.assertEqual(execution["prompt"]["repair"]["class_type"], "blender-scene/repair-parts")
        params = execution["prompt"]["repair"]["inputs"]["params"]
        self.assertEqual(params["part_ids"], ["floor", "left-wall"])
        self.assertEqual(params["attachment_ids"], ["wall-floor"])
        self.assertEqual(params["repair_mode"], "parts")
        self.assertFalse(execution["metadata"]["scope_expanded"])
        self.assertEqual(execution["metadata"]["repair_part_ids"], ["floor", "left-wall"])
        definition = recipe["workflow_definition"]
        self.assertEqual(definition["nodes"][0]["type"], "meshNode")
        self.assertEqual(definition["nodes"][1]["data"]["nodePackId"], "blender-scene/repair-parts")
        self.assertEqual(definition["edges"][0]["targetHandle"], "input-0")

    def test_explicit_scope_expansion_without_source_mesh_compiles_building_workflow(self) -> None:
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

    def test_stale_local_part_ids_fail_closed(self) -> None:
        scope = self._scope()
        scope["affected_object_ids"] = ["floor", "missing-wall"]
        recipe = compile_repair_recipe(
            world_id="world-1",
            world=self._world(),
            validation=self._validation(scope, source_mesh="Scenes/cabin.glb"),
            repair_scope_id=scope["id"],
        )
        self.assertEqual(recipe["status"], "blocked")
        self.assertEqual(recipe["blockers"][0]["code"], "repair-scope-stale")
        self.assertIn("missing-wall", recipe["blockers"][0]["message"])

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

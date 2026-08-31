import unittest

from services.repair_scope import (
    REPAIR_SCOPE_KIND,
    attach_repair_scope,
    derive_repair_scopes,
)
from services.world_validation import validate_world


class RepairScopeTests(unittest.TestCase):
    def test_attachment_scope_localizes_to_parts_and_relationship(self) -> None:
        check = {
            "id": "spatial.attachment.cabin.wall-floor",
            "category": "spatial",
            "judge": "spatial",
            "required": True,
            "status": "fail",
            "subjects": ["cabin", "wall-floor"],
            "metrics": {
                "source_part": "floor",
                "target_part": "left-wall",
            },
            "message": "Final GLB violates the BuildSpec attachment tolerance.",
        }
        issues = [
            {
                "code": check["id"],
                "severity": "error",
                "message": check["message"],
                "subject_id": "cabin",
            }
        ]

        scopes = derive_repair_scopes(
            "world.spatial.validate",
            issues,
            checks=[check],
        )

        self.assertEqual(len(scopes), 1)
        scope = scopes[0]
        self.assertEqual(scope["kind"], REPAIR_SCOPE_KIND)
        self.assertEqual(scope["locality"], "relationship")
        self.assertEqual(scope["causal_system"], "construction_geometry")
        self.assertEqual(scope["affected_object_ids"], ["floor", "left-wall"])
        self.assertEqual(scope["affected_relationship_ids"], ["wall-floor"])
        self.assertTrue(scope["safe_to_localize"])
        self.assertEqual(scope["action_hint"], "repair_relationship_geometry")

    def test_camera_scope_keeps_p0_subject_local(self) -> None:
        check = {
            "id": "spatial.p0.door.frustum",
            "category": "spatial",
            "judge": "spatial",
            "required": True,
            "status": "fail",
            "subjects": ["doorway"],
            "metrics": {},
            "message": "P0 object is outside the validated camera frustum.",
        }
        scopes = derive_repair_scopes(
            "world.visual.validate",
            [],
            checks=[check],
        )

        self.assertEqual(scopes[0]["locality"], "object")
        self.assertEqual(scopes[0]["causal_system"], "camera_composition")
        self.assertEqual(scopes[0]["affected_object_ids"], ["doorway"])
        self.assertEqual(scopes[0]["affected_subject_ids"], ["doorway"])
        self.assertTrue(scopes[0]["safe_to_localize"])

    def test_missing_report_is_evidence_scope_not_scene_rebuild(self) -> None:
        scopes = derive_repair_scopes(
            "world.visual.validate",
            [
                {
                    "code": "visual-report-missing",
                    "severity": "warning",
                    "message": "Visual report is missing.",
                }
            ],
        )

        self.assertEqual(scopes[0]["locality"], "evidence")
        self.assertEqual(scopes[0]["causal_system"], "evidence_pipeline")
        self.assertFalse(scopes[0]["safe_to_localize"])
        self.assertEqual(scopes[0]["action_hint"], "regenerate_or_attach_evidence")

    def test_earliest_failure_embeds_matching_scope(self) -> None:
        scopes = derive_repair_scopes(
            "world.visual.validate",
            [],
            checks=[
                {
                    "id": "p0.hero.edge-mae",
                    "category": "silhouette",
                    "judge": "metric",
                    "required": True,
                    "status": "needs_review",
                    "subjects": ["hero"],
                    "message": "P0 edge mismatch.",
                }
            ],
        )
        failure = attach_repair_scope(
            {
                "check_id": "p0.hero.edge-mae",
                "category": "silhouette",
                "status": "needs_review",
                "subjects": ["hero"],
            },
            scopes,
        )

        self.assertIsNotNone(failure)
        self.assertEqual(failure["repair_scope_id"], scopes[0]["id"])
        self.assertEqual(failure["repair_scope"]["causal_system"], "silhouette")

    def test_world_validator_exposes_repair_scopes(self) -> None:
        world = {
            "runtime": {
                "version": 1,
                "scene": {
                    "objects": [{"id": "hero"}],
                    "metadata": {"layoutQuality": {"status": "invalid"}},
                },
            }
        }

        result = validate_world("world-1", world, "world.blockout.validate")

        self.assertEqual(result["status"], "fail")
        self.assertEqual(len(result["repair_scopes"]), 1)
        scope = result["repair_scopes"][0]
        self.assertEqual(scope["causal_system"], "scene_layout")
        self.assertEqual(scope["locality"], "scene")
        self.assertFalse(scope["safe_to_localize"])


if __name__ == "__main__":
    unittest.main()

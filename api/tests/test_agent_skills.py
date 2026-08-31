import tempfile
import unittest
from pathlib import Path

from services.agent_skills import (
    AGENT_SKILL_KIND,
    AgentSkillError,
    bundled_skills_dir,
    get_agent_skill,
    list_agent_skills,
    load_agent_skill,
    read_agent_skill_resource,
)


class AgentSkillsTests(unittest.TestCase):
    def _write_skill(self, root: Path, name: str, frontmatter: str, body: str = "# Instructions\n\nDo the task.\n") -> Path:
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
        return skill_dir

    def test_list_is_metadata_only_and_get_uses_progressive_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(
                root,
                "scene-review",
                "name: scene-review\ndescription: Review a PolyKit scene and choose the next validation action.",
            )

            catalog = list_agent_skills(root)
            self.assertEqual(len(catalog), 1)
            self.assertEqual(catalog[0]["kind"], AGENT_SKILL_KIND)
            self.assertNotIn("instructions", catalog[0])

            full = get_agent_skill("scene-review", root)
            self.assertIn("Do the task.", full["instructions"])
            self.assertFalse(full["allowed_tools_authorized"])

    def test_frontmatter_supports_folded_description_metadata_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = self._write_skill(
                root,
                "reference-build",
                "\n".join([
                    "name: reference-build # directory-aligned name",
                    "description: >",
                    "  Reconstruct a referenced scene",
                    "  through PolyKit workflows.",
                    "compatibility: 'Requires PolyKit API'",
                    "metadata:",
                    "  author: PolyKit",
                    "  version: '1'",
                    "allowed-tools: polykit_world_get polykit_world_validate",
                ]),
            )
            skill = load_agent_skill(skill_dir)
            self.assertEqual(skill["description"], "Reconstruct a referenced scene through PolyKit workflows.")
            self.assertEqual(skill["metadata"], {"author": "PolyKit", "version": "1"})
            self.assertEqual(skill["allowed_tools"], "polykit_world_get polykit_world_validate")
            self.assertFalse(skill["allowed_tools_authorized"])

    def test_skill_name_must_match_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = self._write_skill(
                root,
                "scene-review",
                "name: other-skill\ndescription: Review a scene.",
            )
            with self.assertRaisesRegex(AgentSkillError, "must match directory"):
                load_agent_skill(skill_dir)

    def test_resource_read_is_bounded_to_declared_resource_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = self._write_skill(
                root,
                "scene-review",
                "name: scene-review\ndescription: Review a scene.",
            )
            (skill_dir / "references").mkdir()
            (skill_dir / "references" / "guide.md").write_text("evidence first\n", encoding="utf-8")
            (skill_dir / "scripts").mkdir()
            (skill_dir / "scripts" / "helper.py").write_text("print('not executed')\n", encoding="utf-8")

            resource = read_agent_skill_resource("scene-review", "scripts/helper.py", root)
            self.assertIn("not executed", resource["content"])
            self.assertFalse(resource["executable_by_polykit"])

            with self.assertRaisesRegex(AgentSkillError, "escapes its declared resource directory"):
                read_agent_skill_resource("scene-review", "scripts/../SKILL.md", root)

    def test_bundled_reference_reconstruction_skill_is_discoverable(self) -> None:
        names = [skill["name"] for skill in list_agent_skills(bundled_skills_dir())]
        self.assertIn("reference-reconstruction", names)
        skill = get_agent_skill("reference-reconstruction", bundled_skills_dir())
        self.assertIn("ProductionRecipe", skill["instructions"])
        self.assertIn("WorkflowRun", skill["instructions"])
        self.assertFalse(skill["allowed_tools_authorized"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services.agent_skills as agent_skills
from services.agent_skills import AgentSkillError, list_agent_skills, read_agent_skill_resource


class AgentSkillEfficiencyTests(unittest.TestCase):
    def _write_skill(self, root: Path, name: str) -> Path:
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Review a PolyKit scene efficiently.\n"
            "---\n\n"
            "# Instructions\n\n"
            + ("Long instruction body that catalog discovery should not read.\n" * 1000),
            encoding="utf-8",
        )
        return skill_dir

    def test_catalog_does_not_use_full_file_read_for_skill_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root, "scene-review")
            with patch.object(Path, "read_text", side_effect=AssertionError("full SKILL.md read")):
                catalog = list_agent_skills(root)
            self.assertEqual(catalog[0]["name"], "scene-review")
            self.assertNotIn("instructions", catalog[0])

    def test_resource_read_does_not_reload_selected_skill_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = self._write_skill(root, "scene-review")
            (skill_dir / "references").mkdir()
            (skill_dir / "references" / "guide.md").write_text("evidence first\n", encoding="utf-8")
            with patch.object(
                agent_skills,
                "get_agent_skill",
                side_effect=AssertionError("resource read reloaded SKILL.md"),
            ):
                resource = read_agent_skill_resource("scene-review", "references/guide.md", root)
            self.assertEqual(resource["skill"], "scene-review")
            self.assertEqual(resource["content"], "evidence first\n")

    def test_resource_read_still_validates_skill_identity_from_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = self._write_skill(root, "scene-review")
            (skill_dir / "references").mkdir()
            (skill_dir / "references" / "guide.md").write_text("evidence first\n", encoding="utf-8")
            (skill_dir / "SKILL.md").write_text(
                "---\nname: different-skill\ndescription: Invalid directory identity.\n---\n\n# Body\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(AgentSkillError, "must match directory"):
                read_agent_skill_resource("scene-review", "references/guide.md", root)


if __name__ == "__main__":
    unittest.main()

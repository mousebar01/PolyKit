import unittest

from services.asset_names import output_name, slugify


class AssetNameTests(unittest.TestCase):
    def test_output_name_format(self) -> None:
        name = output_name("My Robot")
        self.assertRegex(name, r"^my_robot_\d{8}-\d{6}_[0-9a-f]{8}\.glb$")

    def test_output_name_tag_and_ext(self) -> None:
        name = output_name("robot", tag="textured", ext=".glb")
        self.assertRegex(name, r"^robot_\d{8}-\d{6}_[0-9a-f]{8}_textured\.glb$")
        name = output_name("robot", ext="png")
        self.assertTrue(name.endswith(".png"))

    def test_slugify_collapses_and_lowercases(self) -> None:
        self.assertEqual(slugify("a b---c__d"), "a_b_c_d")

    def test_slugify_keeps_cjk(self) -> None:
        self.assertEqual(slugify("机器人 v2"), "机器人_v2")

    def test_slugify_empty_falls_back(self) -> None:
        self.assertEqual(slugify(""), "model")
        self.assertEqual(slugify("!!!", fallback="thing"), "thing")

    def test_output_names_are_unique(self) -> None:
        self.assertNotEqual(output_name("robot"), output_name("robot"))


if __name__ == "__main__":
    unittest.main()

import struct
import tempfile
import unittest
from pathlib import Path

from services.fake_generator import FakeGenerator


class FakeGeneratorTests(unittest.TestCase):
    def test_generates_a_valid_marked_glb_without_model_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            generator = FakeGenerator(Path(tmp) / "models", Path(tmp) / "workspace")
            generator.load()
            progress = []
            output = generator.generate(b"test-image", {}, lambda pct, step: progress.append((pct, step)))

            self.assertTrue(output.exists())
            self.assertEqual(output.read_bytes()[:4], b"glTF")
            _magic, version, length = struct.unpack("<4sII", output.read_bytes()[:12])
            self.assertEqual(version, 2)
            self.assertEqual(length, output.stat().st_size)
            self.assertEqual(progress[-1][0], 100)
            self.assertIn("Fake executor", progress[-1][1])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from services.model_pack_subprocess import (
    _P3SAM_KNOB_ENV_VARS,
    ModelPackSubprocess,
    _p3sam_vram_env_overrides,
)


GIB = 1024 * 1024 * 1024


class P3samVramDefaultTests(unittest.TestCase):
    def test_roomy_gpu_injects_nothing(self) -> None:
        self.assertEqual(_p3sam_vram_env_overrides(free_bytes=30 * GIB), {})
        self.assertEqual(_p3sam_vram_env_overrides(free_bytes=None), {})

    def test_mid_tier_matches_validated_safe_profile(self) -> None:
        env = _p3sam_vram_env_overrides(free_bytes=20 * GIB)
        self.assertEqual(
            env,
            {
                "HUNYUAN3D_PART_P3SAM_POINT_NUM": "40000",
                "HUNYUAN3D_PART_P3SAM_PROMPT_NUM": "160",
                "HUNYUAN3D_PART_P3SAM_PROMPT_BS": "4",
            },
        )

    def test_low_tier_mirrors_windows_conservative_profile(self) -> None:
        env = _p3sam_vram_env_overrides(free_bytes=4 * GIB)
        self.assertEqual(
            env,
            {
                "HUNYUAN3D_PART_P3SAM_POINT_NUM": "32768",
                "HUNYUAN3D_PART_P3SAM_PROMPT_NUM": "128",
                "HUNYUAN3D_PART_P3SAM_PROMPT_BS": "1",
            },
        )

    def _build_env(self, manifest_id: str) -> dict:
        with TemporaryDirectory() as td:
            proc = ModelPackSubprocess(Path(td), {"id": manifest_id})
            return proc._build_env()

    def test_build_env_injects_for_hunyuan_pack(self) -> None:
        clean = {var: value for var, value in os.environ.items() if var not in _P3SAM_KNOB_ENV_VARS.values()}
        with mock.patch.dict(os.environ, clean, clear=True):
            with mock.patch("services.model_pack_subprocess._free_cuda_bytes", return_value=20 * GIB):
                env = self._build_env("hunyuan3d-part")
                full_id_env = self._build_env("hunyuan3d-part/decompose-mesh")
        self.assertEqual(env["HUNYUAN3D_PART_P3SAM_POINT_NUM"], "40000")
        self.assertEqual(full_id_env["HUNYUAN3D_PART_P3SAM_POINT_NUM"], "40000")

    def test_build_env_respects_explicit_user_vars(self) -> None:
        pinned = {
            "HUNYUAN3D_PART_P3SAM_POINT_NUM": "90000",
            "HUNYUAN3D_PART_P3SAM_PROMPT_NUM": "300",
            "HUNYUAN3D_PART_P3SAM_PROMPT_BS": "16",
        }
        clean = {var: value for var, value in os.environ.items() if var not in _P3SAM_KNOB_ENV_VARS.values()}
        with mock.patch.dict(os.environ, {**clean, **pinned}, clear=True):
            with mock.patch("services.model_pack_subprocess._free_cuda_bytes", return_value=4 * GIB):
                env = self._build_env("hunyuan3d-part")
        for var, value in pinned.items():
            self.assertEqual(env[var], value)

    def test_build_env_skips_other_packs(self) -> None:
        clean = {var: value for var, value in os.environ.items() if var not in _P3SAM_KNOB_ENV_VARS.values()}
        with mock.patch.dict(os.environ, clean, clear=True):
            with mock.patch("services.model_pack_subprocess._free_cuda_bytes", return_value=4 * GIB):
                env = self._build_env("mesh-optimizer")
        self.assertNotIn("HUNYUAN3D_PART_P3SAM_POINT_NUM", env)


if __name__ == "__main__":
    unittest.main()

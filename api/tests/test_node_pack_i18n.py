import json
import unittest
from pathlib import Path

from services import node_catalog
from services.runtime_paths import runtime_paths


REPO_ROOT = Path(__file__).resolve().parents[2]
TRELLIS_MANIFEST = REPO_ROOT / "node-packs" / "trellis2" / "manifest.json"


class TrellisManifestI18nTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads(TRELLIS_MANIFEST.read_text(encoding="utf-8"))
        self.nodes = {node["id"]: node for node in self.manifest["nodes"]}

    def test_machine_values_remain_language_neutral(self) -> None:
        params = {param["id"]: param for param in self.nodes["generate"]["params_schema"]}
        self.assertEqual(params["pipeline_type"]["default"], "1024_cascade")
        self.assertEqual(
            [option["value"] for option in params["pipeline_type"]["options"]],
            ["512", "1024", "1024_cascade", "1536_cascade"],
        )
        self.assertEqual(
            [option["value"] for option in params["gguf_quant"]["options"]],
            ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"],
        )
        self.assertEqual(params["seed"]["i18n"]["zh-CN"]["label"], "Seed")

    def test_all_trellis_params_have_chinese_presentation(self) -> None:
        for node in self.nodes.values():
            self.assertTrue(node.get("i18n", {}).get("zh-CN", {}).get("name"), node["id"])
            for param in node.get("params_schema", []):
                locale = param.get("i18n", {}).get("zh-CN", {})
                self.assertTrue(locale.get("label"), f"{node['id']}.{param['id']} label")
                self.assertTrue(locale.get("tooltip"), f"{node['id']}.{param['id']} tooltip")


class NodeDefinitionI18nTests(unittest.TestCase):
    def test_model_definition_preserves_source_locale_metadata(self) -> None:
        original = runtime_paths.snapshot()
        try:
            runtime_paths.update(node_packs_dir=REPO_ROOT / "node-packs")
            definitions = {
                definition.class_type: definition
                for definition in node_catalog._model_definitions()
            }
            definition = definitions["trellis2/generate"]
        finally:
            runtime_paths.update(
                models_dir=original.models,
                workspace_dir=original.workspace,
                workflows_dir=original.workflows,
                node_packs_dir=original.node_packs,
            )

        self.assertEqual(definition.i18n["zh-CN"]["name"], "生成网格")
        self.assertEqual(definition.params_schema[0]["id"], "pipeline_type")
        self.assertEqual(definition.params_schema[0]["i18n"]["zh-CN"]["label"], "质量")


if __name__ == "__main__":
    unittest.main()

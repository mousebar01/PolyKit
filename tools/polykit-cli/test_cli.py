import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("polykit.py")
SPEC = importlib.util.spec_from_file_location("polykit_cli", MODULE_PATH)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class ParserTests(unittest.TestCase):
    def test_workflow_run_inspect_is_read_only_command(self) -> None:
        args = cli.build_parser().parse_args(["workflow-run", "inspect", "run-1"])
        self.assertEqual(args.run_id, "run-1")
        self.assertIs(args.handler, cli.cmd_run_inspect)

    def test_world_validate_uses_domain_capability(self) -> None:
        args = cli.build_parser().parse_args([
            "world", "validate", "winter-cabin", "world.construction.validate", "--run-id", "run-7"
        ])
        self.assertEqual(args.world_id, "winter-cabin")
        self.assertEqual(args.capability, "world.construction.validate")
        self.assertEqual(args.run_id, "run-7")

    def test_default_generation_collection_is_not_agent_named(self) -> None:
        args = cli.build_parser().parse_args(["asset", "from-image", "input.png"])
        self.assertEqual(args.collection, "Workflows")

    def test_external_asset_commands_are_explicit(self) -> None:
        search = cli.build_parser().parse_args(["asset", "search-external", "wooden chair", "--category", "furniture"])
        imported = cli.build_parser().parse_args(["asset", "import-external", "Chair_01", "--resolution", "1k"])
        self.assertIs(search.handler, cli.cmd_asset_search_external)
        self.assertIs(imported.handler, cli.cmd_asset_import_external)


class CommandTests(unittest.TestCase):
    def test_inspect_calls_canonical_observability_endpoint(self) -> None:
        args = cli.build_parser().parse_args(["--api-url", "http://api", "workflow-run", "inspect", "run 1"])
        with patch.object(cli, "_api_json", return_value={"status": "running"}) as request:
            result = args.handler(args)
        self.assertEqual(result["status"], "running")
        request.assert_called_once_with("http://api", "GET", "/workflow-runs/run%201/inspect")

    def test_world_validate_posts_run_evidence_reference(self) -> None:
        args = cli.build_parser().parse_args([
            "--api-url", "http://api",
            "world", "validate", "cabin", "world.construction.validate", "--run-id", "run-3",
        ])
        with patch.object(cli, "_api_json", return_value={"status": "pass"}) as request:
            result = args.handler(args)
        self.assertEqual(result["status"], "pass")
        request.assert_called_once_with(
            "http://api",
            "POST",
            "/workspace-library/worlds/cabin/validate",
            {"capability": "world.construction.validate", "run_id": "run-3"},
        )

    def test_world_build_structure_posts_existing_workflow_bridge(self) -> None:
        args = cli.build_parser().parse_args([
            "--api-url", "http://api", "world", "build-structure", "cabin", "--building-id", "main"
        ])
        with patch.object(cli, "_api_json", return_value={"run_id": "run-4"}) as request:
            result = args.handler(args)
        self.assertEqual(result["run_id"], "run-4")
        request.assert_called_once_with(
            "http://api",
            "POST",
            "/workspace-library/worlds/cabin/build-structure",
            {"building_id": "main", "collection": "Scenes", "render_preview": True},
        )

    def test_world_attach_asset_calls_domain_endpoint(self) -> None:
        args = cli.build_parser().parse_args([
            "--api-url", "http://api", "world", "attach-asset", "cabin", "chair", "Worlds/chair.glb", "--run-id", "run-5"
        ])
        with patch.object(cli, "_api_json", return_value={"world_id": "cabin"}) as request:
            args.handler(args)
        request.assert_called_once_with(
            "http://api",
            "POST",
            "/workspace-library/worlds/cabin/artifacts/chair",
            {
                "workspace_path": "Worlds/chair.glb",
                "workflow_id": None,
                "run_id": "run-5",
                "concept_image": None,
            },
        )

    def test_asset_from_image_preserves_world_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "chair.png"
            image.write_bytes(b"png")
            args = cli.build_parser().parse_args([
                "--api-url", "http://api",
                "asset", "from-image", str(image), "--world-id", "cabin", "--proto-id", "chair",
            ])
            with patch.object(cli, "_api_multipart", return_value={"run_id": "run-6"}) as request:
                result = args.handler(args)
        self.assertEqual(result["run_id"], "run-6")
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["fields"]["world_id"], "cabin")
        self.assertEqual(kwargs["fields"]["proto_id"], "chair")
        self.assertEqual(kwargs["fields"]["node_id"], "chair")
        self.assertEqual(kwargs["fields"]["collection"], "Workflows")

    def test_external_search_posts_read_only_provider_request(self) -> None:
        args = cli.build_parser().parse_args([
            "--api-url", "http://api", "asset", "search-external", "wooden chair", "--category", "furniture", "--limit", "2",
        ])
        with patch.object(cli, "_api_json", return_value={"matches": []}) as request:
            args.handler(args)
        request.assert_called_once_with(
            "http://api",
            "POST",
            "/workspace-library/providers/polyhaven/search",
            {"query": "wooden chair", "category": "furniture", "limit": 2, "refresh": False},
        )

    def test_external_import_posts_explicit_provider_request(self) -> None:
        args = cli.build_parser().parse_args([
            "--api-url", "http://api", "asset", "import-external", "Chair_01", "--resolution", "1k",
        ])
        with patch.object(cli, "_api_json", return_value={"asset": {"asset_id": "Chair_01"}}) as request:
            args.handler(args)
        request.assert_called_once_with(
            "http://api",
            "POST",
            "/workspace-library/providers/polyhaven/import",
            {"asset_id": "Chair_01", "resolution": "1k"},
            timeout=180.0,
        )


if __name__ == "__main__":
    unittest.main()

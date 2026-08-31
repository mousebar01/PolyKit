import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
API_ROOT = REPO_ROOT / "api"


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_web_product_code_uses_canonical_generation_api(self) -> None:
        offenders: list[str] = []
        for path in SRC_ROOT.rglob("*"):
            if path.suffix not in {".ts", ".tsx", ".js", ".mjs"}:
                continue
            if path.name.endswith((".test.ts", ".test.tsx", ".test.mjs", ".test.js")):
                continue
            if "/generate/" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(offenders, [], f"Web product code must use /workflow-runs/*: {offenders}")

    def test_workflow_run_store_stays_server_owned(self) -> None:
        path = REPO_ROOT / "src" / "areas" / "workflows" / "workflowRunStore.ts"
        text = path.read_text(encoding="utf-8")
        self.assertIn("/workflow-runs/execute", text)

        desktop_runtime_tokens = ("nodePacks.runProcess", "executeNodePackNode", "topoSort(")
        found = [token for token in desktop_runtime_tokens if token in text]
        self.assertEqual(found, [], f"workflowRunStore contains a client execution path: {found}")

    def test_retired_shared_ui_layer_stays_removed(self) -> None:
        self.assertFalse((REPO_ROOT / "src" / "shared" / "ui").exists())

        offenders: list[str] = []
        for path in SRC_ROOT.rglob("*"):
            if path.suffix not in {".ts", ".tsx", ".js", ".mjs"}:
                continue
            if "@shared/ui" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(offenders, [], f"Retired @shared/ui imports returned: {offenders}")

    def test_storage_consumers_use_runtime_paths(self) -> None:
        paths = [
            API_ROOT / "main.py",
            API_ROOT / "routers" / "workspace_library.py",
            API_ROOT / "routers" / "model.py",
            API_ROOT / "routers" / "export.py",
            API_ROOT / "routers" / "optimize.py",
            API_ROOT / "routers" / "status.py",
            API_ROOT / "services" / "asset_previews.py",
            API_ROOT / "services" / "asset_thumbnails.py",
            API_ROOT / "services" / "model_pack_subprocess.py",
            API_ROOT / "services" / "workflow_engine.py",
            API_ROOT / "services" / "workflow_executor.py",
            API_ROOT / "services" / "workflow_store.py",
        ]
        offenders = []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if "import services.model_runtime_registry as" in text:
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(offenders, [], f"Storage/path consumers must read RuntimePaths directly: {offenders}")

    def test_run_coordinator_does_not_reach_into_model_runtime_private_state(self) -> None:
        text = (API_ROOT / "services" / "run_coordinator.py").read_text(encoding="utf-8")
        forbidden = (
            "model_runtime_registry._generators",
            "model_runtime_registry._active_id",
            "generator._proc",
            "generator._loaded",
        )
        found = [token for token in forbidden if token in text]
        self.assertEqual(found, [], f"RunCoordinator crossed the model-runtime boundary: {found}")

    def test_runtime_paths_is_independent_from_model_runtime(self) -> None:
        text = (API_ROOT / "services" / "runtime_paths.py").read_text(encoding="utf-8")
        self.assertNotIn("model_runtime_registry", text)

    def test_workflow_engine_owns_execution(self) -> None:
        support = (API_ROOT / "services" / "workflow_executor.py").read_text(encoding="utf-8")
        canonical = (API_ROOT / "services" / "workflow_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("class WorkflowEngine", support)
        self.assertIn("class WorkflowEngine:", canonical)
        self.assertIn("ExecutionContext.create", canonical)

    def test_embedded_agent_runtime_stays_removed(self) -> None:
        forbidden_paths = [
            REPO_ROOT / "agent",
            API_ROOT / "mcp_server.py",
            API_ROOT / "services" / "agent_runtime.py",
            API_ROOT / "services" / "world_agent.py",
            SRC_ROOT / "areas" / "agent",
            SRC_ROOT / "areas" / "settings" / "components" / "AgentSection.tsx",
            SRC_ROOT / "areas" / "settings" / "components" / "McpSection.tsx",
        ]
        existing = [path.relative_to(REPO_ROOT).as_posix() for path in forbidden_paths if path.exists()]
        self.assertEqual(existing, [], f"Retired embedded Agent runtime paths returned: {existing}")

        forbidden_tokens = ("/settings/agent", "api/mcp_server.py", "services.world_agent")
        offenders: list[str] = []
        roots = (API_ROOT, SRC_ROOT)
        for root in roots:
            for path in root.rglob("*"):
                if path.suffix not in {".py", ".ts", ".tsx", ".js", ".mjs"}:
                    continue
                if path == Path(__file__):
                    continue
                text = path.read_text(encoding="utf-8")
                if any(token in text for token in forbidden_tokens):
                    offenders.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(offenders, [], f"Retired embedded Agent references returned: {offenders}")


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class NamingConventionTests(unittest.TestCase):
    def test_node_pack_area_uses_product_language(self) -> None:
        area = REPO_ROOT / "src" / "areas" / "node-packs"
        self.assertTrue(area.is_dir())
        self.assertFalse((REPO_ROOT / "src" / "areas" / "models").exists())

        routes = (REPO_ROOT / "src" / "shared" / "router" / "routes.tsx").read_text(encoding="utf-8")
        nav_store = (REPO_ROOT / "src" / "shared" / "stores" / "navStore.ts").read_text(encoding="utf-8")
        page = (area / "NodePacksPage.tsx").read_text(encoding="utf-8")
        self.assertIn("@areas/node-packs/NodePacksPage", routes)
        self.assertNotIn("@areas/models/", routes)
        self.assertIn("'nodePacks'", nav_store)
        self.assertIn("function NodePacksPage", page)
        self.assertNotIn("function ModelsPage", page)

    def test_node_pack_feature_uses_canonical_translation_namespace(self) -> None:
        area = REPO_ROOT / "src" / "areas" / "node-packs"
        files = [
            area / "NodePacksPage.tsx",
            area / "components" / "NodePackCard.tsx",
            area / "components" / "NodePackDrawer.tsx",
            area / "components" / "nodePackShared.tsx",
        ]
        source = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertIn("nodePacks.", source)
        self.assertNotIn("t('" + "models" + ".", source)

    def test_model_name_helper_has_specific_name(self) -> None:
        area = REPO_ROOT / "src" / "areas" / "node-packs"
        self.assertTrue((area / "modelNames.ts").is_file())
        self.assertTrue((area / "modelNames.test.mjs").is_file())
        self.assertFalse((area / "utils.ts").exists())
        self.assertFalse((area / "utils.test.mjs").exists())

    def test_canonical_backend_module_names_exist(self) -> None:
        services = REPO_ROOT / "api" / "services"
        routers = REPO_ROOT / "api" / "routers"
        for path in (
            services / "workflow_engine.py",
            services / "node_catalog.py",
            services / "run_coordinator.py",
            services / "model_runtime_registry.py",
            services / "model_pack_subprocess.py",
            services / "image_generation.py",
            services / "workflow_store.py",
            routers / "workspace_library.py",
            routers / "node_types.py",
            routers / "legacy_generation.py",
        ):
            self.assertTrue(path.is_file(), path)

    def test_no_deprecated_modules_exist(self) -> None:
        retired_routers = (
            "generation",
            "library",
            "nodes",
            "_".join(("workflow", "definitions")),
        )
        retired_services = (
            "_".join(("generator", "registry")),
            "_".join(("job", "runtime")),
            "_".join(("generation", "jobs")),
            "_".join(("node", "registry")),
            "_".join(("workflow", "artifact", "engine")),
            "_".join(("workflow", "definitions")),
            "_".join(("node", "pack", "process")),
        )
        for name in retired_routers:
            path = REPO_ROOT / "api" / "routers" / f"{name}.py"
            self.assertFalse(path.exists(), path)
        for name in retired_services:
            path = REPO_ROOT / "api" / "services" / f"{name}.py"
            self.assertFalse(path.exists(), path)
        self.assertFalse((REPO_ROOT / "src" / "areas" / "node-packs" / "utils.ts").exists())

    def test_runtime_dependencies_follow_boundaries(self) -> None:
        production_files = [
            *((REPO_ROOT / "api" / "services").glob("*.py")),
            *((REPO_ROOT / "api" / "routers").glob("*.py")),
        ]
        deprecated_imports = tuple(
            f"services.{name}"
            for name in (
                "_".join(("generator", "registry")),
                "_".join(("job", "runtime")),
                "_".join(("generation", "jobs")),
                "_".join(("node", "registry")),
                "_".join(("workflow", "artifact", "engine")),
                "_".join(("workflow", "definitions")),
                "_".join(("node", "pack", "process")),
            )
        ) + tuple(f"routers.{name}" for name in ("generation", "library", "nodes"))
        offenders = {
            path.relative_to(REPO_ROOT).as_posix(): token
            for path in production_files
            for token in deprecated_imports
            if token in path.read_text(encoding="utf-8")
        }
        self.assertEqual(offenders, {})

        executor = (REPO_ROOT / "api" / "services" / "workflow_executor.py").read_text(encoding="utf-8")
        self.assertNotIn("class WorkflowEngine", executor)
        self.assertIn("Workflow graph primitives and node-execution support helpers", executor)

        coordinator = (REPO_ROOT / "api" / "services" / "run_coordinator.py").read_text(encoding="utf-8")
        self.assertNotIn("model_runtime_registry._", coordinator)

    def test_api_entrypoint_uses_canonical_router_names(self) -> None:
        main = (REPO_ROOT / "api" / "main.py").read_text(encoding="utf-8")
        self.assertIn("legacy_generation", main)
        self.assertIn("workspace_library", main)
        self.assertIn("node_types", main)
        self.assertNotIn("app.include_router(generation.router", main)
        self.assertNotIn("app.include_router(library.router", main)
        self.assertNotIn("app.include_router(nodes.router", main)

    def test_workflow_store_router_uses_store_language(self) -> None:
        router = (REPO_ROOT / "api" / "routers" / "workflow_store.py").read_text(encoding="utf-8")
        self.assertIn("services.workflow_store", router)
        self.assertNotIn("services." + "_".join(("workflow", "definitions")), router)

    def test_legacy_generation_router_uses_image_generation_service(self) -> None:
        router = (REPO_ROOT / "api" / "routers" / "legacy_generation.py").read_text(encoding="utf-8")
        self.assertIn("services.image_generation", router)
        self.assertNotIn("services." + "_".join(("generation", "jobs")), router)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from routers import polyhaven as router_module


class PolyHavenRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_is_read_only_provider_endpoint(self) -> None:
        with patch.object(router_module, "search_models", return_value=[{"asset_id": "Chair_01"}]) as search:
            result = await router_module.search_polyhaven(router_module.PolyHavenSearchRequest(query="chair", limit=1))
        self.assertEqual(result, {"success": True, "provider": "polyhaven", "matches": [{"asset_id": "Chair_01"}]})
        search.assert_called_once_with("chair", category=None, limit=1, refresh=False)

    async def test_import_is_explicit_and_returns_asset_without_run_state(self) -> None:
        asset = {"asset_id": "Chair_01", "workspace_path": "Workflows/PolyHaven/chair_1k.glb", "format": "glb"}
        with patch.object(router_module, "import_model", return_value=asset) as importer:
            result = await router_module.import_polyhaven(router_module.PolyHavenImportRequest(asset_id="Chair_01", resolution="1k"))
        self.assertEqual(result, {"success": True, "provider": "polyhaven", "asset": asset})
        importer.assert_called_once_with("Chair_01", resolution="1k")

    async def test_upstream_failure_maps_to_gateway_error(self) -> None:
        with patch.object(router_module, "search_models", side_effect=router_module.PolyHavenError("upstream unavailable")):
            with self.assertRaises(HTTPException) as context:
                await router_module.search_polyhaven(router_module.PolyHavenSearchRequest(query="chair"))
        self.assertEqual(context.exception.status_code, 502)
        self.assertIn("upstream unavailable", str(context.exception.detail))


if __name__ == "__main__":
    unittest.main()

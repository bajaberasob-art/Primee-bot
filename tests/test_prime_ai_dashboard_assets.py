"""Shipping checks for the production AI dashboard and its optional React island."""

import re
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import web_server


class PrimeAIDashboardAssetContracts(unittest.TestCase):
    def test_service_worker_matches_ai_assets_in_production_html(self):
        html = (web_server.DASHBOARD_DIR / "index.html").read_text("utf-8")
        worker = web_server.service_worker_source()
        paths = re.findall(
            r'(?:src|href)="(static/ai-control\.(?:js|css)\?[^"]+)"', html,
        )
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assertIn(f'"./{path}"', worker)
        javascript = next(path for path in paths if ".js?" in path)
        self.assertIn(
            f'"./{javascript.replace("ai-control.js", "ai-magic-island.js")}"',
            worker,
        )

    def test_magic_ui_is_optional_and_is_disposed_with_its_dashboard(self):
        source = (web_server.DASHBOARD_DIR / "ai-control.js").read_text("utf-8")
        self.assertIn("function disposeIslands()", source)
        self.assertIn("function cleanup()", source)
        cleanup = source.split("return function cleanup()", 1)[1]
        self.assertIn("disposeIslands()", cleanup)
        self.assertIn("state.disposed = true", cleanup)
        self.assertIn("function loadIslandScript()", source)

    def test_dedicated_conversation_pages_reuse_the_working_talk_controls(self):
        source = (web_server.DASHBOARD_DIR / "ai-control.js").read_text("utf-8")
        for destination, section in (
            ("permissions", "accessSection"),
            ("context", "contextSection"),
            ("personality", "personalitySection"),
            ("personas", "personaSection"),
            ("responses", "responseSection"),
        ):
            with self.subTest(destination=destination):
                self.assertIn(f'["{destination}", {section}]', source)
                self.assertIn(f'addControlSection("talk", {section})', source)


class PrimeAIDashboardStaticAssets(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Only static routes are exercised. No bot, database, OAuth or provider
        # is started or called by this app.
        app = web.Application()
        app.add_routes(web_server.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_real_magic_ui_bundle_is_served(self):
        response = await self.client.get("/static/ai-magic-island.js")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/javascript")
        source = await response.text()
        self.assertGreater(len(source), 20000)
        self.assertIn("PrimeAIMagic", source)

    async def test_static_allowlist_still_rejects_other_files(self):
        response = await self.client.get("/static/nonexistent-private-file.py")
        self.assertEqual(response.status, 404)

"""Checks the existing dashboard ships and cleans up the new destination."""
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

import web_server


ROOT = Path(__file__).resolve().parents[1]


class AnnouncementAssets(unittest.IsolatedAsyncioTestCase):
    async def test_new_styles_and_script_are_allowed(self):
        for name, content_type in (
            ("announcement-space.css", "text/css"),
            ("announcement-space.js", "application/javascript"),
        ):
            result = await web_server.static_asset(SimpleNamespace(match_info={"name": name}))
            self.assertEqual(result.status, 200)
            self.assertEqual(result.content_type, content_type)

    def test_worker_matches_html_asset_versions(self):
        html = (ROOT / "dashboard/index.html").read_text()
        worker = web_server.service_worker_source()
        for name in ("announcement-space.css", "announcement-space.js", "app.js"):
            asset = re.search(rf'static/{re.escape(name)}\?v=[^"]+', html)
            self.assertIsNotNone(asset)
            self.assertIn("./" + asset.group(), worker)

    def test_navigation_teardown_and_legible_action_colors(self):
        source = (ROOT / "dashboard/app.js").read_text()
        self.assertIn('navButton("announcements")', source)
        self.assertIn("window.PrimeAnnouncements.mount(host", source)
        self.assertIn("state.announcementCleanup();", source)
        styles = (ROOT / "dashboard/announcement-space.css").read_text()
        button = re.search(r"\.announcement-space \.as-btn \{([^}]+)\}", styles).group(1)
        self.assertIn("color: var(--as-text)", button)
        self.assertIn("background: var(--as-raised)", button)

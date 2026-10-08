"""Presentation shipping contracts; no Discord or live database operations."""
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

import web_server


ROOT = Path(__file__).resolve().parents[1]


class SubscriptionDashboardAssetTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_stylesheet_is_allowed(self):
        request = SimpleNamespace(match_info={"name": "subscriptions.css"})
        response = await web_server.static_asset(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "text/css")

    def test_html_and_worker_ship_matching_assets(self):
        html = (ROOT / "dashboard/index.html").read_text()
        worker = web_server.service_worker_source()
        for filename in ("subscriptions.css", "app.js"):
            asset = re.search(rf'static/{re.escape(filename)}\?v=[^"]+', html)
            self.assertIsNotNone(asset)
            self.assertIn("./" + asset.group(), worker)
        self.assertLess(html.index("visual-refresh.css"), html.index("subscriptions.css"))

    def test_real_stat_fallback_and_cleanup_are_preserved(self):
        source = (ROOT / "dashboard/app.js").read_text()
        self.assertIn('class: "subscription-stat-card"', source)
        self.assertIn("enhanceSubscriptionStats(metricHost", source)
        self.assertIn("disposeSubscriptionMagic();", source)
        self.assertIn("window.PrimeAIMagic.dispose(subscriptionMagicHost)", source)
        self.assertIn("subscriptionMagicHost === host", source)
        css = (ROOT / "dashboard/subscriptions.css").read_text()
        self.assertIn("prefers-reduced-motion: no-preference", css)
        self.assertIn("env(safe-area-inset-bottom)", css)

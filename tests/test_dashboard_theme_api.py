"""Account-scoped dashboard theme API tests."""

import json
import os
import time
import unittest

import database
import web_server as ws
from tests.test_settings_api import call, request


class DashboardThemeApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = "/tmp/test_dashboard_theme_api.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()
        ws.SESSIONS.clear()
        ws.RATE_BUCKETS.clear()
        for user_id in ("100000000000000010", "100000000000000011"):
            sid = f"theme-{user_id}"
            ws.SESSIONS[sid] = {
                "id": user_id,
                "username": "Theme Test",
                "csrf": f"csrf-{user_id}",
                "guilds": [],
                "expires_at": time.time() + 60,
            }

    @staticmethod
    def sid(user_id="100000000000000010"):
        return f"theme-{user_id}"

    @staticmethod
    def headers(user_id="100000000000000010"):
        return {
            "Origin": "https://dash.test",
            "X-CSRF-Token": f"csrf-{user_id}",
        }

    @staticmethod
    def theme(primary="#3b82f6"):
        return {
            "preset": "prime-blue",
            "primary": primary,
            "secondary": "#5865f2",
            "background": "#05070b",
            "surface": "#0a0e17",
            "surfaceAlt": "#111827",
            "text": "#f1f5f9",
            "muted": "#a3b0c2",
            "border": "#1e293b",
            "buttonStyle": "solid",
        }

    async def test_theme_requires_authentication_and_csrf(self):
        status, _ = await call(
            ws.api_get_user_theme,
            request("GET", "/api/user/theme"),
        )
        self.assertEqual(status, 401)

        bad_headers = {**self.headers(), "X-CSRF-Token": "wrong"}
        status, _ = await call(
            ws.api_save_user_theme,
            request("POST", "/api/user/theme", self.sid(), {"theme": self.theme()}, bad_headers),
        )
        self.assertEqual(status, 403)

    async def test_themes_are_persisted_per_discord_account(self):
        first = self.theme()
        second = self.theme("#10b981")
        status, body = await call(
            ws.api_save_user_theme,
            request("POST", "/api/user/theme", self.sid(), {"theme": first}, self.headers()),
        )
        self.assertEqual((status, body["theme"]["primary"]), (200, "#3b82f6"))

        status, body = await call(
            ws.api_save_user_theme,
            request(
                "POST",
                "/api/user/theme",
                self.sid("100000000000000011"),
                {"theme": second},
                self.headers("100000000000000011"),
            ),
        )
        self.assertEqual((status, body["theme"]["primary"]), (200, "#10b981"))

        status, body = await call(
            ws.api_get_user_theme,
            request("GET", "/api/user/theme", self.sid()),
        )
        self.assertEqual((status, body["theme"]["primary"]), (200, "#3b82f6"))

        status, body = await call(
            ws.api_get_user_theme,
            request("GET", "/api/user/theme", self.sid("100000000000000011")),
        )
        self.assertEqual((status, body["theme"]["primary"]), (200, "#10b981"))

    async def test_invalid_theme_is_rejected_and_reset_removes_only_own_preference(self):
        invalid = self.theme("red; background: url(https://example.invalid)")
        status, body = await call(
            ws.api_save_user_theme,
            request("POST", "/api/user/theme", self.sid(), {"theme": invalid}, self.headers()),
        )
        self.assertEqual(status, 400)
        self.assertIn("primary", body["fields"])

        other_user_theme = self.theme("#10b981")
        await call(
            ws.api_save_user_theme,
            request(
                "POST",
                "/api/user/theme",
                self.sid("100000000000000011"),
                {"theme": other_user_theme},
                self.headers("100000000000000011"),
            ),
        )
        status, body = await call(
            ws.api_save_user_theme,
            request("POST", "/api/user/theme", self.sid(), {"theme": None}, self.headers()),
        )
        self.assertEqual((status, body["theme"]), (200, None))
        self.assertIsNone(await database.get_user_dashboard_theme("100000000000000010"))
        self.assertEqual(
            (await database.get_user_dashboard_theme("100000000000000011"))["primary"],
            "#10b981",
        )

    async def test_theme_tokens_are_read_from_prime_design_system(self):
        response = await ws.api_design_system_tokens(
            request("GET", "/api/design-system/tokens", self.sid())
        )
        body = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["color"]["dark"]["primary"]["$value"], "#3b82f6")
        self.assertIn("sans", body["typography"]["fontFamily"])


if __name__ == "__main__":
    unittest.main()
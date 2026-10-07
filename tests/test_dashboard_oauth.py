import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from aiohttp.test_utils import make_mocked_request

import web_server as dashboard


def request(path="/", cookies="", app=None):
    return make_mocked_request("GET", path, headers={"Cookie": cookies}, app=app)


class ProviderResponse:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.data


class ProviderSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def post(self, *args, **kwargs):
        return ProviderResponse({"access_token": "test-only"})

    def get(self, url, **kwargs):
        if url.endswith("/guilds"):
            return ProviderResponse([
                {"id": "1", "permissions": "8"},
                {"id": "2", "permissions": "0"},
                {"id": "3", "permissions": "0", "owner": True},
                {"id": "4", "permissions": "8"},
            ])
        return ProviderResponse({"id": "10", "username": "<script>test</script>"})


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dashboard.STATES.clear()
        dashboard.SESSIONS.clear()
        self.config = patch.multiple(
            dashboard, C_ID="test-id", C_SEC="test-secret",
            R_URI="https://example.test/api/auth/callback",
        )
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_callback_route_aliases_are_registered(self):
        routes = {
            (route.method, route.path)
            for route in dashboard.routes._items
        }
        self.assertIn(("GET", "/callback"), routes)
        self.assertIn(("GET", "/callback/"), routes)
        self.assertIn(("GET", "/api/auth/callback"), routes)

    def test_health_route_aliases_are_registered(self):
        routes = {
            (route.method, route.path)
            for route in dashboard.routes._items
        }
        self.assertIn(("GET", "/healthz"), routes)
        self.assertIn(("GET", "/health"), routes)

    async def test_liveness_payload_is_render_keep_alive_safe(self):
        response = await dashboard.healthz(request("/healthz"))
        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["bot"], "online")
        self.assertIsInstance(payload["uptime"], int)

    async def test_login_and_missing_config(self):
        response = await dashboard.login(request())
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(query["redirect_uri"], [dashboard.R_URI])
        self.assertEqual(query["scope"], ["identify guilds"])
        state = response.cookies["oauth_state"]
        self.assertEqual(query["state"], [state.value])
        self.assertNotIn("prompt", query)
        self.assertTrue(state["secure"])
        self.assertTrue(state["httponly"])
        with patch.object(dashboard, "C_SEC", None):
            self.assertEqual((await dashboard.login(request())).status, 503)

    async def test_login_strips_redirect_uri_before_encoding(self):
        with patch.object(
            dashboard,
            "R_URI",
            "https://example.test/api/auth/callback\r\n",
        ):
            response = await dashboard.login(request())
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(
            query["redirect_uri"],
            ["https://example.test/api/auth/callback"],
        )

    async def test_provider_error_has_visible_retry_link(self):
        dashboard.STATES["declined"] = time.time()
        response = await dashboard.callback(request(
            "/api/auth/callback?error=login_required&state=declined",
            "oauth_state=declined",
        ))
        self.assertEqual(response.status, 400)
        self.assertEqual(response.content_type, "text/html")
        self.assertIn("تعذر تسجيل الدخول عبر Discord", response.text)
        self.assertIn(
            f'href="{dashboard.DASHBOARD_BASE_PATH}login"',
            response.text,
        )

    async def test_bot_invite_url_uses_public_install_scopes(self):
        url = dashboard.bot_invite_url()
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["client_id"], ["test-id"])
        self.assertEqual(query["scope"], ["bot applications.commands"])
        self.assertEqual(query["permissions"], [dashboard.BOT_INVITE_PERMISSIONS])

    async def test_state_expiry_and_browser_binding(self):
        dashboard.STATES["old"] = time.time() - 301
        dashboard.STATES["live"] = time.time()
        for state, cookie in [("old", "old"), ("live", "wrong"), ("live", "")]:
            response = await dashboard.callback(request(
                f"/api/auth/callback?code=test&state={state}",
                f"oauth_state={cookie}",
            ))
            self.assertEqual(response.status, 403)
        self.assertNotIn("old", dashboard.STATES)

    async def test_callback_filtering_session_escape_and_logout(self):
        guilds = {
            i: SimpleNamespace(id=i, name="<b>server</b>", member_count=5)
            for i in [1, 2, 3]
        }
        dashboard.STATES["valid"] = time.time()
        callback_request = request(
            "/api/auth/callback?code=test&state=valid", "oauth_state=valid",
        )
        with (
            patch.object(dashboard.aiohttp, "ClientSession", return_value=ProviderSession()),
            patch.object(dashboard, "bot_ref", SimpleNamespace(get_guild=guilds.get)),
        ):
            response = await dashboard.callback(callback_request)
        self.assertEqual(response.status, 302)
        sid = response.cookies["bot_session"].value
        self.assertEqual(
            int(response.cookies["bot_session"]["max-age"]),
            2_592_000,
        )
        self.assertEqual([g["id"] for g in dashboard.SESSIONS[sid]["guilds"]], ["1", "3"])
        self.assertEqual((await dashboard.callback(callback_request)).status, 403)
        authenticated = request(cookies=f"bot_session={sid}")
        page = await dashboard.index(authenticated)
        self.assertNotIn("<b>server</b>", page.text)
        self.assertIn('src="static/app.js"', page.text)
        me = await dashboard.api_me(authenticated)
        self.assertEqual(me.status, 200)
        self.assertIn('"csrf"', me.text)
        self.assertNotIn("expires_at", me.text)
        await dashboard.logout(authenticated)
        self.assertEqual((await dashboard.api_me(authenticated)).status, 401)

    async def test_server_side_session_expiry(self):
        dashboard.SESSIONS["expired"] = {"expires_at": time.time() - 1}
        response = await dashboard.api_me(request(cookies="bot_session=expired"))
        self.assertEqual(response.status, 401)
        self.assertNotIn("expired", dashboard.SESSIONS)

    async def test_local_login_is_explicit_dev_only_and_persistent(self):
        with patch.dict("os.environ", {"DASHBOARD_LOCAL_LOGIN": "1"}, clear=False):
            response = await dashboard.local_login(request("/__local_login"))
        self.assertEqual(response.status, 302)
        cookie = response.cookies["bot_session"]
        self.assertEqual(cookie.value, dashboard.LOCAL_LOGIN_COOKIE)
        self.assertEqual(int(cookie["max-age"]), dashboard.LOCAL_LOGIN_MAX_AGE)
        self.assertFalse(cookie["secure"])

        with patch.dict("os.environ", {"DASHBOARD_LOCAL_LOGIN": ""}, clear=False):
            with self.assertRaises(dashboard.web.HTTPNotFound):
                await dashboard.local_login(request("/__local_login"))

    async def test_local_session_rehydrates_after_server_restart(self):
        guild = SimpleNamespace(
            id=777,
            name="PR1ME TEAM",
            member_count=82,
            icon=None,
            owner_id=99,
        )
        live_bot = SimpleNamespace(
            guilds=[guild],
            is_ready=lambda: True,
            get_guild=lambda guild_id: guild if guild_id == guild.id else None,
        )
        with (
            patch.dict("os.environ", {"DASHBOARD_LOCAL_LOGIN": "1"}, clear=False),
            patch.object(dashboard, "bot_ref", live_bot),
        ):
            response = await dashboard.api_me(
                request("/api/me", f"bot_session={dashboard.LOCAL_LOGIN_COOKIE}"),
            )
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual([item["id"] for item in payload["session"]["guilds"]], ["777"])
        self.assertNotIn(dashboard.LOCAL_LOGIN_COOKIE, dashboard.SESSIONS)

    async def test_manage_guild_permission_is_dashboard_authorization(self):
        guilds = {
            1: SimpleNamespace(id=1, name="managed", member_count=5),
        }
        dashboard.STATES["managed"] = time.time()
        callback_request = request(
            "/api/auth/callback?code=test&state=managed", "oauth_state=managed",
        )

        class ManageGuildProvider(ProviderSession):
            def get(self, url, **kwargs):
                if url.endswith("/guilds"):
                    return ProviderResponse([
                        {"id": "1", "permissions": str(dashboard.MANAGE_GUILD_BIT)},
                    ])
                return super().get(url, **kwargs)

        with (
            patch.object(
                dashboard.aiohttp,
                "ClientSession",
                return_value=ManageGuildProvider(),
            ),
            patch.object(dashboard, "bot_ref", SimpleNamespace(get_guild=guilds.get)),
        ):
            response = await dashboard.callback(callback_request)

        self.assertEqual(response.status, 302)
        sid = response.cookies["bot_session"].value
        self.assertEqual([g["id"] for g in dashboard.SESSIONS[sid]["guilds"]], ["1"])

    async def test_api_me_uses_app_bot_and_fetches_role_authorization(self):
        role = SimpleNamespace(id=900, name="Prime")
        member = SimpleNamespace(
            id=42,
            roles=[role],
            guild_permissions=SimpleNamespace(
                administrator=False,
                manage_guild=False,
            ),
        )

        class LiveGuild:
            id = 777
            name = "PR1ME TEAM"
            owner_id = 99
            member_count = 82
            icon = None

            def get_member(self, user_id):
                return None

            async def fetch_member(self, user_id):
                self.fetched_user_id = user_id
                return member

        guild = LiveGuild()
        live_bot = SimpleNamespace(
            guilds=[guild],
            is_ready=lambda: False,
            get_guild=lambda guild_id: guild if guild_id == guild.id else None,
        )
        dashboard.bot_ref = None
        dashboard.SESSIONS["live"] = {
            "id": "42",
            "username": "operator",
            "guilds": [],
            "expires_at": time.time() + 60,
            "csrf": "csrf",
        }
        response = await dashboard.api_me(
            request("/api/me", "bot_session=live", app={"bot": live_bot}),
        )
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual([item["id"] for item in payload["session"]["guilds"]], ["777"])
        self.assertEqual(guild.fetched_user_id, 42)


if __name__ == "__main__":
    unittest.main()
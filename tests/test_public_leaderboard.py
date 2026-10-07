import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aiohttp.test_utils import make_mocked_request
from aiohttp import web

import database
from public_leaderboard import register_public_leaderboard_routes


GUILD_ID = 900000000000000001
MEMBER_ONE = 100000000000000101
MEMBER_TWO = 100000000000000102
REMOVED_MEMBER = 100000000000000103


class FakeGuild:
    id = GUILD_ID
    name = "PRIME Test"
    member_count = 42
    icon = None

    def __init__(self):
        self.members = {
            MEMBER_ONE: SimpleNamespace(
                id=MEMBER_ONE,
                name="Member One",
                display_name="Member One",
                display_avatar=SimpleNamespace(
                    url="https://cdn.discordapp.com/avatars/1.png"
                ),
            ),
            MEMBER_TWO: SimpleNamespace(
                id=MEMBER_TWO,
                name="Member Two",
                display_name="Member Two",
                display_avatar=None,
            ),
        }

    def get_member(self, user_id):
        return self.members.get(int(user_id))


class PublicLeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = f"/tmp/public_leaderboard_{os.getpid()}.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()
        await database.update_level_settings(
            GUILD_ID,
            {"web_leaderboard_enabled": 1, "web_slug": "prime-test"},
        )
        self.guild = FakeGuild()
        self.bot = SimpleNamespace(get_guild=lambda guild_id: (
            self.guild if int(guild_id) == GUILD_ID else None
        ))
        routes = web.RouteTableDef()
        register_public_leaderboard_routes(
            routes, bot_getter=lambda _request: self.bot, logger=Mock()
        )
        self.handlers = {
            (route.method, route.path): route.handler
            for route in routes
        }
        import public_leaderboard
        public_leaderboard._page_cache.clear()

        rows = [
            (MEMBER_ONE, 500, 400, 12, 900),
            (MEMBER_TWO, 300, 900, 8, 1800),
            (REMOVED_MEMBER, 200, 700, 4, 600),
        ]
        rows.extend(
            (100000000000000200 + index, 1, 1, 0, 0)
            for index in range(9)
        )
        for user_id, text_xp, voice_xp, messages, seconds in rows:
            await database.create_user_level(GUILD_ID, user_id)
            await database.update_user_level(GUILD_ID, user_id, {
                "text_xp": text_xp,
                "voice_xp": voice_xp,
                "total_messages": messages,
                "total_voice_seconds": seconds,
                "current_streak": 3,
            })

    def make_request(self, path, slug="prime-test"):
        request = make_mocked_request("GET", path)
        request.match_info["slug"] = slug
        return request

    async def call(self, route_path, request_path, slug="prime-test"):
        handler = self.handlers[("GET", route_path)]
        return await handler(self.make_request(request_path, slug))

    async def test_html_shell_and_voice_data_are_public_and_privacy_safe(self):
        page = await self.call("/lb/{slug}", "/lb/prime-test")
        self.assertEqual(page.status, 200)
        self.assertIn("lang=\"ar\" dir=\"rtl\"", page.text)
        self.assertIn("../dashboard/static/app.js", page.text)

        response = await self.call(
            "/lb/{slug}/data",
            f"/lb/prime-test/data?mode=voice&user_id={MEMBER_ONE}",
        )
        self.assertEqual(response.status, 200)
        data = json.loads(response.text)
        self.assertEqual(data["mode"], "voice")
        self.assertEqual(data["pageSize"], 10)
        self.assertEqual(data["total"], 12)
        self.assertEqual(data["totalPages"], 2)
        self.assertEqual(len(data["rows"]), 10)
        self.assertEqual(data["server"]["name"], "PRIME Test")
        self.assertEqual(data["summary"]["activeMembers"], 12)
        self.assertEqual(data["summary"]["totalXp"], 3018)
        self.assertEqual(data["rows"][0]["name"], "Member Two")
        self.assertEqual(data["rows"][0]["xp"], 900)
        self.assertEqual(data["rows"][0]["level"], 4)
        self.assertEqual(data["rows"][1]["rank"], 2)
        self.assertTrue(data["rows"][1]["removed"])
        self.assertIsNone(data["rows"][1]["xp"])
        self.assertEqual(data["rows"][2]["level"], 2)
        self.assertEqual(data["viewerRank"]["rank"], 3)
        self.assertEqual(data["viewerRank"]["name"], "Member One")
        serialized = response.text
        for user_id in (MEMBER_ONE, MEMBER_TWO, REMOVED_MEMBER):
            self.assertNotIn(str(user_id), serialized)

        second_page = await self.call(
            "/lb/{slug}/data", "/lb/prime-test/data?mode=voice&page=2"
        )
        second_page_data = json.loads(second_page.text)
        self.assertEqual(second_page_data["page"], 2)
        self.assertEqual(
            [row["rank"] for row in second_page_data["rows"]], [11, 12]
        )

        text = await self.call(
            "/lb/{slug}/data", "/lb/prime-test/data?mode=text"
        )
        text_data = json.loads(text.text)
        self.assertEqual(text_data["mode"], "text")
        self.assertEqual(text_data["rows"][0]["name"], "Member One")
        self.assertEqual(text_data["rows"][0]["xp"], 500)

    async def test_data_validation_and_unavailable_slugs_are_generic(self):
        # Invalid input fails before the public settings lookup.
        for query in ("?mode=other", "?page=0", "?page=1000000", "?user_id=abc"):
            req = self.make_request(f"/lb/prime-test/data{query}")
            response = await self.handlers[("GET", "/lb/{slug}/data")](req)
            self.assertEqual(response.status, 400)
            self.assertEqual(json.loads(response.text)["error"], "invalid_request")

        response = await self.call(
            "/lb/{slug}/data", "/lb/unknown-board/data", slug="unknown-board"
        )
        self.assertEqual(response.status, 404)
        self.assertEqual(json.loads(response.text), {"error": "not_found"})
        disabled = await database.update_level_settings(
            GUILD_ID, {"web_leaderboard_enabled": 0}
        )
        self.assertEqual(disabled["web_leaderboard_enabled"], 0)
        response = await self.call(
            "/lb/{slug}/data", "/lb/prime-test/data"
        )
        self.assertEqual((response.status, json.loads(response.text)), (404, {"error": "not_found"}))

    async def test_missing_guild_is_not_disclosed_and_db_errors_are_generic(self):
        self.bot.get_guild = lambda _guild_id: None
        response = await self.call(
            "/lb/{slug}/data", "/lb/prime-test/data"
        )
        self.assertEqual((response.status, json.loads(response.text)), (404, {"error": "not_found"}))

        self.bot.get_guild = lambda _guild_id: self.guild
        with patch.object(
            database, "get_level_leaderboard_page", side_effect=RuntimeError("private detail")
        ):
            response = await self.call(
                "/lb/{slug}/data", "/lb/prime-test/data"
            )
        self.assertEqual(response.status, 503)
        self.assertEqual(json.loads(response.text), {"error": "unavailable"})
        self.assertNotIn("private detail", response.text)
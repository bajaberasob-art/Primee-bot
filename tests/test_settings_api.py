import json
import os
import time
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.streams import StreamReader
from aiohttp.test_utils import make_mocked_request

import database
import web_server as ws
from tests.dashboard_harness import CHANNELS, ROLES, FakeBot, FakeGuild

GID = str(FakeGuild.id)


class TicketCommunityStub:
    def __init__(self):
        self.ticket = {
            "id": 42,
            "guild_id": FakeGuild.id,
            "channel_id": 300000000000000002,
            "subject": "مشكلة في الشحن",
            "category_label": "دعم الشحن",
            "priority": "high",
            "claimed_by": None,
            "status": "active",
        }
        self.responses = []
        self.calls = []

    async def get_active_tickets(self, guild_id):
        self.calls.append(("active", guild_id))
        return [dict(self.ticket)]

    async def get_ticket_archive(self, guild_id, query=""):
        self.calls.append(("archive", guild_id, query))
        return [{
            "id": 7,
            "guild_id": guild_id,
            "subject": "تذكرة مغلقة",
            "category_label": "عام",
            "close_reason": "تم الحل",
        }]

    async def get_ticket_transcript(self, guild_id, ticket_id):
        self.calls.append(("transcript", guild_id, ticket_id))
        return {
            "ticket_id": ticket_id,
            "content_html": "<!doctype html><html><body><p>Transcript</p></body></html>",
        }

    async def get_staff_kpis(self, guild_id):
        self.calls.append(("kpis", guild_id))
        return [{"staff_id": "10", "tickets_handled": 2, "avg_rating": 5.0}]

    async def get_canned_responses(self, guild_id):
        self.calls.append(("canned", guild_id))
        return list(self.responses)

    async def save_canned_response(
        self,
        guild_id,
        title,
        content,
        category,
        created_by,
        response_id=None,
        shortcut=None,
        sticker_id=None,
    ):
        item = {
            "id": response_id or 1,
            "guild_id": guild_id,
            "title": title,
            "content": content,
            "category": category,
            "shortcut": shortcut,
            "sticker_id": sticker_id,
            "created_by": created_by,
        }
        self.responses = [item]
        self.calls.append(("save_canned", guild_id, response_id))
        return item

    async def delete_canned_response(self, guild_id, response_id):
        self.calls.append(("delete_canned", guild_id, response_id))
        self.responses = []
        return True

    async def reassign_ticket(self, guild_id, ticket_id, staff_id):
        self.calls.append(("reassign", guild_id, ticket_id, staff_id))
        return {**self.ticket, "claimed_by": staff_id}

    async def force_close_ticket(self, guild_id, ticket_id, staff_id, reason):
        self.calls.append(("close", guild_id, ticket_id, staff_id, reason))
        return {**self.ticket, "status": "closed", "closed_by": staff_id}


def request(method, path, sid=None, body=None, headers=None):
    h = {"Host": "dash.test", **(headers or {})}
    if sid:
        h["Cookie"] = f"bot_session={sid}"
    payload = json.dumps(body).encode() if body is not None else None
    if payload is not None:
        h.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
    req = make_mocked_request(method, path, headers=h)
    req.match_info["guild_id"] = GID
    if payload is not None:
        reader = StreamReader(Mock(), 2**16)
        reader.feed_data(payload)
        reader.feed_eof()
        req._payload = reader
    return req


async def call(handler, req):
    try:
        response = await handler(req)
    except ws.web.HTTPException as error:
        response = error
    return response.status, json.loads(response.text)


def leveling_handler(method, suffix):
    path = f"/api/guild/{{guild_id}}/leveling/{suffix}"
    return next(
        route.handler for route in ws.routes._items
        if route.method == method and route.path == path
    )


class SettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = "/tmp/test_settings_api.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()
        ws.bot_ref = FakeBot()
        ws.SESSIONS.clear(), ws.RATE_BUCKETS.clear(), ws.GRANT_CACHE.clear()
        self.community = TicketCommunityStub()
        self._community_cog = ws._community_cog
        ws._community_cog = lambda: self.community
        for uid in (10, 11):
            ws.SESSIONS[f"s{uid}"] = {
                "id": str(uid), "username": "u", "avatar": "", "csrf": f"csrf{uid}",
                "guilds": [{"id": GID}], "expires_at": time.time() + 60,
            }
        self.headers = {"X-CSRF-Token": "csrf10", "Origin": "https://dash.test"}

    async def asyncTearDown(self):
        ws._community_cog = self._community_cog

    def test_ip_rate_limits_allow_180_standard_and_keep_sensitive_at_10(self):
        ws.RATE_BUCKETS.clear()
        standard_key = ("ip", "203.0.113.10", "standard")
        sensitive_key = ("ip", "203.0.113.10", "sensitive")
        with patch("web_server.time.monotonic", return_value=1000.0):
            for _ in range(180):
                self.assertEqual(
                    ws.rate_limited(standard_key, ws.IP_API_LIMIT),
                    0,
                )
            self.assertGreater(
                ws.rate_limited(standard_key, ws.IP_API_LIMIT),
                0,
            )

            for _ in range(10):
                self.assertEqual(
                    ws.rate_limited(sensitive_key, ws.IP_SENSITIVE_LIMIT),
                    0,
                )
            self.assertGreater(
                ws.rate_limited(sensitive_key, ws.IP_SENSITIVE_LIMIT),
                0,
            )

    async def test_authorization_is_enforced_server_side(self):
        self.assertEqual((await call(ws.api_get_settings, request("GET", "/x")))[0], 401)
        # user 11 is in the session guild list but lacks the 0x8 bit on the live guild
        self.assertEqual((await call(ws.api_get_settings, request("GET", "/x", "s11")))[0], 403)
        self.assertEqual(ws.SESSIONS["s11"]["guilds"], [])
        status, data = await call(ws.api_get_settings, request("GET", "/x", "s10"))
        self.assertEqual((status, data["revision"], data["settings"]["prefix"]), (200, 0, "!"))

    async def test_write_requires_csrf_and_same_origin(self):
        body = {"revision": 0, "changes": {"prefix": "?"}}
        bad_token = {**self.headers, "X-CSRF-Token": "nope"}
        self.assertEqual((await call(ws.api_post_settings, request("POST", "/x", "s10", body, bad_token)))[0], 403)
        cross = {**self.headers, "Origin": "https://evil.test"}
        self.assertEqual((await call(ws.api_post_settings, request("POST", "/x", "s10", body, cross)))[0], 403)

    async def test_validation_save_conflict_and_rate_limit(self):
        managed_role, high_role = ROLES[5], ROLES[4]
        bad = {"revision": 0, "changes": {
            "prefix": "a b", "anti_alt_days": 999, "auto_role_id": str(high_role.id),
            "captcha_role_id": str(managed_role.id), "welcome_channel_id": "300000000000000099", "hack": 1,
        }}
        status, data = await call(ws.api_post_settings, request("POST", "/x", "s10", bad, self.headers))
        self.assertEqual(status, 400)
        self.assertEqual(set(data["fields"]), set(bad["changes"]))

        good = {"revision": 0, "changes": {
            "prefix": "?", "captcha_role_id": str(ROLES[2].id), "welcome_channel_id": str(CHANNELS[0].id),
            "welcome_message": "<b>{user}</b>", "economy_tax": 2.5,
        }}
        status, data = await call(ws.api_post_settings, request("POST", "/x", "s10", good, self.headers))
        self.assertEqual((status, data["revision"], data["settings"]["captcha_role_id"]), (200, 1, str(ROLES[2].id)))
        self.assertEqual(data["settings"]["welcome_message"], "<b>{user}</b>")  # stored raw, rendered as text

        stale = {"revision": 0, "changes": {"prefix": "$"}}
        status, data = await call(ws.api_post_settings, request("POST", "/x", "s10", stale, self.headers))
        self.assertEqual((status, data["error"], data["settings"]["prefix"]), (409, "conflict", "?"))

        # three attempts are already counted above (400, 200, 409); the 5/10s save limit trips soon after
        statuses, revision = [], 1
        for amount in range(500, 506):
            body = {"revision": revision, "changes": {"daily_amount": amount}}
            status, data = await call(ws.api_post_settings, request("POST", "/x", "s10", body, self.headers))
            statuses.append(status)
            revision = data.get("revision", revision)
        self.assertEqual(statuses[:2], [200, 200])
        self.assertEqual(statuses[2:], [429] * 4)
        self.assertEqual((await database.get_guild_settings(FakeGuild.id))["settings"]["daily_amount"], 501)

    async def test_meta_marks_roles_the_bot_cannot_assign(self):
        status, meta = await call(ws.api_guild_meta, request("GET", "/x", "s10"))
        assignable = {r["name"]: r["assignable"] for r in meta["roles"]}
        self.assertEqual(status, 200)
        self.assertNotIn("@everyone", assignable)
        self.assertEqual((assignable["مدير"], assignable["Nitro Booster"], assignable["قيد التحقق"]), (False, False, True))
        self.assertEqual(
            [(emoji["name"], emoji["token"]) for emoji in meta["emojis"]],
            [("party", "<:party:400000000000000001>"), ("spark", "<a:spark:400000000000000002>")],
        )

    async def test_commands_and_auto_responses_api(self):
        status, data = await call(ws.api_guild_commands, request("GET", "/x", "s10"))
        self.assertEqual(status, 200)
        self.assertEqual(data["commands"][0]["command_name"], "ping")
        self.assertEqual(data["commands"][0]["enabled"], True)

        body = {
            "command_name": "ping",
            "enabled": False,
            "allowed_roles": [str(ROLES[2].id)],
            "allowed_channels": [str(CHANNELS[1].id)],
        }
        status, data = await call(
            ws.api_guild_commands_toggle,
            request("POST", "/x", "s10", body, self.headers),
        )
        self.assertEqual((status, data["command"]["enabled"]), (200, False))
        status, data = await call(ws.api_guild_commands, request("GET", "/x", "s10"))
        command = next(item for item in data["commands"] if item["command_name"] == "ping")
        self.assertEqual(
            (
                command["enabled"],
                command["allowed_roles"],
                command["allowed_channels"],
            ),
            (False, [str(ROLES[2].id)], [str(CHANNELS[1].id)]),
        )

        policy_body = {
            "is_enabled": True,
            "aliases": ["انذار", "!مسح"],
            "allowed_roles": [str(ROLES[1].id)],
            "allowed_channels": [str(CHANNELS[0].id)],
            "response_mode": "compact",
            "custom_template": "تم تنفيذ {command}",
        }
        req = request(
            "POST",
            "/x",
            "s10",
            policy_body,
            self.headers,
        )
        req.match_info["command_name"] = "ping"
        status, data = await call(ws.api_guild_command_policy, req)
        self.assertEqual(
            (
                status,
                data["command"]["enabled"],
                data["command"]["aliases"],
                data["command"]["allowed_roles"],
                data["command"]["allowed_channels"],
                data["command"]["response_mode"],
                data["command"]["custom_template"],
            ),
            (
                200,
                True,
                ["انذار", "مسح"],
                [str(ROLES[1].id)],
                [str(CHANNELS[0].id)],
                "compact",
                "تم تنفيذ {command}",
            ),
        )
        grouped_req = request(
            "POST",
            "/x",
            "s10",
            {
                "is_enabled": True,
                "aliases": ["قائمة"],
                "allowed_roles": [],
                "allowed_channels": [],
            },
            self.headers,
        )
        grouped_req.match_info["command_name"] = "admin bot_list"
        grouped_status, grouped_data = await call(ws.api_guild_command_policy, grouped_req)
        self.assertEqual((grouped_status, grouped_data["command"]["command_name"]), (200, "admin bot_list"))

        rule_body = {
            "trigger": "hello",
            "match_type": "contains",
            "response": "Hi {user}",
            "cooldown_seconds": 10,
            "channel_id": str(CHANNELS[1].id),
        }
        status, data = await call(
            ws.api_guild_auto_responses_save,
            request("POST", "/x", "s10", rule_body, self.headers),
        )
        self.assertEqual(status, 200)
        rule_id = data["rule"]["id"]
        self.assertEqual(data["rule"]["channel_id"], str(CHANNELS[1].id))
        status, data = await call(ws.api_guild_auto_responses, request("GET", "/x", "s10"))
        self.assertEqual((status, len(data["rules"])), (200, 1))
        delete_req = request("DELETE", "/x", "s10", headers=self.headers)
        delete_req.match_info["rule_id"] = str(rule_id)
        status, data = await call(
            ws.api_guild_auto_responses_delete,
            delete_req,
        )
        self.assertEqual((status, data["deleted"]), (200, True))

    async def test_command_shortcuts_support_multiple_aliases(self):
        for trigger in ("عيب", "تحذير", "انجب"):
            status, data = await call(
                ws.api_guild_shortcut_save,
                request(
                    "POST",
                    "/x",
                    "s10",
                    {"trigger": trigger, "target_type": "command", "target": "/warn"},
                    self.headers,
                ),
            )
            self.assertEqual((status, data["shortcut"]["trigger"], data["shortcut"]["target"]), (200, trigger, "/warn"))

        status, data = await call(ws.api_guild_commands, request("GET", "/x", "s10"))
        self.assertEqual(
            [item["trigger"] for item in data["shortcuts"]],
            ["عيب", "تحذير", "انجب"],
        )
        shortcut_id = data["shortcuts"][1]["id"]
        delete_request = request("DELETE", "/x", "s10", headers=self.headers)
        delete_request.match_info["shortcut_id"] = str(shortcut_id)
        status, data = await call(ws.api_guild_shortcut_delete, delete_request)
        self.assertEqual((status, data["deleted"]), (200, True))

    async def test_ticket_studio_read_and_write_contracts(self):
        status, data = await call(ws.api_guild_tickets_active, request("GET", "/x", "s10"))
        self.assertEqual((status, data["tickets"][0]["id"]), (200, 42))

        status, data = await call(
            ws.api_guild_tickets_archive,
            request("GET", "/x?q=shipping", "s10"),
        )
        self.assertEqual((status, data["query"], data["tickets"][0]["id"]), (200, "shipping", 7))

        status, data = await call(ws.api_guild_tickets_kpis, request("GET", "/x", "s10"))
        self.assertEqual((status, data["kpis"][0]["avg_rating"]), (200, 5.0))

        status, data = await call(ws.api_guild_tickets_canned_get, request("GET", "/x", "s10"))
        self.assertEqual((status, data["responses"]), (200, []))

        body = {
            "title": "سياسة الاسترداد",
            "content": "سنراجع طلبك.",
            "category": "billing",
            "shortcut": "refund",
            "sticker_id": None,
        }
        status, data = await call(
            ws.api_guild_tickets_canned,
            request("POST", "/x", "s10", body, self.headers),
        )
        self.assertEqual(
            (status, data["response"]["title"], data["response"]["shortcut"], data["response"]["sticker_id"]),
            (200, "سياسة الاسترداد", "refund", None),
        )

        status, data = await call(
            ws.api_guild_tickets_action,
            request(
                "POST",
                "/x",
                "s10",
                {"ticket_id": 42, "action": "reassign", "staff_id": 10},
                self.headers,
            ),
        )
        self.assertEqual((status, data["ticket"]["claimed_by"]), (200, 10))

        transcript_request = request("GET", "/x", "s10")
        transcript_request.match_info["ticket_id"] = "42"
        transcript = await ws.api_guild_ticket_transcript(transcript_request)
        self.assertEqual((transcript.status, transcript.content_type), (200, "text/html"))
        self.assertIn("Transcript", transcript.text)
        self.assertEqual(
            transcript.headers["Cache-Control"],
            "no-store",
        )

        status, data = await call(
            ws.api_guild_tickets_canned,
            request(
                "POST",
                "/x",
                "s10",
                {"action": "delete", "id": 1},
                self.headers,
            ),
        )
        self.assertEqual((status, data["deleted"]), (200, True))
        self.assertIn(("reassign", FakeGuild.id, 42, 10), self.community.calls)

    async def test_legacy_ticket_categories_without_support_roles_remain_editable(self):
        categories, error = ws._ticket_role_ids(
            FakeGuild(),
            [{
                "key": "general",
                "label": "الدعم العام",
                "emoji": "🔧",
                "support_role_ids": [],
                "senior_role_ids": [],
            }],
        )
        self.assertIsNone(error)
        self.assertEqual(categories[0]["support_role_ids"], [])

    async def test_leveling_snapshot_save_conflict_and_validation(self):
        settings_get = leveling_handler("GET", "settings")
        settings_save = leveling_handler("POST", "settings")
        status, snapshot = await call(
            settings_get, request("GET", f"/api/guild/{GID}/leveling/settings", "s10"),
        )
        self.assertEqual((status, snapshot["revision"], snapshot["configured"]), (200, 0, False))
        self.assertIsNone(await database.get_level_settings(FakeGuild.id))
        self.assertEqual(snapshot["draft"]["streak"]["timezone"], "Asia/Riyadh")
        self.assertEqual(snapshot["draft"]["streak"]["resetTime"], "00:00")

        invalid = json.loads(json.dumps(snapshot["draft"]))
        invalid["points"]["roleMult"] = [{"id": str(CHANNELS[0].id), "mult": 2}]
        status, data = await call(
            settings_save,
            request("POST", "/api/guild/x/leveling/settings", "s10",
                    {"revision": 0, "draft": invalid}, self.headers),
        )
        self.assertEqual(status, 400)
        self.assertIn("role", data["fields"]["_"])
        self.assertIsNone(await database.get_level_settings(FakeGuild.id))

        invalid_reminder = json.loads(json.dumps(snapshot["draft"]))
        invalid_reminder["streak"]["messages"]["reminder"]["time"] = "25:99"
        status, data = await call(
            settings_save,
            request(
                "POST", "/api/guild/x/leveling/settings", "s10",
                {"revision": 0, "draft": invalid_reminder}, self.headers,
            ),
        )
        self.assertEqual(status, 400)
        self.assertIn("reminder time", data["fields"]["_"])
        self.assertIsNone(await database.get_level_settings(FakeGuild.id))

        draft = json.loads(json.dumps(snapshot["draft"]))
        draft["public"] = {"enabled": True, "slug": "phase-nine-board"}
        draft["general"]["text"] = False
        draft["voice"]["minMembers"] = 1
        draft["points"]["allowedChannels"] = [str(CHANNELS[1].id)]
        draft["points"]["roleMult"] = [{"id": str(ROLES[1].id), "mult": 2.5}]
        draft["points"]["bl"]["users"] = ["100000000000000050"]
        draft["points"]["bl"]["roles"] = [str(ROLES[2].id)]
        draft["points"]["boosts"] = [{
            "label": "اختبار", "mult": 1.5, "hours": 2,
        }]
        draft["rewards"]["list"] = [{
            "level": 5, "role": str(ROLES[1].id), "type": "text",
        }]
        draft["messages"]["levelup"]["channel"] = str(CHANNELS[0].id)
        draft["streak"]["messages"]["reminder"] = {
            "enabled": False,
            "time": "22:30",
            "message": "تذكير مخصص لـ {user}: {current_streak} يوم",
        }
        bot_permissions = getattr(FakeGuild.me, "guild_permissions", None)
        FakeGuild.me.guild_permissions = SimpleNamespace(manage_roles=True)
        try:
            status, saved = await call(
                settings_save,
                request("POST", "/api/guild/x/leveling/settings", "s10",
                        {"revision": 0, "draft": draft}, self.headers),
            )
        finally:
            if bot_permissions is None:
                del FakeGuild.me.guild_permissions
            else:
                FakeGuild.me.guild_permissions = bot_permissions
        self.assertEqual((status, saved["revision"], saved["configured"]), (200, 1, True))
        self.assertEqual(
            saved["draft"]["streak"]["messages"]["reminder"],
            {
                "enabled": False,
                "time": "22:30",
                "message": "تذكير مخصص لـ {user}: {current_streak} يوم",
            },
        )
        self.assertEqual(
            saved["draft"]["public"],
            {"enabled": True, "slug": "phase-nine-board"},
        )
        stored = await database.get_level_settings(FakeGuild.id)
        self.assertEqual(stored["web_leaderboard_enabled"], 1)
        self.assertEqual(stored["web_slug"], "phase-nine-board")
        self.assertEqual(stored["text_xp_enabled"], 0)
        self.assertEqual(stored["voice_min_members"], 1)
        self.assertEqual(
            stored["prime_controls"]["streak"]["messages"]["reminder"],
            {
                "enabled": False,
                "time": "22:30",
                "message": "تذكير مخصص لـ {user}: {current_streak} يوم",
            },
        )
        self.assertEqual(stored["text_allowed_channels"], [str(CHANNELS[1].id)])
        self.assertEqual(stored["timed_xp_boosts"][0]["label"], "اختبار")
        self.assertEqual(
            [row["target_type"] for row in await database.get_level_blacklist(FakeGuild.id)],
            ["role", "user"],
        )
        self.assertEqual(len(await database.get_level_rewards(FakeGuild.id)), 1)
        self.assertEqual(len(await database.get_level_multipliers(FakeGuild.id)), 1)

        stale = json.loads(json.dumps(snapshot["draft"]))
        stale["general"]["text"] = True
        status, conflict = await call(
            settings_save,
            request("POST", "/api/guild/x/leveling/settings", "s10",
                    {"revision": 0, "draft": stale}, self.headers),
        )
        self.assertEqual((status, conflict["error"], conflict["currentRevision"]), (409, "conflict", 1))
        self.assertEqual((await database.get_level_settings(FakeGuild.id))["text_xp_enabled"], 0)

    async def test_leveling_public_slug_conflict_is_reported_without_saving(self):
        await database.update_level_settings(
            FakeGuild.id + 1,
            {"web_leaderboard_enabled": 1, "web_slug": "taken-board"},
        )
        settings_get = leveling_handler("GET", "settings")
        settings_save = leveling_handler("POST", "settings")
        status, snapshot = await call(
            settings_get,
            request("GET", f"/api/guild/{GID}/leveling/settings", "s10"),
        )
        self.assertEqual(status, 200)
        draft = json.loads(json.dumps(snapshot["draft"]))
        draft["public"] = {"enabled": True, "slug": "taken-board"}
        status, result = await call(
            settings_save,
            request(
                "POST",
                f"/api/guild/{GID}/leveling/settings",
                "s10",
                {"revision": snapshot["revision"], "draft": draft},
                self.headers,
            ),
        )
        self.assertEqual((status, result["error"]), (409, "slug_conflict"))
        self.assertIn("public.slug", result["fields"])
        self.assertIsNone(await database.get_level_settings(FakeGuild.id))

    async def test_leveling_analytics_and_paginated_leaderboards_are_live(self):
        for user_id, text_xp, voice_xp, messages, seconds in (
            (101, 200, 80, 4, 120),
            (102, 100, 150, 2, 600),
            (103, 100, 300, 3, 900),
        ):
            await database.create_user_level(FakeGuild.id, user_id)
            await database.update_user_level(FakeGuild.id, user_id, {
                "text_xp": text_xp, "text_level": text_xp // 100,
                "voice_xp": voice_xp, "voice_level": voice_xp // 100,
                "total_messages": messages, "total_voice_seconds": seconds,
            })

        status, analytics = await call(
            leveling_handler("GET", "analytics"),
            request("GET", f"/api/guild/{GID}/leveling/analytics", "s10"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(analytics["totals"]["participants"], 3)
        self.assertEqual(analytics["totals"]["text_xp"], 400)
        self.assertEqual(analytics["totals"]["voice_xp"], 530)
        self.assertEqual(analytics["totals"]["total_messages"], 9)
        self.assertEqual(analytics["totals"]["total_voice_seconds"], 1620)
        self.assertFalse(analytics["history_available"])
        self.assertEqual(sum(row["members"] for row in analytics["distribution"]), 3)

        text_status, text = await call(
            leveling_handler("GET", "leaderboard"),
            request("GET", f"/api/guild/{GID}/leveling/leaderboard?mode=text&limit=1&offset=1", "s10"),
        )
        voice_status, voice = await call(
            leveling_handler("GET", "leaderboard"),
            request("GET", f"/api/guild/{GID}/leveling/leaderboard?mode=voice&limit=1&offset=1", "s10"),
        )
        self.assertEqual((text_status, text["rows"][0]["user_id"], text["rows"][0]["rank"]), (200, "102", 2))
        self.assertEqual((voice_status, voice["rows"][0]["user_id"], voice["rows"][0]["rank"]), (200, "102", 2))

    async def test_leveling_reset_requires_production_admin_confirmation_and_is_guild_scoped(self):
        now = datetime.now(timezone.utc)
        for guild_id, user_id in ((FakeGuild.id, 101), (FakeGuild.id + 1, 201)):
            await database.award_text_xp(guild_id, user_id, 120, now, cooldown_seconds=0)
            await database.award_voice_xp(guild_id, user_id, 90, 0, 60, awarded_at=now)
        await database.update_level_settings(
            FakeGuild.id, {"xp_multiplier": 2.5, "web_leaderboard_enabled": 1}
        )
        await database.add_level_reward(FakeGuild.id, "text", 2, ROLES[1].id)
        reset = leveling_handler("POST", "reset-progress")
        request_path = f"/api/guild/{GID}/leveling/reset-progress"

        status, _ = await call(
            reset, request("POST", request_path, "s10", {}, self.headers),
        )
        self.assertEqual(status, 400)
        self.assertIsNotNone(await database.get_user_level(FakeGuild.id, 101))

        # Manage Server alone passes dashboard access but not level-admin access.
        original_get_member = FakeGuild.get_member
        FakeGuild.get_member = lambda self, uid: (
            SimpleNamespace(
                id=uid, guild_permissions=SimpleNamespace(
                    administrator=False, manage_guild=True, value=32,
                ), roles=[],
            )
            if uid == 12 else original_get_member(self, uid)
        )
        ws.GRANT_CACHE.clear()
        try:
            ws.SESSIONS["s-manage-only"] = {
                **ws.SESSIONS["s10"],
                "id": "12",
            }
            status, _ = await call(
                reset,
                request("POST", request_path, "s-manage-only",
                        {"confirmation": "RESET_LEVEL_PROGRESS"}, self.headers),
            )
            self.assertEqual(status, 403)
        finally:
            FakeGuild.get_member = original_get_member
            ws.SESSIONS.pop("s-manage-only", None)
            ws.GRANT_CACHE.clear()

        status, result = await call(
            reset,
            request("POST", request_path, "s10",
                    {"confirmation": "RESET_LEVEL_PROGRESS"}, self.headers),
        )
        self.assertEqual((status, result["members_reset"]), (200, 1))
        self.assertIsNone(await database.get_user_level(FakeGuild.id, 101))
        self.assertIsNotNone(await database.get_user_level(FakeGuild.id + 1, 201))
        settings = await database.get_level_settings(FakeGuild.id)
        self.assertEqual(settings["xp_multiplier"], 2.5)
        self.assertEqual(len(await database.get_level_rewards(FakeGuild.id)), 1)

        ws.SESSIONS["s-local"] = {
            **ws.SESSIONS["s10"],
            "_local_dev": True,
            "id": "0",
        }
        status, _ = await call(
            reset,
            request("POST", request_path, "s-local",
                    {"confirmation": "RESET_LEVEL_PROGRESS"}, self.headers),
        )
        self.assertEqual(status, 403)

    async def test_rank_card_preview_uses_authenticated_identity_and_real_renderer(self):
        sid = "card-admin"
        user_id = "100000000000000010"
        ws.SESSIONS[sid] = {
            "id": user_id, "username": "admin", "avatar": "",
            "csrf": "card-csrf", "guilds": [{"id": GID}],
            "expires_at": time.time() + 60,
        }
        with patch("leveling_api.generate_rank_card", new_callable=AsyncMock) as renderer:
            renderer.return_value = BytesIO(b"existing-rank-card")
            response = await leveling_handler("GET", "card-preview")(
                request(
                    "GET",
                    f"/api/guild/{GID}/leveling/card-preview?layout=stats&user_id=100000000000000099",
                    sid,
                )
            )
        self.assertEqual((response.status, response.content_type, response.body),
                         (200, "image/png", b"existing-rank-card"))
        renderer.assert_awaited_once()
        self.assertEqual(renderer.await_args.args[0].id, int(user_id))

    async def test_gif_card_preview_validates_and_passes_design_without_saving(self):
        sid = "card-gif-admin"
        user_id = "100000000000000010"
        ws.SESSIONS[sid] = {
            "id": user_id, "username": "admin", "avatar": "",
            "csrf": "card-gif-csrf", "guilds": [{"id": GID}],
            "expires_at": time.time() + 60,
        }
        with patch("leveling_api.generate_level_up_gif", new_callable=AsyncMock) as renderer:
            renderer.return_value = BytesIO(b"animated-card")
            response = await leveling_handler("GET", "card-preview")(
                request(
                    "GET",
                    f"/api/guild/{GID}/leveling/card-preview?format=gif&layout=spotlight"
                    "&glowStrength=77&particleDensity=21&frame=diamond&showMessages=false",
                    sid,
                )
            )
        self.assertEqual((response.status, response.content_type, response.body),
                         (200, "image/gif", b"animated-card"))
        settings = renderer.await_args.args[-1]
        self.assertEqual(settings["card_layout"], "spotlight")
        self.assertEqual(settings["card_design"]["glowStrength"], 77)
        self.assertEqual(settings["card_design"]["particleDensity"], 21)
        self.assertEqual(settings["card_design"]["frame"], "diamond")
        self.assertFalse(settings["card_design"]["stats"]["messages"])
        self.assertIsNone(await database.get_level_settings(FakeGuild.id))

    async def test_auto_card_preview_returns_gif_for_animated_background(self):
        sid = "card-auto-admin"
        user_id = "100000000000000010"
        ws.SESSIONS[sid] = {
            "id": user_id, "username": "admin", "avatar": "",
            "csrf": "card-auto-csrf", "guilds": [{"id": GID}],
            "expires_at": time.time() + 60,
        }
        with (
            patch("leveling_api.has_animated_background", new=AsyncMock(return_value=True)),
            patch("leveling_api.generate_level_up_gif", new_callable=AsyncMock) as renderer,
        ):
            renderer.return_value = BytesIO(b"animated-background-card")
            response = await leveling_handler("GET", "card-preview")(
                request(
                    "GET",
                    f"/api/guild/{GID}/leveling/card-preview?format=auto"
                    "&bg=https%3A%2F%2Fexample.com%2Fanimated.gif",
                    sid,
                )
            )
        self.assertEqual(
            (response.status, response.content_type, response.body),
            (200, "image/gif", b"animated-background-card"),
        )
        renderer.assert_awaited_once()
        self.assertEqual(
            renderer.await_args.args[-1]["card_bg_url"],
            "https://example.com/animated.gif",
        )


if __name__ == "__main__":
    unittest.main()

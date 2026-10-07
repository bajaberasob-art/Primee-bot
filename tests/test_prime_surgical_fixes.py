"""Focused regression tests for the PRIME surgical stability fixes."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import database
from cogs.levels import Levels, RolePromotionEvent
from leveling_api import _format_template
from prime_level_controls import TEMPLATE_VARIABLES, controls_with_defaults, render_template


class _Role:
    def __init__(self, role_id, position=1, managed=False):
        self.id = role_id
        self.position = position
        self.managed = managed

    def __lt__(self, other):
        return self.position < other.position

    def is_default(self):
        return self.id == 1


class PrimeSurgicalFixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "prime.db")
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_NAME = self.previous_db
        self.directory.cleanup()

    async def test_periodic_both_mode_sums_text_and_voice_and_stale_claim_recovers(self):
        now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
        await database.award_text_xp(888, 123, 10, now, cooldown_seconds=0)
        await database.award_voice_xp(888, 123, 20, 0, 0, awarded_at=now)

        rows = await database.get_level_periodic_top_leaderboard(
            888, [123], "both", now - timedelta(hours=1), now + timedelta(hours=1), limit=10,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["xp"], 30)
        self.assertEqual(rows[0]["total_xp"], 30)
        self.assertIn("total_messages", rows[0])
        self.assertIn("total_voice_seconds", rows[0])

        self.assertTrue(await database.claim_level_periodic_top_run(888, "daily", "2026-10-03"))
        self.assertFalse(await database.claim_level_periodic_top_run(888, "daily", "2026-10-03"))

        stale = (now - timedelta(minutes=20)).isoformat()
        async with database.connect() as db:
            await db.execute(
                """
                UPDATE level_periodic_top_runs
                SET claimed_at = ?, completed_at = NULL
                WHERE guild_id = ? AND period = ? AND period_key = ?
                """,
                (stale, 888, "daily", "2026-10-03"),
            )
            await db.commit()
        self.assertTrue(await database.claim_level_periodic_top_run(888, "daily", "2026-10-03"))

    async def test_role_promotion_emits_only_when_a_role_is_actually_granted(self):
        roles = {1: _Role(1), 10: _Role(10), 100: _Role(100)}
        guild = SimpleNamespace(
            id=888,
            get_role=roles.get,
            me=SimpleNamespace(
                guild_permissions=SimpleNamespace(manage_roles=True),
                top_role=roles[100],
            ),
        )
        member = SimpleNamespace(
            id=123,
            bot=False,
            guild=guild,
            roles=[roles[1]],
            add_roles=AsyncMock(),
            remove_roles=AsyncMock(),
            mention="<@123>",
            display_name="Member",
            name="member",
        )
        message = SimpleNamespace(
            guild=guild,
            author=member,
            channel=SimpleNamespace(id=456, parent_id=None),
        )
        bot = SimpleNamespace(dispatch=Mock())
        cog = Levels(bot)

        await database.create_default_level_settings(888)
        await database.add_level_reward(888, "text", 1, 10)

        award = {
            "text_level": 1,
            "old_level": 0,
            "text_xp": 120,
            "old_xp": 0,
            "overtakes": [],
        }
        settings = {
            "rewards_single_highest": True,
            "milestone_alert_enabled": False,
            "overtake_alert_enabled": False,
        }
        with patch.object(Levels, "_manageable", return_value=True):
            await cog._handle_text_award(member, settings, award)

        promotions = [
            call.args[1]
            for call in bot.dispatch.call_args_list
            if call.args[0] == "lona_role_promotion"
        ]
        self.assertEqual(len(promotions), 1)
        self.assertIsInstance(promotions[0], RolePromotionEvent)
        self.assertEqual(promotions[0].role.id, 10)
        self.assertEqual(promotions[0].old_level, 0)
        self.assertEqual(promotions[0].new_level, 1)

    def test_template_source_and_safe_fallback_are_unified(self):
        required = {
            "user", "username", "mention", "level", "old_level", "xp",
            "required_xp", "progress", "rank", "total_members", "messages",
            "voice_time", "streak", "server",
        }
        self.assertTrue(required.issubset(TEMPLATE_VARIABLES))
        controls = controls_with_defaults()
        self.assertIn("role_promotion", controls["notifications"])
        self.assertEqual(controls["periodic"]["daily"]["mode"], "both")
        self.assertEqual(
            render_template("ok {user} {does_not_exist}", {"user": "X"}),
            "ok X {does_not_exist}",
        )
        self.assertEqual(
            _format_template("hello {role} {old_level}", "role_promotion"),
            "hello {role} {old_level}",
        )
        with self.assertRaises(ValueError):
            _format_template("bad {unknown}", "role_promotion")


if __name__ == "__main__":
    unittest.main()

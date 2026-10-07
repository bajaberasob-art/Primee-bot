import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
from unittest.mock import Mock

import database
from cogs.levels import Levels


class DailyStreakCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "daily-streak.db")
        await database.init_db()

        self.guild_id = 888
        self.user_id = 123
        self.channel_id = 456
        self.base_time = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        self.member = SimpleNamespace(id=self.user_id, bot=False, roles=[])
        self.channel = SimpleNamespace(id=self.channel_id)
        self.guild = SimpleNamespace(id=self.guild_id)
        self.bot = SimpleNamespace(dispatch=Mock())
        self.cogs = [Levels(self.bot)]
        await database.update_level_settings(
            self.guild_id,
            {"streak_channel_id": self.channel_id, "text_xp_enabled": False},
        )

    async def asyncTearDown(self):
        for cog in self.cogs:
            cog.cog_unload()
        database.DB_NAME = self.previous_db
        self.directory.cleanup()

    def message(
        self,
        at=None,
        *,
        channel_id=None,
        author=None,
        message_type=discord.MessageType.default,
        webhook_id=None,
    ):
        channel_id = self.channel_id if channel_id is None else channel_id
        return SimpleNamespace(
            id=500,
            guild=self.guild,
            author=author or self.member,
            channel=SimpleNamespace(id=channel_id),
            type=message_type,
            webhook_id=webhook_id,
            created_at=at or self.base_time,
            content="hello",
        )

    async def result(self, message=None, cog=None):
        return await (cog or self.cogs[0]).record_message_streak(
            message or self.message()
        )

    async def user_row(self):
        return await database.get_user_level(self.guild_id, self.user_id)

    async def activity_rows(self):
        async with database.connect() as db:
            async with db.execute(
                """
                SELECT activity_date, first_activity_at
                FROM streak_daily_activity
                WHERE guild_id = ? AND user_id = ?
                ORDER BY activity_date
                """,
                (self.guild_id, self.user_id),
            ) as cursor:
                return await cursor.fetchall()

    async def test_first_message_claim_records_streak_without_xp_or_level_changes(self):
        await self.cogs[0].on_message(self.message())

        row = await self.user_row()
        activity = await self.activity_rows()
        self.assertEqual((row["current_streak"], row["best_streak"]), (1, 1))
        self.assertEqual((row["text_xp"], row["text_level"], row["total_messages"]), (0, 0, 0))
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0][0], "2026-10-01")
        self.assertIn("+03:00", activity[0][1])
        async with database.connect() as db:
            async with db.execute("SELECT COUNT(*) FROM level_xp_daily") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 0)
            async with db.execute("SELECT COUNT(*) FROM level_xp_events") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 0)
        self.bot.dispatch.assert_not_called()

    async def test_consecutive_days_increment_current_and_best(self):
        first = await self.result(self.message(self.base_time))
        second = await self.result(self.message(self.base_time + timedelta(days=1)))

        self.assertEqual(first["status"], "success")
        self.assertEqual((second["current_streak"], second["best_streak"]), (2, 2))
        self.assertEqual(len(await self.activity_rows()), 2)

    async def test_missed_day_breaks_current_but_preserves_best_streak(self):
        for day in range(3):
            await self.result(self.message(self.base_time + timedelta(days=day)))

        after_break = await self.result(
            self.message(self.base_time + timedelta(days=5))
        )
        self.assertEqual((after_break["current_streak"], after_break["best_streak"]), (1, 3))

    async def test_same_day_duplicate_has_no_additional_record_or_state_change(self):
        first = await self.result(self.message())
        duplicate = await self.result(
            self.message(self.base_time + timedelta(hours=8))
        )

        self.assertEqual(first["status"], "success")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual((await self.user_row())["current_streak"], 1)
        self.assertEqual(len(await self.activity_rows()), 1)

    async def test_concurrent_claims_across_cog_instances_have_one_winner(self):
        second_cog = Levels(self.bot)
        self.cogs.append(second_cog)
        message = self.message()

        results = await asyncio.gather(
            self.result(message, self.cogs[0]),
            self.result(message, second_cog),
        )

        self.assertEqual(
            sorted(result["status"] for result in results),
            ["duplicate", "success"],
        )
        self.assertEqual(len(await self.activity_rows()), 1)
        self.assertEqual((await self.user_row())["current_streak"], 1)

    async def test_wrong_channel_bot_webhook_and_system_message_are_ignored(self):
        wrong_channel = await self.result(self.message(channel_id=999))
        bot_member = SimpleNamespace(id=124, bot=True, roles=[])
        bot_result = await self.result(self.message(author=bot_member))
        webhook_result = await self.result(self.message(webhook_id=777))
        system_result = await self.result(
            self.message(message_type=discord.MessageType.pins_add)
        )

        self.assertEqual(wrong_channel["status"], "wrong_channel")
        self.assertEqual(bot_result["status"], "ignored")
        self.assertEqual(webhook_result["status"], "ignored")
        self.assertEqual(system_result["status"], "ignored")
        self.assertEqual(await self.user_row(), None)
        self.assertEqual(await self.activity_rows(), [])

    async def test_reply_message_is_allowed_and_disabled_streak_does_not_record(self):
        reply = await self.result(
            self.message(message_type=discord.MessageType.reply)
        )
        self.assertEqual(reply["status"], "success")

        await database.update_level_settings(self.guild_id, {"streak_enabled": False})
        disabled = await self.result(
            self.message(self.base_time + timedelta(days=1))
        )
        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(len(await self.activity_rows()), 1)

    async def test_restart_and_reconnect_read_database_instead_of_memory(self):
        first = await self.result(self.message())
        restarted_cog = Levels(self.bot)
        self.cogs.append(restarted_cog)
        reconnect_replay = await self.result(self.message(), restarted_cog)

        self.assertEqual(first["status"], "success")
        self.assertEqual(reconnect_replay["status"], "duplicate")
        self.assertEqual(len(await self.activity_rows()), 1)
        self.assertEqual((await self.user_row())["current_streak"], 1)

    async def test_riyadh_midnight_changes_activity_date_and_advances_streak(self):
        before_midnight_utc = datetime(2026, 10, 1, 20, 59, tzinfo=timezone.utc)
        after_midnight_utc = datetime(2026, 10, 1, 21, 1, tzinfo=timezone.utc)

        first = await self.result(self.message(before_midnight_utc))
        second = await self.result(self.message(after_midnight_utc))

        self.assertEqual(first["activity_date"], "2026-10-01")
        self.assertEqual(second["activity_date"], "2026-10-02")
        self.assertEqual((second["current_streak"], second["best_streak"]), (2, 2))
        self.assertEqual(
            [row[0] for row in await self.activity_rows()],
            ["2026-10-01", "2026-10-02"],
        )

    async def test_channel_setting_requires_a_positive_numeric_id(self):
        with self.assertRaises(ValueError):
            await database.update_level_settings(
                self.guild_id, {"streak_channel_id": "general"}
            )
        with self.assertRaises(ValueError):
            await database.update_level_settings(
                self.guild_id, {"streak_channel_id": True}
            )
        await database.update_level_settings(
            self.guild_id, {"streak_channel_id": None}
        )
        self.assertEqual((await self.result())["status"], "channel_not_configured")


if __name__ == "__main__":
    unittest.main()
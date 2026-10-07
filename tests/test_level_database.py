import os
import unittest
from datetime import datetime, timezone

import database


class LevelDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = f"/tmp/lona_level_phase1_{os.getpid()}.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()

    async def test_level_tables_indexes_and_defaults(self):
        async with database.connect() as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ) as cur:
                tables = {row[0] for row in await cur.fetchall()}
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ) as cur:
                indexes = {row[0] for row in await cur.fetchall()}
        self.assertTrue(
            {
                "level_settings",
                "user_levels",
                    "level_xp_daily",
                "level_role_rewards",
                "level_multipliers",
                "level_blacklist",
            }.issubset(tables)
        )
        self.assertTrue(
            {
                "idx_user_levels_text",
                "idx_user_levels_voice",
                "idx_level_role_rewards_guild",
                "idx_level_multipliers_guild",
                "idx_level_blacklist_guild",
                "idx_level_xp_daily_period",
            }.issubset(indexes)
        )

        self.assertIsNone(await database.get_level_settings(700))
        settings = await database.create_default_level_settings(700)
        self.assertEqual(settings["guild_id"], 700)
        self.assertEqual(settings["is_enabled"], 1)
        self.assertEqual(settings["command_rank_channels"], [])
        self.assertEqual(
            settings["command_rank_aliases"],
            ["rank", "level", "لفل", "رانك"],
        )
        self.assertEqual(
            settings["command_top_aliases"],
            ["top", "توب", "متصدرين"],
        )
        self.assertEqual(settings["weekly_reset_day"], "Friday")
        self.assertEqual(await database.get_level_settings(700), settings)

    async def test_legacy_level_settings_add_streak_channel_id(self):
        async with database.connect() as db:
            await db.execute(
                "ALTER TABLE level_settings DROP COLUMN streak_channel_id"
            )
            await db.commit()

        await database.init_db()
        async with database.connect() as db:
            async with db.execute("PRAGMA table_info(level_settings)") as cur:
                columns = {row[1] for row in await cur.fetchall()}
        self.assertIn("streak_channel_id", columns)
        settings = await database.create_default_level_settings(711)
        self.assertIsNone(settings["streak_channel_id"])

    async def test_level_settings_update_is_partial_and_validated(self):
        settings = await database.update_level_settings(
            701,
            {
                "xp_multiplier": 2.25,
                "command_rank_channels": ["1001", "1002"],
                "command_rank_aliases": ["rank", "رتبة"],
                "web_leaderboard_enabled": 0,
                "card_design": {
                    "glowStrength": 73,
                    "frame": "gold",
                    "stats": {"messages": True, "voice": False},
                    "presets": [],
                },
            },
        )
        self.assertEqual(settings["xp_multiplier"], 2.25)
        self.assertEqual(settings["command_rank_channels"], ["1001", "1002"])
        self.assertEqual(settings["command_rank_aliases"], ["rank", "رتبة"])
        self.assertEqual(settings["web_leaderboard_enabled"], 0)
        self.assertEqual(settings["card_design"]["glowStrength"], 73)
        self.assertEqual(settings["card_design"]["frame"], "gold")
        self.assertEqual(settings["weekly_reset_day"], "Friday")
        with self.assertRaises(ValueError):
            await database.update_level_settings(701, {"guild_id": 999})
        with self.assertRaises(ValueError):
            await database.update_level_settings(
                701, {"command_top_channels": "not-json"}
            )

    async def test_user_level_crud_and_text_voice_leaderboards(self):
        self.assertIsNone(await database.get_user_level(702, 1))
        first = await database.create_user_level(702, 1)
        self.assertEqual(first["text_xp"], 0)
        self.assertEqual(first["voice_level"], 0)
        await database.create_user_level(702, 2)
        await database.create_user_level(702, 3)
        await database.update_user_level(
            702,
            1,
            {
                "text_xp": 150,
                "text_level": 4,
                "voice_xp": 60,
                "voice_level": 2,
                "total_messages": 15,
            },
        )
        await database.update_user_level(
            702, 2, {"text_xp": 300, "voice_xp": 20, "total_voice_seconds": 90}
        )
        await database.update_user_level(702, 3, {"text_xp": 80, "voice_xp": 95})

        text = await database.get_text_leaderboard(702, 2)
        voice = await database.get_voice_leaderboard(702, 2)
        self.assertEqual([row["user_id"] for row in text], [2, 1])
        self.assertEqual([row["text_xp"] for row in text], [300, 150])
        self.assertEqual([row["user_id"] for row in voice], [3, 1])
        self.assertEqual([row["voice_xp"] for row in voice], [95, 60])
        self.assertEqual((await database.get_user_level(702, 1))["total_messages"], 15)
        with self.assertRaises(ValueError):
            await database.update_user_level(702, 1, {"guild_id": 999})

    async def test_take_levels_clamps_level_and_preserves_fitting_progress(self):
        await database.update_user_level(703, 1, {
            "text_xp": 515, "text_level": 3, "voice_xp": 40, "voice_level": 0,
            "total_messages": 8,
        })
        result = await database.take_text_levels(703, 1, 2)
        row = await database.get_user_level(703, 1)
        self.assertEqual((result["old_level"], result["text_level"], result["text_xp"]), (3, 1, 140))
        self.assertEqual((row["voice_xp"], row["total_messages"]), (40, 8))
        self.assertEqual(result["levels_removed"], 2)

        await database.take_text_levels(703, 1, 100)
        row = await database.get_user_level(703, 1)
        self.assertEqual((row["text_level"], row["text_xp"]), (0, 40))
        with self.assertRaises(ValueError):
            await database.take_text_levels(703, 1, 0)

    async def test_reset_progress_is_atomic_scoped_and_preserves_level_settings(self):
        now = datetime.now(timezone.utc)
        await database.award_text_xp(704, 1, 120, now, cooldown_seconds=0)
        await database.award_voice_xp(704, 1, 60, 0, 120, awarded_at=now)
        await database.award_text_xp(705, 2, 80, now, cooldown_seconds=0)
        await database.update_level_settings(704, {"xp_multiplier": 2.0})

        result = await database.reset_level_progress(704)
        self.assertEqual(result["members_reset"], 1)
        self.assertEqual(result["daily_rows_removed"], 1)
        self.assertEqual(result["xp_events_removed"], 2)
        self.assertIsNone(await database.get_user_level(704, 1))
        self.assertIsNotNone(await database.get_user_level(705, 2))
        self.assertEqual((await database.get_level_settings(704))["xp_multiplier"], 2.0)

    async def test_command_leaderboard_utc_periods_preserve_lifetime_xp(self):
        utc = timezone.utc
        awards = (
            (10, datetime(2026, 9, 26, 12, tzinfo=utc), 40),
            (10, datetime(2026, 9, 30, 12, tzinfo=utc), 30),
            (10, datetime(2026, 10, 2, 12, tzinfo=utc), 20),
            (10, datetime(2026, 10, 3, 12, tzinfo=utc), 15),
            (20, datetime(2026, 9, 29, 12, tzinfo=utc), 50),
            # This offset timestamp is October 2 in UTC, not October 3.
            (20, datetime.fromisoformat("2026-10-03T00:30:00+03:00"), 7),
            (20, datetime(2026, 10, 3, 12, tzinfo=utc), 10),
        )
        for user_id, awarded_at, amount in awards:
            await database.award_text_xp(
                708, user_id, amount, awarded_at, cooldown_seconds=0,
            )
        await database.update_user_level(708, 30, {"text_xp": 999})
        now = datetime(2026, 10, 3, 12, tzinfo=utc)

        daily = await database.get_command_level_leaderboard(
            708, [10, 20, 30], "text", "daily", now,
        )
        weekly = await database.get_command_level_leaderboard(
            708, [10, 20, 30], "text", "weekly", now,
        )
        monthly = await database.get_command_level_leaderboard(
            708, [10, 20, 30], "text", "monthly", now,
        )
        all_time = await database.get_command_level_leaderboard(
            708, [10, 20, 30], "text", "all_time", now,
        )
        self.assertEqual([(row["user_id"], row["xp"]) for row in daily], [(10, 15), (20, 10)])
        self.assertEqual([(row["user_id"], row["xp"]) for row in weekly], [(20, 67), (10, 65)])
        self.assertEqual([(row["user_id"], row["xp"]) for row in monthly], [(10, 35), (20, 17)])
        self.assertEqual(
            [(row["user_id"], row["xp"], row["total_xp"]) for row in all_time],
            [(30, 999, 999), (10, 105, 105), (20, 67, 67)],
        )
        # Legacy/lifetime-only XP remains available all-time but is not
        # fabricated into a daily period, and current-member filtering applies.
        filtered = await database.get_command_level_leaderboard(
            708, [10], "text", "daily", now,
        )
        self.assertEqual([row["user_id"] for row in filtered], [10])

        await database.award_voice_xp(
            708, 10, 6, 0, 60, awarded_at=datetime(2026, 10, 3, 12, tzinfo=utc),
        )
        await database.award_voice_xp(
            708, 20, 100, 0, 60, awarded_at=datetime(2026, 9, 29, 12, tzinfo=utc),
        )
        await database.award_voice_xp(
            708, 20, 9, 0, 60, awarded_at=datetime(2026, 10, 3, 12, tzinfo=utc),
        )
        voice_weekly = await database.get_command_level_leaderboard(
            708, [10, 20], "voice", "weekly", now,
        )
        self.assertEqual(
            [(row["user_id"], row["xp"]) for row in voice_weekly],
            [(20, 109), (10, 6)],
        )

    async def test_public_leaderboard_slug_pages_ranks_and_summary(self):
        await database.update_level_settings(
            704, {"web_leaderboard_enabled": 1, "web_slug": "prime-arena"}
        )
        self.assertEqual(
            await database.get_public_level_settings_by_slug("prime-arena"),
            {"guild_id": 704},
        )
        self.assertIsNone(await database.get_public_level_settings_by_slug("Prime-Arena"))
        self.assertIsNone(await database.get_public_level_settings_by_slug("bad--slug"))
        await database.update_level_settings(704, {"web_leaderboard_enabled": 0})
        self.assertIsNone(await database.get_public_level_settings_by_slug("prime-arena"))
        await database.update_level_settings(704, {"web_leaderboard_enabled": 1})

        for user_id, text_xp, voice_xp, messages, voice_seconds in (
            (1003, 100, 400, 4, 900),
            (1001, 200, 400, 7, 600),
            (1002, 300, 800, 9, 1200),
        ):
            await database.create_user_level(704, user_id)
            await database.update_user_level(704, user_id, {
                "text_xp": text_xp,
                "voice_xp": voice_xp,
                "total_messages": messages,
                "total_voice_seconds": voice_seconds,
            })

        text_page = await database.get_level_leaderboard_page(
            704, mode="text", limit=2, offset=1
        )
        voice_page = await database.get_level_leaderboard_page(
            704, mode="voice", limit=2, offset=0
        )
        self.assertEqual(text_page["total"], 3)
        self.assertEqual(
            [(row["user_id"], row["rank"]) for row in text_page["rows"]],
            [(1001, 2), (1003, 3)],
        )
        self.assertEqual(
            [(row["user_id"], row["rank"]) for row in voice_page["rows"]],
            [(1002, 1), (1001, 2)],
        )
        self.assertEqual(voice_page["rows"][1]["xp"], 400)
        rank = await database.get_level_user_rank(704, 1001, mode="voice")
        self.assertEqual((rank["user_id"], rank["rank"], rank["activity_total"]), (1001, 2, 600))
        with self.assertRaises(ValueError):
            await database.get_level_user_rank(704, 1001, mode="invalid")

        self.assertEqual(
            await database.get_public_level_summary(704),
            {"active_members": 3, "total_xp": 2200},
        )

    async def test_public_slug_legacy_ambiguity_fails_closed(self):
        for guild_id in (705, 706):
            await database.create_default_level_settings(guild_id)
        async with database.connect() as db:
            await db.execute(
                "UPDATE level_settings SET web_slug = ?, web_leaderboard_enabled = 1 "
                "WHERE guild_id IN (?, ?)",
                ("legacy-prime", 705, 706),
            )
            await db.commit()
        self.assertIsNone(
            await database.get_public_level_settings_by_slug("legacy-prime")
        )
        with self.assertRaises(database.LevelingSlugConflict):
            await database.update_level_settings(
                707, {"web_leaderboard_enabled": 1, "web_slug": "legacy-prime"}
            )

    async def test_rewards_multipliers_blacklist_and_unrelated_economy(self):
        text_reward = await database.add_level_reward(703, "text", 5, 9001)
        voice_reward = await database.add_level_reward(703, "voice", 3, 9002)
        self.assertEqual(
            [(row["reward_type"], row["role_id"]) for row in await database.get_level_rewards(703)],
            [("text", 9001), ("voice", 9002)],
        )
        with self.assertRaises(ValueError):
            await database.add_level_reward(703, "xp", 5, 9003)
        self.assertTrue(await database.delete_level_reward(703, text_reward["id"]))
        self.assertFalse(await database.delete_level_reward(703, text_reward["id"]))
        self.assertEqual(len(await database.get_level_rewards(703)), 1)

        multiplier = await database.add_level_multiplier(
            703, "role", 9100, 2.0
        )
        await database.add_level_multiplier(703, "channel", 9101)
        self.assertEqual(len(await database.get_level_multipliers(703)), 2)
        self.assertEqual(
            next(
                row["multiplier"]
                for row in await database.get_level_multipliers(703)
                if row["id"] == multiplier["id"]
            ),
            2.0,
        )
        with self.assertRaises(ValueError):
            await database.add_level_multiplier(703, "member", 9102)
        self.assertTrue(
            await database.delete_level_multiplier(703, multiplier["id"])
        )
        self.assertEqual(len(await database.get_level_multipliers(703)), 1)

        blacklist = await database.add_level_blacklist(703, "channel", 9200)
        await database.add_level_blacklist(703, "role", 9201)
        await database.add_level_blacklist(703, "user", 9202)
        self.assertEqual(
            {row["target_type"] for row in await database.get_level_blacklist(703)},
            {"channel", "role", "user"},
        )
        self.assertTrue(
            await database.delete_level_blacklist(703, blacklist["id"])
        )
        self.assertEqual(len(await database.get_level_blacklist(703)), 2)

        wallet = await database.get_or_create_user(42, 703)
        self.assertEqual(wallet["balance"], 100)
        self.assertEqual(await database.update_balance(42, 703, 25), 125)
        async with database.connect() as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('tickets', 'warnings', 'guild_settings')"
            ) as cur:
                unrelated_tables = {row[0] for row in await cur.fetchall()}
        self.assertEqual(unrelated_tables, {"tickets", "warnings", "guild_settings"})


if __name__ == "__main__":
    unittest.main()
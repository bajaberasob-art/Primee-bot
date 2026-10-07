"""Phase 2 integration tests against an isolated real SQLite database."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ext import commands

import database
from cogs.levels import Levels, resolve_multiplier, setup
from level_progression import level_from_xp, total_xp_for_level, xp_required


class Role:
    def __init__(self, role_id, position=1, managed=False):
        self.id = role_id
        self.position = position
        self.managed = managed

    def __lt__(self, other):
        return self.position < other.position

    def is_default(self):
        return self.id == 1


class LevelsEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "levels.db")
        await database.init_db()
        self.bot = SimpleNamespace(dispatch=Mock())
        self.cog = Levels(self.bot)
        self.roles = {i: Role(i, i) for i in (1, 10, 20, 30)}
        self.guild = SimpleNamespace(
            id=888, get_role=self.roles.get,
            me=SimpleNamespace(
                guild_permissions=SimpleNamespace(manage_roles=True),
                top_role=Role(100, 100),
            ),
        )
        self.member = SimpleNamespace(
            id=123, bot=False, guild=self.guild, roles=[self.roles[1]],
            add_roles=AsyncMock(), remove_roles=AsyncMock(),
        )
        self.message = SimpleNamespace(
            guild=self.guild, author=self.member,
            channel=SimpleNamespace(id=456, parent_id=None),
        )
        self.now = datetime(2026, 10, 2, 10, tzinfo=timezone.utc)
        self.clock = 1000.0
        self.random_patch = patch("cogs.levels.random.randint", return_value=20)
        self.random_patch.start()
        self.datetime_patch = patch("cogs.levels.datetime", wraps=datetime)
        self.mock_datetime = self.datetime_patch.start()
        self.mock_datetime.now.side_effect = lambda *args: self.now
        self.clock_patch = patch("cogs.levels.monotonic", side_effect=lambda: self.clock)
        self.clock_patch.start()
        await database.create_default_level_settings(self.guild.id)

    async def asyncTearDown(self):
        self.clock_patch.stop()
        self.datetime_patch.stop()
        self.random_patch.stop()
        database.DB_NAME = self.previous_db
        self.directory.cleanup()

    async def send(self):
        await self.cog.on_message(self.message)
        return await database.get_user_level(self.guild.id, self.member.id)

    async def settings(self, **values):
        await database.update_level_settings(self.guild.id, values)

    async def test_normal_award_and_text_only_fields(self):
        await database.update_user_level(888, 123, {
            "voice_xp": 321, "voice_level": 4, "total_voice_seconds": 999,
        })
        row = await self.send()
        self.assertEqual(row["text_xp"], 20)
        self.assertEqual(row["text_level"], 0)
        self.assertEqual(row["total_messages"], 1)
        self.assertEqual(row["last_message_at"], self.now.isoformat())
        self.assertEqual((row["voice_xp"], row["voice_level"], row["total_voice_seconds"]),
                         (321, 4, 999))

    async def test_bot_and_dm_ignored(self):
        self.member.bot = True
        self.assertIsNone(await self.send())
        self.member.bot = False
        self.message.guild = None
        await self.cog.on_message(self.message)
        self.assertIsNone(await database.get_user_level(888, 123))

    async def test_disabled_ignored(self):
        await self.settings(is_enabled=False)
        self.assertIsNone(await self.send())
        self.bot.dispatch.assert_not_called()

    async def test_defaults_created_for_new_guild(self):
        self.guild.id = 889
        row = await self.send()
        self.assertEqual(row["text_xp"], 20)
        self.assertEqual((await database.get_level_settings(889))["message_cooldown_seconds"], 60)

    async def test_cooldown_and_expiry_no_database_work_on_cached_ignore(self):
        await self.send()
        with patch("cogs.levels.database.get_level_settings", new=AsyncMock()) as read:
            with patch("cogs.levels.database.award_text_xp", new=AsyncMock()) as award:
                await self.cog.on_message(self.message)
                read.assert_not_awaited()
                award.assert_not_awaited()
        self.clock += 59
        self.now += timedelta(seconds=59)
        self.assertEqual((await self.send())["text_xp"], 20)
        self.clock += 1
        self.now += timedelta(seconds=1)
        row = await self.send()
        self.assertEqual((row["text_xp"], row["total_messages"]), (40, 2))

    async def test_custom_cooldown(self):
        await self.settings(message_cooldown_seconds=5)
        await self.send()
        self.clock += 5
        self.now += timedelta(seconds=5)
        self.assertEqual((await self.send())["total_messages"], 2)

    async def test_restart_and_cache_eviction_preserve_cooldown(self):
        await self.send()
        self.cog = Levels(self.bot)
        row = await self.send()
        self.assertEqual(row["text_xp"], 20)
        self.assertIn((888, 123), self.cog._cooldowns)
        self.cog._cooldown_capacity = 1
        self.cog._remember_cooldown((888, 999), self.clock + 60)
        self.assertEqual(len(self.cog._cooldowns), 1)
        self.assertEqual((await self.send())["text_xp"], 20)

    async def test_concurrent_messages_award_once(self):
        await asyncio.gather(*(self.cog.on_message(self.message) for _ in range(8)))
        self.assertEqual((await database.get_user_level(888, 123))["total_messages"], 1)

    async def test_atomic_cross_instance_awards(self):
        results = await asyncio.gather(*(
            database.award_text_xp(888, 123, 20, self.now, 60) for _ in range(5)
        ))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual((await database.get_user_level(888, 123))["text_xp"], 20)

    async def test_blacklisted_channel_and_parent_thread(self):
        await database.add_level_blacklist(888, "channel", 456)
        self.assertIsNone(await self.send())
        self.message.channel.id = 789
        self.message.channel.parent_id = 456
        self.assertIsNone(await self.send())

    async def test_blacklisted_role(self):
        self.member.roles.append(self.roles[10])
        await database.add_level_blacklist(888, "role", 10)
        self.assertIsNone(await self.send())

    async def test_blacklist_leaves_existing_data_untouched(self):
        await self.send()
        before = await database.get_user_level(888, 123)
        self.clock += 60
        self.now += timedelta(seconds=60)
        await database.add_level_blacklist(888, "channel", 456)
        self.assertEqual(await self.send(), before)

    async def test_global_multiplier(self):
        await self.settings(xp_multiplier=2)
        self.assertEqual((await self.send())["text_xp"], 40)

    async def test_role_multiplier(self):
        self.member.roles.append(self.roles[10])
        await database.add_level_multiplier(888, "role", 10, 1.5)
        await database.add_level_multiplier(888, "role", 20, 9)
        self.assertEqual((await self.send())["text_xp"], 30)

    async def test_channel_multiplier(self):
        await database.add_level_multiplier(888, "channel", 456, 2)
        self.assertEqual((await self.send())["text_xp"], 40)

    async def test_combined_and_duplicate_multipliers(self):
        self.member.roles.append(self.roles[10])
        await database.add_level_multiplier(888, "role", 10, 1.5)
        await database.add_level_multiplier(888, "role", 10, 2)
        await database.add_level_multiplier(888, "channel", 456, 2)
        await self.settings(xp_multiplier=2)
        self.assertEqual((await self.send())["text_xp"], 240)

    async def test_temporary_boost(self):
        await self.settings(boost_multiplier=3, boost_expires_at=(self.now + timedelta(hours=1)).isoformat())
        self.assertEqual((await self.send())["text_xp"], 60)

    async def test_expired_and_invalid_boost(self):
        await self.settings(boost_multiplier=3, boost_expires_at=self.now.isoformat())
        self.assertEqual((await self.send())["text_xp"], 20)
        self.cog._cooldowns.clear()
        self.now += timedelta(seconds=60)
        await self.settings(boost_expires_at="invalid", card_color="#ABCDEF")
        self.assertEqual((await self.send())["text_xp"], 40)
        settings = await database.get_level_settings(888)
        self.assertEqual(settings["card_color"], "#ABCDEF")
        self.assertEqual(settings["boost_multiplier"], 3)

    async def test_multiplier_clamp_and_zero(self):
        await self.settings(xp_multiplier=100000)
        self.assertEqual((await self.send())["text_xp"], 2000)
        settings = {"xp_multiplier": 0}
        multipliers = [{"id": i, "target_type": "role", "target_id": 10, "multiplier": 100}
                       for i in range(300)]
        self.assertEqual(resolve_multiplier(settings, multipliers, {10}, {456}, self.now), 0)
        self.assertLessEqual(resolve_multiplier({"xp_multiplier": 1}, multipliers, {10}, {456}, self.now), 100.000001)

    async def test_zero_multiplier_no_record(self):
        await self.settings(xp_multiplier=0)
        self.assertIsNone(await self.send())

    async def test_new_formula_boundaries_and_large_xp(self):
        self.assertEqual([xp_required(i) for i in range(3)], [100, 155, 220])
        self.assertEqual([level_from_xp(x) for x in (0, 99, 100, 254, 255, 475)], [0, 0, 1, 1, 2, 3])
        for level in (1, 2, 50, 10**7):
            threshold = total_xp_for_level(level)
            self.assertEqual(level_from_xp(threshold), level)
            self.assertEqual(level_from_xp(threshold - 1), level - 1)

    async def test_level_up_event_and_multiple_thresholds(self):
        await database.update_user_level(888, 123, {"text_xp": 95})
        self.assertEqual((await self.send())["text_level"], 1)
        event_name, event = self.bot.dispatch.call_args.args
        self.assertEqual(event_name, "lona_text_level_up")
        self.assertEqual((event.old_level, event.new_level, event.current_xp), (0, 1, 115))
        self.assertEqual(event.xp_required_for_next_level, 155)
        self.assertEqual(event.next_level_total_xp, 255)
        self.assertIs(event.guild, self.guild)
        self.assertIs(event.member, self.member)
        self.now += timedelta(seconds=60)
        self.clock += 60
        await self.settings(xp_multiplier=100)
        row = await self.send()
        event = self.bot.dispatch.call_args.args[1]
        self.assertEqual(event.new_level, level_from_xp(row["text_xp"]))
        self.assertGreater(event.new_level - event.old_level, 1)

    async def test_stacked_rewards_all_crossed_levels_and_text_only(self):
        await self.settings(xp_multiplier=100, rewards_single_highest=False)
        await database.add_level_reward(888, "text", 1, 10)
        await database.add_level_reward(888, "text", 2, 20)
        await database.add_level_reward(888, "voice", 1, 30)
        await self.send()
        self.assertEqual([call.args[0].id for call in self.member.add_roles.await_args_list], [10, 20])
        self.member.remove_roles.assert_not_awaited()

    async def test_single_highest_removes_lower(self):
        self.member.roles.append(self.roles[10])
        await database.add_level_reward(888, "text", 1, 10)
        await database.add_level_reward(888, "text", 2, 20)
        await self.settings(xp_multiplier=100)
        await self.send()
        self.member.add_roles.assert_awaited_once_with(self.roles[20], reason="Lona text level reward")
        self.member.remove_roles.assert_awaited_once_with(self.roles[10], reason="Lona highest text reward")

    async def test_missing_and_unmanageable_roles_do_not_crash(self):
        await self.settings(xp_multiplier=100, rewards_single_highest=False)
        await database.add_level_reward(888, "text", 1, 999)
        await database.add_level_reward(888, "text", 2, 20)
        self.guild.me.guild_permissions.manage_roles = False
        self.assertEqual((await self.send())["text_xp"], 2000)
        self.member.add_roles.assert_not_awaited()
        self.bot.dispatch.assert_called_once()

    async def test_hierarchy_and_managed_roles(self):
        await self.settings(xp_multiplier=100, rewards_single_highest=False)
        await database.add_level_reward(888, "text", 1, 10)
        await database.add_level_reward(888, "text", 2, 20)
        self.roles[10].managed = True
        self.roles[20].position = 101
        await self.send()
        self.member.add_roles.assert_not_awaited()

    async def test_api_failure_keeps_old_role_and_level_event(self):
        self.member.roles.append(self.roles[10])
        await database.add_level_reward(888, "text", 1, 10)
        await database.add_level_reward(888, "text", 2, 20)
        self.member.add_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
        await self.settings(xp_multiplier=100)
        self.assertEqual((await self.send())["text_xp"], 2000)
        self.member.remove_roles.assert_not_awaited()
        self.bot.dispatch.assert_called_once()

    async def test_removal_failure_and_shared_voice_role(self):
        self.member.roles.extend([self.roles[10], self.roles[30]])
        await database.add_level_reward(888, "text", 1, 10)
        await database.add_level_reward(888, "text", 1, 30)
        await database.add_level_reward(888, "voice", 1, 30)
        await database.add_level_reward(888, "text", 2, 20)
        self.member.remove_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
        await self.settings(xp_multiplier=100)
        await self.send()
        self.assertEqual([call.args[0].id for call in self.member.remove_roles.await_args_list], [10])
        event_names = [call.args[0] for call in self.bot.dispatch.call_args_list]
        self.assertIn("lona_role_promotion", event_names)
        self.assertIn("prime_role_promotion", event_names)
        self.assertIn("lona_text_level_up", event_names)

    async def test_milestone_90_percent_no_repeated_events(self):
        await database.update_user_level(888, 123, {"text_xp": 70})
        await self.send()
        event_name, event = self.bot.dispatch.call_args.args
        self.assertEqual(event_name, "lona_text_milestone")
        self.assertEqual((event.current_level, event.next_level, event.current_xp), (0, 1, 90))
        self.assertEqual((event.percentage, event.next_level_required_xp), (90, 100))
        self.bot.dispatch.reset_mock()
        await self.settings(message_cooldown_seconds=0, xp_multiplier=0.2)
        self.cog._cooldowns.clear()
        await self.send()
        self.bot.dispatch.assert_not_called()

    async def test_ranking_on_demand_and_ties(self):
        await self.send()
        await database.update_user_level(888, 124, {"text_xp": 20})
        await database.update_user_level(888, 125, {"text_xp": 40})
        await database.create_user_level(888, 126)
        rank = await database.get_text_rank(888, 123)
        self.assertEqual((rank["text_xp"], rank["text_level"], rank["rank"], rank["total_eligible_members"]),
                         (20, 0, 2, 3))
        self.assertEqual((await database.get_text_rank(888, 124))["rank"], 3)
        self.assertIsNone((await database.get_text_rank(888, 126))["rank"])
        self.assertIsNone(await database.get_text_rank(889, 123))

    async def test_ranking_not_called_on_message(self):
        with patch("cogs.levels.database.get_text_rank", new=AsyncMock()) as rank:
            await self.send()
            rank.assert_not_awaited()

    async def test_database_failures_contained(self):
        with patch("cogs.levels.database.award_text_xp", new=AsyncMock(side_effect=RuntimeError("db unavailable"))):
            self.assertIsNone(await self.send())
        self.assertFalse(self.cog._cooldowns)
        self.bot.dispatch.assert_not_called()

    async def test_role_query_failure_does_not_lose_level_event(self):
        await self.settings(xp_multiplier=100)
        with patch("cogs.levels.database.get_level_rewards", new=AsyncMock(side_effect=RuntimeError("db unavailable"))):
            self.assertEqual((await self.send())["text_xp"], 2000)
        self.bot.dispatch.assert_called_once()

    async def test_legacy_wallet_still_works(self):
        await self.send()
        self.assertEqual(await database.update_balance(123, 888, 25), 125)
        self.assertEqual((await database.get_user_level(888, 123))["text_xp"], 20)

    async def test_migration_retains_settings_and_user_data(self):
        await self.settings(card_color="#112233")
        await self.send()
        async with database.connect() as db:
            await db.execute("ALTER TABLE level_settings DROP COLUMN message_cooldown_seconds")
            await db.commit()
        await database.init_db()
        settings = await database.get_level_settings(888)
        self.assertEqual(settings["message_cooldown_seconds"], 60)
        self.assertEqual(settings["card_color"], "#112233")
        self.assertEqual((await database.get_user_level(888, 123))["text_xp"], 20)

    async def test_setup_adds_listener_without_commands_or_replacing_listeners(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        existing = AsyncMock()
        bot.add_listener(existing, "on_message")
        before_commands = set(bot.all_commands)
        await setup(bot)
        self.assertIsInstance(bot.get_cog("Levels"), Levels)
        self.assertIn(existing, bot.extra_events["on_message"])
        self.assertEqual(len(bot.extra_events["on_message"]), 2)
        self.assertEqual(set(bot.all_commands), before_commands)
        self.assertFalse(bot.tree.get_commands())
        await bot.remove_cog("Levels")
        self.assertEqual(bot.extra_events["on_message"], [existing])
        await bot.close()


if __name__ == "__main__":
    unittest.main()
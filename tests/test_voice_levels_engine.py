"""Phase 3: real SQLite, deterministic clocks, simulated Discord voice states."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

import database
from cogs.levels import Levels
from level_progression import level_from_xp


class Role:
    def __init__(self, role_id, position=1):
        self.id, self.position, self.managed = role_id, position, False

    def __lt__(self, other):
        return self.position < other.position

    def is_default(self):
        return self.id == 1


class VoiceEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "voice.db")
        await database.init_db()
        self.clock = 1000.0
        self.now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        self.clock_patch = patch("cogs.levels.monotonic", side_effect=lambda: self.clock)
        self.clock_patch.start()
        self.datetime_patch = patch("cogs.levels.datetime", wraps=datetime)
        self.mock_datetime = self.datetime_patch.start()
        self.mock_datetime.now.side_effect = lambda *args: self.now
        self.roles = {i: Role(i, i) for i in (1, 10, 20, 30)}
        self.channels = {i: SimpleNamespace(id=i) for i in (456, 789)}
        self.members = {}
        self.guild = SimpleNamespace(
            id=888, unavailable=False, voice_states={},
            get_member=self.members.get, get_channel=self.channels.get,
            get_role=self.roles.get,
            me=SimpleNamespace(top_role=Role(100, 100),
                               guild_permissions=SimpleNamespace(manage_roles=True)),
        )
        for user_id in (123, 124, 125):
            self.members[user_id] = SimpleNamespace(
                id=user_id, guild=self.guild, bot=False, roles=[self.roles[1]],
                voice=None, add_roles=AsyncMock(), remove_roles=AsyncMock(),
            )
        self.ready = asyncio.Event()
        self.bot = SimpleNamespace(
            guilds=[self.guild], get_guild=lambda guild_id: self.guild if guild_id == 888 else None,
            dispatch=Mock(), wait_until_ready=self.ready.wait,
        )
        self.cog = Levels(self.bot)
        self.cog._voice_online = True
        await database.create_default_level_settings(888)
        await self.settings(voice_min_two_members=False)

    async def asyncTearDown(self):
        task = self.cog.voice_xp_worker.get_task()
        self.cog.cog_unload()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        self.clock_patch.stop()
        self.datetime_patch.stop()
        database.DB_NAME = self.previous_db
        self.directory.cleanup()

    def advance(self, seconds):
        self.clock += seconds
        self.now += timedelta(seconds=seconds)

    async def settings(self, **data):
        await database.update_level_settings(888, data)

    @staticmethod
    def state(channel=None, **flags):
        return SimpleNamespace(
            channel=channel,
            **{name: flags.get(name, False) for name in ("self_mute", "mute", "self_deaf", "deaf")},
        )

    async def move(self, user_id=123, channel_id=456, **flags):
        member = self.members[user_id]
        before = self.guild.voice_states.get(user_id, self.state())
        after = self.state(self.channels.get(channel_id), **flags)
        if after.channel:
            self.guild.voice_states[user_id] = after
            member.voice = after
        else:
            self.guild.voice_states.pop(user_id, None)
            member.voice = None
        await self.cog.on_voice_state_update(member, before, after)

    async def tick(self, seconds=60):
        self.advance(seconds)
        await self.cog.process_voice_tick()

    async def row(self, user_id=123):
        return await database.get_user_level(888, user_id)

    async def test_join_leave_batches_without_event_database_writes(self):
        with patch("cogs.levels.database.award_voice_xp", new=AsyncMock()) as write:
            await self.move()
            self.advance(30)
            await self.move(channel_id=None)
            write.assert_not_awaited()
        self.assertNotIn((888, 123), self.cog.voice_sessions)
        self.assertIsNone(await self.row())
        await self.tick(30)
        row = await self.row()
        self.assertEqual((row["voice_xp"], row["total_voice_seconds"]), (10, 30))
        self.assertFalse(self.cog._voice_pending)

    async def test_actual_elapsed_and_fractional_credit_carry(self):
        await self.move()
        await self.tick(1)
        self.assertEqual((await self.row())["voice_xp"], 0)
        await self.tick(1)
        await self.tick(1)
        self.assertEqual((await self.row())["voice_xp"], 1)
        await self.tick(87)
        row = await self.row()
        self.assertEqual((row["voice_xp"], row["total_voice_seconds"]), (30, 90))

    async def test_fractional_seconds_survive_worker_ticks(self):
        await self.move()
        await self.tick(0.4)
        self.assertIsNone(await self.row())
        await self.tick(0.7)
        self.assertEqual((await self.row())["total_voice_seconds"], 1)
        await self.tick(1.9)
        self.assertEqual((await self.row())["voice_xp"], 1)

    async def test_channel_move_has_no_double_award(self):
        await database.add_level_multiplier(888, "channel", 789, 2)
        await self.move()
        self.advance(30)
        await self.move(channel_id=789)
        await self.tick(30)
        row = await self.row()
        self.assertEqual((row["voice_xp"], row["total_voice_seconds"]), (30, 60))
        self.assertEqual(self.cog.voice_sessions[(888, 123)].channel_id, 789)

    async def test_mute_unmute_exact_boundaries(self):
        await self.move()
        self.advance(30)
        await self.move(self_mute=True)
        await self.tick(60)
        self.assertEqual((await self.row())["total_voice_seconds"], 30)
        await self.move(self_mute=False)
        await self.tick(30)
        self.assertEqual((await self.row())["voice_xp"], 20)

    async def test_all_mute_deafen_flags_block(self):
        for user_id, flag in ((123, "mute"), (124, "self_deaf"), (125, "deaf")):
            await self.move(user_id, **{flag: True})
        await self.tick()
        for user_id in self.members:
            self.assertIsNone(await self.row(user_id))

    async def test_mute_deafen_settings_can_allow_xp(self):
        await self.settings(voice_mute_no_xp=False, voice_deafen_no_xp=False)
        await self.move(self_mute=True, mute=True, self_deaf=True, deaf=True)
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 20)

    async def test_second_human_join_leave_updates_first_member(self):
        await self.settings(voice_min_two_members=True)
        await self.move()
        self.advance(30)
        await self.move(124)
        self.advance(30)
        await self.move(124, channel_id=None)
        await self.tick(30)
        for user_id in (123, 124):
            row = await self.row(user_id)
            self.assertEqual((row["voice_xp"], row["total_voice_seconds"]), (10, 30))

    async def test_bots_neither_track_nor_satisfy_two_member_rule(self):
        await self.settings(voice_min_two_members=True)
        await self.move()
        self.members[124].bot = True
        await self.move(124)
        await self.tick()
        self.assertIsNone(await self.row())
        self.assertIsNone(await self.row(124))
        self.assertNotIn((888, 124), self.cog.voice_sessions)

    async def test_second_member_move_rechecks_both_channels(self):
        await self.settings(voice_min_two_members=True)
        await self.move()
        await self.move(124)
        self.advance(30)
        await self.move(124, channel_id=789)
        await self.tick(30)
        self.assertEqual((await self.row())["total_voice_seconds"], 30)
        self.assertEqual((await self.row(124))["total_voice_seconds"], 30)

    async def test_disabled_and_voice_disabled(self):
        await self.settings(is_enabled=False)
        await self.move()
        await self.tick()
        self.assertIsNone(await self.row())
        await self.settings(is_enabled=True, voice_xp_enabled=False)
        await self.tick()
        self.assertIsNone(await self.row())

    async def test_channel_and_role_blacklist(self):
        await database.add_level_blacklist(888, "channel", 456)
        await database.add_level_blacklist(888, "role", 10)
        self.members[124].roles.append(self.roles[10])
        await self.move()
        await self.move(124, 789)
        await self.tick()
        self.assertIsNone(await self.row())
        self.assertIsNone(await self.row(124))

    async def test_blacklist_changes_fail_closed_no_retroactive_xp(self):
        await self.move()
        self.advance(30)
        entry = await database.add_level_blacklist(888, "channel", 456)
        await self.tick(30)
        self.assertIsNone(await self.row())
        await database.delete_level_blacklist(888, entry["id"])
        await self.tick()
        self.assertIsNone(await self.row())
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 20)

    async def test_member_role_change_does_not_backfill_blacklisted_time(self):
        await database.add_level_blacklist(888, "role", 10)
        await self.move()
        self.advance(30)
        self.members[123].roles.append(self.roles[10])
        await self.tick(30)
        self.assertIsNone(await self.row())
        self.members[123].roles.pop()
        await self.tick()
        self.assertIsNone(await self.row())
        await self.tick()
        self.assertEqual((await self.row())["total_voice_seconds"], 60)

    async def test_global_role_channel_and_boost_multipliers(self):
        self.members[123].roles.append(self.roles[10])
        await database.add_level_multiplier(888, "role", 10, 1.5)
        await database.add_level_multiplier(888, "channel", 456, 2)
        await self.settings(xp_multiplier=2, boost_multiplier=2,
                            boost_expires_at=(self.now + timedelta(minutes=5)).isoformat())
        await self.move()
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 240)

    async def test_boost_expiry_splits_actual_time(self):
        await self.settings(boost_multiplier=2, boost_expires_at=(self.now + timedelta(seconds=30)).isoformat())
        await self.move()
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 30)
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 50)
        self.assertEqual((await database.get_level_settings(888))["boost_multiplier"], 2)

    async def test_expired_boost_not_applied(self):
        await self.settings(boost_multiplier=5, boost_expires_at=self.now.isoformat())
        await self.move()
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 20)

    async def test_diminishing_threshold_splits_interval(self):
        await self.settings(voice_diminishing_enabled=True, voice_diminishing_mins=1,
                            voice_diminishing_rate=0.5)
        await self.move()
        await self.tick(90)
        row = await self.row()
        self.assertEqual((row["voice_xp"], row["total_voice_seconds"]), (25, 90))
        await self.tick(30)
        self.assertEqual((await self.row())["voice_xp"], 30)

    async def test_diminishing_uses_eligible_time_not_connection_time(self):
        await self.settings(voice_diminishing_enabled=True, voice_diminishing_mins=1,
                            voice_diminishing_rate=0.5)
        await self.move(self_mute=True)
        self.advance(3600)
        await self.move()
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 20)
        self.assertEqual(self.cog.voice_sessions[(888, 123)].eligible_duration, 60)
        self.advance(10)
        await self.move(channel_id=789)
        await self.tick(50)
        self.assertEqual((await self.row())["voice_xp"], 30)

    async def test_invalid_voice_settings_logged_and_safe(self):
        await self.settings(voice_diminishing_enabled=True, voice_diminishing_mins=0,
                            voice_diminishing_rate=-5, voice_xp_per_minute="invalid")
        with self.assertLogs("LonaLevels", level="WARNING") as logs:
            await self.move()
        self.assertEqual(len(logs.output), 2)
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 10)

    async def test_separate_voice_level_up_and_multiple_thresholds(self):
        await self.settings(voice_xp_per_minute=500)
        await self.move()
        await self.tick()
        row = await self.row()
        self.assertEqual((row["voice_level"], row["voice_xp"]), (3, 500))
        self.assertEqual((row["text_level"], row["text_xp"]), (0, 0))
        name, event = self.bot.dispatch.call_args.args
        self.assertEqual(name, "lona_voice_level_up")
        self.assertEqual((event.old_level, event.new_level, event.current_xp), (0, 3, 500))
        self.assertIs(event.guild, self.guild)
        self.assertIs(event.member, self.members[123])

    async def test_combined_mode_text_event_only_and_no_chat_cooldown_change(self):
        await database.update_user_level(888, 123, {
            "voice_xp": 77, "voice_level": 8, "text_xp": 90,
            "total_messages": 7, "last_message_at": "2026-10-01T12:00:00+00:00",
        })
        await self.settings(voice_separate_levels=False)
        await self.move()
        await self.tick()
        row = await self.row()
        self.assertEqual((row["text_xp"], row["text_level"]), (110, 1))
        self.assertEqual((row["voice_xp"], row["voice_level"]), (77, 8))
        self.assertEqual(row["total_messages"], 7)
        self.assertEqual(row["last_message_at"], "2026-10-01T12:00:00+00:00")
        self.assertEqual(self.bot.dispatch.call_count, 1)
        self.assertEqual(self.bot.dispatch.call_args.args[0], "lona_text_level_up")

    async def test_mode_changes_do_not_double_award(self):
        await self.move()
        await self.tick()
        await self.settings(voice_separate_levels=False)
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 20)
        self.assertEqual((await self.row())["text_xp"], 0)
        await self.tick()
        self.assertEqual((await self.row())["text_xp"], 20)

    async def test_live_xp_rate_change_no_retroactive_rate_application(self):
        await self.move()
        self.advance(30)
        await self.settings(voice_xp_per_minute=40)
        await self.tick(30)
        self.assertIsNone(await self.row())
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 40)

    async def test_voice_stacked_rewards_only_configured_voice_roles(self):
        await self.settings(voice_xp_per_minute=500, rewards_single_highest=False)
        await database.add_level_reward(888, "voice", 1, 10)
        await database.add_level_reward(888, "voice", 2, 20)
        await database.add_level_reward(888, "text", 1, 30)
        await self.move()
        await self.tick()
        member = self.members[123]
        self.assertEqual([call.args[0].id for call in member.add_roles.await_args_list], [10, 20])
        member.remove_roles.assert_not_awaited()

    async def test_voice_highest_reward_removes_only_lower_voice_roles(self):
        self.members[123].roles.extend([self.roles[10], self.roles[30]])
        await self.settings(voice_xp_per_minute=500)
        await database.add_level_reward(888, "voice", 1, 10)
        await database.add_level_reward(888, "voice", 2, 20)
        await database.add_level_reward(888, "text", 1, 30)
        await self.move()
        await self.tick()
        member = self.members[123]
        member.add_roles.assert_awaited_once_with(self.roles[20], reason="Lona voice level reward")
        member.remove_roles.assert_awaited_once_with(self.roles[10], reason="Lona highest voice reward")

    async def test_combined_rewards_never_grant_voice_roles(self):
        await self.settings(voice_xp_per_minute=500, voice_separate_levels=False)
        await database.add_level_reward(888, "text", 1, 10)
        await database.add_level_reward(888, "voice", 1, 20)
        await self.move()
        await self.tick()
        self.assertEqual([call.args[0].id for call in self.members[123].add_roles.await_args_list], [10])
        event_names = [call.args[0] for call in self.bot.dispatch.call_args_list]
        self.assertIn("lona_role_promotion", event_names)
        self.assertIn("prime_role_promotion", event_names)
        self.assertNotIn("lona_voice_level_up", event_names)

    async def test_deleted_permission_hierarchy_and_api_failures_safe(self):
        await self.settings(voice_xp_per_minute=500, rewards_single_highest=False)
        await database.add_level_reward(888, "voice", 1, 999)
        await database.add_level_reward(888, "voice", 1, 10)
        await database.add_level_reward(888, "voice", 1, 20)
        self.roles[10].position = 101
        self.members[123].add_roles.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "denied")
        await self.move()
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 500)
        self.bot.dispatch.assert_called_once()
        self.guild.me.guild_permissions.manage_roles = False
        await self.cog.apply_voice_rewards(self.members[123], 3, await database.get_level_settings(888))

    async def test_failed_member_credit_retried_without_duplicate_and_worker_continues(self):
        await self.move()
        await self.move(124)
        real_award = database.award_voice_xp

        async def fail_one(guild_id, user_id, *args, **kwargs):
            if user_id == 123:
                raise RuntimeError("db unavailable")
            return await real_award(guild_id, user_id, *args, **kwargs)

        with patch("cogs.levels.database.award_voice_xp", side_effect=fail_one):
            await self.tick()
        self.assertIsNone(await self.row())
        self.assertEqual((await self.row(124))["voice_xp"], 20)
        await self.cog.process_voice_tick()
        self.assertEqual((await self.row())["voice_xp"], 20)
        self.assertEqual((await self.row(124))["voice_xp"], 20)

    async def test_database_config_failure_does_not_backfill_unknown_time(self):
        await self.move()
        with patch("cogs.levels.database.get_level_settings", new=AsyncMock(side_effect=RuntimeError("db unavailable"))):
            await self.tick()
        await self.tick()
        self.assertIsNone(await self.row())
        await self.tick()
        self.assertEqual((await self.row())["voice_xp"], 20)

    async def test_tick_and_voice_event_race_does_not_double_count(self):
        await self.move()
        self.advance(60)
        await asyncio.gather(self.cog.process_voice_tick(), self.move(channel_id=789))
        await self.cog.process_voice_tick()
        row = await self.row()
        # Cache reconciliation may fail closed, but never duplicates an interval.
        self.assertLessEqual(row["voice_xp"] if row else 0, 20)
        self.assertLessEqual(row["total_voice_seconds"] if row else 0, 60)

    async def test_reconnect_rebuild_starts_from_resume_not_disconnect(self):
        await self.move()
        self.advance(30)
        await self.cog.on_disconnect()
        self.advance(3600)
        await self.cog.process_voice_tick()
        self.assertIsNone(await self.row())
        await self.cog.on_resumed()
        self.assertEqual(self.cog.voice_sessions[(888, 123)].session_start, self.clock)
        await self.tick()
        row = await self.row()
        self.assertEqual((row["voice_xp"], row["total_voice_seconds"]), (20, 60))

    async def test_restart_recovers_cached_voice_members_and_excludes_bots(self):
        await self.move()
        self.members[124].bot = True
        await self.move(124)
        self.advance(3600)
        self.cog = Levels(self.bot)
        await self.cog.on_ready()
        self.assertEqual(set(self.cog.voice_sessions), {(888, 123)})
        await self.tick()
        self.assertEqual((await self.row())["total_voice_seconds"], 60)

    async def test_restart_recovers_members_from_voice_channels_without_mapping(self):
        await self.move()
        self.channels[456].members = [self.members[123]]
        self.guild.voice_channels = [self.channels[456]]
        del self.guild.voice_states
        self.advance(3600)
        self.cog = Levels(self.bot)
        await self.cog.on_ready()
        self.assertEqual(set(self.cog.voice_sessions), {(888, 123)})
        await self.tick()
        self.assertEqual((await self.row())["total_voice_seconds"], 60)

    async def test_worker_start_guard_ready_repeats_and_shutdown(self):
        await self.cog.cog_load()
        task = self.cog.voice_xp_worker.get_task()
        await self.cog.cog_load()
        self.assertIs(self.cog.voice_xp_worker.get_task(), task)
        self.assertEqual(self.cog.voice_xp_worker.seconds, 60)
        await self.cog.on_ready()
        await self.cog.on_ready()
        self.assertIs(self.cog.voice_xp_worker.get_task(), task)
        self.cog.cog_unload()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(self.cog.voice_xp_worker.is_running())

    async def test_concurrent_chat_and_combined_voice_keep_all_xp(self):
        await self.settings(voice_separate_levels=False)
        await self.move()
        self.advance(60)
        message = SimpleNamespace(guild=self.guild, author=self.members[123],
                                  channel=SimpleNamespace(id=999, parent_id=None))
        with patch("cogs.levels.random.randint", return_value=20):
            await asyncio.gather(self.cog.on_message(message), self.cog.process_voice_tick())
        row = await self.row()
        self.assertEqual((row["text_xp"], row["total_messages"], row["total_voice_seconds"]), (40, 1, 60))
        self.assertEqual(row["text_level"], level_from_xp(40))

    async def test_voice_records_do_not_prevent_first_chat_award(self):
        await self.move()
        await self.tick()
        message = SimpleNamespace(guild=self.guild, author=self.members[123],
                                  channel=SimpleNamespace(id=999, parent_id=None))
        with patch("cogs.levels.random.randint", return_value=20):
            await self.cog.on_message(message)
        row = await self.row()
        self.assertEqual((row["text_xp"], row["voice_xp"], row["total_messages"]), (20, 20, 1))
        self.assertEqual(await database.update_balance(123, 888, 25), 125)

    async def test_missing_channel_stops_time_without_crash(self):
        await self.move()
        self.channels.pop(456)
        await self.tick()
        self.assertIsNone(await self.row())


if __name__ == "__main__":
    unittest.main()
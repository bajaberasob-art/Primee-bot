"""Phase 4 integration tests with real SQLite and simulated Discord objects."""
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
from cogs.levels import Levels


class Role:
    def __init__(self, role_id, position=1):
        self.id, self.position, self.managed = role_id, position, False

    def __lt__(self, other):
        return self.position < other.position

    def is_default(self):
        return self.id == 1


class EngagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "engagement.db")
        await database.init_db()
        self.now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        self.clock = 1000.0
        self.date_patch = patch("cogs.levels.datetime", wraps=datetime)
        self.date_mock = self.date_patch.start()
        self.date_mock.now.side_effect = lambda *args: self.now
        self.clock_patch = patch("cogs.levels.monotonic", side_effect=lambda: self.clock)
        self.clock_patch.start()
        self.roles = {i: Role(i, i) for i in (1, 10, 20)}
        self.members = {}
        self.channels = {}
        self.guild = SimpleNamespace(
            id=888, unavailable=False, voice_states={}, get_member=self.members.get,
            get_channel=self.channels.get, get_role=self.roles.get,
            fetch_member=AsyncMock(side_effect=lambda user_id: self.members[user_id]),
            me=SimpleNamespace(top_role=Role(100, 100),
                               guild_permissions=SimpleNamespace(manage_roles=True)),
        )
        for user_id in (123, 124, 125, 126):
            self.members[user_id] = SimpleNamespace(
                id=user_id, guild=self.guild, bot=False, roles=[self.roles[1]],
                add_roles=AsyncMock(), remove_roles=AsyncMock(),
            )
        self.message = SimpleNamespace(id=555, guild=self.guild, author=self.members[124])
        self.channel = SimpleNamespace(id=456, parent_id=None, fetch_message=AsyncMock(return_value=self.message))
        self.channels[456] = self.channel
        self.message.channel = self.channel
        self.bot = SimpleNamespace(
            get_guild=lambda guild_id: self.guild if guild_id == 888 else None,
            get_channel=self.channels.get, dispatch=Mock(), guilds=[self.guild],
        )
        self.cog = Levels(self.bot)
        await database.create_default_level_settings(888)

    async def asyncTearDown(self):
        self.cog.cog_unload()
        self.date_patch.stop()
        self.clock_patch.stop()
        database.DB_NAME = self.previous_db
        self.directory.cleanup()

    async def settings(self, **data):
        await database.update_level_settings(888, data)

    def advance(self, seconds):
        self.clock += seconds
        self.now += timedelta(seconds=seconds)

    def payload(self, reactor=123, message_id=555, emoji_name="👍", emoji_id=None):
        return SimpleNamespace(guild_id=888, channel_id=456, user_id=reactor,
                               member=self.members[reactor], message_id=message_id,
                               emoji=SimpleNamespace(id=emoji_id, name=emoji_name))

    async def react(self, **kwargs):
        await self.cog.on_raw_reaction_add(self.payload(**kwargs))

    async def row(self, user_id=123):
        return await database.get_user_level(888, user_id)

    def events(self, event_name):
        return [call.args[1] for call in self.bot.dispatch.call_args_list if call.args[0] == event_name]

    async def claim(self, user_id=123, **kwargs):
        return await self.cog.claim_daily_streak(self.members[user_id], **kwargs)

    async def test_reactor_and_author_awarded_without_chat_or_voice_changes(self):
        await database.update_user_level(888, 123, {
            "voice_xp": 321, "voice_level": 4, "total_voice_seconds": 999,
            "last_message_at": "2026-10-01T12:00:00+00:00",
        })
        await self.react()
        reactor, author = await self.row(), await self.row(124)
        self.assertEqual((reactor["text_xp"], author["text_xp"]), (5, 5))
        self.assertEqual(reactor["total_messages"], 0)
        self.assertEqual(reactor["last_message_at"], "2026-10-01T12:00:00+00:00")
        self.assertEqual((reactor["voice_xp"], reactor["voice_level"], reactor["total_voice_seconds"]), (321, 4, 999))

    async def test_reactor_only_does_not_fetch_message(self):
        await self.settings(reaction_xp_author=False)
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)
        self.assertIsNone(await self.row(124))
        self.channel.fetch_message.assert_not_awaited()

    async def test_author_only_and_initiator_cooldown(self):
        await self.settings(reaction_xp_reactor=False)
        await self.react()
        self.assertIsNone(await self.row())
        self.assertEqual((await self.row(124))["text_xp"], 5)
        self.message.author = self.members[125]
        await self.react(message_id=556)
        self.assertIsNone(await self.row(125))
        self.advance(60)
        await self.react(message_id=556)
        self.assertEqual((await self.row(125))["text_xp"], 5)

    async def test_cooldown_expiry_and_no_reads_for_cached_ignore(self):
        await self.react()
        with patch("level_engagement.database.get_level_settings", new=AsyncMock()) as read:
            await self.react(message_id=556)
            read.assert_not_awaited()
        self.advance(59)
        await self.react(message_id=556)
        self.assertEqual((await self.row())["text_xp"], 5)
        self.advance(1)
        await self.react(message_id=556)
        self.assertEqual((await self.row())["text_xp"], 10)

    async def test_recipient_cooldown_blocks_author_farming_from_other_reactors(self):
        await self.react()
        await self.react(reactor=125, message_id=556)
        self.assertEqual((await self.row(124))["text_xp"], 5)
        self.assertEqual((await self.row(125))["text_xp"], 5)

    async def test_duplicate_reaction_persists_after_cooldown_cache_eviction_and_restart(self):
        await self.react()
        self.advance(120)
        self.cog._reaction_cache_capacity = 1
        self.cog._reaction_cache_put(self.cog._reaction_seen, ("other",), True)
        self.cog = Levels(self.bot)
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)
        self.assertEqual((await self.row(124))["text_xp"], 5)

    async def test_custom_emoji_identity_uses_id_not_name(self):
        await self.react(emoji_id=42, emoji_name="old")
        self.advance(60)
        await self.react(emoji_id=42, emoji_name="renamed")
        self.assertEqual((await self.row())["text_xp"], 5)

    async def test_same_author_and_reactor_is_awarded_once(self):
        self.message.author = self.members[123]
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)

    async def test_concurrent_duplicate_raw_events_award_once(self):
        payload = self.payload()
        await asyncio.gather(*(self.cog.on_raw_reaction_add(payload) for _ in range(6)))
        self.assertEqual((await self.row())["text_xp"], 5)
        self.assertEqual((await self.row(124))["text_xp"], 5)

    async def test_cross_instance_duplicate_protection(self):
        second = Levels(self.bot)
        payload = self.payload()
        await asyncio.gather(self.cog.on_raw_reaction_add(payload), second.on_raw_reaction_add(payload))
        self.assertEqual((await self.row())["text_xp"], 5)
        self.assertEqual((await self.row(124))["text_xp"], 5)

    async def test_zero_cooldown_still_deduplicates(self):
        await self.settings(reaction_cooldown_seconds=0)
        await self.react()
        await self.react()
        await self.react(message_id=556)
        self.assertEqual((await self.row())["text_xp"], 10)

    async def test_dm_bot_disabled_and_both_sources_off(self):
        payload = self.payload()
        payload.guild_id = None
        await self.cog.on_raw_reaction_add(payload)
        self.assertIsNone(await self.row())
        self.members[123].bot = True
        await self.react()
        self.assertIsNone(await self.row())
        self.members[123].bot = False
        await self.settings(is_enabled=False)
        await self.react()
        self.assertIsNone(await self.row())
        await self.settings(is_enabled=True, reaction_xp_reactor=False, reaction_xp_author=False)
        await self.react()
        self.assertIsNone(await self.row())

    async def test_bot_author_never_receives_xp(self):
        self.members[124].bot = True
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)
        self.assertIsNone(await self.row(124))

    async def test_allowed_channels_and_thread_parent(self):
        await self.settings(reaction_allowed_channels=["789"])
        await self.react()
        self.assertIsNone(await self.row())
        self.channel.parent_id = 789
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)

    async def test_channel_blacklist_blocks_both_recipients(self):
        await database.add_level_blacklist(888, "channel", 456)
        await self.react()
        self.assertIsNone(await self.row())
        self.assertIsNone(await self.row(124))

    async def test_reactor_role_blacklist_blocks_entire_event(self):
        self.members[123].roles.append(self.roles[10])
        await database.add_level_blacklist(888, "role", 10)
        await self.react()
        self.assertIsNone(await self.row())
        self.assertIsNone(await self.row(124))

    async def test_author_role_blacklist_blocks_only_author(self):
        self.members[124].roles.append(self.roles[10])
        await database.add_level_blacklist(888, "role", 10)
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)
        self.assertIsNone(await self.row(124))

    async def test_reaction_multipliers_per_recipient(self):
        self.members[123].roles.append(self.roles[10])
        await database.add_level_multiplier(888, "role", 10, 1.5)
        await database.add_level_multiplier(888, "channel", 456, 2)
        await self.settings(xp_multiplier=2, boost_multiplier=2,
                            boost_expires_at=(self.now + timedelta(hours=1)).isoformat())
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 60)
        self.assertEqual((await self.row(124))["text_xp"], 40)

    async def test_expired_reaction_boost(self):
        await self.settings(boost_multiplier=3, boost_expires_at=self.now.isoformat())
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)

    async def test_reaction_level_up_and_rewards_use_existing_engine(self):
        await database.update_user_level(888, 123, {"text_xp": 98})
        await database.add_level_reward(888, "text", 1, 10)
        await self.react()
        self.assertEqual((await self.row())["text_level"], 1)
        event = self.events("lona_text_level_up")[0]
        self.assertEqual((event.old_level, event.new_level, event.current_xp), (0, 1, 103))
        self.members[123].add_roles.assert_awaited_once_with(self.roles[10], reason="Lona text level reward")

    async def test_message_fetch_failure_contained_reactor_can_still_award(self):
        self.channel.fetch_message.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "denied")
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)
        self.assertIsNone(await self.row(124))

    async def test_reaction_transaction_failure_rolls_back_ledger_and_xp(self):
        real_credit = database._add_level_text_credit

        async def fail_after_write(*args, **kwargs):
            await real_credit(*args, **kwargs)
            raise RuntimeError("test failure")

        with patch("database._add_level_text_credit", side_effect=fail_after_write):
            await self.react()
        self.assertIsNone(await self.row())
        self.assertFalse(self.cog._reaction_seen)
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)

    async def test_reaction_does_not_claim_daily_or_change_chat_cooldown(self):
        await self.react()
        self.assertIsNone((await self.row())["last_daily_claim"])
        self.assertEqual((await self.row())["current_streak"], 0)
        self.message.author = self.members[123]
        with patch("cogs.levels.random.randint", return_value=20):
            await self.cog.on_message(self.message)
        self.assertEqual((await self.row())["text_xp"], 25)
        self.assertEqual((await self.row())["total_messages"], 1)

    async def test_streak_first_claim(self):
        result = await self.claim()
        self.assertEqual((result["status"], result["current_streak"], result["xp_awarded"]), ("claimed", 1, 50))
        row = await self.row()
        self.assertEqual((row["text_xp"], row["total_messages"], row["voice_xp"]), (50, 0, 0))
        self.assertEqual(row["last_daily_claim"], self.now.isoformat())

    async def test_streak_consecutive_day_and_level_up(self):
        await self.claim()
        self.advance(86400)
        result = await self.claim()
        self.assertEqual((result["current_streak"], result["xp_awarded"]), (2, 100))
        self.assertEqual((await self.row())["text_xp"], 150)
        self.assertEqual(self.events("lona_text_level_up")[0].new_level, 1)

    async def test_streak_missed_day_resets(self):
        await self.claim()
        self.advance(86400)
        await self.claim()
        self.advance(2 * 86400)
        result = await self.claim()
        self.assertEqual((result["current_streak"], result["xp_awarded"]), (1, 50))

    async def test_same_day_claim_never_repeats_after_restart(self):
        await self.claim()
        self.cog = Levels(self.bot)
        self.advance(3600)
        self.assertEqual((await self.claim())["status"], "already_claimed")
        self.assertEqual((await self.row())["text_xp"], 50)

    async def test_concurrent_daily_claims_exactly_once(self):
        second = Levels(self.bot)
        results = await asyncio.gather(
            self.claim(), second.claim_daily_streak(self.members[123]),
            database.claim_level_streak(888, 123, self.now),
        )
        self.assertEqual(sum(result["status"] == "claimed" for result in results), 1)
        self.assertEqual((await self.row())["text_xp"], 50)

    async def test_streak_cap_applies_after_multipliers(self):
        await self.settings(streak_daily_xp=100, streak_max_cap=150, xp_multiplier=2)
        first = await self.claim()
        self.assertEqual(first["xp_awarded"], 150)
        self.advance(86400)
        second = await self.claim()
        self.assertEqual(second["xp_awarded"], 150)
        self.assertEqual(second["current_streak"], 2)

    async def test_streak_disabled_and_bot_ignored(self):
        await self.settings(streak_enabled=False)
        self.assertEqual((await self.claim())["status"], "disabled")
        self.assertIsNone(await self.row())
        await self.settings(streak_enabled=True, is_enabled=False)
        self.assertEqual((await self.claim())["status"], "disabled")
        self.members[123].bot = True
        self.assertEqual((await self.claim())["status"], "ignored")

    async def test_streak_riyadh_date_boundary_and_equivalent_timezone(self):
        first = datetime(2026, 10, 2, 20, 59, tzinfo=timezone.utc)
        await self.claim(claimed_at=first)
        # The same instant remains the same Riyadh claim date.
        local = first.astimezone(timezone(timedelta(hours=3)))
        self.assertEqual((await self.claim(claimed_at=local))["status"], "already_claimed")
        # 21:00 UTC is midnight in Riyadh and starts the next streak day.
        result = await self.claim(claimed_at=first + timedelta(minutes=2))
        self.assertEqual(result["current_streak"], 2)

    async def test_streak_naive_dates_are_utc_and_backdated_claim_is_rejected(self):
        await self.claim(claimed_at=datetime(2026, 10, 2, 12))
        result = await self.claim(claimed_at=datetime(2026, 10, 1, 12))
        self.assertEqual(result["status"], "already_claimed")
        self.assertEqual((await self.row())["text_xp"], 50)

    async def test_zero_daily_xp_can_advance_streak_without_awarding_xp(self):
        await self.settings(streak_daily_xp=0)
        result = await self.claim()
        self.assertEqual((result["xp_awarded"], result["current_streak"]), (0, 1))
        self.bot.dispatch.assert_not_called()
        self.assertEqual((await self.claim())["status"], "already_claimed")

    async def test_invalid_streak_configuration_fails_without_partial_claim(self):
        await self.settings(streak_daily_xp=-5)
        self.assertEqual((await self.claim())["status"], "error")
        self.assertIsNone(await self.row())

    async def test_streak_preserves_chat_voice_fields_and_blacklist(self):
        await database.update_user_level(888, 123, {
            "voice_xp": 100, "voice_level": 1, "total_voice_seconds": 300,
            "total_messages": 8, "last_message_at": "2026-10-01T12:00:00+00:00",
        })
        await self.claim()
        row = await self.row()
        self.assertEqual((row["voice_xp"], row["voice_level"], row["total_voice_seconds"], row["total_messages"]),
                         (100, 1, 300, 8))
        self.assertEqual(row["last_message_at"], "2026-10-01T12:00:00+00:00")
        self.members[124].roles.append(self.roles[10])
        await database.add_level_blacklist(888, "role", 10)
        self.assertEqual((await self.claim(124))["status"], "blacklisted")
        self.assertIsNone(await self.row(124))

    async def seed_overtake(self):
        await database.update_user_level(888, 123, {"text_xp": 10})
        await database.update_user_level(888, 124, {"text_xp": 14})
        await database.update_user_level(888, 125, {"text_xp": 20})

    async def test_reaction_overtake_passed_member_rank_xp(self):
        await self.seed_overtake()
        await self.settings(reaction_xp_author=False)
        await self.react()
        event = self.events("lona_text_overtake")[0]
        self.assertIs(event.passer, self.members[123])
        self.assertIs(event.passed, self.members[124])
        self.assertIs(event.guild, self.guild)
        self.assertEqual((event.previous_rank, event.new_rank, event.xp), (3, 2, 15))
        self.assertEqual((await database.get_text_rank(888, 123))["rank"], 2)

    async def test_overtake_no_duplicate_on_same_reaction_or_unchanged_rank(self):
        await self.seed_overtake()
        await self.settings(reaction_xp_author=False)
        await self.react()
        await self.react()
        self.advance(60)
        # 20 ties the member with a larger ID, so 123 overtakes them once.
        await self.react(message_id=556)
        self.advance(60)
        await self.react(message_id=557)
        self.assertEqual(len(self.events("lona_text_overtake")), 2)
        self.assertEqual([event.new_rank for event in self.events("lona_text_overtake")], [2, 1])

    async def test_unchanged_rank_and_new_participant_no_overtake(self):
        await database.update_user_level(888, 124, {"text_xp": 100})
        await self.settings(reaction_xp_author=False)
        await self.react()
        self.advance(60)
        await self.react(message_id=556)
        self.assertFalse(self.events("lona_text_overtake"))

    async def test_reaction_two_recipient_transaction_uses_final_ranks(self):
        await database.update_user_level(888, 123, {"text_xp": 10})
        await database.update_user_level(888, 124, {"text_xp": 14})
        await self.react()
        self.assertEqual((await self.row())["text_xp"], 15)
        self.assertEqual((await self.row(124))["text_xp"], 19)
        self.assertFalse(self.events("lona_text_overtake"))

    async def test_reaction_batch_does_not_emit_when_rank_net_unchanged(self):
        self.message.author = self.members[125]
        await database.update_user_level(888, 123, {"text_xp": 10})
        await database.update_user_level(888, 124, {"text_xp": 14})
        await database.update_user_level(888, 125, {"text_xp": 9})
        self.members[125].roles.append(self.roles[10])
        await database.add_level_multiplier(888, "role", 10, 3)
        await self.react()
        # 123 passes 124 but is passed by 125 in the same atomic reaction.
        self.assertEqual((await database.get_text_rank(888, 123))["rank"], 2)
        self.assertFalse([event for event in self.events("lona_text_overtake") if event.passer.id == 123])

    async def test_chat_overtakes_all_crossed_members(self):
        await self.seed_overtake()
        self.message.author = self.members[123]
        with patch("cogs.levels.random.randint", return_value=20):
            await self.cog.on_message(self.message)
        events = self.events("lona_text_overtake")
        self.assertEqual({event.passed.id for event in events}, {124, 125})
        self.assertTrue(all(event.new_rank == 1 for event in events))

    async def test_streak_overtake_uses_common_events(self):
        await self.seed_overtake()
        await self.claim()
        self.assertEqual(len(self.events("lona_text_overtake")), 2)
        await self.claim()
        self.assertEqual(len(self.events("lona_text_overtake")), 2)

    async def test_combined_voice_overtake_and_separate_voice_no_text_alert(self):
        await self.seed_overtake()
        result = await database.award_voice_xp(888, 123, 0, 20, 60, detect_overtakes=True)
        await self.cog._handle_text_award(self.members[123], await database.get_level_settings(888), result)
        self.assertEqual(len(self.events("lona_text_overtake")), 2)
        result = await database.award_voice_xp(888, 123, 500, 0, 60, detect_overtakes=True)
        self.assertFalse(result["overtakes"])
        self.assertEqual(len(self.events("lona_text_overtake")), 2)

    async def test_disabled_overtake_skips_queries_and_alerts(self):
        await self.seed_overtake()
        await self.settings(overtake_alert_enabled=False, reaction_xp_author=False)
        with patch("database._level_text_overtakes", wraps=database._level_text_overtakes) as query:
            await self.react()
        self.assertFalse(self.events("lona_text_overtake"))
        self.assertTrue(all(not call.args[5] for call in query.call_args_list))

    async def test_reaction_does_not_use_full_leaderboard_helpers(self):
        with patch("database.get_text_leaderboard", new=AsyncMock(side_effect=AssertionError("full leaderboard"))):
            with patch("database.get_text_rank", new=AsyncMock(side_effect=AssertionError("rank helper"))):
                await self.react()
        self.assertEqual((await self.row())["text_xp"], 5)

    async def test_uncached_passed_member_keeps_id_without_api_fetch(self):
        await self.seed_overtake()
        self.members.pop(124)
        await self.settings(reaction_xp_author=False)
        await self.react()
        self.assertEqual(self.events("lona_text_overtake")[0].passed.id, 124)
        self.guild.fetch_member.assert_not_awaited()

    async def test_listener_registered_without_commands_or_replacing_other_reaction_listener(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        other_listener = AsyncMock()
        bot.add_listener(other_listener, "on_raw_reaction_add")
        before = set(bot.all_commands)
        cog = Levels(bot)
        await bot.add_cog(cog)
        self.assertIn(other_listener, bot.extra_events["on_raw_reaction_add"])
        self.assertEqual(len(bot.extra_events["on_raw_reaction_add"]), 2)
        self.assertEqual(set(bot.all_commands), before)
        self.assertFalse(bot.tree.get_commands())
        await bot.remove_cog("Levels")
        await bot.close()

    async def test_unrelated_wallet_and_tables_preserved(self):
        await self.react()
        await self.claim()
        self.assertEqual(await database.update_balance(123, 888, 25), 125)
        async with database.connect() as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tickets','warnings','guild_settings')"
            ) as cur:
                self.assertEqual({row[0] for row in await cur.fetchall()}, {"tickets", "warnings", "guild_settings"})


if __name__ == "__main__":
    unittest.main()
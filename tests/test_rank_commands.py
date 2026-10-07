"""Phase 6: real SQLite, command registration, mocked Discord delivery."""
import asyncio
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image

import database
from cogs.rank_commands import LeaderboardView, RankCommands, publish_rank_commands
from cogs import card_generator
from cogs.levels import Levels
from cogs.utilities import Utilities, CommandIntercepted
from level_progression import level_from_xp, xp_required


class Response:
    def __init__(self):
        self.done = False
        self.send_message = AsyncMock()
        self.defer = AsyncMock(side_effect=self._defer)

    def is_done(self):
        return self.done

    async def _defer(self, **kwargs):
        self.done = True


class RankCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "rank.db")
        await database.init_db()
        self.members = {}
        self.channel = SimpleNamespace(id=456, parent_id=None, send=AsyncMock())
        self.guild = SimpleNamespace(
            id=888, unavailable=False, chunked=True, members=[], get_member=self.members.get,
            fetch_member=AsyncMock(side_effect=discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"), "missing")),
            chunk=AsyncMock(),
        )
        for uid in range(1, 21):
            member = SimpleNamespace(
                id=uid, name=f"user{uid}", display_name=f"عضو {uid}", bot=False,
                guild=self.guild, display_avatar=SimpleNamespace(url=f"https://cdn.discordapp.com/avatars/{uid}.png"),
                roles=[], guild_permissions=discord.Permissions.none(),
            )
            self.members[uid] = member
        self.guild.members = list(self.members.values())
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        self.bot.get_guild = lambda gid: self.guild if gid == 888 else None
        self.cog = RankCommands(self.bot)
        await self.bot.add_cog(self.cog)
        self.clock = 100.0
        self.clock_patch = patch("cogs.rank_commands.monotonic", side_effect=lambda: self.clock)
        self.clock_patch.start()
        self.generated = []

        async def fake_generator(*args):
            self.generated.append(args)
            result = io.BytesIO()
            Image.new("RGB", (8, 8)).save(result, "PNG")
            result.seek(0)
            return result
        async def fake_animated_generator(*args):
            self.generated.append(args)
            result = io.BytesIO()
            Image.new("RGB", (8, 8)).save(result, "GIF")
            result.seek(0)
            return result
        self.card_patch = patch("cogs.rank_commands.generate_rank_card", side_effect=fake_generator)
        self.generator = self.card_patch.start()
        self.gif_patch = patch(
            "cogs.rank_commands.generate_level_up_gif",
            side_effect=fake_animated_generator,
        )
        self.gif_generator = self.gif_patch.start()
        self.views = []

    async def asyncTearDown(self):
        for view in self.views:
            view.stop()
        self.card_patch.stop()
        self.gif_patch.stop()
        self.clock_patch.stop()
        await self.bot.close()
        database.DB_NAME = self.original_db
        self.directory.cleanup()

    def interaction(self, user_id=1):
        message = SimpleNamespace(edit=AsyncMock())
        return SimpleNamespace(
            guild=self.guild, user=self.members[user_id], channel=self.channel,
            channel_id=self.channel.id, response=Response(),
            followup=SimpleNamespace(send=AsyncMock(return_value=message)), message=message,
            client=self.bot, permissions=discord.Permissions.all(), command=None,
        )

    async def seed(self, user_id, text=0, voice=0, **kwargs):
        await database.update_user_level(888, user_id, {
            "text_xp": text, "text_level": level_from_xp(text),
            "voice_xp": voice, "voice_level": level_from_xp(voice), **kwargs,
        })

    async def rank(self, interaction, member=None):
        await self.cog.rank_slash.callback(self.cog, interaction, member)

    async def give_level(self, interaction, member, levels):
        await self.cog.give_level_slash.callback(self.cog, interaction, member, levels)

    async def take_level(self, interaction, member, levels):
        await self.cog.take_level_slash.callback(self.cog, interaction, member, levels)

    async def top(self, interaction, mode="text", period=None):
        if period is None:
            await self.cog.top_slash.callback(self.cog, interaction, mode)
        else:
            await self.cog.top_slash.callback(self.cog, interaction, mode, period)
        call = interaction.followup.send.call_args or interaction.response.send_message.call_args
        kwargs = call.kwargs
        if "view" in kwargs:
            self.views.append(kwargs["view"])
        return kwargs

    @staticmethod
    def embeds(reply):
        return reply.get("embeds") or [reply["embed"]]

    async def test_rank_self_real_values_avatar_settings_and_stats(self):
        await self.seed(1, 155, total_messages=25, total_voice_seconds=3660, current_streak=3)
        await self.seed(2, 500)
        await database.update_level_settings(888, {
            "card_layout": "ring", "card_color": "#ef55ba", "card_particles": "petals",
            "card_bg_url": "https://example.com/bg.png", "card_animated_bar": False,
            "card_design": {"animationEnabled": False},
        })
        itx = self.interaction()
        await self.rank(itx)
        args = self.generated[0]
        self.assertIs(args[0], self.members[1])
        self.assertEqual(args[0].display_avatar.url, "https://cdn.discordapp.com/avatars/1.png")
        self.assertEqual(args[1:6], (1, 155, xp_required(1), 2, 20))
        settings = args[6]
        self.assertEqual(settings["card_layout"], "ring")
        self.assertEqual(settings["card_particles"], "petals")
        self.assertEqual(settings["card_color"], "#ef55ba")
        self.assertEqual(settings["card_bg_url"], "https://example.com/bg.png")
        self.assertFalse(settings["card_animated_bar"])
        self.assertEqual((settings["total_messages"], settings["total_voice_seconds"], settings["current_streak"]),
                         (25, 3660, 3))
        sent = itx.followup.send.call_args.kwargs
        self.assertEqual(sent["file"].filename, "prime-rank.png")
        self.assertNotIn("embed", sent)
        itx.response.defer.assert_awaited_once()

    async def test_rank_other_member(self):
        await self.seed(2, 500)
        await self.rank(self.interaction(), self.members[2])
        self.assertIs(self.generated[0][0], self.members[2])
        self.assertEqual(self.generated[0][2], 500)

    async def test_no_records_rank_level_zero_and_no_writes(self):
        await self.rank(self.interaction())
        self.assertEqual(self.generated[0][1:6], (0, 0, 100, None, 20))
        self.assertIsNone(await database.get_user_level(888, 1))
        self.assertIsNone(await database.get_level_settings(888))

    async def test_give_level_adds_levels_and_preserves_progress_and_other_stats(self):
        await self.seed(
            2, text=155, voice=500, total_messages=41,
            total_voice_seconds=9000, current_streak=4,
        )
        interaction = self.interaction()
        await self.give_level(interaction, self.members[2], 2)

        row = await database.get_user_level(888, 2)
        self.assertEqual((row["text_level"], row["text_xp"]), (3, 530))
        self.assertEqual((row["voice_xp"], row["total_messages"]), (500, 41))
        self.assertEqual((row["total_voice_seconds"], row["current_streak"]), (9000, 4))
        self.assertIn(
            "مستواه النصي الآن 3",
            interaction.response.send_message.call_args.args[0],
        )
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_give_level_denies_manage_guild_without_admin(self):
        command = self.bot.tree.get_command("give_level")
        self.assertEqual([option.name for option in command.parameters], ["member", "levels"])
        self.assertIsNone(command.default_permissions)
        interaction = self.interaction()
        interaction.user.guild_permissions = discord.Permissions(manage_guild=True)
        with self.assertRaises(app_commands.CheckFailure):
            await command._check_can_run(interaction)

    async def test_trusted_admin_role_can_use_level_mutation_commands(self):
        command = self.bot.tree.get_command("give_level")
        interaction = self.interaction()
        interaction.user.roles = [SimpleNamespace(id=987654321, name="level-staff")]
        with patch.dict(os.environ, {"ADMIN_ROLE_IDS": "987654321"}):
            self.assertTrue(await command._check_can_run(interaction))

    async def test_take_level_preserves_progress_and_does_not_change_other_stats(self):
        await self.seed(
            2, text=515, voice=500, total_messages=41,
            total_voice_seconds=9000, current_streak=4,
        )
        interaction = self.interaction()
        await self.take_level(interaction, self.members[2], 2)
        row = await database.get_user_level(888, 2)
        self.assertEqual((row["text_level"], row["text_xp"]), (1, 140))
        self.assertEqual((row["voice_xp"], row["total_messages"]), (500, 41))
        self.assertEqual((row["total_voice_seconds"], row["current_streak"]), (9000, 4))
        self.assertIn("مستواه النصي الآن 1", interaction.response.send_message.call_args.args[0])
        self.assertIn("لم يتم تغيير رتب المكافآت", interaction.response.send_message.call_args.args[0])

    async def test_take_level_never_drops_below_zero_or_creates_missing_record(self):
        await self.seed(2, text=90)
        interaction = self.interaction()
        await self.take_level(interaction, self.members[2], 100)
        row = await database.get_user_level(888, 2)
        self.assertEqual((row["text_level"], row["text_xp"]), (0, 90))

        interaction = self.interaction()
        await self.take_level(interaction, self.members[3], 5)
        self.assertIsNone(await database.get_user_level(888, 3))

    async def test_level_mutations_require_administrator_or_trusted_role(self):
        command = self.bot.tree.get_command("take_level")
        interaction = self.interaction()
        interaction.user.guild_permissions = discord.Permissions(manage_guild=True)
        with self.assertRaises(app_commands.CheckFailure):
            await command._check_can_run(interaction)
        interaction.user.guild_permissions = discord.Permissions(administrator=True)
        self.assertTrue(await command._check_can_run(interaction))

    async def test_rank_card_animation_follows_dashboard_setting(self):
        await database.update_level_settings(888, {
            "card_design": {"animationEnabled": True, "animationIntensity": 62},
        })
        interaction = self.interaction()
        await self.rank(interaction)
        self.gif_generator.assert_awaited_once()
        self.assertEqual(
            interaction.followup.send.call_args.kwargs["file"].filename,
            "prime-rank.gif",
        )

        self.clock += 8
        self.gif_generator.reset_mock()
        await database.update_level_settings(888, {
            "card_design": {"animationEnabled": False, "animationIntensity": 62},
        })
        interaction = self.interaction()
        await self.rank(interaction)
        self.generator.assert_awaited_once()
        self.gif_generator.assert_not_awaited()
        self.assertEqual(
            interaction.followup.send.call_args.kwargs["file"].filename,
            "prime-rank.png",
        )

    async def test_zero_animation_intensity_keeps_rank_card_static(self):
        await database.update_level_settings(888, {
            "card_design": {"animationEnabled": True, "animationIntensity": 0},
        })
        interaction = self.interaction()
        await self.rank(interaction)
        self.generator.assert_awaited_once()
        self.gif_generator.assert_not_awaited()
        self.assertEqual(
            interaction.followup.send.call_args.kwargs["file"].filename,
            "prime-rank.png",
        )

    async def test_animated_background_keeps_rank_card_as_gif_when_lights_are_off(self):
        await database.update_level_settings(888, {
            "card_bg_url": "https://example.com/animated.gif",
            "card_design": {"animationEnabled": False, "animationIntensity": 0},
        })
        interaction = self.interaction()
        with patch(
            "cogs.card_generator.has_animated_background",
            new=AsyncMock(return_value=True),
        ):
            await self.rank(interaction)
        self.gif_generator.assert_awaited_once()
        self.assertEqual(
            interaction.followup.send.call_args.kwargs["file"].filename,
            "prime-rank.gif",
        )

    async def test_prefix_aliases_share_command_and_handler(self):
        rank = self.bot.get_command("rank")
        for alias in ("rank", "level", "lvl", "لفل", "رانك"):
            self.assertIs(self.bot.get_command(alias), rank)
            message = SimpleNamespace(
                author=self.members[1], guild=self.guild, channel=self.channel, _state=None)
            self.clock += 8
            await rank.callback(self.cog, SimpleNamespace(message=message), None)
        self.assertEqual(len(self.generated), 5)
        self.assertEqual(self.channel.send.await_count, 5)

    async def test_top_prefix_aliases_use_the_same_lifetime_flow(self):
        top = self.bot.get_command("top")
        for alias in ("top", "توب", "متصدرين"):
            self.assertIs(self.bot.get_command(alias), top)
            self.clock += 5
            message = SimpleNamespace(
                author=self.members[1], guild=self.guild, channel=self.channel, _state=None)
            await top.callback(self.cog, SimpleNamespace(message=message))
        self.assertEqual(self.channel.send.await_count, 3)
        for call in self.channel.send.await_args_list:
            kwargs = call.kwargs
            self.assertIn("PRIME TOP", kwargs["embeds"][0].title)
            self.assertEqual(kwargs["view"].mode, "text")
            self.assertEqual(len(kwargs["view"].children), 2)
    async def test_image_only_contract_never_allows_an_empty_response(self):
        await database.update_level_settings(888, {"prime_controls": {"rank": {
            "imageOnly": True, "showCard": False, "showCustomMessage": False, "sendEmbed": False,
        }}})
        await self.rank(self.interaction())
        self.assertEqual(len(self.generated), 1)
    async def test_rank_cooldown_shared_between_slash_and_arabic_prefix(self):
        await self.rank(self.interaction())
        message = SimpleNamespace(author=self.members[1], guild=self.guild, channel=self.channel, _state=None)
        await self.bot.get_command("لفل").callback(self.cog, SimpleNamespace(message=message), None)
        self.assertEqual(len(self.generated), 1)
        self.assertIn("الانتظار", self.channel.send.call_args.args[0])
        self.clock += 8
        await self.rank(self.interaction())
        self.assertEqual(len(self.generated), 2)

    async def test_cooldown_is_per_member_not_global(self):
        await self.rank(self.interaction(1))
        await self.rank(self.interaction(2))
        self.assertEqual(len(self.generated), 2)

    async def test_missing_avatar_uses_real_phase5_generator(self):
        self.members[1].display_avatar = None
        data = io.BytesIO()
        await database.update_level_settings(888, {
            "card_design": {"animationEnabled": False},
        })
        with patch("cogs.rank_commands.generate_rank_card", wraps=card_generator.generate_rank_card):
            itx = self.interaction()
            # Capture PNG before Discord.File closes its BytesIO.
            async def capture(content=None, **kwargs):
                data.write(kwargs["file"].fp.getvalue())
            itx.followup.send.side_effect = capture
            await self.rank(itx)
        with Image.open(io.BytesIO(data.getvalue())) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, card_generator.LAYOUTS["vertical"])

    async def test_all_five_layouts_use_phase5_and_actual_avatar_path(self):
        avatar = io.BytesIO()
        Image.new("RGB", (80, 80), "#22cba1").save(avatar, "PNG")
        async def fetch(url):
            return avatar.getvalue() if url and "cdn.discordapp.com" in url else None
        for layout in card_generator.LAYOUTS:
            self.clock += 8
            await database.update_level_settings(888, {
                "card_layout": layout,
                "card_design": {"animationEnabled": False},
            })
            itx = self.interaction()
            captured = []
            async def capture(content=None, **kwargs):
                captured.append(kwargs["file"].fp.getvalue())
            itx.followup.send.side_effect = capture
            with patch("cogs.rank_commands.generate_rank_card", wraps=card_generator.generate_rank_card):
                with patch("cogs.card_generator.fetch_image", side_effect=fetch) as download:
                    await self.rank(itx)
            self.assertTrue(any("cdn.discordapp.com" in str(call.args[0]) for call in download.call_args_list))
            with Image.open(io.BytesIO(captured[0])) as image:
                self.assertEqual(image.size, card_generator.LAYOUTS[layout])

    async def test_top_text_and_voice_sorted_top_ten_excludes_bots_and_departed(self):
        self.members[2].bot = True
        now = datetime.now(timezone.utc)
        for uid in range(1, 21):
            await database.award_text_xp(888, uid, uid * 10, now, cooldown_seconds=0)
            await database.award_voice_xp(
                888, uid, (21 - uid) * 20, 0, 0, awarded_at=now,
            )
        await database.award_text_xp(888, 999, 100000, now, cooldown_seconds=0)
        await database.award_voice_xp(888, 999, 100000, 0, 0, awarded_at=now)
        text_embeds = self.embeds(await self.top(self.interaction(), "text"))
        self.assertEqual(len(text_embeds), 10)
        names = [embed.author.name.rsplit("· ", 1)[-1] for embed in text_embeds]
        self.assertEqual(names[0], "عضو 20")
        self.assertNotIn("عضو 2", names)
        text = "\n".join(embed.description for embed in text_embeds)
        self.assertNotIn("100,000", text)
        self.assertIn("PRIME TOP", text_embeds[0].title)
        self.clock += 5
        voice_embeds = self.embeds(await self.top(self.interaction(), "voice"))
        self.assertEqual(len(voice_embeds), 10)
        self.assertEqual(voice_embeds[0].author.name.rsplit("· ", 1)[-1], "عضو 1")
        self.assertIn("VOICE", voice_embeds[0].fields[0].name)
        self.assertTrue(all("XP" in embed.description for embed in voice_embeds))

    async def test_ten_humans_after_many_higher_bot_records(self):
        for uid in range(1, 21):
            self.members[uid].bot = uid <= 10
            await self.seed(uid, text=10000-uid, voice=uid)
        rows = await database.get_command_level_leaderboard(888, list(range(11, 21)), "text")
        self.assertEqual([r["user_id"] for r in rows], list(range(11, 21)))

    async def test_rank_excludes_bots_departed_and_tie_breaking_matches_top(self):
        self.members[2].bot = True
        for uid in (1, 2, 3):
            await self.seed(uid, text=500)
        await self.seed(999, text=99999)
        await self.rank(self.interaction(3))
        self.assertEqual(self.generated[0][4], 2)
        self.assertEqual(self.generated[0][5], 19)
        rows = await database.get_command_level_leaderboard(888, [1, 3], "text")
        self.assertEqual([r["user_id"] for r in rows], [1, 3])

    async def test_empty_leaderboard_and_no_xp_writes(self):
        reply = await self.top(self.interaction())
        self.assertIn("لا يوجد", self.embeds(reply)[0].description)
        self.assertIsNone(await database.get_user_level(888, 1))
        self.assertIsNone(await database.get_level_settings(888))

    async def test_deleted_member_missing_data_and_bot_denied(self):
        target = SimpleNamespace(id=999)
        itx = self.interaction()
        await self.rank(itx, target)
        self.assertFalse(self.generated)
        self.assertIn("لم أجد", itx.followup.send.call_args.args[0])
        self.clock += 8
        self.members[2].bot = True
        itx = self.interaction()
        await self.rank(itx, self.members[2])
        self.assertIn("للبوتات", itx.followup.send.call_args.args[0])

    async def test_deleted_guild_dm_and_unavailable_guild(self):
        for mode in ("dm", "unavailable", "deleted"):
            itx = self.interaction()
            if mode == "dm":
                itx.guild = None
            elif mode == "unavailable":
                self.guild.unavailable = True
            else:
                self.bot.get_guild = lambda gid: None
            await self.rank(itx)
            self.assertTrue(itx.response.send_message.await_count)
            self.guild.unavailable = False
        self.assertFalse(self.generated)

    async def test_partial_member_cache_chunked_once(self):
        self.guild.chunked = False
        async def chunk(**kwargs):
            self.guild.chunked = True
        self.guild.chunk.side_effect = chunk
        await asyncio.gather(self.cog.human_members(self.guild), self.cog.human_members(self.guild))
        self.guild.chunk.assert_awaited_once_with(cache=True)

    async def test_partial_cache_fails_explicitly_not_incorrect_leaderboard(self):
        self.guild.chunked = False
        self.guild.chunk.side_effect = asyncio.TimeoutError()
        itx = self.interaction()
        await self.rank(itx)
        self.assertFalse(self.generated)
        self.assertIn("غير جاهزة", itx.followup.send.call_args.args[0])

    async def test_database_and_card_errors_are_contained(self):
        for target in ("database.get_level_settings", "cogs.rank_commands.generate_level_up_gif"):
            self.clock += 8
            itx = self.interaction()
            with patch(target, new=AsyncMock(side_effect=RuntimeError("test failure"))):
                with self.assertLogs("PrimeRankCommands", level="ERROR"):
                    await self.rank(itx)
            self.assertIn("تعذر إنشاء", itx.followup.send.call_args.args[0])

    async def test_top_database_error_and_cooldown(self):
        itx = self.interaction()
        with patch("database.get_command_level_leaderboard", new=AsyncMock(side_effect=RuntimeError("db failed"))):
            with self.assertLogs("PrimeRankCommands", level="ERROR"):
                await self.top(itx)
        self.assertIn("تعذر تحميل", itx.followup.send.call_args.args[0])
        itx = self.interaction()
        await self.top(itx)
        self.assertIn("الانتظار", itx.response.send_message.call_args.args[0])

    async def test_enabled_flags_channels_and_thread_parent(self):
        await database.update_level_settings(888, {"command_rank_enabled": False})
        itx = self.interaction()
        await self.rank(itx)
        self.assertIn("معطل", itx.followup.send.call_args.args[0])
        self.clock += 8
        await database.update_level_settings(888, {"command_rank_enabled": True, "command_rank_channels": ["789"]})
        itx = self.interaction()
        await self.rank(itx)
        self.assertIn("قنوات", itx.followup.send.call_args.args[0])
        self.clock += 8
        self.channel.parent_id = 789
        await self.rank(self.interaction())
        self.assertEqual(len(self.generated), 1)

    async def test_buttons_switch_text_voice_owner_only_and_timeout(self):
        await self.seed(1, text=155, voice=1000)
        now = datetime.now(timezone.utc)
        await database.award_text_xp(888, 1, 155, now, cooldown_seconds=0)
        await database.award_voice_xp(888, 1, 1000, 0, 0, awarded_at=now)
        reply = await self.top(self.interaction())
        view = reply["view"]
        self.assertEqual(view.mode, "text")
        self.assertEqual(len(view.children), 2)
        other = self.interaction(2)
        self.assertFalse(await view.interaction_check(other))
        click = self.interaction()
        self.assertTrue(await view.interaction_check(click))
        await view.voice_button.callback(click)
        embed = click.message.edit.call_args.kwargs["embeds"][0]
        self.assertIn("VOICE", embed.fields[0].name)
        self.assertEqual(view.voice_button.style, discord.ButtonStyle.primary)
        too_fast = self.interaction()
        await view.text_button.callback(too_fast)
        self.assertIn("الانتظار", too_fast.response.send_message.call_args.args[0])
        self.clock += 2
        await view.text_button.callback(self.interaction())
        self.assertEqual(view.text_button.style, discord.ButtonStyle.primary)
        await view.on_timeout()
        self.assertTrue(all(child.disabled for child in view.children))
    async def test_text_and_voice_level_up_notices_attach_animated_cards(self):
        await self.seed(
            1, text=155, voice=270, total_messages=41,
            total_voice_seconds=9000, current_streak=12,
        )
        await database.update_level_settings(888, {
            "levelup_channel_id": 456,
            "levelup_voice_channel_id": 456,
            "card_layout": "minimal",
            "card_show_stats": False,
        })
        self.guild.get_channel = lambda channel_id: self.channel if channel_id == 456 else None

        async def fake_level_card(*args):
            self.generated.append(args)
            result = io.BytesIO()
            Image.new("RGB", (8, 8)).save(result, "GIF")
            result.seek(0)
            return result

        levels = Levels(self.bot)
        with patch("cogs.levels.generate_level_up_gif", side_effect=fake_level_card) as generator:
            await levels.on_lona_text_level_up(
                SimpleNamespace(guild=self.guild, member=self.members[1])
            )
            await levels.on_lona_voice_level_up(
                SimpleNamespace(guild=self.guild, member=self.members[1])
            )

        self.assertEqual(generator.await_count, 2)
        for args in self.generated:
            card_settings = args[6]
            self.assertFalse(card_settings["card_show_stats"])
            self.assertEqual(
                (card_settings["total_messages"], card_settings["total_voice_seconds"],
                 card_settings["current_streak"]),
                (41, 9000, 12),
            )
            self.assertIs(args[0], self.members[1])
        self.assertEqual(self.channel.send.await_count, 2)
        for call in self.channel.send.await_args_list:
            self.assertEqual(call.kwargs["file"].filename, "prime-level-up.gif")
            self.assertEqual(call.kwargs["embed"].image.url, "attachment://prime-level-up.gif")

    async def test_disabling_level_up_animation_keeps_the_png_notice(self):
        await self.seed(1, text=155, total_messages=4)
        await database.update_level_settings(888, {
            "levelup_channel_id": 456,
            "card_design": {"animationEnabled": False},
        })
        self.guild.get_channel = lambda channel_id: self.channel if channel_id == 456 else None
        levels = Levels(self.bot)
        result = io.BytesIO()
        Image.new("RGB", (8, 8)).save(result, "PNG")
        result.seek(0)
        with patch("cogs.levels.generate_rank_card", new=AsyncMock(return_value=result)) as renderer:
            await levels.on_lona_text_level_up(
                SimpleNamespace(guild=self.guild, member=self.members[1])
            )
        renderer.assert_awaited_once()
        call = self.channel.send.await_args
        self.assertEqual(call.kwargs["file"].filename, "prime-level-up.png")
        self.assertEqual(call.kwargs["embed"].image.url, "attachment://prime-level-up.png")

    async def test_animated_background_keeps_level_up_notice_as_gif_when_lights_are_off(self):
        await self.seed(1, text=155, total_messages=4)
        await database.update_level_settings(888, {
            "levelup_channel_id": 456,
            "card_bg_url": "https://example.com/animated.gif",
            "card_design": {"animationEnabled": False, "animationIntensity": 0},
        })
        self.guild.get_channel = lambda channel_id: self.channel if channel_id == 456 else None
        levels = Levels(self.bot)
        result = io.BytesIO()
        Image.new("RGB", (8, 8)).save(result, "GIF")
        result.seek(0)
        with (
            patch("cogs.levels.has_animated_background", new=AsyncMock(return_value=True)),
            patch(
                "cogs.levels.generate_level_up_gif",
                new=AsyncMock(return_value=result),
            ) as renderer,
        ):
            await levels.on_lona_text_level_up(
                SimpleNamespace(guild=self.guild, member=self.members[1])
            )
        renderer.assert_awaited_once()
        call = self.channel.send.await_args
        self.assertEqual(call.kwargs["file"].filename, "prime-level-up.gif")
        self.assertEqual(call.kwargs["embed"].image.url, "attachment://prime-level-up.gif")

    async def test_button_rechecks_disabled_top_settings_and_global_policy(self):
        reply = await self.top(self.interaction())
        view = reply["view"]
        await database.update_level_settings(888, {"command_top_enabled": False})
        click = self.interaction()
        await view.voice_button.callback(click)
        click.message.edit.assert_not_awaited()
        self.assertIn("معطل", click.followup.send.call_args.args[0])
        self.clock += 2
        await database.update_level_settings(888, {"command_top_enabled": True})
        utilities = SimpleNamespace(app_command_interceptor=AsyncMock(return_value=False))
        with patch.object(self.bot, "get_cog", return_value=utilities):
            click = self.interaction()
            await view.voice_button.callback(click)
            click.message.edit.assert_not_awaited()
            self.assertEqual(utilities.app_command_interceptor.call_args.args[0].command.name, "top")

    async def test_bot_registration_preserves_existing_commands_no_admin_requirement(self):
        @app_commands.command(name="unrelated", description="Existing command")
        async def unrelated(interaction: discord.Interaction):
            pass
        self.bot.tree.add_command(unrelated)
        self.assertEqual(
            {c.name for c in self.bot.tree.get_commands()},
            {"rank", "top", "give_level", "take_level", "unrelated"},
        )
        self.assertIsNone(self.bot.tree.get_command("rank").default_permissions)
        self.assertTrue(self.bot.tree.get_command("rank").guild_only)
        self.assertTrue(await self.bot.tree.get_command("rank")._check_can_run(self.interaction()))
        self.assertIsNone(self.bot.tree.get_command("give_level").default_permissions)

    async def test_indexed_queries_large_human_list_and_scope(self):
        await self.seed(1, text=50, voice=30)
        await database.update_user_level(777, 1, {"text_xp": 99999})
        human_ids = list(range(1, 5001))
        rows = await database.get_command_level_leaderboard(888, human_ids, "text")
        self.assertEqual([r["user_id"] for r in rows], [1])
        async with database.connect() as db:
            async with db.execute(
                """EXPLAIN QUERY PLAN SELECT user_id FROM user_levels
                   WHERE guild_id=? AND text_xp>0
                   AND user_id IN (SELECT value FROM json_each(?))
                   ORDER BY text_xp DESC,user_id ASC LIMIT 10""",
                (888, json.dumps(human_ids)),
            ) as cur:
                plan = "\n".join(str(row) for row in await cur.fetchall())
        self.assertIn("idx_user_levels_text", plan)

    async def test_prefix_parse_error_is_friendly(self):
        ctx = SimpleNamespace(send=AsyncMock())
        await self.cog.cog_command_error(ctx, commands.MemberNotFound("missing"))
        self.assertIn("لم أجد", ctx.send.call_args.args[0])

    async def test_prefix_member_converter_targets_another_member(self):
        uid = 123456789012345678
        target = discord.Member(
            data={"user": {"id": str(uid), "username": "other", "discriminator": "0",
                           "avatar": None, "bot": False}, "roles": [], "joined_at": None, "flags": 0},
            guild=self.guild, state=self.bot._connection)
        self.members[uid] = target
        self.guild.members.append(target)
        message = SimpleNamespace(author=self.members[1], guild=self.guild, channel=self.channel, _state=None)
        ctx = SimpleNamespace(message=message, guild=self.guild, bot=self.bot)
        command = self.bot.get_command("لفل")
        for mention in (f"<@{uid}>", f"<@!{uid}>"):
            self.clock += 8
            await command.callback(self.cog, ctx, mention)
            self.assertIs(self.generated[-1][0], target)

    async def test_invalid_prefix_target_never_silently_shows_self(self):
        ctx = SimpleNamespace(message=SimpleNamespace(author=self.members[1], guild=self.guild,
                                                     channel=self.channel, _state=None))
        with patch.object(commands.MemberConverter, "convert",
                          new=AsyncMock(side_effect=commands.MemberNotFound("missing"))):
            with self.assertRaises(commands.MemberNotFound):
                await self.bot.get_command("lvl").callback(self.cog, ctx, "missing")
        self.assertFalse(self.generated)

    async def test_existing_global_policy_checks_prefix_and_slash(self):
        utilities = Utilities(self.bot)
        await self.bot.add_cog(utilities)
        await utilities.toggle_command(888, "rank", False)
        itx = self.interaction()
        itx.command = self.bot.tree.get_command("rank")
        self.assertFalse(await utilities.app_command_interceptor(itx))
        ctx = SimpleNamespace(guild=self.guild, command=self.bot.get_command("level"),
                              author=self.members[1], channel=self.channel, send=AsyncMock())
        with self.assertRaises(CommandIntercepted):
            await utilities.command_interceptor(ctx)
        await self.bot.remove_cog("Utilities")


class SelectiveRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_development_guild_registration_is_scoped(self):
        local = {name: SimpleNamespace(to_dict=lambda tree, name=name: {
            "name": name, "description": name, "type": 1, "options": []})
            for name in ("rank", "top", "give_level", "take_level")}
        bot = SimpleNamespace(
            application_id=123, tree=SimpleNamespace(fetch_commands=AsyncMock(return_value=[]),
                                                    get_command=local.get),
            http=SimpleNamespace(upsert_global_command=AsyncMock(), upsert_guild_command=AsyncMock()))
        self.assertTrue(await publish_rank_commands(bot, guild=discord.Object(id=888)))
        self.assertEqual(bot.http.upsert_guild_command.await_count, 4)
        bot.http.upsert_global_command.assert_not_awaited()
        self.assertEqual(bot.http.upsert_guild_command.call_args.args[:2], (123, 888))

    async def test_only_level_commands_upserted_and_unchanged_restart_is_read_only(self):
        payloads = {name: {"name": name, "description": name, "type": 1, "options": []}
                    for name in ("rank", "top", "give_level", "take_level")}
        local = {name: SimpleNamespace(to_dict=lambda tree, name=name: payloads[name]) for name in payloads}
        bot = SimpleNamespace(
            application_id=123, tree=SimpleNamespace(fetch_commands=AsyncMock(return_value=[]),
                                                    get_command=local.get),
            http=SimpleNamespace(upsert_global_command=AsyncMock(), upsert_guild_command=AsyncMock()),
        )
        self.assertTrue(await publish_rank_commands(bot))
        self.assertEqual([call.args[1]["name"] for call in bot.http.upsert_global_command.call_args_list],
                         ["rank", "top", "give_level", "take_level"])
        bot.http.upsert_global_command.reset_mock()
        bot.tree.fetch_commands.return_value = [
            SimpleNamespace(name=name, to_dict=lambda name=name: payloads[name])
            for name in ("rank", "top", "give_level", "take_level")
        ] + [SimpleNamespace(name="unrelated", to_dict=lambda: {"name": "unrelated"})]
        self.assertTrue(await publish_rank_commands(bot))
        bot.http.upsert_global_command.assert_not_awaited()

    async def test_selective_registration_errors_do_not_crash_or_bulk_delete(self):
        bot = SimpleNamespace(tree=SimpleNamespace(fetch_commands=AsyncMock(side_effect=RuntimeError("offline"))))
        with self.assertLogs("PrimeRankCommands", level="ERROR"):
            self.assertFalse(await publish_rank_commands(bot))


if __name__ == "__main__":
    unittest.main()
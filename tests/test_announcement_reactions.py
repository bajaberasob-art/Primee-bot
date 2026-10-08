"""Real SQLite and fake Discord objects; no live Discord actions."""
import asyncio
import json
import io
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image
from aiohttp import web

import announcement_dashboard as api
import announcement_reactions as store
import announcement_images as images
import database
import web_server as ws
from cogs.announcement_reactions import AnnouncementReactions


GUILD = 100000000000000001
CHANNEL = "300000000000000001"
OTHER = "300000000000000002"
IDS = [str(400000000000000001 + index) for index in range(6)]


class AnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = database.DB_NAME
        database.DB_NAME = os.path.join(self.directory.name, "announcements.db")
        await database.init_db()
        self.perms = SimpleNamespace(
            view_channel=True, read_message_history=True, add_reactions=True,
            use_external_emojis=False,
            send_messages=True, attach_files=True,
        )
        self.channels = [
            SimpleNamespace(id=int(key), name="announcements", type=discord.ChannelType.text,
                            permissions_for=lambda member: self.perms, send=AsyncMock())
            for key in (CHANNEL, OTHER)
        ]
        self.emojis = [
            SimpleNamespace(id=int(key), name=f"custom_{index}", animated=False,
                            available=True, url=f"https://cdn.discordapp.com/emojis/{key}.png",
                            is_usable=lambda: True)
            for index, key in enumerate(IDS)
        ]
        self.guild = SimpleNamespace(
            id=GUILD, me=object(), emojis=self.emojis, channels=self.channels,
            get_channel=lambda key: next((ch for ch in self.channels if ch.id == key), None),
        )
        self.cog = AnnouncementReactions(None)

    async def asyncTearDown(self):
        await self.cog.cog_unload()
        database.DB_NAME = self.original_db
        self.directory.cleanup()

    def body(self, **changes):
        return {"channel_id": CHANNEL, "emoji_ids": IDS[:4], "enabled": True, "revision": 0, **changes}

    async def enable(self, **changes):
        previous = await store.get_settings(GUILD)
        body = self.body(revision=previous["revision"], **changes)
        config, revision = store.validate_changes(self.guild, body, previous)
        result = await store.save_settings(GUILD, config, revision)
        self.cog.update_config(result)
        return result

    def message(self, channel=CHANNEL, age=0):
        return SimpleNamespace(
            id=time.time_ns(), guild=self.guild,
            channel=self.guild.get_channel(int(channel)),
            created_at=datetime.fromtimestamp(time.time() - age, timezone.utc),
            add_reaction=AsyncMock(),
            content="نص إعلان جديد", attachments=[], stickers=[], embeds=[],
        )

    async def drain_one(self):
        message, revision = self.cog.queue.get_nowait()
        try:
            await self.cog.react(message, revision)
        finally:
            self.cog.queue.task_done()

    async def test_settings_are_persisted_and_rehydrated_after_restart(self):
        result = await self.enable(emoji_ids=IDS[:5])
        await self.cog.cog_unload()
        self.cog = AnnouncementReactions(None)
        await self.cog.cog_load()
        reloaded = self.cog.configs[GUILD]
        self.assertEqual(reloaded["channel_id"], CHANNEL)
        self.assertEqual(reloaded["emoji_ids"], IDS[:5])
        self.assertEqual(reloaded["activated_at"], result["activated_at"])
        self.assertIsInstance(reloaded["emoji_ids"][0], str)

    async def test_four_and_five_only_when_enabled(self):
        old = await store.get_settings(GUILD)
        for count in (0, 1, 3, 6):
            with self.assertRaises(ValueError):
                store.validate_changes(self.guild, self.body(emoji_ids=IDS[:count]), old)
        for count in (4, 5):
            store.validate_changes(self.guild, self.body(emoji_ids=IDS[:count]), old)
        store.validate_changes(self.guild, self.body(enabled=False, channel_id=None, emoji_ids=[]), old)

    async def test_unicode_duplicates_foreign_and_numeric_ids_rejected(self):
        old = await store.get_settings(GUILD)
        for ids in (["❤️"] * 4, [IDS[0]] * 4, IDS[:3] + ["900000000000000001"], [int(key) for key in IDS[:4]]):
            with self.assertRaises(ValueError):
                store.validate_changes(self.guild, self.body(emoji_ids=ids), old)
        with self.assertRaises(ValueError):
            store.validate_changes(self.guild, self.body(enabled=1), old)

    async def test_nonexistent_channel_and_unusable_emoji_rejected(self):
        old = await store.get_settings(GUILD)
        with self.assertRaises(ValueError):
            store.validate_changes(self.guild, self.body(channel_id="900000000000000001"), old)
        self.emojis[0].is_usable = lambda: False
        with self.assertRaises(ValueError):
            store.validate_changes(self.guild, self.body(), old)

    async def test_optimistic_conflict_never_overwrites_current_state(self):
        await self.enable()
        with self.assertRaises(store.SettingsConflict):
            await store.save_settings(GUILD, self.body(enabled=False), 0)
        self.assertTrue((await store.get_settings(GUILD))["enabled"])

    async def test_new_message_gets_exact_chosen_emojis_in_order(self):
        await self.enable(emoji_ids=IDS[:5])
        msg = self.message()
        with patch.object(store, "get_settings", side_effect=AssertionError("No per-message DB read")):
            await self.cog.on_message(msg)
            await self.drain_one()
        self.assertEqual([str(call.args[0].id) for call in msg.add_reaction.await_args_list], IDS[:5])

    async def test_other_channel_dm_and_pre_activation_messages_are_ignored(self):
        await self.enable()
        for msg in (self.message(OTHER), self.message(age=100), SimpleNamespace(guild=None)):
            await self.cog.on_message(msg)
        self.assertTrue(self.cog.queue.empty())

    async def test_disable_and_channel_change_cancel_queued_work(self):
        await self.enable()
        msg = self.message()
        await self.cog.on_message(msg)
        await self.enable(enabled=False)
        await self.drain_one()
        msg.add_reaction.assert_not_awaited()
        await self.enable()
        old_channel_msg = self.message()
        await self.cog.on_message(old_channel_msg)
        await self.enable(channel_id=OTHER)
        await self.drain_one()
        old_channel_msg.add_reaction.assert_not_awaited()
        new_channel_msg = self.message(OTHER)
        await self.cog.on_message(new_channel_msg)
        await self.drain_one()
        self.assertEqual(new_channel_msg.add_reaction.await_count, 4)

    async def test_late_cache_update_cannot_reenable_disabled_system(self):
        old = await self.enable()
        await self.enable(enabled=False)
        self.cog.update_config(old)
        self.assertNotIn(GUILD, self.cog.configs)

    async def test_late_failure_cannot_restore_errors_after_a_new_save(self):
        old = await self.enable_line(reactions=True)
        await self.enable(enabled=False, line_enabled=False)
        with patch.object(store, "record_error", new_callable=AsyncMock) as record:
            await self.cog.note_error(old, "old reaction failure")
            await self.cog.note_error(old, "old line failure", "line")
            record.assert_not_awaited()
        self.assertFalse(self.cog.errors)

    async def test_duplicate_gateway_event_is_not_queued_twice(self):
        await self.enable()
        msg = self.message()
        await self.cog.on_message(msg)
        await self.cog.on_message(msg)
        self.assertEqual(self.cog.queue.qsize(), 1)

    async def test_missing_permission_is_reported_without_discord_calls(self):
        config = await self.enable()
        self.perms.add_reactions = False
        self.assertEqual(store.inspect_configuration(self.guild, config)["code"], "missing_permissions")
        msg = self.message()
        await self.cog.on_message(msg)
        await self.drain_one()
        msg.add_reaction.assert_not_awaited()
        self.assertIn("add_reactions", (await store.get_settings(GUILD))["last_error"])

    async def test_external_permission_not_required_for_same_guild_emojis(self):
        config = await self.enable()
        self.assertEqual(store.inspect_configuration(self.guild, config)["code"], "ready")
        self.assertFalse(self.perms.use_external_emojis)

    async def test_deleted_emoji_safe_disable_remains_possible(self):
        config = await self.enable()
        self.emojis.pop(0)
        self.assertEqual(store.inspect_configuration(self.guild, config)["code"], "unavailable_emojis")
        await self.enable(enabled=False)
        self.assertFalse((await store.get_settings(GUILD))["enabled"])

    async def test_deleted_channel_safe_disable_remains_possible(self):
        config = await self.enable()
        self.channels.pop(0)
        self.assertEqual(store.inspect_configuration(self.guild, config)["code"], "missing_channel")
        await self.enable(enabled=False)

    async def test_discord_failure_is_persisted_and_next_message_recovers(self):
        await self.enable()
        msg = self.message()
        msg.add_reaction.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
        await self.cog.on_message(msg)
        await self.drain_one()
        self.assertIsNotNone((await store.get_settings(GUILD))["last_error"])
        next_message = self.message()
        await self.cog.on_message(next_message)
        await self.drain_one()
        self.assertIsNone((await store.get_settings(GUILD))["last_error"])
        self.assertEqual(next_message.add_reaction.await_count, 4)

    async def test_full_queue_is_bounded_and_errors_are_throttled(self):
        await self.enable()
        for _ in range(256):
            self.cog.queue.put_nowait((self.message(), 1))
        with patch.object(store, "record_error", new_callable=AsyncMock) as record:
            await self.cog.on_message(self.message())
            await self.cog.on_message(self.message())
            self.assertEqual(record.await_count, 1)
        self.assertEqual(self.cog.queue.qsize(), 256)
        self.assertEqual(self.cog.runtime_status(GUILD)["dropped"], 2)

    async def test_api_save_reload_and_conflict_reuse_security_gate(self):
        bot = SimpleNamespace(get_cog=lambda name: self.cog)
        request = SimpleNamespace()
        with patch.object(ws, "authorize", new=AsyncMock(return_value=({"id": "10"}, self.guild))) as authorize, \
             patch.object(ws, "read_json_body", new=AsyncMock(return_value=self.body())), \
             patch.object(ws, "bot_ref", bot):
            response = await api.api_save(request)
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.text)["config"]["emoji_ids"], IDS[:4])
            authorize.assert_awaited_with(request, write=True, management_tier="admin")
            response = await api.api_get(request)
            self.assertTrue(json.loads(response.text)["config"]["enabled"])
            authorize.assert_awaited_with(request, management_tier="admin")
            response = await api.api_save(request)
            self.assertEqual(response.status, 409)
        with patch.object(ws, "authorize", new=AsyncMock(side_effect=web.HTTPForbidden())):
            with self.assertRaises(web.HTTPForbidden):
                await api.api_save(request)

    async def test_worker_contains_errors_and_continues_processing(self):
        await self.enable()
        worker = asyncio.create_task(self.cog.worker())
        self.cog.workers = [worker]
        bad = self.message()
        bad.add_reaction.side_effect = RuntimeError("synthetic")
        good = self.message()
        await self.cog.on_message(bad)
        await self.cog.on_message(good)
        await asyncio.wait_for(self.cog.queue.join(), 3)
        self.assertEqual(good.add_reaction.await_count, 4)
        self.assertFalse(worker.done())
        self.assertEqual(self.cog.runtime_status(GUILD)["queue_size"], 0)

    def image_bytes(self, format="PNG", color="purple"):
        buffer = io.BytesIO()
        Image.new("RGB", (256, 16), color).save(buffer, format=format)
        return buffer.getvalue()

    async def enable_line(self, reactions=False, **changes):
        image = await images.upload(GUILD, self.image_bytes())
        return await self.enable(
            enabled=reactions, line_enabled=True, line_channel_ids=[CHANNEL],
            line_image_id=image["id"], **changes,
        )

    async def test_optional_second_channel_uses_same_emojis_and_rejects_duplicates(self):
        await self.enable(second_channel_id=OTHER)
        msg = self.message(OTHER)
        await self.cog.on_message(msg)
        await self.drain_one()
        self.assertEqual(msg.add_reaction.await_count, 4)
        old = await store.get_settings(GUILD)
        for second in (CHANNEL, "900000000000000001", 123):
            with self.assertRaises(ValueError):
                store.validate_changes(self.guild, self.body(second_channel_id=second), old)

    async def test_strict_text_only_excludes_captioned_media_empty_stickers_system_and_self_bot(self):
        await self.enable_line(reactions=True)
        self.cog.bot = SimpleNamespace(user=SimpleNamespace(id=500000000000000001))
        for changes in (
            {"content": ""}, {"content": "  \n"}, {"attachments": [object()]},
            {"stickers": [object()]}, {"type": discord.MessageType.pins_add},
            {"content": "", "embeds": [object()]},
            {"embeds": [SimpleNamespace(type="gifv")]},
            {"embeds": [SimpleNamespace(type="rich", image=SimpleNamespace(url="https://example.test/a.png"))]},
            {"author": self.cog.bot.user},
        ):
            msg = self.message()
            for key, value in changes.items():
                setattr(msg, key, value)
            await self.cog.on_message(msg)
        self.assertTrue(self.cog.queue.empty())
        # Other bots' actual text announcements remain supported.
        msg = self.message()
        msg.author = SimpleNamespace(id=500000000000000002, bot=True)
        await self.cog.on_message(msg)
        self.assertEqual(self.cog.queue.qsize(), 1)

    async def test_line_works_independently_and_uses_saved_image_without_mentions(self):
        await self.enable_line()
        captured = []

        async def send(**kwargs):
            captured.append(kwargs["file"].fp.getvalue())
            self.assertFalse(kwargs["allowed_mentions"].everyone)
            self.assertFalse(kwargs["allowed_mentions"].users)
        self.channels[0].send.side_effect = send
        msg = self.message()
        await self.cog.on_message(msg)
        await self.drain_one()
        msg.add_reaction.assert_not_awaited()
        self.assertEqual(captured, [self.image_bytes()])
        self.assertTrue(self.channels[0].send.await_args.kwargs["file"].fp.closed)
        await self.cog.on_message(self.message(OTHER))
        await self.cog.on_message(self.message(age=3600))
        self.assertTrue(self.cog.queue.empty())

    async def test_reactions_and_line_use_independent_channel_selections(self):
        image = await images.upload(GUILD, self.image_bytes())
        await self.enable(
            line_enabled=True, line_channel_ids=[OTHER], line_image_id=image["id"],
        )
        msg = self.message(CHANNEL)
        await self.cog.on_message(msg)
        await self.drain_one()
        self.assertEqual(msg.add_reaction.await_count, 4)
        self.channels[0].send.assert_not_awaited()
        other = self.message(OTHER)
        await self.cog.on_message(other)
        await self.drain_one()
        other.add_reaction.assert_not_awaited()
        self.assertEqual(self.channels[1].send.await_count, 1)

    async def test_line_queued_disable_prevents_send_and_late_cache_update_cannot_restore_it(self):
        old = await self.enable_line()
        msg = self.message()
        await self.cog.on_message(msg)
        await self.enable(enabled=False, line_enabled=False)
        self.cog.update_config(old)
        await self.drain_one()
        self.channels[0].send.assert_not_awaited()

    async def test_reaction_permission_failure_does_not_stop_line_and_recovery_is_independent(self):
        await self.enable_line(reactions=True)
        self.perms.add_reactions = False
        msg = self.message()
        await self.cog.on_message(msg)
        await self.drain_one()
        msg.add_reaction.assert_not_awaited()
        self.assertEqual(self.channels[0].send.await_count, 1)
        self.assertTrue((await store.get_settings(GUILD))["last_error"])
        self.perms.add_reactions = True
        self.perms.attach_files = False
        next_msg = self.message()
        await self.cog.on_message(next_msg)
        await self.drain_one()
        self.assertEqual(next_msg.add_reaction.await_count, 4)
        self.assertEqual(self.channels[0].send.await_count, 1)
        saved = await store.get_settings(GUILD)
        self.assertIsNone(saved["last_error"])
        self.assertIn("attach_files", saved["line_last_error"])
        self.perms.attach_files = True
        await self.cog.on_message(self.message())
        await self.drain_one()
        self.assertIsNone((await store.get_settings(GUILD))["line_last_error"])

    async def test_image_upload_is_draft_and_active_asset_is_not_pruned(self):
        old = await self.enable_line()
        for color in ("blue", "red", "green", "yellow"):
            await images.upload(GUILD, self.image_bytes(color=color))
        self.assertEqual((await store.get_settings(GUILD))["line_image_id"], old["line_image_id"])
        self.assertIsNotNone(await images.get_image(GUILD, old["line_image_id"], True))
        self.assertIsNone(await images.get_image(GUILD + 1, old["line_image_id"]))
        async with database.connect() as db:
            async with db.execute("SELECT COUNT(*) FROM announcement_line_images WHERE guild_id=?", (GUILD,)) as cursor:
                self.assertLessEqual((await cursor.fetchone())[0], 4)

    async def test_image_types_dimensions_and_size_are_validated(self):
        for format, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("GIF", "image/gif")):
            image = await images.upload(GUILD, self.image_bytes(format=format))
            self.assertEqual(image["mime"], mime)
        for payload in (b"<svg></svg>", b"not an image", b"x" * (images.MAX_IMAGE_BYTES + 1)):
            with self.assertRaises(ValueError):
                await images.upload(GUILD, payload)
        buffer = io.BytesIO()
        Image.new("RGB", (4097, 1)).save(buffer, "PNG")
        with self.assertRaises(ValueError):
            await images.upload(GUILD, buffer.getvalue())

    async def test_restart_rehydrates_line_image_channels_and_independent_activation(self):
        config = await self.enable_line()
        await self.cog.cog_unload()
        self.cog = AnnouncementReactions(None)
        await self.cog.cog_load()
        restored = self.cog.configs[GUILD]
        self.assertFalse(restored["enabled"])
        self.assertTrue(restored["line_enabled"])
        self.assertEqual(restored["line_activated_at"], config["line_activated_at"])
        self.assertEqual(restored["line_channel_ids"], [CHANNEL])
        msg = self.message()
        await self.cog.on_message(msg)
        await asyncio.wait_for(self.cog.queue.join(), 3)
        self.assertEqual(self.channels[0].send.await_count, 1)

    async def test_missing_or_foreign_image_rejected_through_authenticated_save(self):
        request = SimpleNamespace()
        foreign = await images.upload(GUILD + 1, self.image_bytes())
        for key in ("0" * 64, foreign["id"]):
            body = self.body(enabled=False, line_enabled=True, line_channel_ids=[CHANNEL], line_image_id=key)
            with patch.object(ws, "authorize", new=AsyncMock(return_value=({"id": "10"}, self.guild))), \
                 patch.object(ws, "read_json_body", new=AsyncMock(return_value=body)):
                response = await api.api_save(request)
                self.assertEqual(response.status, 400)
        self.assertEqual((await store.get_settings(GUILD))["revision"], 0)

    async def test_line_validation_requires_image_distinct_one_or_two_server_channels(self):
        old = await store.get_settings(GUILD)
        for extras in (
            {"line_image_id": None, "line_channel_ids": [CHANNEL]},
            {"line_image_id": "a" * 64, "line_channel_ids": []},
            {"line_image_id": "a" * 64, "line_channel_ids": [CHANNEL, CHANNEL]},
            {"line_image_id": "a" * 64, "line_channel_ids": [CHANNEL, OTHER, "300000000000000003"]},
            {"line_image_id": "a" * 64, "line_channel_ids": ["900000000000000001"]},
        ):
            with self.assertRaises(ValueError):
                store.validate_changes(self.guild, self.body(enabled=False, line_enabled=True, **extras), old)

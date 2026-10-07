import asyncio
import os
import unittest
from types import SimpleNamespace

import database
from cogs import moderation as moderation_module


class FakeGuild:
    id = 1550042204091715634


class FakeMessage:
    def __init__(self, content, user_id=42, mentions=None, roles=None, channel_id=101):
        self.content = content
        self.id = 1
        self.guild = FakeGuild()
        self.author = SimpleNamespace(
            id=user_id,
            bot=False,
            mention=f"<@{user_id}>",
            guild_permissions=SimpleNamespace(manage_messages=False),
            roles=roles or [],
        )
        self.mentions = mentions or []
        self.channel = SimpleNamespace(id=channel_id, mention="#general")


class FakeBot:
    user = SimpleNamespace(id=999)


class AutoModTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = "/tmp/test_moderation_automod.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()
        self.mod = moderation_module.Moderation(FakeBot())
        self.actions = []

        async def fake_action(message, reason, **kwargs):
            self.actions.append((reason, kwargs))

        self.mod._apply_violation = fake_action

        async def config(_guild_id):
            return {
                "anti_invites": True,
                "anti_links": True,
                "anti_spam": True,
                "anti_mass_mention": True,
                "anti_spam_max_messages": 5,
                "anti_spam_time_window_seconds": 4,
                "anti_spam_action": "timeout",
                "anti_spam_timeout_duration_minutes": 10,
                "anti_spam_ignored_role_ids": [],
                "anti_spam_ignored_channel_ids": [],
                "anti_mention_max_per_message": 3,
                "anti_mention_target_enabled": True,
                "anti_mention_target_max_repeats": 3,
                "anti_mention_target_time_window_seconds": 10,
                "anti_mention_action": "timeout",
                "anti_mention_timeout_duration_minutes": 5,
                "anti_mention_ignored_role_ids": [],
                "anti_mention_ignored_channel_ids": [],
                "banned_words_list": ["forbidden phrase"],
                "log_channel_id": None,
            }

        self.mod.moderation_settings = config

    async def test_dynamic_link_and_word_rules(self):
        await self.mod.on_message(FakeMessage("join https://discord.gg/abc123"))
        self.assertEqual(self.actions[0][0], "نشر رابط دعوة Discord ممنوع")

        self.actions.clear()
        await self.mod.on_message(FakeMessage("this contains forbidden phrase"))
        self.assertEqual(self.actions[0][0], "استخدام كلمة محظورة")

    async def test_mass_mentions_threshold_is_more_than_three(self):
        await self.mod.on_message(FakeMessage("hello", mentions=[object() for _ in range(3)]))
        self.assertEqual(self.actions, [])
        await self.mod.on_message(FakeMessage("hello", mentions=[object() for _ in range(4)]))
        self.assertEqual(self.actions[0][1]["timeout_minutes"], 5)
        self.assertEqual(self.actions[0][1]["action"], "timeout")

    async def test_spam_triggers_on_sixth_message_in_three_seconds(self):
        for index in range(5):
            await self.mod.on_message(FakeMessage(f"message {index}"))
        self.assertEqual(self.actions, [])
        await self.mod.on_message(FakeMessage("message 5"))
        self.assertEqual(self.actions[0][1]["timeout_minutes"], 10)

    async def test_targeted_mentions_trigger_after_repeated_messages(self):
        target = SimpleNamespace(id=900)
        for _ in range(3):
            await self.mod.on_message(FakeMessage("stop", mentions=[target]))
        self.assertEqual(self.actions, [])
        await self.mod.on_message(FakeMessage("stop", mentions=[target]))
        self.assertEqual(self.actions[0][1]["action"], "timeout")

    async def test_ignored_role_bypasses_spam_rule(self):
        async def ignored_config(_guild_id):
            return {
            **{
                "anti_invites": True,
                "anti_links": True,
                "anti_spam": True,
                "anti_mass_mention": True,
                "banned_words_list": [],
                "log_channel_id": None,
            },
            "anti_spam_max_messages": 1,
            "anti_spam_time_window_seconds": 60,
            "anti_spam_action": "ban",
            "anti_spam_timeout_duration_minutes": 10,
            "anti_spam_ignored_role_ids": ["777"],
            "anti_spam_ignored_channel_ids": [],
        }
        self.mod.moderation_settings = ignored_config
        for index in range(3):
            await self.mod.on_message(
                FakeMessage(f"message {index}", roles=[SimpleNamespace(id=777)])
            )
        self.assertEqual(self.actions, [])

    async def test_warning_helpers_use_database(self):
        await database.add_warning(42, FakeGuild.id, 999, "رابط مشبوه")
        records = await self.mod.get_recent_infractions(FakeGuild.id)
        self.assertEqual(len(records), 1)
        revoked = await self.mod.revoke_warning(records[0]["id"])
        self.assertEqual(revoked["user_id"], 42)
        self.assertEqual(await self.mod.get_recent_infractions(FakeGuild.id), [])


if __name__ == "__main__":
    unittest.main()
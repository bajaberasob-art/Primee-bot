import os
import unittest
from types import SimpleNamespace

import database
from cogs import engagement as engagement_module


class FakeInvite:
    def __init__(self, code, uses, inviter):
        self.code = code
        self.uses = uses
        self.inviter = inviter


class FakeChannel:
    id = 300000000000000001

    def __init__(self):
        self.sent = []

    async def send(self, content, **kwargs):
        self.sent.append((content, kwargs))
        return SimpleNamespace(id=800000000000000001)


class FakeGuild:
    id = 1550042204091715634
    name = "Test Guild"
    member_count = 42

    def __init__(self):
        self.channel = FakeChannel()
        self.system_channel = self.channel
        self._invite_reads = [
            [FakeInvite("abc", 4, SimpleNamespace(id=77, name="Inviter"))],
        ]

    async def invites(self):
        return self._invite_reads.pop(0)

    def get_member(self, user_id):
        return SimpleNamespace(id=user_id, display_name="Inviter")

    def get_channel(self, channel_id):
        return self.channel if channel_id == self.channel.id else None


class FakeBot:
    user = SimpleNamespace(id=999)

    def __init__(self, guild):
        self.guilds = [guild]

    def get_guild(self, guild_id):
        return self.guilds[0] if guild_id == self.guilds[0].id else None


class EngagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = "/tmp/test_engagement.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()
        self.guild = FakeGuild()
        self.cog = engagement_module.Engagement(FakeBot(self.guild))
        self.stats = []

        async def config(_guild_id):
            return {
                "welcome_channel_id": None,
                "welcome_message": "أهلاً {user} في {server} — {username} — {count} — {inviter}",
                "leave_message": "",
                "welcome_dm_enabled": False,
                "auto_role_id": None,
                "member_auto_role_id": None,
                "bot_auto_role_id": None,
                "verified_role_id": None,
                "unverified_role_id": None,
                "rules_channel_id": None,
            }

        self.cog.engagement_settings = config

        async def record(guild_id, inviter_id):
            self.stats.append((guild_id, inviter_id))
            return 1

        engagement_module.record_invite_use = record

    async def test_invite_delta_identifies_inviter_and_renders_welcome(self):
        self.cog.invite_cache[self.guild.id] = {
            "abc": {"uses": 3, "inviter_id": 77, "inviter_name": "Inviter"}
        }
        member = SimpleNamespace(
            id=88,
            bot=False,
            mention="<@88>",
            display_name="New Member",
            guild=self.guild,
            add_roles=self._noop,
            send=self._noop,
        )
        await self.cog.on_member_join(member)
        self.assertEqual(self.stats, [(self.guild.id, 77)])
        self.assertIn("<@88>", self.guild.channel.sent[0][0])
        self.assertIn("42nd", self.guild.channel.sent[0][0])
        self.assertIn("<@77>", self.guild.channel.sent[0][0])

    async def test_invite_stats_and_rules_agreement_are_persistent(self):
        self.assertEqual(await database.record_invite_use(1, 77), 1)
        self.assertEqual(await database.record_invite_use(1, 77), 2)
        agreed_at = await database.record_rules_agreement(1, 88, 123)
        self.assertTrue(agreed_at)
        async with database.connect() as db:
            async with db.execute(
                "SELECT verified_role_id FROM rules_agreements WHERE guild_id = 1 AND user_id = 88"
            ) as cur:
                self.assertEqual((await cur.fetchone())[0], 123)

    async def test_test_welcome_helper_uses_safe_preview_mentions(self):
        result = await self.cog.send_test_welcome(
            self.guild.id,
            self.guild.channel.id,
            {"username": "Preview"},
        )
        self.assertTrue(result["ok"])
        content, kwargs = self.guild.channel.sent[-1]
        self.assertIn("Preview", content)
        self.assertIsNotNone(kwargs["allowed_mentions"])

    async def _noop(self, *args, **kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
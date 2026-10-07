import os
import unittest

import discord
from discord.ext import commands

import database
from cogs.chat_jail import ChatJailCog
from cogs.command_meta import MASTER_COMMANDS_REGISTRY
from cogs.moderation import Moderation
from cogs.sanctions_voice import SanctionsVoiceCog


def command_names(cog_type):
    return [
        value.name
        for value in cog_type.__dict__.values()
        if getattr(value, "name", None)
        and value.__class__.__name__ == "Command"
    ]


class ChatJailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = f"/tmp/prime_chat_jail_{os.getpid()}.db"
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(database.DB_NAME + suffix)
            except FileNotFoundError:
                pass
        database.COMMAND_CACHE.clear()
        await database.init_db()

    async def asyncTearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(database.DB_NAME + suffix)
            except FileNotFoundError:
                pass

    def test_chat_jail_commands_are_unique_and_do_not_duplicate_moderation(self):
        new_names = command_names(ChatJailCog)
        moderation_names = command_names(Moderation)
        self.assertEqual(len(new_names), 27)
        self.assertEqual(len(new_names), len(set(new_names)))
        self.assertNotIn("clear", new_names)
        self.assertNotIn("slowmode", new_names)
        self.assertIn("clear", moderation_names)
        self.assertIn("slowmode", moderation_names)

    async def test_step_three_surface_has_29_names_and_stays_disjoint_from_sanctions(self):
        intents = discord.Intents.none()
        bot = commands.Bot(command_prefix="!", intents=intents)
        await bot.add_cog(Moderation(bot))
        await bot.add_cog(ChatJailCog(bot))
        registered_names = [command.name for command in bot.tree.walk_commands()]
        step_three_names = set(command_names(ChatJailCog)) | {"clear", "slowmode"}
        self.assertEqual(len(step_three_names), 29)
        self.assertEqual(len(step_three_names), len(set(step_three_names)))
        self.assertEqual(
            set(command.name for command in bot.tree.walk_commands())
            & step_three_names,
            step_three_names,
        )
        sanctions_names = command_names(SanctionsVoiceCog)
        self.assertEqual(len(sanctions_names), 25)
        self.assertEqual(len(set(sanctions_names)), 25)
        self.assertTrue(step_three_names.isdisjoint(sanctions_names))
        await bot.close()

    def test_step_three_commands_have_central_metadata(self):
        expected = set(command_names(ChatJailCog)) | {"clear", "slowmode"}
        self.assertTrue(expected.issubset(MASTER_COMMANDS_REGISTRY))
        self.assertEqual(
            MASTER_COMMANDS_REGISTRY["emergency"]["default_aliases"][0],
            "panic",
        )

    async def test_jail_and_channel_restrictions_round_trip(self):
        await database.jail_user(700, 55, 99, "[123, 456]", "solo", 8080)
        jailed = await database.get_jailed_user(700, 55)
        self.assertEqual(jailed["saved_roles"], "[123, 456]")
        self.assertEqual(jailed["private_channel_id"], 8080)

        await database.add_channel_restriction(700, 8080, 55, "write_block")
        await database.add_channel_restriction(700, 8080, 55, "hide_member")
        restrictions = await database.get_channel_restrictions(700, 8080)
        self.assertEqual(
            {row["restriction_type"] for row in restrictions},
            {"write_block", "hide_member"},
        )
        await database.add_channel_restriction(700, 8080, 55, "write_block")
        self.assertTrue(
            await database.remove_channel_restriction(700, 8080, 55, "write_block")
        )
        self.assertFalse(
            await database.remove_channel_restriction(700, 8080, 55, "write_block")
        )

        record = await database.unjail_user(700, 55)
        self.assertEqual(record["jail_type"], "solo")
        self.assertIsNone(await database.get_jailed_user(700, 55))
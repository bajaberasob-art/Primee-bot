import datetime as dt
import os
import unittest

import database
from cogs.sanctions_voice import SanctionsVoiceCog, parse_duration, target_ids


class SanctionsVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = f"/tmp/prime_sanctions_voice_{os.getpid()}.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()

    async def asyncTearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(database.DB_NAME + suffix)
            except FileNotFoundError:
                pass

    def test_duration_and_target_parsing(self):
        self.assertEqual(parse_duration("10m"), dt.timedelta(minutes=10))
        self.assertEqual(parse_duration("2h"), dt.timedelta(hours=2))
        self.assertIsNone(parse_duration("0m"))
        self.assertIsNone(parse_duration("ten minutes"))
        self.assertEqual(
            target_ids("<@123> 456 456 <@!789>"),
            [123, 456, 789],
        )

    async def test_timed_voice_and_text_penalties_are_persistent(self):
        await database.add_temp_ban(700, 55, "2000-01-01 00:00:00")
        expired = await database.get_expired_temp_bans()
        self.assertEqual([(row["guild_id"], row["user_id"]) for row in expired], [(700, 55)])
        self.assertTrue(await database.remove_temp_ban(700, 55))
        self.assertFalse(await database.remove_temp_ban(700, 55))

        await database.add_voice_ban(700, 55, 99)
        self.assertTrue(await database.is_voice_banned(700, 55))
        self.assertTrue(await database.remove_voice_ban(700, 55))
        self.assertFalse(await database.is_voice_banned(700, 55))

        await database.add_text_mute(700, 55, 99)
        self.assertEqual((await database.get_text_mutes(700))[0]["user_id"], 55)
        self.assertTrue(await database.remove_text_mute(700, 55))

    def test_cog_registers_only_unique_new_commands(self):
        names = [
            value.name
            for value in SanctionsVoiceCog.__dict__.values()
            if getattr(value, "name", None)
            and value.__class__.__name__ == "Command"
        ]
        self.assertEqual(len(names), 25)
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("timeout", names)
        self.assertNotIn("untimeout", names)
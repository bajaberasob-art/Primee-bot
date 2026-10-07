import datetime as dt
import os
import unittest

from cogs.admin_advanced import AdminAdvancedCog
from cogs.chat_jail import ChatJailCog
from cogs.command_meta import MASTER_COMMANDS_REGISTRY
from cogs.moderation import Moderation
import database


def command_names(cog_type):
    return [
        value.name
        for value in cog_type.__dict__.values()
        if getattr(value, "name", None)
        and value.__class__.__name__ == "Command"
    ]


class AdminAdvancedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = f"/tmp/prime_admin_advanced_{os.getpid()}.db"
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(database.DB_NAME + suffix)
            except FileNotFoundError:
                pass
        await database.init_db()

    async def asyncTearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(database.DB_NAME + suffix)
            except FileNotFoundError:
                pass

    def test_step_four_commands_are_unique_and_non_overlapping(self):
        names = command_names(AdminAdvancedCog)
        self.assertEqual(len(names), 26)
        self.assertEqual(len(names), len(set(names)))
        existing = set(command_names(Moderation)) | set(command_names(ChatJailCog))
        self.assertTrue(set(names).isdisjoint(existing))
        expected_metadata = set(names) | {"warn", "warnings"}
        self.assertTrue(expected_metadata.issubset(MASTER_COMMANDS_REGISTRY))
        self.assertEqual(MASTER_COMMANDS_REGISTRY["dossier"]["default_aliases"][0], "mod_log")
        self.assertIn("لقب", MASTER_COMMANDS_REGISTRY["setnick"]["default_aliases"])
        self.assertIn("نداء", MASTER_COMMANDS_REGISTRY["summon"]["default_aliases"])

    async def test_step_four_records_round_trip_and_expiry_query(self):
        warning_id = await database.add_member_warning(700, 55, 99, "مخالفة")
        self.assertEqual((await database.get_member_warnings(700, 55))[0]["id"], warning_id)
        self.assertTrue(await database.delete_member_warning(warning_id, 700))

        note_id = await database.add_mod_note(700, 55, 99, "متابعة خاصة")
        self.assertEqual((await database.get_mod_notes(700, 55))[0]["note_text"], "متابعة خاصة")
        self.assertTrue(await database.delete_mod_note(note_id, 700))

        entry_id = await database.add_temp_role(
            700,
            55,
            8080,
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1),
        )
        expired = await database.get_expired_temp_roles()
        self.assertEqual(expired[0]["id"], entry_id)
        self.assertTrue(await database.remove_temp_role_entry(entry_id))

        self.assertEqual(await database.adjust_event_points(700, 55, 10), 10)
        self.assertEqual(await database.adjust_event_points(700, 55, -3), 7)
        self.assertEqual((await database.get_event_leaderboard(700))[0]["points"], 7)
        self.assertEqual(await database.reset_event_points(700), 1)
        self.assertEqual((await database.get_event_leaderboard(700))[0]["points"], 0)
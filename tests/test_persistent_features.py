import asyncio
import os
import unittest

import database
from cogs.economy import LiveGiveaway
from cogs.tournaments import TournamentEntryView


class PersistentFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = f"/tmp/prime_persistent_features_{os.getpid()}.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()

    async def test_giveaway_entries_survive_until_completion(self):
        giveaway_id = await database.create_giveaway(
            700,
            300,
            "جائزة",
            "2099-01-01 00:00:00",
            99,
        )
        await database.set_giveaway_message(giveaway_id, 800)
        self.assertTrue(await database.add_giveaway_entry(giveaway_id, 55))
        self.assertFalse(await database.add_giveaway_entry(giveaway_id, 55))
        self.assertEqual(await database.get_giveaway_entries(giveaway_id), [55])
        self.assertEqual(len(await database.get_open_giveaways()), 1)
        self.assertTrue(await database.complete_giveaway(giveaway_id))
        self.assertFalse(await database.complete_giveaway(giveaway_id))
        self.assertEqual(await database.get_open_giveaways(), [])

    async def test_tournament_registration_is_durable_and_atomic(self):
        tournament_id = await database.create_tournament(700, 300, "بطولة", 2, 99)
        await database.set_tournament_message(tournament_id, 801)
        self.assertEqual(
            await database.add_tournament_entry(tournament_id, 11),
            (True, 1, 2),
        )
        self.assertEqual(
            await database.add_tournament_entry(tournament_id, 22),
            (True, 2, 2),
        )
        self.assertEqual(
            await database.add_tournament_entry(tournament_id, 33),
            (False, 2, 2),
        )
        self.assertEqual(
            await database.get_tournament_entries(tournament_id),
            [11, 22],
        )
        started = await database.start_tournament(tournament_id)
        self.assertEqual(started["entries"], [11, 22])
        self.assertIsNone(await database.start_tournament(tournament_id))

    async def test_economy_transfer_is_atomic_and_logged(self):
        await database.get_or_create_user(1, 700)
        await database.get_or_create_user(2, 700)
        await database.update_balance(1, 700, 100)
        self.assertTrue(await database.transfer_balance(700, 1, 2, 150))
        self.assertFalse(await database.transfer_balance(700, 1, 2, 1000))
        sender = await database.get_or_create_user(1, 700)
        receiver = await database.get_or_create_user(2, 700)
        self.assertEqual(sender["balance"], 50)
        self.assertEqual(receiver["balance"], 250)
        async with database.connect() as db:
            async with db.execute(
                "SELECT amount FROM economy_transactions WHERE guild_id = 700"
            ) as cursor:
                self.assertEqual((await cursor.fetchall())[0][0], 150)

    async def test_reminder_can_be_cancelled_before_due(self):
        reminder_id = await database.create_reminder(
            700,
            55,
            300,
            "اختبار",
            "2099-01-01 00:00:00",
        )
        self.assertEqual(len(await database.get_user_reminders(700, 55)), 1)
        self.assertTrue(await database.cancel_reminder(700, 55, reminder_id))
        self.assertEqual(await database.get_user_reminders(700, 55), [])
        self.assertFalse(await database.cancel_reminder(700, 55, reminder_id))

    async def test_step_five_user_reminder_is_due_and_deletable(self):
        reminder_id = await database.add_reminder(
            700,
            55,
            300,
            "تذكير جديد",
            "2000-01-01 00:00:00",
        )
        due = await database.get_due_user_reminders("2099-01-01 00:00:00")
        self.assertEqual(
            [(row["id"], row["reminder_text"]) for row in due],
            [(reminder_id, "تذكير جديد")],
        )
        self.assertTrue(await database.delete_reminder(reminder_id))
        self.assertFalse(await database.delete_reminder(reminder_id))

    async def test_interactive_views_have_restart_safe_unique_ids(self):
        giveaway = LiveGiveaway("جائزة", 41)
        tournament = TournamentEntryView(42, "بطولة", 8)
        self.assertEqual(
            [item.custom_id for item in giveaway.children],
            ["giveaway:enter:41"],
        )
        self.assertEqual(
            [item.custom_id for item in tournament.children],
            ["tournament:join:42", "tournament:start:42"],
        )

    async def test_concurrent_user_creation_and_wallet_updates_are_atomic(self):
        users = await asyncio.gather(
            *(database.get_or_create_user(77, 700) for _ in range(12))
        )
        self.assertTrue(all(user["balance"] == 100 for user in users))

        results = await asyncio.gather(
            *(database.update_balance(77, 700, 15) for _ in range(20))
        )
        user = await database.get_or_create_user(77, 700)
        self.assertEqual(user["balance"], 400)
        self.assertEqual(len(results), 20)

    async def test_daily_reward_is_single_use_and_creates_new_account(self):
        claimed = await asyncio.gather(
            *(
                database.claim_daily_reward(88, 700, "2099-01-01", 50)
                for _ in range(8)
            )
        )
        self.assertEqual(sum(claimed), 1)
        user = await database.get_or_create_user(88, 700)
        self.assertEqual(user["balance"], 150)
        self.assertEqual(user["last_daily"], "2099-01-01")
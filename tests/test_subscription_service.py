"""Phase 5 subscription lifecycle, XP, outbox, and Discord command tests."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

import database
import subscription_service
from cogs.subscription_commands import (
    SubscriptionCommands,
    publish_subscription_commands,
)


class SubscriptionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "subscriptions.db")
        await database.init_db()
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def asyncTearDown(self):
        database.DB_NAME = self.original_db
        self.directory.cleanup()

    async def test_create_is_atomic_idempotent_and_uses_existing_text_xp(self):
        await database.update_user_level(
            700,
            55,
            {
                "text_xp": 255,
                "text_level": 2,
                "total_messages": 42,
                "current_streak": 8,
                "last_daily_claim": "2025-12-31",
            },
        )
        with patch("subscription_service.secrets.randbelow", return_value=7):
            first = await subscription_service.create_subscription(
                700,
                55,
                30,
                idempotency_key="discord:grant:one",
                actor_id=99,
                now=self.now,
            )
            retry = await subscription_service.create_subscription(
                700,
                55,
                30,
                idempotency_key="discord:grant:one",
                actor_id=99,
                now=self.now + timedelta(minutes=1),
            )

        self.assertEqual(first["status"], "created")
        self.assertEqual(first["xp"]["amount"], 117)  # 100 + 5*2 + 7
        self.assertEqual(retry["status"], "duplicate")
        self.assertTrue(retry["idempotent"])
        self.assertEqual(retry["xp"]["transaction_id"], first["xp"]["transaction_id"])
        self.assertEqual(
            retry["subscription"]["subscription_id"],
            first["subscription"]["subscription_id"],
        )

        level = await database.get_user_level(700, 55)
        self.assertEqual(level["text_xp"], 372)
        self.assertEqual(level["text_level"], 2)
        self.assertEqual(level["total_messages"], 42)
        self.assertEqual(level["current_streak"], 8)
        self.assertEqual(level["last_daily_claim"], "2025-12-31")
        async with database.connect() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM subscription_xp_transactions"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 1)
            async with db.execute(
                "SELECT text_xp FROM level_xp_daily WHERE guild_id = 700 AND user_id = 55"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 117)
            async with db.execute(
                "SELECT COUNT(*) FROM level_xp_events WHERE guild_id = 700 AND user_id = 55"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 1)

        with self.assertRaisesRegex(ValueError, "different request"):
            await subscription_service.create_subscription(
                700,
                56,
                30,
                idempotency_key="discord:grant:one",
                now=self.now,
            )

    async def test_create_rolls_back_all_writes_if_existing_xp_ledger_fails(self):
        await database.update_user_level(
            706,
            60,
            {
                "text_xp": 255,
                "text_level": 2,
                "total_messages": 9,
                "current_streak": 4,
            },
        )
        with patch(
            "database._record_level_daily_xp",
            side_effect=RuntimeError("simulated XP ledger failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated XP ledger failure"):
                await subscription_service.create_subscription(
                    706,
                    60,
                    30,
                    idempotency_key="create-atomic-failure",
                    now=self.now,
                )

        level = await database.get_user_level(706, 60)
        self.assertEqual(
            (level["text_xp"], level["total_messages"], level["current_streak"]),
            (255, 9, 4),
        )
        async with database.connect() as db:
            for table in (
                "subscriptions",
                "subscription_history",
                "subscription_xp_transactions",
                "subscription_notifications",
            ):
                async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                    self.assertEqual((await cursor.fetchone())[0], 0, table)

    async def test_concurrent_retries_create_one_subscription_and_one_xp_credit(self):
        with patch("subscription_service.secrets.randbelow", return_value=3):
            results = await asyncio.gather(
                *[
                    subscription_service.create_subscription(
                        707,
                        61,
                        7,
                        idempotency_key="concurrent-create-61",
                        now=self.now,
                    )
                    for _ in range(4)
                ]
            )
        self.assertEqual(
            sorted(result["status"] for result in results),
            ["created", "duplicate", "duplicate", "duplicate"],
        )
        self.assertEqual(len({result["subscription"]["subscription_id"] for result in results}), 1)
        async with database.connect() as db:
            for table in (
                "subscriptions",
                "subscription_history",
                "subscription_xp_transactions",
            ):
                async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                    self.assertEqual((await cursor.fetchone())[0], 1, table)

    async def test_renewal_cancel_and_analytics_keep_ledgers_consistent(self):
        with patch("subscription_service.secrets.randbelow", return_value=30):
            created = await subscription_service.create_subscription(
                701,
                56,
                2,
                idempotency_key="create-56",
                now=self.now,
            )
            renewed = await subscription_service.renew_subscription(
                701,
                created["subscription"]["subscription_id"],
                3,
                idempotency_key="renew-56",
                actor_id=99,
                now=self.now + timedelta(days=1),
            )
            duplicate = await subscription_service.renew_subscription(
                701,
                created["subscription"]["subscription_id"],
                3,
                idempotency_key="renew-56",
                actor_id=99,
                now=self.now + timedelta(days=1, minutes=1),
            )

        self.assertEqual(renewed["xp"]["amount"], 185)  # 150 + 5*1 + 30
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(
            duplicate["renewal"]["transaction_id"],
            renewed["renewal"]["transaction_id"],
        )
        self.assertEqual(
            renewed["renewal"]["new_end_date"],
            (
                self.now + timedelta(days=5)
            ).isoformat(),
        )

        cancelled = await subscription_service.cancel_subscription(
            701,
            created["subscription"]["subscription_id"],
            idempotency_key="cancel-56",
            actor_id=99,
            reason="admin request",
            now=self.now + timedelta(days=1, hours=1),
        )
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaisesRegex(ValueError, "cannot be renewed"):
            await subscription_service.renew_subscription(
                701,
                created["subscription"]["subscription_id"],
                2,
                idempotency_key="renew-cancelled",
                now=self.now + timedelta(days=2),
            )
        stats = await subscription_service.get_subscription_analytics(
            701, now=self.now + timedelta(days=1, hours=2)
        )
        self.assertEqual(stats["new_subscriptions"], 1)
        self.assertEqual(stats["renewals"], 1)
        self.assertEqual(stats["total_subscription_xp"], 315)
        self.assertEqual(stats["active_subscriptions"], 0)

    async def test_expiration_reminders_are_deduplicated_and_only_send_nearest_due(self):
        created = await subscription_service.create_subscription(
            702,
            57,
            10,
            idempotency_key="create-57",
            now=self.now,
        )
        end_date = datetime.fromisoformat(created["subscription"]["end_date"])

        # At 48 hours remaining, only the 72-hour reminder is still relevant.
        reminder_time = end_date - timedelta(hours=48)
        first_pass = await subscription_service.process_due_subscriptions(reminder_time)
        second_pass = await subscription_service.process_due_subscriptions(reminder_time)
        self.assertEqual(first_pass["reminders_queued"], 1)
        self.assertEqual(second_pass["reminders_queued"], 0)
        async with database.connect() as db:
            async with db.execute(
                """
                SELECT reminder_hours FROM subscription_notifications
                WHERE event_type = 'expiring'
                """
            ) as cursor:
                self.assertEqual((await cursor.fetchall())[0][0], 72)

        expired = await subscription_service.process_due_subscriptions(
            end_date + timedelta(seconds=1)
        )
        repeated_expiry = await subscription_service.process_due_subscriptions(
            end_date + timedelta(seconds=2)
        )
        self.assertEqual(expired["expired"], 1)
        self.assertEqual(repeated_expiry["expired"], 0)
        row = await subscription_service.get_subscription(
            702,
            created["subscription"]["subscription_id"],
            now=end_date + timedelta(seconds=3),
        )
        self.assertEqual(row["status"], "expired")
        history = await subscription_service.get_subscription_history(
            702, created["subscription"]["subscription_id"]
        )
        self.assertEqual(
            [event["event_type"] for event in history].count("expired"), 1
        )

    async def test_settings_are_partial_and_templates_are_safe(self):
        original = await subscription_service.update_subscription_settings(
            703,
            {
                "xp_enabled": False,
                "notifications_enabled": False,
                "new_xp_base": 210,
                "templates": {"created": "Welcome {user} on {server}"},
            },
        )
        updated = await subscription_service.update_subscription_settings(
            703, {"notifications_enabled": True}
        )
        self.assertFalse(original["xp_enabled"])
        self.assertEqual(original["new_xp_base"], 210)
        self.assertTrue(updated["notifications_enabled"])
        self.assertFalse(updated["xp_enabled"])
        self.assertEqual(
            updated["notification_templates"]["created"],
            "Welcome {user} on {server}",
        )
        with self.assertRaisesRegex(ValueError, "formatting options"):
            await subscription_service.update_subscription_settings(
                703, {"templates": {"created": "{user:>999999999}"}}
            )
        with self.assertRaisesRegex(ValueError, "unsupported template fields"):
            await subscription_service.update_subscription_settings(
                703, {"templates": {"created": "{user.__class__}"}}
            )

    async def test_stale_notification_claim_recovers_and_dm_failure_is_recorded(self):
        created = await subscription_service.create_subscription(
            704,
            58,
            1,
            idempotency_key="create-58",
            now=self.now,
        )
        claimed = await subscription_service.claim_due_notifications(now=self.now)
        self.assertEqual(len(claimed), 1)
        notification = claimed[0]
        async with database.connect() as db:
            await db.execute(
                """
                UPDATE subscription_notifications SET claimed_at = ?
                WHERE notification_id = ?
                """,
                (
                    (self.now - timedelta(minutes=20)).isoformat(),
                    notification["notification_id"],
                ),
            )
            await db.commit()

        user = SimpleNamespace(
            send=AsyncMock(
                side_effect=discord.Forbidden(
                    SimpleNamespace(status=403, reason="Forbidden"),
                    "DMs are closed",
                )
            )
        )
        fake_bot = SimpleNamespace(
            get_user=lambda user_id: user,
            fetch_user=AsyncMock(),
            get_guild=lambda guild_id: SimpleNamespace(name="PRIME"),
        )
        cog = SubscriptionCommands(fake_bot)
        await cog._deliver_notification(notification)

        async with database.connect() as db:
            async with db.execute(
                """
                SELECT status, last_error FROM subscription_notifications
                WHERE notification_id = ?
                """,
                (notification["notification_id"],),
            ) as cursor:
                row = await cursor.fetchone()
        self.assertEqual(row[0], "failed")
        self.assertIn("Forbidden", row[1])
        user.send.assert_awaited_once()
        self.assertIsNotNone(created["subscription"]["subscription_id"])

    async def test_expired_notification_is_suppressed_if_renewed_before_delivery(self):
        created = await subscription_service.create_subscription(
            705,
            59,
            1,
            idempotency_key="create-59",
            now=self.now,
        )
        end_date = datetime.fromisoformat(created["subscription"]["end_date"])
        await subscription_service.process_due_subscriptions(
            end_date + timedelta(seconds=1)
        )
        claimed = await subscription_service.claim_due_notifications(
            now=end_date + timedelta(seconds=2)
        )
        expiry_notice = next(
            item for item in claimed if item["event_type"] == "expired"
        )
        renewed = await subscription_service.renew_subscription(
            705,
            created["subscription"]["subscription_id"],
            5,
            idempotency_key="renew-59-after-expiry",
            now=end_date + timedelta(minutes=1),
        )
        self.assertEqual(renewed["subscription"]["status"], "active")
        self.assertFalse(
            await subscription_service.notification_is_current(
                expiry_notice["notification_id"],
                now=end_date + timedelta(minutes=2),
            )
        )

    async def test_command_publisher_only_upserts_subscription_group(self):
        payload = {
            "name": "subscription",
            "description": "Manage subscriptions",
            "type": 1,
            "options": [],
        }
        command = Mock()
        command.to_dict.return_value = payload
        remote = Mock()
        remote.name = "other-command"
        remote.to_dict.return_value = {
            "name": "other-command",
            "description": "Leave it unchanged",
            "type": 1,
            "options": [],
        }
        tree = SimpleNamespace(
            get_command=Mock(return_value=command),
            fetch_commands=AsyncMock(return_value=[remote]),
        )
        http = SimpleNamespace(upsert_global_command=AsyncMock())
        bot = SimpleNamespace(
            tree=tree,
            http=http,
            application_id=123,
        )

        self.assertTrue(await publish_subscription_commands(bot))
        http.upsert_global_command.assert_awaited_once_with(123, payload)

        matching = Mock()
        matching.name = "subscription"
        matching.to_dict.return_value = payload
        tree.fetch_commands.return_value = [remote, matching]
        http.upsert_global_command.reset_mock()
        self.assertTrue(await publish_subscription_commands(bot))
        http.upsert_global_command.assert_not_awaited()
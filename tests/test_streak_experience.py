import asyncio
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import discord
from PIL import Image

import database
from cogs.card_generator import (
    _streak_card_theme,
    format_streak_days_remaining,
    generate_streak_card,
)
from cogs.levels import Levels
from streak_experience import build_streak_context, format_time_remaining


RIYADH = ZoneInfo("Asia/Riyadh")


class StreakExperienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "streak-experience.db")
        await database.init_db()
        self.guild_id = 880
        self.user_id = 123
        self.channel_id = 456
        self.bot = SimpleNamespace(dispatch=Mock())
        self.cog = Levels(self.bot)
        await database.update_level_settings(
            self.guild_id,
            {"streak_channel_id": self.channel_id, "text_xp_enabled": False},
        )
        self.guild = SimpleNamespace(id=self.guild_id, name="PRIME")
        self.member = SimpleNamespace(
            id=self.user_id,
            bot=False,
            roles=[],
            name="streak-user",
            display_name="Streak User",
            mention=f"<@{self.user_id}>",
        )

    async def asyncTearDown(self):
        pending = list(self.cog._streak_tasks)
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.cog.cog_unload()
        database.DB_NAME = self.previous_db
        self.directory.cleanup()

    def make_message(self, at=None, *, channel=None, author=None):
        return SimpleNamespace(
            id=800,
            guild=self.guild,
            author=author or self.member,
            channel=channel or SimpleNamespace(id=self.channel_id),
            created_at=at or datetime(2026, 10, 1, 12, tzinfo=RIYADH),
            type=discord.MessageType.default,
            webhook_id=None,
            content="hello",
        )

    def test_remaining_days_use_arabic_singular_dual_and_plural_forms(self):
        self.assertEqual(format_streak_days_remaining(1), "يوم واحد")
        self.assertEqual(format_streak_days_remaining(2), "يومين")
        self.assertEqual(format_streak_days_remaining(3), "3 أيام")
        self.assertEqual(format_streak_days_remaining(11), "11 يومًا")

    async def test_default_stages_are_data_driven_and_extend_past_365_days(self):
        stages = await database.get_streak_stages()
        self.assertEqual(
            [stage["threshold"] for stage in stages],
            [1, 3, 7, 14, 30, 100, 365],
        )
        self.assertTrue(all(stage["image"] is None for stage in stages))
        self.assertEqual(await database.get_streak_milestones(), [])

        added = await database.upsert_streak_stage(
            {
                "stage_key": "millennium",
                "threshold": 1000,
                "name": "الألفية",
                "message": "وصل {user} إلى {streak} يومًا.",
                "image": None,
                "color": "#88CCFF",
                "reaction": "💠",
                "description": "ألف يوم متواصل.",
                "glow": 100,
                "particle": "neon",
            }
        )
        self.assertEqual(added["message"], "وصل {user} إلى {streak} يومًا.")

        member = SimpleNamespace(name="member", display_name="Member", mention="@member")
        guild = SimpleNamespace(name="PRIME")
        context_365 = build_streak_context(
            member,
            guild,
            {"current_streak": 365, "best_streak": 365},
            await database.get_streak_stages(),
            {"server_rank": 2, "global_rank": 8},
            datetime(2026, 10, 4, 12, tzinfo=RIYADH),
        )
        self.assertEqual(context_365["stage"]["stage_key"], "eternal")
        self.assertEqual(context_365["next_stage"]["stage_key"], "millennium")
        self.assertEqual(context_365["remaining"], 635)

        context_1000 = build_streak_context(
            member,
            guild,
            {"current_streak": 1000, "best_streak": 1000},
            await database.get_streak_stages(),
            {"server_rank": 1, "global_rank": 1},
        )
        self.assertEqual(context_1000["stage"]["stage_key"], "millennium")
        self.assertIsNone(context_1000["next_stage"])
        self.assertEqual(context_1000["progress"], 1.0)

    async def test_reset_countdown_uses_riyadh_and_configured_reset_time(self):
        self.assertEqual(
            format_time_remaining(datetime(2026, 10, 1, 23, 59, 30, tzinfo=RIYADH)),
            "0س 1د",
        )
        self.assertEqual(
            format_time_remaining(
                datetime(2026, 10, 1, 4, 0, tzinfo=RIYADH), "04:30"
            ),
            "0س 30د",
        )
        self.assertEqual(
            format_time_remaining(
                datetime(2026, 10, 1, 4, 30, tzinfo=RIYADH), "04:30"
            ),
            "24س 0د",
        )

        settings = await database.get_level_settings(self.guild_id)
        settings["day_reset_time"] = "04:30"
        with patch(
            "cogs.levels.database.get_level_settings",
            new=AsyncMock(return_value=settings),
        ):
            _, _, context, _ = await self.cog._build_streak_context(
                self.make_message(),
                {"current_streak": 1, "best_streak": 1},
                now=datetime(2026, 10, 1, 4, 0, tzinfo=RIYADH),
            )
        self.assertEqual(context["time_remaining"], "0س 30د")

    async def test_stage_and_milestone_events_are_once_per_member(self):
        await database.upsert_streak_milestone(
            {
                "threshold": 7,
                "message": "وصل {user} إلى {threshold}.",
                "image": None,
                "reaction": "🎉",
            }
        )
        first = await database.record_streak_experience_events(
            self.guild_id, self.user_id, 6, 7, "2026-10-07"
        )
        retry_after_restart = await database.record_streak_experience_events(
            self.guild_id, self.user_id, 6, 7, "2026-10-07"
        )
        self.assertEqual([stage["stage_key"] for stage in first["stages"]], ["flame"])
        self.assertEqual([item["threshold"] for item in first["milestones"]], [7])
        self.assertEqual(retry_after_restart, {"stages": [], "milestones": []})

    async def test_live_claim_crosses_custom_1000_day_stage_without_changing_math(self):
        await database.upsert_streak_stage(
            {
                "stage_key": "millennium",
                "threshold": 1000,
                "name": "الألفية",
                "color": "#88CCFF",
                "reaction": "💠",
                "description": "ألف يوم.",
                "glow": 100,
                "particle": "neon",
            }
        )
        previous_day = datetime(2026, 10, 3, 12, tzinfo=RIYADH)
        activity_time = datetime(2026, 10, 4, 12, tzinfo=RIYADH)
        async with database.connect() as db:
            await db.execute(
                """
                INSERT INTO user_levels
                    (guild_id, user_id, current_streak, best_streak, last_daily_claim)
                VALUES (?, ?, 999, 999, ?)
                """,
                (self.guild_id, self.user_id, previous_day.isoformat()),
            )
            await db.commit()

        result = await self.cog.record_message_streak(
            self.make_message(activity_time)
        )
        self.assertEqual(
            (
                result["status"],
                result["previous_streak"],
                result["current_streak"],
                result["best_streak"],
            ),
            ("success", 999, 1000, 1000),
        )
        self.assertEqual(
            [stage["stage_key"] for stage in result["experience_events"]["stages"]],
            ["millennium"],
        )

    async def test_opt_in_reminder_skips_claimed_day_and_is_idempotent(self):
        activity_day = datetime(2026, 10, 2, 12, tzinfo=RIYADH)
        await database.record_level_streak_activity(
            self.guild_id,
            self.user_id,
            self.channel_id,
            activity_day,
        )
        await database.set_streak_reminder(self.guild_id, self.user_id, True)
        async with database.connect() as db:
            await db.execute(
                """
                UPDATE streak_reminder_settings SET enabled_at = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (
                    datetime(2026, 10, 2, 20, tzinfo=RIYADH).isoformat(),
                    self.guild_id,
                    self.user_id,
                ),
            )
            await db.commit()

        claimed_day = datetime(2026, 10, 2, 21, 10, tzinfo=RIYADH)
        self.assertEqual(
            await database.claim_due_streak_reminders(claimed_day), []
        )
        next_day = claimed_day + timedelta(days=1)
        first_delivery = await database.claim_due_streak_reminders(next_day)
        retry = await database.claim_due_streak_reminders(next_day)
        self.assertEqual(len(first_delivery), 1)
        self.assertEqual(first_delivery[0]["reminder_date"], "2026-10-03")
        self.assertEqual(retry, [])

    async def test_reminder_opt_in_after_default_time_waits_until_next_day(self):
        claim_time = datetime(2026, 10, 2, 12, tzinfo=RIYADH)
        other_user = 124
        await database.record_level_streak_activity(
            self.guild_id, other_user, self.channel_id, claim_time
        )
        await database.set_streak_reminder(self.guild_id, other_user, True)
        async with database.connect() as db:
            await db.execute(
                """
                UPDATE streak_reminder_settings SET enabled_at = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (
                    datetime(2026, 10, 2, 22, tzinfo=RIYADH).isoformat(),
                    self.guild_id,
                    other_user,
                ),
            )
            await db.commit()
        self.assertEqual(
            await database.claim_due_streak_reminders(
                datetime(2026, 10, 2, 22, 10, tzinfo=RIYADH)
            ),
            [],
        )
        next_day = await database.claim_due_streak_reminders(
            datetime(2026, 10, 3, 21, 10, tzinfo=RIYADH)
        )
        self.assertEqual([item["user_id"] for item in next_day], [other_user])

    async def test_server_reminder_controls_set_time_template_and_enable_state(self):
        activity_day = datetime(2026, 10, 4, 12, tzinfo=RIYADH)
        await database.record_level_streak_activity(
            self.guild_id, self.user_id, self.channel_id, activity_day
        )
        await database.set_streak_reminder(self.guild_id, self.user_id, True)
        await database.update_level_settings(
            self.guild_id,
            {"prime_controls": {"streak": {"messages": {"reminder": {
                "enabled": True,
                "time": "22:30",
                "message": "تذكير {user}: {current_streak}/{best_streak}",
            }}}}},
        )
        reminder_state = await database.set_streak_reminder(
            self.guild_id, self.user_id, True
        )
        self.assertEqual(reminder_state["reminder_time"], "22:30")
        self.assertTrue(reminder_state["delivery_enabled"])
        other_guild_id, other_channel_id = self.guild_id + 1, self.channel_id + 1
        await database.update_level_settings(
            other_guild_id, {"streak_channel_id": other_channel_id}
        )
        await database.record_level_streak_activity(
            other_guild_id, self.user_id, other_channel_id, activity_day
        )
        await database.set_streak_reminder(other_guild_id, self.user_id, True)
        await database.update_level_settings(
            other_guild_id,
            {"prime_controls": {"streak": {"messages": {"reminder": {
                "enabled": True,
                "time": "20:30",
                "message": "تذكير السيرفر الآخر",
            }}}}},
        )
        async with database.connect() as db:
            await db.execute(
                """
                UPDATE streak_reminder_settings SET enabled_at = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (
                    datetime(2026, 10, 5, 20, tzinfo=RIYADH).isoformat(),
                    self.guild_id,
                    self.user_id,
                ),
            )
            await db.execute(
                """
                UPDATE streak_reminder_settings SET enabled_at = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (
                    datetime(2026, 10, 5, 19, tzinfo=RIYADH).isoformat(),
                    other_guild_id,
                    self.user_id,
                ),
            )
            await db.commit()

        other_delivery = await database.claim_due_streak_reminders(
            datetime(2026, 10, 5, 20, 31, tzinfo=RIYADH)
        )
        self.assertEqual(
            [(item["guild_id"], item["reminder_message"]) for item in other_delivery],
            [(other_guild_id, "تذكير السيرفر الآخر")],
        )
        self.assertEqual(
            await database.claim_due_streak_reminders(
                datetime(2026, 10, 5, 22, 29, tzinfo=RIYADH)
            ),
            [],
        )
        delivery = await database.claim_due_streak_reminders(
            datetime(2026, 10, 5, 22, 31, tzinfo=RIYADH)
        )
        self.assertEqual(len(delivery), 1)
        self.assertEqual(delivery[0]["reminder_time"], "22:30")
        self.assertEqual(
            delivery[0]["reminder_message"],
            "تذكير {user}: {current_streak}/{best_streak}",
        )
        self.assertEqual(
            (delivery[0]["current_streak"], delivery[0]["best_streak"]),
            (1, 1),
        )
        self.assertEqual(
            await database.claim_due_streak_reminders(
                datetime(2026, 10, 5, 22, 32, tzinfo=RIYADH)
            ),
            [],
        )
        await database.update_level_settings(
            other_guild_id,
            {"prime_controls": {"streak": {"messages": {"reminder": {
                "enabled": False,
                "time": "20:30",
                "message": "تعطيل",
            }}}}},
        )

        await database.update_level_settings(
            self.guild_id,
            {"prime_controls": {"streak": {"messages": {"reminder": {
                "enabled": False,
                "time": "20:30",
                "message": "تعطيل",
            }}}}},
        )
        reminder_state = await database.set_streak_reminder(
            self.guild_id, self.user_id, True
        )
        self.assertEqual(reminder_state["reminder_time"], "20:30")
        self.assertFalse(reminder_state["delivery_enabled"])
        self.assertEqual(
            await database.claim_due_streak_reminders(
                datetime(2026, 10, 6, 23, tzinfo=RIYADH)
            ),
            [],
        )
        await database.update_level_settings(
            self.guild_id,
            {"prime_controls": {"streak": {"messages": {"reminder": {
                "enabled": True,
                "time": "20:30",
                "message": "تم تفعيل التذكير مجدداً",
            }}}}},
        )
        async with database.connect() as db:
            await db.execute(
                "UPDATE streak_reminder_settings SET enabled_at = ? "
                "WHERE guild_id = ? AND user_id = ?",
                (
                    datetime(2026, 10, 6, 19, tzinfo=RIYADH).isoformat(),
                    self.guild_id,
                    self.user_id,
                ),
            )
            await db.commit()
        next_delivery = await database.claim_due_streak_reminders(
            datetime(2026, 10, 6, 20, 31, tzinfo=RIYADH)
        )
        self.assertEqual(len(next_delivery), 1)
        self.assertEqual(next_delivery[0]["reminder_message"], "تم تفعيل التذكير مجدداً")

    async def test_success_reaction_permission_failure_does_not_block_card_or_claim(self):
        forbidden = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "missing permission"
        )
        channel = SimpleNamespace(id=self.channel_id, send=AsyncMock())
        message = self.make_message(channel=channel)
        message.add_reaction = AsyncMock(side_effect=forbidden)
        message.delete = AsyncMock()
        generate_card = AsyncMock(return_value=io.BytesIO(b"card"))
        with patch(
            "cogs.levels.generate_streak_card",
            new=generate_card,
        ):
            await self.cog.on_message(message)
            await asyncio.gather(
                *list(self.cog._streak_tasks), return_exceptions=True
            )

        row = await database.get_user_level(self.guild_id, self.user_id)
        self.assertEqual((row["current_streak"], row["best_streak"]), (1, 1))
        self.assertEqual(row["text_xp"], 0)
        message.add_reaction.assert_awaited_once_with("🔥")
        generate_card.assert_awaited_once()
        self.assertEqual(
            generate_card.await_args.kwargs["stages"],
            await database.get_streak_stages(),
        )
        channel.send.assert_awaited_once()
        self.assertEqual(set(channel.send.await_args.kwargs), {"file"})
        self.assertEqual(
            channel.send.await_args.kwargs["file"].filename,
            "streak-progress.png",
        )
        message.delete.assert_not_awaited()

    async def test_success_reacts_then_sends_only_standalone_png(self):
        order = []

        async def add_reaction(reaction):
            order.append(("reaction", reaction))

        async def send_card(**kwargs):
            order.append(("card", kwargs))

        channel = SimpleNamespace(
            id=self.channel_id,
            send=AsyncMock(side_effect=send_card),
        )
        message = self.make_message(channel=channel)
        message.add_reaction = AsyncMock(side_effect=add_reaction)
        message.delete = AsyncMock()
        with patch(
            "cogs.levels.generate_streak_card",
            new=AsyncMock(return_value=io.BytesIO(b"png")),
        ):
            await self.cog.on_message(message)
            await asyncio.gather(
                *list(self.cog._streak_tasks), return_exceptions=True
            )

        self.assertEqual(order[0], ("reaction", "🔥"))
        self.assertEqual(order[1][0], "card")
        self.assertEqual(set(order[1][1]), {"file"})
        self.assertEqual(order[1][1]["file"].filename, "streak-progress.png")
        channel.send.assert_awaited_once()
        message.delete.assert_not_awaited()

    async def test_duplicate_keeps_first_message_and_deletes_second_and_reply_after_ten_seconds(self):
        order = []
        response = SimpleNamespace(
            delete=AsyncMock(side_effect=lambda: order.append("delete_reply"))
        )
        sleep_started = asyncio.Event()
        release_delete = asyncio.Event()

        async def send_reply(*args, **kwargs):
            order.append(("reply", args[0]))
            return response

        async def delete_user():
            order.append("delete_user")

        async def controlled_sleep(delay):
            order.append(("sleep", delay))
            sleep_started.set()
            await release_delete.wait()

        first_channel = SimpleNamespace(id=self.channel_id, send=AsyncMock())
        first = self.make_message(channel=first_channel)
        first.add_reaction = AsyncMock()
        first.delete = AsyncMock()
        second_channel = SimpleNamespace(id=self.channel_id, send=AsyncMock())
        second = self.make_message(
            datetime(2026, 10, 1, 18, tzinfo=RIYADH), channel=second_channel
        )
        second.reply = AsyncMock(side_effect=send_reply)
        second.delete = AsyncMock(side_effect=delete_user)
        await database.update_level_settings(
            self.guild_id,
            {"prime_controls": {"streak": {"messages": {"duplicate": {
                "enabled": True,
                "message": "تم تسجيل {streak} بالفعل.",
            }}}}},
        )
        with patch(
            "cogs.levels.generate_streak_card",
            new=AsyncMock(return_value=io.BytesIO(b"png")),
        ):
            await self.cog.on_message(first)
            await asyncio.gather(
                *list(self.cog._streak_tasks), return_exceptions=True
            )
            with patch("cogs.levels.asyncio.sleep", new=controlled_sleep):
                await self.cog.on_message(second)
                await sleep_started.wait()
                first.delete.assert_not_awaited()
                second.delete.assert_not_awaited()
                response.delete.assert_not_awaited()
                second.reply.assert_awaited_once()
                reply_text = second.reply.await_args.args[0]
                self.assertIn("تم تسجيل 1 بالفعل", reply_text)
                self.assertIn("الستريك القادم بعد", reply_text)
                self.assertRegex(reply_text, r"\d+س \d+د")
                self.assertEqual(order[1], ("sleep", 10))
                release_delete.set()
                pending = list(self.cog._streak_tasks)
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

        first.delete.assert_not_awaited()
        second.delete.assert_awaited_once()
        response.delete.assert_awaited_once()
        self.assertEqual(order[0][0], "reply")
        self.assertEqual(order[-2:], ["delete_user", "delete_reply"])
        row = await database.get_user_level(self.guild_id, self.user_id)
        self.assertEqual((row["current_streak"], row["best_streak"]), (1, 1))
        self.assertEqual(first_channel.send.await_count, 1)
        self.assertEqual(second_channel.send.await_count, 0)

    async def test_progress_card_renders_without_a_stage_asset(self):
        stages = await database.get_streak_stages()
        stage = stages[0]
        state = {
            "current_streak": 1,
            "best_streak": 4,
            "remaining": 2,
            "progress": 0.0,
        }
        with patch("cogs.card_generator.fetch_image", new=AsyncMock(return_value=None)):
            result = await generate_streak_card(
                self.member,
                state,
                stage,
                stages[1],
                {"server_rank": 1, "global_rank": 1},
                stages=stages,
            )
        with Image.open(result) as image:
            self.assertEqual(image.size, (1000, 300))
            self.assertEqual(image.format, "PNG")

    async def test_each_named_streak_stage_has_a_distinct_rendered_card(self):
        stages = await database.get_streak_stages()
        cards = set()
        accents = set()
        with patch("cogs.card_generator.fetch_image", new=AsyncMock(return_value=None)):
            for index, stage in enumerate(stages):
                current = int(stage["threshold"])
                next_stage = stages[index + 1] if index + 1 < len(stages) else None
                state = {
                    "current_streak": current,
                    "best_streak": current,
                    "remaining": (
                        int(next_stage["threshold"]) - current if next_stage else 0
                    ),
                    "progress": 0.0,
                }
                accents.add(_streak_card_theme(stage)["accent"])
                image = await generate_streak_card(
                    self.member,
                    state,
                    stage,
                    next_stage,
                    {"server_rank": index + 1, "global_rank": index + 10},
                    stages=stages,
                )
                image.seek(0)
                cards.add(image.read())
                image.seek(0)
                with Image.open(image) as rendered:
                    self.assertEqual(rendered.size, (1000, 300))
                    self.assertEqual(rendered.format, "PNG")

        self.assertEqual(len(cards), 7)
        self.assertEqual(len(accents), 7)

    async def test_streak_card_uses_configured_stage_names_and_thresholds(self):
        stages = await database.get_streak_stages()
        await database.upsert_streak_stage(
            {
                "stage_key": "millennium",
                "threshold": 1000,
                "name": "الألفية",
                "message": "وصل {user} إلى {streak} يومًا.",
                "image": None,
                "color": "#88CCFF",
                "reaction": "💠",
                "description": "ألف يوم متواصل.",
                "glow": 100,
                "particle": "neon",
            }
        )
        stages = await database.get_streak_stages()
        stage = stages[-2]
        next_stage = stages[-1]
        state = {
            "current_streak": 365,
            "best_streak": 365,
            "remaining": 635,
            "progress": 0.0,
        }
        with patch("cogs.card_generator.fetch_image", new=AsyncMock(return_value=None)):
            original = await generate_streak_card(
                self.member,
                state,
                stage,
                next_stage,
                {"server_rank": 1, "global_rank": 1},
                stages=stages,
            )
            edited_stages = [dict(item) for item in stages]
            edited_stages[-1]["threshold"] = 1200
            edited_stages[-1]["name"] = "ذروة"
            edited = await generate_streak_card(
                self.member,
                {**state, "remaining": 835},
                stage,
                edited_stages[-1],
                {"server_rank": 1, "global_rank": 1},
                stages=edited_stages,
            )
        self.assertNotEqual(original.getvalue(), edited.getvalue())


if __name__ == "__main__":
    unittest.main()
import os
import sqlite3
import tempfile
import unittest

import database


class StreakDatabaseMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.directory.name, "streak.db")
        database.DB_NAME = self.db_path

    async def asyncTearDown(self):
        database.DB_NAME = self.previous_db
        self.directory.cleanup()

    async def _columns(self, table):
        async with database.connect() as db:
            async with db.execute(f"PRAGMA table_info({table})") as cur:
                return {row[1] for row in await cur.fetchall()}

    async def test_fresh_database_migration_is_idempotent(self):
        await database.init_db()
        await database.migrate_streak_database()
        await database.init_db()

        self.assertIn("best_streak", await self._columns("user_levels"))
        async with database.connect() as db:
            async with db.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='streak_daily_activity'"
            ) as cur:
                create_sql = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='streak_daily_activity'"
            ) as cur:
                table_count = (await cur.fetchone())[0]

        self.assertEqual(table_count, 1)
        self.assertIn("UNIQUE (guild_id, user_id, activity_date)", create_sql)

    async def test_existing_streak_state_and_xp_rows_are_preserved(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                CREATE TABLE user_levels (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    text_xp INTEGER DEFAULT 0,
                    text_level INTEGER DEFAULT 0,
                    voice_xp INTEGER DEFAULT 0,
                    voice_level INTEGER DEFAULT 0,
                    total_messages INTEGER DEFAULT 0,
                    total_voice_seconds INTEGER DEFAULT 0,
                    current_streak INTEGER DEFAULT 0,
                    last_daily_claim TIMESTAMP DEFAULT NULL,
                    last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            db.execute(
                """
                INSERT INTO user_levels (
                    guild_id, user_id, text_xp, current_streak, last_daily_claim
                ) VALUES (700, 200, 345, 4, '2026-10-04T09:30:00+00:00')
                """
            )
            db.execute(
                """
                CREATE TABLE level_xp_daily (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day_utc TEXT NOT NULL,
                    text_xp INTEGER NOT NULL DEFAULT 0,
                    voice_xp INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, day_utc)
                )
                """
            )
            db.execute(
                """
                INSERT INTO level_xp_daily (guild_id, user_id, day_utc, text_xp)
                VALUES (700, 200, '2026-10-04', 345)
                """
            )
            db.commit()

        await database.init_db()
        await database.migrate_streak_database()

        async with database.connect() as db:
            async with db.execute(
                """
                SELECT COUNT(*), MIN(text_xp), MIN(current_streak),
                       MIN(best_streak), MIN(last_daily_claim)
                FROM user_levels WHERE guild_id = 700 AND user_id = 200
                """
            ) as cur:
                user_row = await cur.fetchone()
            async with db.execute(
                "SELECT COUNT(*), SUM(text_xp) FROM level_xp_daily "
                "WHERE guild_id = 700 AND user_id = 200"
            ) as cur:
                daily_row = await cur.fetchone()

        self.assertEqual(tuple(user_row), (
            1, 345, 4, 4, "2026-10-04T09:30:00+00:00"
        ))
        self.assertEqual(tuple(daily_row), (1, 345))

    async def test_streak_analytics_aggregates_existing_records_only(self):
        await database.init_db()
        async with database.connect() as db:
            await db.executemany(
                """
                INSERT INTO user_levels (guild_id, user_id, current_streak, best_streak)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (700, 201, 5, 9),
                    (700, 202, 3, 6),
                    (700, 203, 0, 4),
                    (700, 204, 0, 0),
                    (701, 201, 99, 100),
                ],
            )
            await db.commit()

        result = await database.get_streak_dashboard_analytics(700)

        self.assertEqual(result, {
            "tracked_members": 3,
            "current_streak_members": 2,
            "total_current_streak_days": 8,
            "average_current_streak": 4.0,
            "highest_current_streak": 5,
            "members_with_best_streak": 3,
            "average_best_streak": 6.33,
            "highest_best_streak": 9,
        })

    async def test_unique_daily_key_blocks_only_same_guild_user_and_date(self):
        await database.init_db()
        rows = (
            (100, 200, "2026-10-04", "2026-10-04T00:10:00+03:00"),
            (100, 200, "2026-10-05", "2026-10-05T00:10:00+03:00"),
            (100, 201, "2026-10-04", "2026-10-04T00:11:00+03:00"),
            (101, 200, "2026-10-04", "2026-10-04T00:12:00+03:00"),
        )
        async with database.connect() as db:
            for guild_id, user_id, activity_date, first_at in rows:
                await db.execute(
                    """
                    INSERT INTO streak_daily_activity (
                        guild_id, user_id, activity_date, first_activity_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (guild_id, user_id, activity_date, first_at),
                )
            await db.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                await db.execute(
                    """
                    INSERT INTO streak_daily_activity (
                        guild_id, user_id, activity_date, first_activity_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (100, 200, "2026-10-04", "2026-10-04T18:00:00+03:00"),
                )
            await db.rollback()

            async with db.execute(
                "SELECT COUNT(*) FROM streak_daily_activity"
            ) as cur:
                count = (await cur.fetchone())[0]

        self.assertEqual(count, 4)


if __name__ == "__main__":
    unittest.main()
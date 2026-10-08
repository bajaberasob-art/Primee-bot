import aiosqlite
import asyncio
import json
import logging
import math
import os
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

# Koyeb can mount a persistent volume anywhere through DB_PATH. Keep the
# legacy filename as a local-development fallback when it already exists so
# an upgrade never silently starts with an empty database.
DB_PATH = (os.environ.get("DB_PATH") or "").strip() or "data/bot.db"
DB_NAME = (
    "bot_database.db"
    if "DB_PATH" not in os.environ
    and DB_PATH == "data/bot.db"
    and os.path.exists("bot_database.db")
    and not os.path.exists(DB_PATH)
    else DB_PATH
)
logger = logging.getLogger("DatabaseEngine")
DB_TIMEOUT = 30.0
WAL_CHECKPOINT_INTERVAL = 30 * 60
STREAK_TIMEZONE = ZoneInfo("Asia/Riyadh")
STREAK_DEFAULT_REMINDER_TIME = "21:00"
DEFAULT_STREAK_STAGES = (
    (1, "spark", "شرارة", None, "#F5C84C", "⚡", "بداية رحلتك اليومية.", 48, "sparks"),
    (3, "ember", "جمرة", None, "#F27A3D", "🔥", "استمر، بدأت جمرة الالتزام تتوهج.", 56, "embers"),
    (7, "flame", "شعلة", None, "#FF5B36", "🔥", "أسبوع كامل من الحضور المتواصل.", 64, "embers"),
    (14, "blaze", "لهيب", None, "#F04438", "🔥", "أسبوعان من الثبات والعزيمة.", 72, "sparks"),
    (30, "volcano", "بركان", None, "#E94B35", "🌋", "شهر من الالتزام اليومي.", 82, "embers"),
    (100, "legend", "أسطورة", None, "#B88CFF", "👑", "إنجاز استثنائي؛ أصبحت أسطورة.", 90, "shine"),
    (365, "eternal", "خالد", None, "#65D8D0", "♾️", "عام كامل من الستريك المتواصل.", 96, "neon"),
)
STREAK_PARTICLES = {"none", "sparks", "shine", "embers", "snow", "petals", "neon"}

# -------------------------------------------------------------
# إعدادات السيرفر: المخطط، القيم الافتراضية، والتحقق
# -------------------------------------------------------------
# key -> (sql type, default, python kind)
SETTINGS_SCHEMA: Dict[str, Tuple[str, Any, str]] = {
    "prefix": ("TEXT", "!", "str"),
    "anti_nuke": ("INTEGER", True, "bool"),
    "anti_alt_days": ("INTEGER", 3, "int"),
    "welcome_channel_id": ("INTEGER", None, "id"),
    "leave_channel_id": ("INTEGER", None, "id"),
    "welcome_message": ("TEXT", "", "str"),
    "welcome_embed_enabled": ("INTEGER", False, "bool"),
    "welcome_embed_color": ("TEXT", "#7c3aed", "str"),
    "welcome_embed_title": ("TEXT", "أهلاً بك في {server} ✨", "str"),
    "welcome_embed_description": ("TEXT", "", "str"),
    "welcome_embed_image_url": ("TEXT", "", "str"),
    "welcome_embed_sticker_id": ("INTEGER", None, "id"),
    "welcome_embed_footer": ("TEXT", "PRIME | TEAM • تطوير abood2026", "str"),
    "welcome_embed_show_avatar": ("INTEGER", True, "bool"),
    "auto_role_id": ("INTEGER", None, "id"),
    "log_channel_id": ("INTEGER", None, "id"),
    "captcha_enabled": ("INTEGER", False, "bool"),
    "captcha_role_id": ("INTEGER", None, "id"),
    "welcome_dm_enabled": ("INTEGER", False, "bool"),
    "member_auto_role_id": ("INTEGER", None, "id"),
    "bot_auto_role_id": ("INTEGER", None, "id"),
    "verified_role_id": ("INTEGER", None, "id"),
    "unverified_role_id": ("INTEGER", None, "id"),
    "quarantine_role_id": ("INTEGER", None, "id"),
    "rules_channel_id": ("INTEGER", None, "id"),
    "leave_message": ("TEXT", "", "str"),
    "economy_tax": ("REAL", 0.0, "float"),
    "daily_amount": ("INTEGER", 450, "int"),
    # Economy expansion settings (kept additive to the legacy daily_amount).
    "leaderboard_channel_id": ("INTEGER", 0, "id"),
    "leaderboard_message_id": ("INTEGER", 0, "id"),
    "daily_base_amount": ("INTEGER", 200, "int"),
    "role_multipliers": ("TEXT", {}, "json_map"),
    "management_role_ids": (
        "TEXT",
        {"admin": "", "moderator": "", "staff": ""},
        "json_map",
    ),
    "economy_support_role_ids": ("TEXT", [], "json_list"),
    # أعمدة قديمة يتم الإبقاء عليها للتوافق
    "anti_spam_enabled": ("INTEGER", True, "bool"),
    "anti_link_enabled": ("INTEGER", True, "bool"),
    # إعدادات Auto-Mod الحديثة
    "anti_invites": ("INTEGER", True, "bool"),
    "anti_links": ("INTEGER", True, "bool"),
    "anti_spam": ("INTEGER", True, "bool"),
    "anti_mass_mention": ("INTEGER", True, "bool"),
    # Granular Auto-Mod rules. These remain additive to the legacy toggles.
    "anti_spam_max_messages": ("INTEGER", 5, "int"),
    "anti_spam_time_window_seconds": ("INTEGER", 4, "int"),
    "anti_spam_action": ("TEXT", "timeout", "str"),
    "anti_spam_timeout_duration_minutes": ("INTEGER", 10, "int"),
    "anti_spam_ignored_role_ids": ("TEXT", [], "json_list"),
    "anti_spam_ignored_channel_ids": ("TEXT", [], "json_list"),
    "anti_mention_max_per_message": ("INTEGER", 3, "int"),
    "anti_mention_target_enabled": ("INTEGER", True, "bool"),
    "anti_mention_target_max_repeats": ("INTEGER", 3, "int"),
    "anti_mention_target_time_window_seconds": ("INTEGER", 10, "int"),
    "anti_mention_action": ("TEXT", "timeout", "str"),
    "anti_mention_timeout_duration_minutes": ("INTEGER", 5, "int"),
    "anti_mention_ignored_role_ids": ("TEXT", [], "json_list"),
    "anti_mention_ignored_channel_ids": ("TEXT", [], "json_list"),
    "banned_words_list": ("TEXT", [], "json_list"),
}
SETTINGS_DEFAULTS: Dict[str, Any] = {k: v[1] for k, v in SETTINGS_SCHEMA.items()}
# الأعمدة القديمة التي تُغذّي الأعمدة الجديدة عند الترحيل (new <- legacy)
LEGACY_ALIASES = {
    "welcome_channel_id": "welcome_channel",
    "log_channel_id": "mod_log_channel",
    "anti_spam": "anti_spam_enabled",
    "anti_links": "anti_link_enabled",
}
AUTOMOD_ACTIONS = {"warn_delete", "timeout", "kick", "ban"}

CACHE_TTL = 60.0
CACHE_MAX = 1024
_settings_cache: "OrderedDict[int, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_stats_cache: "OrderedDict[int, Tuple[float, Dict[str, Any]]]" = OrderedDict()
COMMAND_CACHE: Dict[int, Dict[str, Dict[str, Any]]] = {}
LOG_ROUTING_CACHE: Dict[int, Dict[str, int]] = {}
LEGACY_LOG_ROUTING_KEYS = (
    "log_messages",
    "log_roles",
    "log_channels",
    "log_moderation",
    "log_warnings",
    "log_voice",
)
LOG_ROUTING_KEYS = (
    "log_sanctions",
    "log_violations",
    "log_automod",
    "log_ticket",
    "log_channel",
    "log_server",
    "log_member",
    "log_message",
    "log_voice",
    "log_react",
    "log_roles",
)
LOG_ROUTING_ALIASES = {
    "log_moderation": "log_sanctions",
    "log_warnings": "log_violations",
    "log_messages": "log_message",
    "log_channels": "log_channel",
}
LOG_ROUTING_ALL_KEYS = tuple(dict.fromkeys((*LOG_ROUTING_KEYS, *LEGACY_LOG_ROUTING_KEYS)))
_guild_locks: Dict[int, asyncio.Lock] = {}
_db_semaphore: Optional[asyncio.Semaphore] = None
_UNSET = object()


class SettingsConflict(Exception):
    """تعارض في رقم الإصدار: تم تعديل الإعدادات من جهة أخرى."""

    def __init__(self, current: Dict[str, Any]):
        super().__init__("settings revision conflict")
        self.current = current


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ensure_db_directory(path: str | None = None) -> str:
    """Create the SQLite parent directory before the first connection opens."""
    target = os.path.abspath(path or DB_NAME)
    db_dir = os.path.dirname(target)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    return target


async def _configure(db: aiosqlite.Connection) -> None:
    """تطبيق إعدادات الأداء والسلامة على كل اتصال."""
    await db.execute("PRAGMA foreign_keys = ON;")
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute("PRAGMA synchronous = NORMAL;")
    await db.execute("PRAGMA cache_size = -32000;")
    await db.execute("PRAGMA temp_store = MEMORY;")
    await db.execute(f"PRAGMA busy_timeout = {int(DB_TIMEOUT * 1000)};")


class _Connection:
    """اتصال محدود العدد (الكاش 64MB لكل اتصال) مع تطبيق PRAGMA تلقائياً."""

    def __init__(self, row_factory=None):
        self._row_factory = row_factory
        self._db: Optional[aiosqlite.Connection] = None

    async def __aenter__(self) -> aiosqlite.Connection:
        global _db_semaphore
        if _db_semaphore is None:
            _db_semaphore = asyncio.Semaphore(8)
        await _db_semaphore.acquire()
        try:
            self._db = await aiosqlite.connect(
                DB_NAME,
                timeout=DB_TIMEOUT,
            )
            if self._row_factory:
                self._db.row_factory = self._row_factory
            await _configure(self._db)
            return self._db
        except Exception:
            if self._db is not None:
                await self._db.close()
            _db_semaphore.release()
            raise

    async def __aexit__(self, *exc) -> None:
        try:
            await self._db.close()
        finally:
            _db_semaphore.release()


def connect(row_factory=None) -> _Connection:
    return _Connection(row_factory)


async def get_user_dashboard_theme(user_id: str) -> Optional[Dict[str, Any]]:
    """Read the signed-in Discord user's private dashboard theme."""
    async with connect() as db:
        async with db.execute(
            "SELECT theme_json FROM user_dashboard_preferences WHERE user_id = ?",
            (str(user_id),),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid dashboard theme JSON for user %s", user_id)
        return None
    return value if isinstance(value, dict) else None


async def save_user_dashboard_theme(
    user_id: str,
    theme: Optional[Dict[str, Any]],
) -> None:
    """Save or clear one Discord user's dashboard theme without touching guild settings."""
    async with connect() as db:
        if theme is None:
            await db.execute(
                "DELETE FROM user_dashboard_preferences WHERE user_id = ?",
                (str(user_id),),
            )
        else:
            await db.execute(
                """
                INSERT INTO user_dashboard_preferences (user_id, theme_json)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    theme_json = excluded.theme_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    str(user_id),
                    json.dumps(theme, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        await db.commit()


async def _apply_streak_database_migration(db: aiosqlite.Connection) -> None:
    """Add streak state and the daily ledger without replacing existing rows.

    `activity_date` is the server-selected Asia/Riyadh calendar date.
    Timestamps retain their timezone offset for auditability.
    """
    savepoint = "phase1_streak_database"
    await db.execute(f"SAVEPOINT {savepoint}")
    try:
        async with db.execute("PRAGMA table_info(user_levels)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if not columns:
            raise RuntimeError("user_levels must exist before streak migration")
        required = {"guild_id", "user_id", "current_streak", "last_daily_claim"}
        missing = required - columns
        if missing:
            raise RuntimeError(
                "user_levels is missing required streak state columns: "
                + ", ".join(sorted(missing))
            )

        if "best_streak" not in columns:
            await db.execute(
                "ALTER TABLE user_levels ADD COLUMN "
                "best_streak INTEGER NOT NULL DEFAULT 0 "
                "CHECK (best_streak >= 0)"
            )

        # Existing lifetime history has no best-streak ledger. Preserve the
        # known lower bound (the current streak) without lowering any value
        # already present in a database that has partially adopted this schema.
        await db.execute(
            """
            UPDATE user_levels
            SET best_streak = CASE
                WHEN COALESCE(current_streak, 0) > COALESCE(best_streak, 0)
                    THEN COALESCE(current_streak, 0)
                ELSE COALESCE(best_streak, 0)
            END
            WHERE best_streak IS NULL
               OR best_streak < 0
               OR COALESCE(current_streak, 0) > COALESCE(best_streak, 0)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS streak_daily_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                activity_date TEXT NOT NULL,
                first_activity_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (guild_id, user_id, activity_date)
            )
            """
        )
        await db.execute(f"RELEASE SAVEPOINT {savepoint}")
    except BaseException:
        await db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        await db.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


async def _apply_streak_experience_migration(db: aiosqlite.Connection) -> None:
    """Add durable experience configuration and delivery guards without rebuilding data."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS streak_stages (
            stage_key TEXT PRIMARY KEY,
            threshold INTEGER NOT NULL UNIQUE CHECK (threshold > 0),
            name TEXT NOT NULL,
            message TEXT DEFAULT NULL,
            image TEXT DEFAULT NULL,
            color TEXT NOT NULL DEFAULT '#F5C84C',
            reaction TEXT DEFAULT NULL,
            description TEXT NOT NULL DEFAULT '',
            glow INTEGER NOT NULL DEFAULT 0 CHECK (glow BETWEEN 0 AND 100),
            particle TEXT NOT NULL DEFAULT 'none',
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
        )
    """)
    async with db.execute("PRAGMA table_info(streak_stages)") as cur:
        stage_columns = {row[1] for row in await cur.fetchall()}
    if "message" not in stage_columns:
        await db.execute(
            "ALTER TABLE streak_stages ADD COLUMN message TEXT DEFAULT NULL"
        )
    await db.execute("""
        CREATE TABLE IF NOT EXISTS streak_milestones (
            threshold INTEGER PRIMARY KEY CHECK (threshold > 0),
            message TEXT NOT NULL DEFAULT '',
            image TEXT DEFAULT NULL,
            reaction TEXT DEFAULT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS streak_experience_events (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('stage', 'milestone')),
            threshold INTEGER NOT NULL,
            activity_date TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id, event_key)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS streak_reminder_settings (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            reminder_time TEXT NOT NULL DEFAULT '21:00',
            enabled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS streak_reminder_deliveries (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reminder_date TEXT NOT NULL,
            claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id, reminder_date)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_streak_reminder_settings_enabled
        ON streak_reminder_settings(enabled, reminder_time)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_streak_experience_user
        ON streak_experience_events(guild_id, user_id, event_type)
    """)
    await db.executemany(
        """
        INSERT OR IGNORE INTO streak_stages
            (threshold, stage_key, name, image, color, reaction,
             description, glow, particle)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        DEFAULT_STREAK_STAGES,
    )


async def migrate_streak_database() -> None:
    """Run only the additive streak migration against the configured SQLite DB."""
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'user_levels'"
            ) as cur:
                if await cur.fetchone() is None:
                    raise RuntimeError(
                        "user_levels is absent; initialize the existing schema first"
                    )
            await _apply_streak_database_migration(db)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise


async def checkpoint_wal() -> tuple:
    """Run a non-blocking WAL checkpoint without replacing or rebuilding tables."""
    async with connect() as db:
        async with db.execute("PRAGMA wal_checkpoint(PASSIVE);") as cur:
            result = await cur.fetchone()
    return tuple(result or ())


async def wal_checkpoint_loop() -> None:
    """Keep the WAL bounded while allowing normal bot traffic to continue."""
    while True:
        try:
            await asyncio.sleep(WAL_CHECKPOINT_INTERVAL)
            await checkpoint_wal()
            logger.debug("[DB] Passive WAL checkpoint completed.")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[DB] Passive WAL checkpoint failed.", exc_info=True)


async def _migrate_guild_settings(db: aiosqlite.Connection) -> None:
    """إضافة الأعمدة الناقصة فقط دون حذف أي بيانات قديمة، مع تعبئة القيم القديمة."""
    async with db.execute("PRAGMA table_info(guild_settings);") as cur:
        existing = {row[1] for row in await cur.fetchall()}
    wanted = dict(SETTINGS_SCHEMA)
    wanted["revision"] = ("INTEGER", 0, "int")
    wanted["updated_at"] = ("TEXT", None, "str")
    missing = set()
    for column, (sql_type, default, kind) in wanted.items():
        if column in existing:
            continue
        missing.add(column)
        if default is None:
            await db.execute(f"ALTER TABLE guild_settings ADD COLUMN {column} {sql_type} DEFAULT NULL;")
        else:
            if kind in ("bool", "int"):
                literal = str(int(default))
            elif kind == "float":
                literal = repr(float(default))
            else:
                literal = "'" + str(default).replace("'", "''") + "'"
            # ALTER TABLE لا يقبل معاملات مرتبطة في DEFAULT؛ القيم هنا من المخطط الثابت فقط.
            await db.execute(f"ALTER TABLE guild_settings ADD COLUMN {column} {sql_type} DEFAULT {literal};")
    for new_col, legacy in LEGACY_ALIASES.items():
        if legacy in existing and new_col in missing:
            await db.execute(
                f"UPDATE guild_settings SET {new_col} = {legacy} "
                f"WHERE {legacy} IS NOT NULL;"
            )
    await db.execute("UPDATE guild_settings SET revision = 0 WHERE revision IS NULL;")
    await db.execute("UPDATE guild_settings SET updated_at = ? WHERE updated_at IS NULL;", (_utc_now(),))


async def _ensure_canonical_views(db: aiosqlite.Connection) -> None:
    """Expose one stable read contract while preserving existing live tables."""
    views = {
        "infractions": """
            SELECT id, guild_id, user_id, moderator_id AS mod_id,
                   'warning' AS type, reason, timestamp
            FROM warnings
        """,
        "auto_responses": """
            SELECT id, guild_id, trigger AS trigger_word,
                   response AS response_text, match_type
            FROM guild_auto_responders
        """,
        "economy_vault": """
            SELECT user_id, guild_id, balance AS wallet,
                   bank, last_daily
            FROM users
        """,
    }
    for name, query in views.items():
        async with db.execute(
            "SELECT type FROM sqlite_master WHERE name = ?",
            (name,),
        ) as cursor:
            existing = await cursor.fetchone()
        if existing and existing[0] != "view":
            logger.warning(
                "[DB_SCHEMA] canonical name %s is already a table; keeping it intact",
                name,
            )
            continue
        await db.execute(f"CREATE VIEW IF NOT EXISTS {name} AS {query}")


async def _migrate_logging_channels(db: aiosqlite.Connection) -> None:
    """Add the dedicated audit routes without rebuilding the live table."""
    async with db.execute("PRAGMA table_info(logging_channels);") as cur:
        existing = {row[1] for row in await cur.fetchall()}
    missing = [key for key in LOG_ROUTING_KEYS if key not in existing]
    for key in missing:
        await db.execute(
            f"ALTER TABLE logging_channels ADD COLUMN {key} INTEGER DEFAULT 0;"
        )
    # Existing installations used six legacy names. Seed only newly added
    # columns from their matching legacy values, preserving every old value.
    for legacy, dedicated in LOG_ROUTING_ALIASES.items():
        if legacy in existing and dedicated in missing:
            await db.execute(
                f"UPDATE logging_channels SET {dedicated} = {legacy} "
                f"WHERE {legacy} IS NOT NULL AND {legacy} != 0;"
            )


async def _migrate_auto_responder_uniqueness(db: aiosqlite.Connection) -> None:
    """Allow one trigger to have a fallback, role, and member rule together."""
    async with db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'guild_auto_responders'"
    ) as cur:
        row = await cur.fetchone()
    schema = str(row[0] or "") if row else ""
    if "UNIQUE (guild_id, trigger, match_type)" not in schema:
        return

    async with db.execute(
        "SELECT type FROM sqlite_master WHERE name = 'auto_responses'"
    ) as cur:
        canonical = await cur.fetchone()
    if canonical and canonical[0] == "view":
        # ALTER TABLE ... RENAME updates dependent view SQL to the staging
        # name. Drop it before the swap so the canonical view can be rebuilt
        # against the final table name.
        await db.execute("DROP VIEW auto_responses")

    await db.execute("""
        CREATE TABLE guild_auto_responders_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            trigger TEXT NOT NULL,
            match_type TEXT NOT NULL,
            response TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            cooldown_seconds REAL NOT NULL DEFAULT 5,
            bucket_capacity INTEGER NOT NULL DEFAULT 1,
            channel_id INTEGER DEFAULT NULL,
            target_type TEXT NOT NULL DEFAULT 'everyone',
            target_id INTEGER NOT NULL DEFAULT 0,
            reaction_emoji TEXT NOT NULL DEFAULT '',
            execution_count INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (guild_id, trigger, match_type, target_type, target_id)
        );
    """)
    await db.execute("""
        INSERT INTO guild_auto_responders_v2
            (id, guild_id, trigger, match_type, response, enabled,
             cooldown_seconds, bucket_capacity, channel_id, target_type,
             target_id, reaction_emoji, execution_count, updated_at)
        SELECT id, guild_id, trigger, match_type, response, enabled,
               cooldown_seconds, bucket_capacity, channel_id, target_type,
               target_id, reaction_emoji, execution_count, updated_at
        FROM guild_auto_responders
    """)
    await db.execute("DROP TABLE guild_auto_responders")
    await db.execute(
        "ALTER TABLE guild_auto_responders_v2 RENAME TO guild_auto_responders"
    )


async def _migrate_legacy_level_schema(db: aiosqlite.Connection) -> None:
    """Remove the retired XP/level schema without losing wallet data."""
    async with db.execute("PRAGMA table_info(users);") as cur:
        user_columns = {row[1] for row in await cur.fetchall()}
    if {"xp", "level"} & user_columns:
        # The canonical view depends on the old columns, so it must be removed
        # before SQLite swaps the users table.
        await db.execute("DROP VIEW IF EXISTS economy_vault")
        await db.execute("DROP INDEX IF EXISTS idx_users_guild_xp")
        await db.execute(
            """
            CREATE TABLE users_wallet_v2 (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                balance INTEGER DEFAULT 100,
                bank INTEGER DEFAULT 0,
                last_daily TEXT DEFAULT NULL,
                PRIMARY KEY (user_id, guild_id)
            )
            """
        )
        await db.execute(
            """
            INSERT INTO users_wallet_v2
                (user_id, guild_id, balance, bank, last_daily)
            SELECT user_id, guild_id,
                   COALESCE(balance, 100),
                   COALESCE(bank, 0),
                   last_daily
            FROM users
            """
        )
        await db.execute("DROP TABLE users")
        await db.execute("ALTER TABLE users_wallet_v2 RENAME TO users")

    # Level rewards have no remaining consumer and are not part of wallet data.
    await db.execute("DROP TABLE IF EXISTS level_rewards")

    async with db.execute("PRAGMA table_info(economy_audit_logs);") as cur:
        audit_columns = {row[1] for row in await cur.fetchall()}
    if "level_delta" in audit_columns:
        await db.execute(
            """
            CREATE TABLE economy_audit_logs_wallet_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                wallet_delta INTEGER NOT NULL DEFAULT 0,
                details TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            INSERT INTO economy_audit_logs_wallet_v2
                (id, guild_id, user_id, actor_id, action,
                 wallet_delta, details, created_at)
            SELECT id, guild_id, user_id, actor_id, action,
                   wallet_delta, details, created_at
            FROM economy_audit_logs
            """
        )
        await db.execute("DROP TABLE economy_audit_logs")
        await db.execute(
            "ALTER TABLE economy_audit_logs_wallet_v2 RENAME TO economy_audit_logs"
        )

    # self_role_panels is shared with the ordinary self-role studio. Rebuild
    # only to remove the level-gate columns while preserving every studio row.
    async with db.execute("PRAGMA table_info(self_role_panels);") as cur:
        panel_columns = {row[1] for row in await cur.fetchall()}
    if {"min_level", "color_hex"} & panel_columns:
        await db.execute(
            """
            CREATE TABLE self_role_panels_wallet_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '#5865f2',
                emoji TEXT NOT NULL DEFAULT '🏷️',
                role_specs TEXT NOT NULL DEFAULT '[]',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (guild_id, message_id)
            )
            """
        )
        await db.execute(
            """
            INSERT INTO self_role_panels_wallet_v2
                (id, guild_id, channel_id, message_id, title, description,
                 color, emoji, role_specs, created_at, updated_at)
            SELECT id, guild_id, channel_id, message_id, title, description,
                   color, emoji, role_specs, created_at, updated_at
            FROM self_role_panels
            """
        )
        await db.execute("DROP TABLE self_role_panels")
        await db.execute(
            "ALTER TABLE self_role_panels_wallet_v2 RENAME TO self_role_panels"
        )
    await db.execute("DROP TABLE IF EXISTS self_role_buttons")


async def init_db() -> None:
    """تهيئة الجداول، العلاقات، والفهارس مع تفعيل قيود المفاتيح الخارجية."""
    ensure_db_directory()
    try:
        async with connect() as db:

            # 1. جدول الاقتصاد wallet-only
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    balance INTEGER DEFAULT 100,
                    bank INTEGER DEFAULT 0,
                    last_daily TEXT DEFAULT NULL,
                    PRIMARY KEY (user_id, guild_id)
                );
            """)
            # Personal dashboard preferences are keyed to the Discord account,
            # not a guild, so one user's theme never changes another user's UI.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_dashboard_preferences (
                    user_id TEXT PRIMARY KEY,
                    theme_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # PRIME AI is an additive server-scoped layer. It stores only
            # operator settings, explicitly supplied memories, and metadata
            # audit events; it does not duplicate XP, streak, or subscription
            # state and does not persist chat transcripts.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_settings (
                    guild_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    system_prompt TEXT NOT NULL DEFAULT '',
                    allowed_channel_ids TEXT NOT NULL DEFAULT '[]',
                    allowed_channels_migrated INTEGER NOT NULL DEFAULT 0
                        CHECK (allowed_channels_migrated IN (0, 1)),
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            async with db.execute("PRAGMA table_info(prime_ai_settings)") as cur:
                ai_settings_columns = {str(row[1]) for row in await cur.fetchall()}
            if "allowed_channels_migrated" not in ai_settings_columns:
                await db.execute(
                    "ALTER TABLE prime_ai_settings ADD COLUMN "
                    "allowed_channels_migrated INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (allowed_channels_migrated IN (0, 1))"
                )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_memories (
                    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    scope TEXT NOT NULL DEFAULT 'SERVER',
                    scope_id TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    expires_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    source TEXT NOT NULL DEFAULT 'ADMIN',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    owner_user_id INTEGER,
                    candidate_expires_at TEXT,
                    confirmation_message_id INTEGER,
                    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
                    memory_type TEXT NOT NULL DEFAULT 'FACT',
                    importance INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
                    source_channel_id INTEGER,
                    source_message_id INTEGER,
                    related_user_ids_json TEXT NOT NULL DEFAULT '[]'
                );
            """)
            # Additive compatibility for workspaces that already have the
            # original server-only PRIME AI memory table.
            async with db.execute("PRAGMA table_info(prime_ai_memories)") as cur:
                memory_columns = {str(row[1]) for row in await cur.fetchall()}
            for column, declaration in (
                ("scope", "TEXT NOT NULL DEFAULT 'SERVER'"),
                ("scope_id", "TEXT NOT NULL DEFAULT ''"),
                ("enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("expires_at", "TEXT"),
                # ALTER TABLE ADD COLUMN requires a constant default. Existing
                # rows inherit their original creation timestamp below.
                ("updated_at", "TEXT NOT NULL DEFAULT ''"),
                ("source", "TEXT NOT NULL DEFAULT 'ADMIN'"),
                ("confidence", "REAL NOT NULL DEFAULT 1.0"),
                ("status", "TEXT NOT NULL DEFAULT 'ACTIVE'"),
                ("owner_user_id", "INTEGER"),
                ("candidate_expires_at", "TEXT"),
                ("confirmation_message_id", "INTEGER"),
                ("pinned", "INTEGER NOT NULL DEFAULT 0"),
                ("memory_type", "TEXT NOT NULL DEFAULT 'FACT'"),
                ("importance", "INTEGER NOT NULL DEFAULT 3"),
                ("source_channel_id", "INTEGER"),
                ("source_message_id", "INTEGER"),
                ("related_user_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if column not in memory_columns:
                    await db.execute(
                        f"ALTER TABLE prime_ai_memories ADD COLUMN {column} {declaration}"
                    )
            await db.execute(
                "UPDATE prime_ai_memories SET updated_at = created_at "
                "WHERE updated_at IS NULL OR updated_at = ''"
            )
            await db.execute(
                "UPDATE prime_ai_memories SET pinned=1 "
                "WHERE expires_at IS NULL AND status='ACTIVE' AND pinned=0"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_memories_owner_status "
                "ON prime_ai_memories(guild_id, scope, owner_user_id, status, memory_id DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_memory_revisions (
                    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    memory_id INTEGER NOT NULL,
                    before_content TEXT NOT NULL,
                    after_content TEXT NOT NULL,
                    changed_by INTEGER NOT NULL,
                    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    source_channel_id INTEGER,
                    source_message_id INTEGER
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_memory_revisions_lookup "
                "ON prime_ai_memory_revisions(guild_id, memory_id, revision_id DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_conversation_turns (
                    turn_key TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    thread_id INTEGER,
                    user_id INTEGER NOT NULL,
                    topic_key TEXT NOT NULL DEFAULT 'general',
                    user_message_id INTEGER,
                    assistant_message_id INTEGER,
                    reference_message_id INTEGER,
                    mentioned_user_ids_json TEXT NOT NULL DEFAULT '[]',
                    user_content TEXT NOT NULL,
                    assistant_content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_conversation_lookup "
                "ON prime_ai_conversation_turns(guild_id, channel_id, user_id, topic_key, created_at DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_conversation_expiry "
                "ON prime_ai_conversation_turns(expires_at);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    actor_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    result TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_control_settings (
                    guild_id INTEGER PRIMARY KEY,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_skills (
                    guild_id INTEGER NOT NULL,
                    skill_key TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, skill_key)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_operations (
                    operation_id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER,
                    request TEXT NOT NULL DEFAULT '',
                    detected_intent TEXT NOT NULL DEFAULT '',
                    skill TEXT NOT NULL DEFAULT '',
                    tools_json TEXT NOT NULL DEFAULT '[]',
                    target_json TEXT NOT NULL DEFAULT '{}',
                    permissions_json TEXT NOT NULL DEFAULT '{}',
                    confirmation TEXT NOT NULL DEFAULT 'required',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    execution_result TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL,
                    message_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_pending_actions (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    request_text TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, channel_id, user_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_moderation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    message_content TEXT NOT NULL DEFAULT '',
                    detection_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    rule_matched TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT 'LOG',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prime_ai_request_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER,
                    channel_id INTEGER,
                    skill TEXT NOT NULL DEFAULT 'conversation',
                    mode TEXT NOT NULL DEFAULT 'CHAT',
                    result TEXT NOT NULL DEFAULT 'success',
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    tokens_used INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_memories_guild "
                "ON prime_ai_memories(guild_id, scope, scope_id, memory_id DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_audit_guild "
                "ON prime_ai_audit(guild_id, audit_id DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_operations_guild "
                "ON prime_ai_operations(guild_id, created_at DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_moderation_guild "
                "ON prime_ai_moderation_events(guild_id, created_at DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_requests_guild "
                "ON prime_ai_request_events(guild_id, created_at DESC);"
            )

            # Phase 1 Lona leveling foundation. These tables are independent
            # of wallet/economy data and are additive to the existing schema.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_settings (
                    guild_id INTEGER PRIMARY KEY,
                    is_enabled BOOLEAN DEFAULT 1,
                    command_rank_enabled BOOLEAN DEFAULT 1,
                    command_rank_channels TEXT DEFAULT '[]',
                    command_rank_aliases TEXT DEFAULT '["rank","level","لفل","رانك"]',
                    command_top_enabled BOOLEAN DEFAULT 1,
                    command_top_channels TEXT DEFAULT '[]',
                    command_top_aliases TEXT DEFAULT '["top","توب","متصدرين"]',
                    web_leaderboard_enabled BOOLEAN DEFAULT 1,
                    web_slug TEXT DEFAULT NULL,
                    xp_multiplier REAL DEFAULT 1.0,
                    message_cooldown_seconds INTEGER DEFAULT 60,
                    boost_multiplier REAL DEFAULT 1.0,
                    boost_expires_at TIMESTAMP DEFAULT NULL,
                    streak_enabled BOOLEAN DEFAULT 1,
                    streak_channel_id INTEGER DEFAULT NULL,
                    streak_daily_xp INTEGER DEFAULT 50,
                    streak_max_cap INTEGER DEFAULT 500,
                    reaction_xp_reactor BOOLEAN DEFAULT 1,
                    reaction_xp_author BOOLEAN DEFAULT 1,
                    reaction_xp_amount INTEGER DEFAULT 5,
                    reaction_cooldown_seconds INTEGER DEFAULT 60,
                    reaction_allowed_channels TEXT DEFAULT '[]',
                    voice_xp_enabled BOOLEAN DEFAULT 1,
                    voice_xp_per_minute INTEGER DEFAULT 20,
                    voice_mute_no_xp BOOLEAN DEFAULT 1,
                    voice_deafen_no_xp BOOLEAN DEFAULT 1,
                    voice_min_two_members BOOLEAN DEFAULT 1,
                    voice_diminishing_enabled BOOLEAN DEFAULT 0,
                    voice_diminishing_mins INTEGER DEFAULT 60,
                    voice_diminishing_rate REAL DEFAULT 0.5,
                    voice_separate_levels BOOLEAN DEFAULT 1,
                    rewards_single_highest BOOLEAN DEFAULT 1,
                    dynamic_top_day_role INTEGER DEFAULT NULL,
                    dynamic_top_week_role INTEGER DEFAULT NULL,
                    dynamic_top_month_role INTEGER DEFAULT NULL,
                    dynamic_top_all_role INTEGER DEFAULT NULL,
                    weekly_reset_day TEXT DEFAULT 'Friday',
                    card_layout TEXT DEFAULT 'vertical',
                    card_particles TEXT DEFAULT 'none',
                    card_animated_bar BOOLEAN DEFAULT 1,
                    card_color TEXT DEFAULT '#1E293B',
                    card_bg_url TEXT DEFAULT NULL,
                    card_design TEXT NOT NULL DEFAULT '{}',
                    levelup_channel_id INTEGER DEFAULT NULL,
                    levelup_channel_type TEXT DEFAULT 'channel',
                    levelup_format TEXT DEFAULT 'embed',
                    levelup_title TEXT DEFAULT '🎉 ارتقاء مستوى!',
                    levelup_template TEXT DEFAULT 'مبروك {user} وصلت للمستوى {level} في سيرفر {server}!',
                    levelup_voice_enabled BOOLEAN DEFAULT 1,
                    levelup_voice_channel_id INTEGER DEFAULT NULL,
                    levelup_voice_template TEXT DEFAULT 'مبروك {user} ارتقيت للمستوى الصوتي {level}!',
                    overtake_alert_enabled BOOLEAN DEFAULT 1,
                    overtake_template TEXT DEFAULT '⚡ {passer} تخطى {passed} في توب السيرفر وأصبح المركز #{rank}!',
                    bot_embed_color TEXT DEFAULT '#6366F1',
                    prime_controls TEXT NOT NULL DEFAULT '{}'
                );
            """)
            async with db.execute("PRAGMA table_info(level_settings)") as cur:
                level_columns = {row[1] for row in await cur.fetchall()}
            if "message_cooldown_seconds" not in level_columns:
                await db.execute(
                    "ALTER TABLE level_settings ADD COLUMN "
                    "message_cooldown_seconds INTEGER DEFAULT 60"
                )
            level_additive_columns = {
                "revision": "INTEGER NOT NULL DEFAULT 0",
                "text_xp_enabled": "BOOLEAN DEFAULT 1",
                "text_xp_min": "INTEGER DEFAULT 15",
                "text_xp_max": "INTEGER DEFAULT 25",
                "text_allowed_channels": "TEXT DEFAULT '[]'",
                "reaction_xp_enabled": "BOOLEAN DEFAULT 1",
                "timed_xp_boosts": "TEXT DEFAULT '[]'",
                "voice_min_members": "INTEGER DEFAULT 2",
                "card_show_stats": "BOOLEAN DEFAULT 1",
                "card_design": "TEXT NOT NULL DEFAULT '{}'",
                "levelup_enabled": "BOOLEAN DEFAULT 1",
                "milestone_alert_enabled": "BOOLEAN DEFAULT 1",
                "milestone_channel_id": "INTEGER DEFAULT NULL",
                "milestone_template": "TEXT DEFAULT '{user} حقق إنجازاً جديداً عند المستوى {level}.'",
                "overtake_channel_id": "INTEGER DEFAULT NULL",
                "prime_controls": "TEXT NOT NULL DEFAULT '{}'",
                "streak_channel_id": "INTEGER DEFAULT NULL",
            }
            for column, declaration in level_additive_columns.items():
                if column not in level_columns:
                    await db.execute(
                        f"ALTER TABLE level_settings ADD COLUMN {column} {declaration}"
                    )
            if "voice_min_members" not in level_columns:
                await db.execute(
                    """
                    UPDATE level_settings
                    SET voice_min_members = CASE
                        WHEN voice_min_two_members THEN 2 ELSE 1
                    END
                    """
                )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_levels (
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
                );
            """)
            await _apply_streak_database_migration(db)
            await _apply_streak_experience_migration(db)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_xp_daily (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day_utc TEXT NOT NULL,
                    text_xp INTEGER NOT NULL DEFAULT 0 CHECK (text_xp >= 0),
                    voice_xp INTEGER NOT NULL DEFAULT 0 CHECK (voice_xp >= 0),
                    PRIMARY KEY (guild_id, user_id, day_utc)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_xp_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    awarded_at TEXT NOT NULL,
                    text_xp INTEGER NOT NULL DEFAULT 0 CHECK (text_xp >= 0),
                    voice_xp INTEGER NOT NULL DEFAULT 0 CHECK (voice_xp >= 0),
                    CHECK (text_xp > 0 OR voice_xp > 0)
                );
            """)
            # Subscription records are additive to existing XP and streak
            # state. The ledgers are append-only; retries are deduplicated by
            # their stable operation/event keys.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    subscription_id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    plan_id TEXT DEFAULT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'expired', 'cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (end_date > start_date)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT UNIQUE,
                    request_hash TEXT NOT NULL DEFAULT '',
                    subscription_id TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL
                        CHECK (event_type IN ('created', 'renewed', 'expired', 'cancelled')),
                    actor_id INTEGER,
                    previous_status TEXT,
                    status TEXT NOT NULL
                        CHECK (status IN ('active', 'expired', 'cancelled')),
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (subscription_id)
                        REFERENCES subscriptions(subscription_id) ON DELETE RESTRICT
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_renewals (
                    transaction_id TEXT PRIMARY KEY,
                    subscription_id TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    previous_end_date TEXT NOT NULL,
                    new_end_date TEXT NOT NULL,
                    duration_days INTEGER NOT NULL CHECK (duration_days > 0),
                    actor_id INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (subscription_id)
                        REFERENCES subscriptions(subscription_id) ON DELETE RESTRICT
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_xp_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    subscription_id TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'subscription'
                        CHECK (source = 'subscription'),
                    source_id TEXT NOT NULL,
                    event_type TEXT NOT NULL
                        CHECK (event_type IN ('created', 'renewal')),
                    amount INTEGER NOT NULL CHECK (amount >= 0),
                    base_amount INTEGER NOT NULL DEFAULT 0,
                    level_basis INTEGER NOT NULL DEFAULT 0,
                    level_step INTEGER NOT NULL DEFAULT 5,
                    random_bonus INTEGER NOT NULL DEFAULT 0,
                    cap_amount INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    UNIQUE (source, source_id, event_type),
                    FOREIGN KEY (subscription_id)
                        REFERENCES subscriptions(subscription_id) ON DELETE RESTRICT
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_notifications (
                    notification_id TEXT PRIMARY KEY,
                    event_key TEXT NOT NULL UNIQUE,
                    subscription_id TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL
                        CHECK (event_type IN ('created', 'renewal', 'expiring', 'expired')),
                    reference_end_date TEXT NOT NULL,
                    reminder_hours INTEGER DEFAULT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'cancelled')),
                    created_at TEXT NOT NULL,
                    claimed_at TEXT DEFAULT NULL,
                    completed_at TEXT DEFAULT NULL,
                    last_error TEXT DEFAULT NULL,
                    FOREIGN KEY (subscription_id)
                        REFERENCES subscriptions(subscription_id) ON DELETE RESTRICT
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    xp_enabled INTEGER NOT NULL DEFAULT 1 CHECK (xp_enabled IN (0, 1)),
                    notifications_enabled INTEGER NOT NULL DEFAULT 1
                        CHECK (notifications_enabled IN (0, 1)),
                    new_xp_base INTEGER NOT NULL DEFAULT 100 CHECK (new_xp_base >= 0),
                    renewal_xp_base INTEGER NOT NULL DEFAULT 150 CHECK (renewal_xp_base >= 0),
                    notification_templates TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            # Phase 6 expands the existing subscription settings in place. Keep
            # every change additive: production databases already contain live
            # subscription, XP, reminder, and audit records.
            async with db.execute(
                "PRAGMA table_info(subscription_guild_settings)"
            ) as cur:
                subscription_setting_columns = {
                    row[1] for row in await cur.fetchall()
                }
            subscription_setting_migrations = {
                "enabled": "INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))",
                "new_subscription_enabled": "INTEGER NOT NULL DEFAULT 1 CHECK (new_subscription_enabled IN (0, 1))",
                "renewal_enabled": "INTEGER NOT NULL DEFAULT 1 CHECK (renewal_enabled IN (0, 1))",
                "expiry_enabled": "INTEGER NOT NULL DEFAULT 1 CHECK (expiry_enabled IN (0, 1))",
                "expiry_detection_enabled": "INTEGER NOT NULL DEFAULT 1 CHECK (expiry_detection_enabled IN (0, 1))",
                "reminders_enabled": "INTEGER NOT NULL DEFAULT 1 CHECK (reminders_enabled IN (0, 1))",
                "default_duration_days": "INTEGER NOT NULL DEFAULT 30 CHECK (default_duration_days BETWEEN 1 AND 36500)",
                "renewal_duration_days": "INTEGER NOT NULL DEFAULT 30 CHECK (renewal_duration_days BETWEEN 1 AND 36500)",
                "default_plan_id": "TEXT DEFAULT NULL",
                "level_step_xp": "INTEGER NOT NULL DEFAULT 5 CHECK (level_step_xp BETWEEN 0 AND 100000)",
                "new_xp_jitter": "INTEGER NOT NULL DEFAULT 20 CHECK (new_xp_jitter BETWEEN 0 AND 100000)",
                "renewal_xp_jitter": "INTEGER NOT NULL DEFAULT 30 CHECK (renewal_xp_jitter BETWEEN 0 AND 100000)",
                "new_xp_cap": "INTEGER NOT NULL DEFAULT 2000 CHECK (new_xp_cap BETWEEN 0 AND 100000)",
                "renewal_xp_cap": "INTEGER NOT NULL DEFAULT 3000 CHECK (renewal_xp_cap BETWEEN 0 AND 100000)",
                "xp_multiplier": "REAL NOT NULL DEFAULT 1.0 CHECK (xp_multiplier BETWEEN 0 AND 100)",
                "expiry_action": "TEXT NOT NULL DEFAULT 'expire' CHECK (expiry_action IN ('expire', 'cancel', 'keep_active'))",
                "notification_rules_json": "TEXT NOT NULL DEFAULT '{}'",
                "notification_claim_timeout_minutes": "INTEGER NOT NULL DEFAULT 10 CHECK (notification_claim_timeout_minutes BETWEEN 1 AND 120)",
                "revision": "INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)",
            }
            for column, definition in subscription_setting_migrations.items():
                if column not in subscription_setting_columns:
                    await db.execute(
                        f"ALTER TABLE subscription_guild_settings "
                        f"ADD COLUMN {column} {definition}"
                    )

            async with db.execute(
                "PRAGMA table_info(subscription_notifications)"
            ) as cur:
                notification_columns = {row[1] for row in await cur.fetchall()}
            if "reminder_id" not in notification_columns:
                await db.execute(
                    "ALTER TABLE subscription_notifications "
                    "ADD COLUMN reminder_id TEXT DEFAULT NULL"
                )

            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_plans (
                    plan_id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    duration_days INTEGER NOT NULL CHECK (duration_days BETWEEN 1 AND 36500),
                    price_cents INTEGER DEFAULT NULL CHECK (price_cents IS NULL OR price_cents >= 0),
                    currency TEXT NOT NULL DEFAULT 'USD'
                        CHECK (length(currency) = 3),
                    xp_enabled INTEGER NOT NULL DEFAULT 1 CHECK (xp_enabled IN (0, 1)),
                    new_xp_base INTEGER DEFAULT NULL CHECK (new_xp_base IS NULL OR new_xp_base BETWEEN 0 AND 100000),
                    renewal_xp_base INTEGER DEFAULT NULL CHECK (renewal_xp_base IS NULL OR renewal_xp_base BETWEEN 0 AND 100000),
                    xp_multiplier REAL DEFAULT NULL CHECK (xp_multiplier IS NULL OR xp_multiplier BETWEEN 0 AND 100),
                    notifications_enabled INTEGER NOT NULL DEFAULT 1 CHECK (notifications_enabled IN (0, 1)),
                    reminders_enabled INTEGER NOT NULL DEFAULT 1 CHECK (reminders_enabled IN (0, 1)),
                    expiry_action TEXT DEFAULT NULL
                        CHECK (expiry_action IS NULL OR expiry_action IN ('expire', 'cancel', 'keep_active')),
                    notification_overrides_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    created_by INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (guild_id, normalized_name)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_reminder_rules (
                    reminder_id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    hours_before INTEGER NOT NULL CHECK (hours_before BETWEEN 1 AND 876000),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    dm_enabled INTEGER NOT NULL DEFAULT 1 CHECK (dm_enabled IN (0, 1)),
                    channel_enabled INTEGER NOT NULL DEFAULT 0 CHECK (channel_enabled IN (0, 1)),
                    channel_id INTEGER DEFAULT NULL,
                    template_id TEXT DEFAULT NULL,
                    conditions_json TEXT NOT NULL DEFAULT '{}',
                    created_by INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_templates (
                    template_id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    event_type TEXT NOT NULL
                        CHECK (event_type IN ('created', 'renewal', 'expiring', 'expired')),
                    content TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
                    created_by INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (guild_id, normalized_name)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_admin_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT UNIQUE,
                    request_hash TEXT NOT NULL DEFAULT '',
                    subscription_id TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id INTEGER NOT NULL,
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (subscription_id)
                        REFERENCES subscriptions(subscription_id) ON DELETE RESTRICT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_control_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    entity_type TEXT NOT NULL
                        CHECK (entity_type IN ('settings', 'plan', 'reminder', 'template')),
                    entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    actor_id INTEGER NOT NULL,
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscription_plans_guild_enabled "
                "ON subscription_plans(guild_id, enabled, name);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscription_reminders_due "
                "ON subscription_reminder_rules(guild_id, enabled, hours_before);"
            )
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_reminders_unique_enabled_hours "
                "ON subscription_reminder_rules(guild_id, hours_before) WHERE enabled = 1;"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscription_templates_guild_event "
                "ON subscription_templates(guild_id, event_type, enabled);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscription_admin_audit_guild_history "
                "ON subscription_admin_audit(guild_id, subscription_id, created_at);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscription_control_audit_guild_history "
                "ON subscription_control_audit(guild_id, created_at);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscriptions_member_status "
                "ON subscriptions(guild_id, user_id, status, end_date);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscriptions_expiry "
                "ON subscriptions(status, end_date);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscription_history_member "
                "ON subscription_history(guild_id, user_id, created_at);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscription_xp_period "
                "ON subscription_xp_transactions(guild_id, timestamp, event_type);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscription_notifications_due "
                "ON subscription_notifications(status, created_at);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_periodic_top_runs (
                    guild_id INTEGER NOT NULL,
                    period TEXT NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly')),
                    period_key TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    completed_at TEXT DEFAULT NULL,
                    PRIMARY KEY (guild_id, period, period_key)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_reaction_awards (
                    guild_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    reactor_id INTEGER NOT NULL,
                    emoji_key TEXT NOT NULL,
                    awarded_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, message_id, reactor_id, emoji_key)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_reaction_cooldowns (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    last_award_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_role_rewards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    reward_type TEXT DEFAULT 'text'
                        CHECK (reward_type IN ('text', 'voice')),
                    level_required INTEGER NOT NULL,
                    role_id INTEGER NOT NULL
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_multipliers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    target_type TEXT NOT NULL
                        CHECK (target_type IN ('role', 'channel')),
                    target_id INTEGER NOT NULL,
                    multiplier REAL DEFAULT 1.5
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    target_type TEXT NOT NULL
                        CHECK (target_type IN ('role', 'channel')),
                    target_id INTEGER NOT NULL
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS level_user_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    UNIQUE (guild_id, target_id)
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_levels_text "
                "ON user_levels (guild_id, text_xp DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_levels_voice "
                "ON user_levels (guild_id, voice_xp DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_levels_text_rank "
                "ON user_levels (guild_id, text_xp DESC, user_id ASC) "
                "WHERE text_xp > 0;"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_levels_voice_rank "
                "ON user_levels (guild_id, voice_xp DESC, user_id ASC) "
                "WHERE voice_xp > 0;"
            )
            # Older deployments may already contain duplicate slugs. Keep a
            # non-unique lookup index and enforce uniqueness inside the
            # serialized settings write transaction instead of failing startup.
            await db.execute("DROP INDEX IF EXISTS idx_level_settings_web_slug")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_level_settings_web_slug_lookup "
                "ON level_settings (web_slug) "
                "WHERE web_slug IS NOT NULL AND web_slug <> '';"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_levels_activity "
                "ON user_levels (guild_id, last_message_at DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_level_xp_daily_period "
                "ON level_xp_daily (guild_id, day_utc, user_id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_level_xp_events_period "
                "ON level_xp_events (guild_id, awarded_at, user_id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_level_role_rewards_guild "
                "ON level_role_rewards (guild_id, reward_type, level_required);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_level_multipliers_guild "
                "ON level_multipliers (guild_id, target_type);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_level_blacklist_guild "
                "ON level_blacklist (guild_id, target_type);"
            )

            # 2. جدول الإنذارات الإدارية
            await db.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Step 2 sanctions state. These tables are intentionally isolated
            # from the existing moderation and warning records.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS temp_bans (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    unban_at TIMESTAMP NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS voice_bans (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    banned_by INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS text_mutes (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    muted_by INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jailed_users (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    jailed_by INTEGER NOT NULL,
                    saved_roles TEXT NOT NULL DEFAULT '[]',
                    jail_type TEXT NOT NULL DEFAULT 'general',
                    private_channel_id INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS channel_blacklists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    restriction_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, channel_id, user_id, restriction_type)
                );
            """)
            # Step 4 administration state. These tables are intentionally
            # separate from the legacy warnings table and existing role/
            # moderation records so older commands keep their exact contract.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS member_warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS temp_roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS event_points (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    points INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS mod_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    note_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_temp_bans_expiry "
                "ON temp_bans(unban_at);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_voice_bans_guild "
                "ON voice_bans(guild_id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_text_mutes_guild "
                "ON text_mutes(guild_id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_jailed_users_guild "
                "ON jailed_users(guild_id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_channel_blacklists_lookup "
                "ON channel_blacklists(guild_id, channel_id, user_id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_member_warnings_guild_user "
                "ON member_warnings(guild_id, user_id, id DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_temp_roles_expiry "
                "ON temp_roles(expires_at);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_mod_notes_guild_user "
                "ON mod_notes(guild_id, user_id, id DESC);"
            )

            # 3. جدول إعدادات السيرفر والتذاكر
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    mod_log_channel INTEGER DEFAULT NULL,
                    welcome_channel INTEGER DEFAULT NULL,
                    auto_role_id INTEGER DEFAULT NULL,
                    anti_spam_enabled BOOLEAN DEFAULT 1,
                    anti_link_enabled BOOLEAN DEFAULT 1
                );
            """)
            # ترحيل تدريجي غير مدمّر لبقية الأعمدة (prefix, anti_nuke, captcha, ...)
            await _migrate_guild_settings(db)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS logging_channels (
                    guild_id INTEGER PRIMARY KEY,
                    log_messages INTEGER DEFAULT 0,
                    log_roles INTEGER DEFAULT 0,
                    log_channels INTEGER DEFAULT 0,
                    log_moderation INTEGER DEFAULT 0,
                    log_warnings INTEGER DEFAULT 0,
                    log_voice INTEGER DEFAULT 0,
                    log_sanctions INTEGER DEFAULT 0,
                    log_violations INTEGER DEFAULT 0,
                    log_automod INTEGER DEFAULT 0,
                    log_ticket INTEGER DEFAULT 0,
                    log_channel INTEGER DEFAULT 0,
                    log_server INTEGER DEFAULT 0,
                    log_member INTEGER DEFAULT 0,
                    log_message INTEGER DEFAULT 0,
                    log_react INTEGER DEFAULT 0
                );
            """)
            # Additive migration for the dedicated audit destinations. Existing
            # columns and rows remain untouched; only missing columns are added.
            await _migrate_logging_channels(db)
            # Durable security controls are separate additive tables. They do
            # not rewrite guild settings or any existing moderation records.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS security_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    culprit_id INTEGER NOT NULL,
                    culprit_name TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    mitigation_taken TEXT NOT NULL,
                    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_incidents_guild_time "
                "ON security_incidents(guild_id, id DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS security_whitelist (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS security_lockdown_state (
                    guild_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unlocked',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS security_lockdown_overwrites (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    send_messages INTEGER,
                    send_messages_in_threads INTEGER,
                    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, channel_id)
                );
            """)

            await db.execute("CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings(guild_id, user_id);")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS economy_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    actor_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    wallet_delta INTEGER NOT NULL DEFAULT 0,
                    details TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_economy_audit_guild "
                "ON economy_audit_logs(guild_id, created_at DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS economy_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    from_user_id INTEGER DEFAULT NULL,
                    to_user_id INTEGER DEFAULT NULL,
                    amount INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_economy_transactions_guild "
                "ON economy_transactions(guild_id, created_at DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tournament_scores (
                    guild_id INTEGER NOT NULL,
                    team_name TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, team_name)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS giveaways (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL DEFAULT 0,
                    prize TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_by INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS giveaway_entries (
                    giveaway_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (giveaway_id, user_id)
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_giveaways_due "
                "ON giveaways(status, ends_at);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tournaments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL,
                    max_players INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_by INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tournament_entries (
                    tournament_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tournament_id, user_id)
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tournaments_open "
                "ON tournaments(status, guild_id);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS invite_stats (
                    guild_id INTEGER NOT NULL,
                    inviter_id INTEGER NOT NULL,
                    uses INTEGER NOT NULL DEFAULT 0,
                    last_used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, inviter_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS rules_agreements (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    verified_role_id INTEGER,
                    agreed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS role_panels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER DEFAULT NULL,
                    role_ids TEXT NOT NULL,
                    PRIMARY KEY (guild_id, channel_id, message_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS rules_panels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id, message_id)
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS self_role_panels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    color TEXT NOT NULL DEFAULT '#5865f2',
                    emoji TEXT NOT NULL DEFAULT '🏷️',
                    role_specs TEXT NOT NULL DEFAULT '[]',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, message_id)
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_self_role_panels_guild "
                "ON self_role_panels(guild_id);"
            )
            await _migrate_legacy_level_schema(db)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_economy_audit_guild "
                "ON economy_audit_logs(guild_id, created_at DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_command_controls (
                    guild_id INTEGER NOT NULL,
                    command_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    allowed_roles TEXT NOT NULL DEFAULT '[]',
                    allowed_channels TEXT NOT NULL DEFAULT '[]',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, command_name)
                );
            """)
            async with db.execute("PRAGMA table_info(guild_command_controls)") as cur:
                command_control_columns = {row[1] for row in await cur.fetchall()}
            if "allowed_channels" not in command_control_columns:
                await db.execute(
                    "ALTER TABLE guild_command_controls "
                    "ADD COLUMN allowed_channels TEXT NOT NULL DEFAULT '[]'"
                )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_command_controls_guild "
                "ON guild_command_controls(guild_id);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS command_policies (
                    guild_id INTEGER NOT NULL,
                    command_name TEXT NOT NULL,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    aliases TEXT NOT NULL DEFAULT '[]',
                    allowed_roles TEXT NOT NULL DEFAULT '[]',
                    allowed_channels TEXT NOT NULL DEFAULT '[]',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, command_name)
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_command_policies_guild "
                "ON command_policies(guild_id);"
            )
            async with db.execute("PRAGMA table_info(command_policies)") as cur:
                command_policy_columns = {row[1] for row in await cur.fetchall()}
            policy_migrations = {
                "is_enabled": "INTEGER NOT NULL DEFAULT 1",
                "aliases": "TEXT NOT NULL DEFAULT '[]'",
                "allowed_roles": "TEXT NOT NULL DEFAULT '[]'",
                "allowed_channels": "TEXT NOT NULL DEFAULT '[]'",
                "auto_delete_seconds": "INTEGER NOT NULL DEFAULT 0",
                "response_style": "TEXT NOT NULL DEFAULT 'default'",
                "response_template": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT DEFAULT NULL",
            }
            for column, definition in policy_migrations.items():
                if column not in command_policy_columns:
                    await db.execute(
                        f"ALTER TABLE command_policies ADD COLUMN {column} {definition}"
                    )
            # Preserve policies created by older dashboard versions while
            # making command_policies the canonical store for new writes.
            await db.execute("""
                INSERT OR IGNORE INTO command_policies
                    (guild_id, command_name, is_enabled, aliases,
                     allowed_roles, allowed_channels, updated_at)
                SELECT guild_id, command_name, enabled, '[]',
                       allowed_roles, allowed_channels, updated_at
                FROM guild_command_controls
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_auto_responders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    trigger TEXT NOT NULL,
                    match_type TEXT NOT NULL,
                    response TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    cooldown_seconds REAL NOT NULL DEFAULT 5,
                    bucket_capacity INTEGER NOT NULL DEFAULT 1,
                    channel_id INTEGER DEFAULT NULL,
                    target_type TEXT NOT NULL DEFAULT 'everyone',
                    target_id INTEGER NOT NULL DEFAULT 0,
                    reaction_emoji TEXT NOT NULL DEFAULT '',
                    execution_count INTEGER NOT NULL DEFAULT 0,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, trigger, match_type, target_type, target_id)
                );
            """)
            async with db.execute("PRAGMA table_info(guild_auto_responders)") as cur:
                responder_columns = {row[1] for row in await cur.fetchall()}
            if "channel_id" not in responder_columns:
                await db.execute(
                    "ALTER TABLE guild_auto_responders ADD COLUMN channel_id INTEGER DEFAULT NULL"
                )
            if "execution_count" not in responder_columns:
                await db.execute(
                    "ALTER TABLE guild_auto_responders ADD COLUMN execution_count INTEGER NOT NULL DEFAULT 0"
                )
            if "target_type" not in responder_columns:
                await db.execute(
                    "ALTER TABLE guild_auto_responders "
                    "ADD COLUMN target_type TEXT NOT NULL DEFAULT 'everyone'"
                )
            if "target_id" not in responder_columns:
                await db.execute(
                    "ALTER TABLE guild_auto_responders "
                    "ADD COLUMN target_id INTEGER NOT NULL DEFAULT 0"
                )
            if "reaction_emoji" not in responder_columns:
                await db.execute(
                    "ALTER TABLE guild_auto_responders "
                    "ADD COLUMN reaction_emoji TEXT NOT NULL DEFAULT ''"
                )
            await _migrate_auto_responder_uniqueness(db)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_auto_responders_guild "
                "ON guild_auto_responders(guild_id, enabled);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_shortcuts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    trigger TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    announcement TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, trigger)
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_shortcuts_guild "
                "ON guild_shortcuts(guild_id, enabled);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_panels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    categories TEXT NOT NULL DEFAULT '[]',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, channel_id, message_id)
                );
            """)
            async with db.execute("PRAGMA table_info(ticket_panels)") as cur:
                ticket_panel_columns = {row[1] for row in await cur.fetchall()}
            ticket_panel_migrations = {
                "message_id": "INTEGER DEFAULT NULL",
                "title": "TEXT NOT NULL DEFAULT 'مركز الدعم والتذاكر'",
                "description": "TEXT NOT NULL DEFAULT ''",
                "color": "INTEGER NOT NULL DEFAULT 6514417",
                "mode": "TEXT NOT NULL DEFAULT 'dropdown'",
                "version": "INTEGER NOT NULL DEFAULT 1",
            }
            for column, definition in ticket_panel_migrations.items():
                if column not in ticket_panel_columns:
                    await db.execute(
                        f"ALTER TABLE ticket_panels ADD COLUMN {column} {definition}"
                    )
            # CRM-facing ticket tables are additive. The legacy ticket_* tables
            # remain the source of truth for existing commands and panels.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    ping_role_ids TEXT NOT NULL DEFAULT '[]',
                    staff_role_ids TEXT NOT NULL DEFAULT '[]',
                    description TEXT NOT NULL DEFAULT '',
                    emoji TEXT NOT NULL DEFAULT '🎫',
                    category_id INTEGER DEFAULT NULL,
                    welcome_msg TEXT NOT NULL DEFAULT '',
                    UNIQUE (guild_id, name)
                );
            """)
            async with db.execute("PRAGMA table_info(ticket_categories)") as cur:
                ticket_category_columns = {row[1] for row in await cur.fetchall()}
            ticket_category_migrations = {
                "panel_id": "INTEGER DEFAULT NULL",
                "label": "TEXT NOT NULL DEFAULT ''",
                "button_color": "TEXT NOT NULL DEFAULT 'primary'",
                "naming_format": "TEXT NOT NULL DEFAULT 'ticket-{count}'",
                "closed_naming_format": "TEXT NOT NULL DEFAULT 'closed-{count}'",
                "open_category_id": "INTEGER DEFAULT NULL",
                "closed_category_id": "INTEGER DEFAULT NULL",
                "welcome_message": "TEXT NOT NULL DEFAULT ''",
                "max_open_per_user": "INTEGER NOT NULL DEFAULT 1",
                "auto_close_hours": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in ticket_category_migrations.items():
                if column not in ticket_category_columns:
                    await db.execute(
                        f"ALTER TABLE ticket_categories ADD COLUMN {column} {definition}"
                    )
            # Keep the old name/welcome_msg contract readable while populating
            # the additive CRM vocabulary for upgraded installations.
            await db.execute(
                "UPDATE ticket_categories SET label = name "
                "WHERE label IS NULL OR label = ''"
            )
            await db.execute(
                "UPDATE ticket_categories SET welcome_message = welcome_msg "
                "WHERE (welcome_message IS NULL OR welcome_message = '') "
                "AND welcome_msg IS NOT NULL AND welcome_msg != ''"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_settings (
                    guild_id INTEGER PRIMARY KEY,
                    log_channel_id INTEGER DEFAULT NULL,
                    evaluation_channel_id INTEGER DEFAULT NULL,
                    allow_user_close INTEGER NOT NULL DEFAULT 0,
                    send_transcript_dm INTEGER NOT NULL DEFAULT 1,
                    default_open_category_id INTEGER DEFAULT NULL,
                    closed_category_id INTEGER DEFAULT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            async with db.execute("PRAGMA table_info(ticket_settings)") as cur:
                ticket_setting_columns = {row[1] for row in await cur.fetchall()}
            for column, definition in {
                "send_transcript_dm": "INTEGER NOT NULL DEFAULT 1",
                "default_open_category_id": "INTEGER DEFAULT NULL",
                "closed_category_id": "INTEGER DEFAULT NULL",
                "updated_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
            }.items():
                if column not in ticket_setting_columns:
                    await db.execute(
                        f"ALTER TABLE ticket_settings ADD COLUMN {column} {definition}"
                    )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_permissions (
                    guild_id INTEGER PRIMARY KEY,
                    permissions TEXT NOT NULL DEFAULT '{}',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER DEFAULT NULL,
                    action TEXT NOT NULL,
                    status TEXT DEFAULT NULL,
                    claimed_by INTEGER DEFAULT NULL,
                    closed_by INTEGER DEFAULT NULL,
                    staff_id INTEGER DEFAULT NULL,
                    target_user_id INTEGER DEFAULT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            async with db.execute("PRAGMA table_info(ticket_logs)") as cur:
                ticket_log_columns = {row[1] for row in await cur.fetchall()}
            for column, definition in {
                "channel_id": "INTEGER DEFAULT NULL",
                "status": "TEXT DEFAULT NULL",
                "claimed_by": "INTEGER DEFAULT NULL",
                "closed_by": "INTEGER DEFAULT NULL",
            }.items():
                if column not in ticket_log_columns:
                    await db.execute(
                        f"ALTER TABLE ticket_logs ADD COLUMN {column} {definition}"
                    )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_logs_guild_time "
                "ON ticket_logs(guild_id, created_at DESC, id DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_categories_guild "
                "ON ticket_categories(guild_id, id);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_config (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    message_id INTEGER,
                    embed_title TEXT NOT NULL DEFAULT 'الدعم الفني',
                    embed_description TEXT NOT NULL DEFAULT '',
                    embed_color INTEGER NOT NULL DEFAULT 5793266,
                    footer_text TEXT NOT NULL DEFAULT 'PR1ME TEAM Support',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            async with db.execute("PRAGMA table_info(ticket_config)") as cur:
                ticket_config_columns = {row[1] for row in await cur.fetchall()}
            ticket_config_migrations = {
                "closed_category_id": "INTEGER DEFAULT NULL",
                "log_channel_id": "INTEGER DEFAULT NULL",
                "evaluation_channel_id": "INTEGER DEFAULT NULL",
                "allow_user_close": "INTEGER NOT NULL DEFAULT 0",
                "send_transcript_dm": "INTEGER NOT NULL DEFAULT 1",
                "auto_close_minutes": "INTEGER NOT NULL DEFAULT 0",
                "open_limit": "INTEGER NOT NULL DEFAULT 1",
                "panel_mode": "TEXT NOT NULL DEFAULT 'dropdown'",
                "select_placeholder": "TEXT NOT NULL DEFAULT 'اختر القسم المناسب لطلبك'",
                "permissions_json": "TEXT NOT NULL DEFAULT '{}'",
                "close_config_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for column, definition in ticket_config_migrations.items():
                if column not in ticket_config_columns:
                    await db.execute(
                        f"ALTER TABLE ticket_config ADD COLUMN {column} {definition}"
                    )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_options (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    emoji TEXT NOT NULL DEFAULT '🎫',
                    role_id INTEGER DEFAULT NULL,
                    category_id INTEGER DEFAULT NULL,
                    welcome_msg TEXT NOT NULL DEFAULT ''
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_options_guild "
                "ON ticket_options(guild_id, id);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    expires_at DATETIME DEFAULT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_by INTEGER DEFAULT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, user_id)
                );
            """)
            async with db.execute("PRAGMA table_info(ticket_blacklist)") as cur:
                ticket_blacklist_columns = {row[1] for row in await cur.fetchall()}
            if "expiration" not in ticket_blacklist_columns:
                await db.execute(
                    "ALTER TABLE ticket_blacklist ADD COLUMN expiration DATETIME DEFAULT NULL"
                )
            await db.execute(
                "UPDATE ticket_blacklist SET expiration = expires_at "
                "WHERE expiration IS NULL AND expires_at IS NOT NULL"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_blacklist_lookup "
                "ON ticket_blacklist(guild_id, user_id, expires_at);"
            )
            # Step 6 clan operations and the dashboard-owned ticket dropdown
            # tables are additive. Keep these separate from legacy ticket and
            # gaming tables so existing commands retain their exact contracts.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS clan_applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    kd_ratio TEXT NOT NULL DEFAULT '',
                    device TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS clan_rosters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    lineup_name TEXT NOT NULL,
                    player_id INTEGER NOT NULL,
                    player_name TEXT NOT NULL,
                    role_title TEXT NOT NULL DEFAULT '',
                    display_order INTEGER NOT NULL DEFAULT 0
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS scrim_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    opponent_name TEXT NOT NULL,
                    score_prime INTEGER NOT NULL DEFAULT 0,
                    score_enemy INTEGER NOT NULL DEFAULT 0,
                    map_name TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT 'draw',
                    logged_by INTEGER NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_dropdown_configs (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    message_id INTEGER,
                    embed_title TEXT NOT NULL DEFAULT '🎫 مركز الدعم والتذاكر',
                    embed_description TEXT NOT NULL DEFAULT '',
                    embed_color TEXT NOT NULL DEFAULT '#5865F2',
                    footer_text TEXT NOT NULL DEFAULT 'Help Desk • اختر تصنيفاً لبدء المحادثة'
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_dropdown_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    emoji TEXT NOT NULL DEFAULT '🎫',
                    role_id INTEGER DEFAULT NULL,
                    category_id INTEGER DEFAULT NULL
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS announcement_reaction_settings (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id TEXT,
                    emoji_ids TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0,
                    activated_at REAL,
                    last_error TEXT,
                    last_error_at REAL,
                    updated_at REAL NOT NULL
                )
            """)
            announcement_columns = {
                "second_channel_id": "TEXT",
                "line_enabled": "INTEGER NOT NULL DEFAULT 0",
                "line_channel_ids": "TEXT NOT NULL DEFAULT '[]'",
                "line_image_id": "TEXT",
                "line_activated_at": "REAL",
                "line_last_error": "TEXT",
                "line_last_error_at": "REAL",
            }
            async with db.execute("PRAGMA table_info(announcement_reaction_settings)") as cursor:
                existing_announcement_columns = {row[1] for row in await cursor.fetchall()}
            for name, declaration in announcement_columns.items():
                if name not in existing_announcement_columns:
                    await db.execute(
                        f"ALTER TABLE announcement_reaction_settings ADD COLUMN {name} {declaration}"
                    )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS announcement_line_images (
                    guild_id INTEGER NOT NULL,
                    image_id TEXT NOT NULL,
                    mime TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, image_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS broadcast_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    author_id INTEGER,
                    message_type TEXT,
                    title TEXT,
                    content TEXT,
                    description TEXT,
                    color TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            async with db.execute("PRAGMA table_info(broadcast_logs)") as cur:
                broadcast_columns = {row[1] for row in await cur.fetchall()}
            if "description" not in broadcast_columns:
                await db.execute(
                    "ALTER TABLE broadcast_logs ADD COLUMN description TEXT DEFAULT ''"
                )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_clan_applications_guild_status "
                "ON clan_applications(guild_id, status, created_at DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_clan_rosters_guild_lineup "
                "ON clan_rosters(guild_id, lineup_name, display_order, id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_scrim_logs_guild_time "
                "ON scrim_logs(guild_id, timestamp DESC, id DESC);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_dropdown_categories_guild "
                "ON ticket_dropdown_categories(guild_id, id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_broadcast_logs_guild_time "
                "ON broadcast_logs(guild_id, sent_at DESC, id DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL,
                    category_key TEXT NOT NULL,
                    category_label TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    support_role_ids TEXT NOT NULL DEFAULT '[]',
                    senior_role_ids TEXT NOT NULL DEFAULT '[]',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'active',
                    claimed_by INTEGER DEFAULT NULL,
                    opened_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    first_response_at DATETIME DEFAULT NULL,
                    closed_at DATETIME DEFAULT NULL,
                    closed_by INTEGER DEFAULT NULL,
                    close_reason TEXT NOT NULL DEFAULT '',
                    rating INTEGER DEFAULT NULL
                );
            """)
            async with db.execute("PRAGMA table_info(tickets)") as cur:
                ticket_columns = {row[1] for row in await cur.fetchall()}
            ticket_migrations = {
                "intake_data": "TEXT NOT NULL DEFAULT '{}'",
                "waiting_since": "DATETIME DEFAULT NULL",
                "escalated_at": "DATETIME DEFAULT NULL",
                "last_user_message_at": "DATETIME DEFAULT NULL",
            }
            for column, definition in ticket_migrations.items():
                if column not in ticket_columns:
                    await db.execute(
                        f"ALTER TABLE tickets ADD COLUMN {column} {definition}"
                    )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tickets_guild_status "
                "ON tickets(guild_id, status);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tickets_user_category "
                "ON tickets(guild_id, user_id, category_key, status);"
            )
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_one_open_per_category "
                "ON tickets(guild_id, user_id, category_key) WHERE status != 'closed';"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    staff_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_notes_ticket "
                "ON ticket_notes(guild_id, ticket_id, created_at DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    content_text TEXT NOT NULL,
                    content_html TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_transcripts_guild "
                "ON ticket_transcripts(guild_id, created_at DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL UNIQUE,
                    guild_id INTEGER NOT NULL,
                    staff_id INTEGER DEFAULT NULL,
                    user_id INTEGER NOT NULL,
                    stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
                    feedback TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Existing installations may predate the CHECK constraint. Triggers
            # enforce the same invariant without rebuilding or copying user data.
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS ticket_ratings_stars_insert_guard
                BEFORE INSERT ON ticket_ratings
                WHEN NEW.stars < 1 OR NEW.stars > 5
                BEGIN
                    SELECT RAISE(ABORT, 'ticket_ratings_stars_check');
                END;
            """)
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS ticket_ratings_stars_update_guard
                BEFORE UPDATE OF stars ON ticket_ratings
                WHEN NEW.stars < 1 OR NEW.stars > 5
                BEGIN
                    SELECT RAISE(ABORT, 'ticket_ratings_stars_check');
                END;
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_ratings_staff "
                "ON ticket_ratings(guild_id, staff_id);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS canned_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'عام',
                    shortcut TEXT DEFAULT NULL,
                    sticker_id INTEGER DEFAULT NULL,
                    created_by INTEGER DEFAULT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, title)
                );
            """)
            async with db.execute("PRAGMA table_info(canned_responses)") as cur:
                canned_columns = {row[1] for row in await cur.fetchall()}
            if "shortcut" not in canned_columns:
                await db.execute(
                    "ALTER TABLE canned_responses ADD COLUMN shortcut TEXT DEFAULT NULL"
                )
            if "sticker_id" not in canned_columns:
                await db.execute(
                    "ALTER TABLE canned_responses ADD COLUMN sticker_id INTEGER DEFAULT NULL"
                )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_canned_responses_guild "
                "ON canned_responses(guild_id, updated_at DESC);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    reminder TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminders_due "
                "ON reminders(status, due_at);"
            )
            # Step 5 reminders use a dedicated table so the new command
            # contract can evolve without changing the legacy reminders API.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    reminder_text TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    claimed_at DATETIME DEFAULT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            async with db.execute("PRAGMA table_info(user_reminders)") as cur:
                user_reminder_columns = {row[1] for row in await cur.fetchall()}
            if "reminder_text" not in user_reminder_columns:
                await db.execute(
                    "ALTER TABLE user_reminders ADD COLUMN reminder_text TEXT NOT NULL DEFAULT ''"
                )
                if "text" in user_reminder_columns:
                    await db.execute(
                        "UPDATE user_reminders SET reminder_text = text "
                        "WHERE reminder_text = ''"
                    )
            if "status" not in user_reminder_columns:
                await db.execute(
                    "ALTER TABLE user_reminders ADD COLUMN status "
                    "TEXT NOT NULL DEFAULT 'pending'"
                )
            if "claimed_at" not in user_reminder_columns:
                await db.execute(
                    "ALTER TABLE user_reminders ADD COLUMN claimed_at "
                    "DATETIME DEFAULT NULL"
                )
            # Preserve reminders from the legacy table without deleting or
            # changing that table. The second insert handles id collisions
            # with existing user_reminders rows.
            await db.execute(
                """
                INSERT OR IGNORE INTO user_reminders
                    (id, guild_id, user_id, channel_id, reminder_text,
                     remind_at, status, created_at)
                SELECT id, guild_id, user_id, channel_id, reminder, due_at,
                       status, created_at
                FROM reminders
                """
            )
            await db.execute(
                """
                INSERT INTO user_reminders
                    (guild_id, user_id, channel_id, reminder_text,
                     remind_at, status, created_at)
                SELECT r.guild_id, r.user_id, r.channel_id, r.reminder,
                       r.due_at, r.status, r.created_at
                FROM reminders r
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM user_reminders u
                    WHERE u.guild_id = r.guild_id
                      AND u.user_id = r.user_id
                      AND u.channel_id = r.channel_id
                      AND u.reminder_text = r.reminder
                      AND u.remind_at = r.due_at
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_reminders_due "
                "ON user_reminders(status, remind_at, id);"
            )
            # Gaming & esports additions are intentionally isolated from the
            # existing tournament, giveaway, and ticket tables.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS scrim_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    title TEXT,
                    game_type TEXT,
                    team_size INTEGER,
                    max_slots INTEGER,
                    message_id INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS scrim_registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scrim_id INTEGER,
                    slot_number INTEGER,
                    team_name TEXT,
                    leader_id INTEGER,
                    members_json TEXT,
                    checked_in INTEGER DEFAULT 0,
                    UNIQUE(scrim_id, slot_number)
                );
            """)
            async with db.execute("PRAGMA table_info(scrim_configs)") as cur:
                scrim_config_columns = {row[1] for row in await cur.fetchall()}
            if "message_id" not in scrim_config_columns:
                await db.execute(
                    "ALTER TABLE scrim_configs ADD COLUMN message_id INTEGER DEFAULT 0"
                )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_scrim_configs_guild_active "
                "ON scrim_configs(guild_id, is_active);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_scrim_registrations_scrim "
                "ON scrim_registrations(scrim_id, slot_number);"
            )
            # Analytics storage is isolated from all legacy bot tables. The
            # dashboard can build indexed summaries without touching command,
            # moderation, economy, or ticket records.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS analytics_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    user_id INTEGER,
                    is_voice BOOLEAN DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_analytics_guild_time "
                "ON analytics_messages (guild_id, timestamp);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_analytics_user "
                "ON analytics_messages (user_id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_analytics_channel "
                "ON analytics_messages (channel_id);"
            )
            await db.execute("""
                CREATE TABLE IF NOT EXISTS analytics_voice_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER,
                    user_id INTEGER,
                    started_at TIMESTAMP NOT NULL,
                    ended_at TIMESTAMP,
                    duration_seconds INTEGER DEFAULT 0
                );
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_analytics_voice_guild_time "
                "ON analytics_voice_sessions (guild_id, started_at);"
            )
            await _ensure_canonical_views(db)

            await db.commit()
            logger.info("[DB] جميع الجداول والفهارس تعمل بكفاءة عالية.")
    except Exception as e:
        logger.error(f"[DB_FATAL] خطأ أثناء إنشاء الجداول: {e}")
        raise
    _settings_cache.clear()
    _stats_cache.clear()
    COMMAND_CACHE.clear()
    LOG_ROUTING_CACHE.clear()


# -------------------------------------------------------------
# Lona leveling database helpers
# -------------------------------------------------------------
_LEVEL_SETTINGS_JSON_FIELDS = {
    "command_rank_channels",
    "command_rank_aliases",
    "command_top_channels",
    "command_top_aliases",
    "reaction_allowed_channels",
    "text_allowed_channels",
    "timed_xp_boosts",
    "prime_controls",
    "card_design",
}
_LEVEL_SETTINGS_MUTABLE_FIELDS = {
    "is_enabled",
    "command_rank_enabled",
    "command_rank_channels",
    "command_rank_aliases",
    "command_top_enabled",
    "command_top_channels",
    "command_top_aliases",
    "web_leaderboard_enabled",
    "web_slug",
    "xp_multiplier",
    "message_cooldown_seconds",
    "text_xp_enabled",
    "text_xp_min",
    "text_xp_max",
    "text_allowed_channels",
    "reaction_xp_enabled",
    "timed_xp_boosts",
    "boost_multiplier",
    "boost_expires_at",
    "streak_enabled",
    "streak_channel_id",
    "streak_daily_xp",
    "streak_max_cap",
    "reaction_xp_reactor",
    "reaction_xp_author",
    "reaction_xp_amount",
    "reaction_cooldown_seconds",
    "reaction_allowed_channels",
    "voice_xp_enabled",
    "voice_xp_per_minute",
    "voice_mute_no_xp",
    "voice_deafen_no_xp",
    "voice_min_two_members",
    "voice_min_members",
    "voice_diminishing_enabled",
    "voice_diminishing_mins",
    "voice_diminishing_rate",
    "voice_separate_levels",
    "rewards_single_highest",
    "dynamic_top_day_role",
    "dynamic_top_week_role",
    "dynamic_top_month_role",
    "dynamic_top_all_role",
    "weekly_reset_day",
    "card_layout",
    "card_particles",
    "card_animated_bar",
    "card_color",
    "card_bg_url",
    "card_design",
    "card_show_stats",
    "levelup_channel_id",
    "levelup_enabled",
    "levelup_channel_type",
    "levelup_format",
    "levelup_title",
    "levelup_template",
    "levelup_voice_enabled",
    "levelup_voice_channel_id",
    "levelup_voice_template",
    "overtake_alert_enabled",
    "overtake_channel_id",
    "overtake_template",
    "milestone_alert_enabled",
    "milestone_channel_id",
    "milestone_template",
    "bot_embed_color",
    "prime_controls",
}
_USER_LEVEL_MUTABLE_FIELDS = {
    "text_xp",
    "text_level",
    "voice_xp",
    "voice_level",
    "total_messages",
    "total_voice_seconds",
    "current_streak",
    "last_daily_claim",
    "last_message_at",
}


def _decode_level_settings(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    result = dict(row)
    for key in _LEVEL_SETTINGS_JSON_FIELDS:
        value = result.get(key)
        default = "{}" if key in {"prime_controls", "card_design"} else "[]"
        result[key] = json.loads(value or default)
    return result


def _encode_level_setting(key: str, value: Any) -> Any:
    if key not in _LEVEL_SETTINGS_JSON_FIELDS:
        return value
    if isinstance(value, str):
        value = json.loads(value)
    if key in {"prime_controls", "card_design"}:
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a JSON object")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a JSON array")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def get_level_settings(guild_id: int) -> Optional[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM level_settings WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            return _decode_level_settings(await cur.fetchone())


async def create_default_level_settings(guild_id: int) -> Dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            "INSERT OR IGNORE INTO level_settings (guild_id) VALUES (?)",
            (int(guild_id),),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM level_settings WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    result = _decode_level_settings(row)
    if result is None:
        raise RuntimeError("level settings row disappeared after creation")
    return result


async def update_level_settings(
    guild_id: int,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("level settings data must be a mapping")
    unknown = set(data) - _LEVEL_SETTINGS_MUTABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown level setting: {sorted(unknown)[0]}")
    data = dict(data)
    if "streak_channel_id" in data and data["streak_channel_id"] is not None:
        raw_channel_id = data["streak_channel_id"]
        if isinstance(raw_channel_id, bool):
            raise ValueError("streak_channel_id must be a positive Discord channel ID")
        try:
            channel_id = int(raw_channel_id)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "streak_channel_id must be a positive Discord channel ID"
            ) from exc
        if channel_id <= 0 or channel_id > 9_223_372_036_854_775_807:
            raise ValueError("streak_channel_id is outside the SQLite integer range")
        if isinstance(raw_channel_id, float) and not raw_channel_id.is_integer():
            raise ValueError("streak_channel_id must be a whole number")
        if isinstance(raw_channel_id, str) and not raw_channel_id.strip().isdecimal():
            raise ValueError("streak_channel_id must be a positive Discord channel ID")
        data["streak_channel_id"] = channel_id
    if "voice_min_two_members" in data and "voice_min_members" not in data:
        legacy_minimum = data["voice_min_two_members"]
        if legacy_minimum not in (False, True, 0, 1):
            raise ValueError("voice_min_two_members must be boolean")
        data["voice_min_members"] = 2 if bool(legacy_minimum) else 1
    await create_default_level_settings(guild_id)
    if not data:
        current = await get_level_settings(guild_id)
        if current is None:
            raise RuntimeError("level settings row disappeared after creation")
        return current
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            if data.get("web_slug"):
                async with db.execute(
                    "SELECT 1 FROM level_settings WHERE web_slug = ? AND guild_id <> ? LIMIT 1",
                    (str(data["web_slug"]), int(guild_id)),
                ) as cur:
                    if await cur.fetchone():
                        raise LevelingSlugConflict()
            for new_col, value in data.items():
                # new_col comes only from _LEVEL_SETTINGS_MUTABLE_FIELDS;
                # all user-provided values remain bound SQL parameters.
                await db.execute(
                    f"UPDATE level_settings SET {new_col} = ? WHERE guild_id = ?",
                    (_encode_level_setting(new_col, value), int(guild_id)),
                )
            await db.execute(
                "UPDATE level_settings SET revision = revision + 1 WHERE guild_id = ?",
                (int(guild_id),),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        async with db.execute(
            "SELECT * FROM level_settings WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    result = _decode_level_settings(row)
    if result is None:
        raise RuntimeError("level settings row disappeared after update")
    return result


class LevelingConflict(Exception):
    """Raised when a dashboard save was based on an older settings revision."""

    def __init__(self, current_revision: int):
        self.current_revision = int(current_revision)
        super().__init__("leveling settings changed since they were loaded")


class LevelingSlugConflict(Exception):
    """Raised when a public leaderboard slug is already assigned elsewhere."""


async def replace_level_dashboard_config(
    guild_id: int,
    expected_revision: int,
    settings: Dict[str, Any],
    rewards: List[Dict[str, Any]],
    multipliers: List[Dict[str, Any]],
    blacklist: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Commit one validated dashboard snapshot and its child rows atomically."""
    if not isinstance(settings, dict):
        raise ValueError("level settings data must be a mapping")
    unknown = set(settings) - _LEVEL_SETTINGS_MUTABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown level setting: {sorted(unknown)[0]}")
    encoded = {
        key: _encode_level_setting(key, value)
        for key, value in settings.items()
    }
    guild_id = int(guild_id)
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                "INSERT OR IGNORE INTO level_settings (guild_id) VALUES (?)",
                (guild_id,),
            )
            async with db.execute(
                "SELECT revision FROM level_settings WHERE guild_id = ?",
                (guild_id,),
            ) as cur:
                row = await cur.fetchone()
            revision = int(row["revision"] or 0) if row else 0
            if revision != int(expected_revision):
                await db.rollback()
                raise LevelingConflict(revision)
            public_slug = settings.get("web_slug")
            if public_slug:
                async with db.execute(
                    "SELECT 1 FROM level_settings WHERE web_slug = ? AND guild_id <> ? LIMIT 1",
                    (str(public_slug), guild_id),
                ) as cur:
                    if await cur.fetchone():
                        raise LevelingSlugConflict()
            for column, value in encoded.items():
                await db.execute(
                    f"UPDATE level_settings SET {column} = ? WHERE guild_id = ?",
                    (value, guild_id),
                )
            await db.execute(
                "UPDATE level_settings SET revision = revision + 1 WHERE guild_id = ?",
                (guild_id,),
            )
            await db.execute(
                "DELETE FROM level_role_rewards WHERE guild_id = ?", (guild_id,)
            )
            await db.executemany(
                """
                INSERT INTO level_role_rewards
                    (guild_id, reward_type, level_required, role_id)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        guild_id, item["reward_type"], int(item["level_required"]),
                        int(item["role_id"]),
                    )
                    for item in rewards
                ],
            )
            await db.execute(
                "DELETE FROM level_multipliers WHERE guild_id = ?", (guild_id,)
            )
            await db.executemany(
                """
                INSERT INTO level_multipliers
                    (guild_id, target_type, target_id, multiplier)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        guild_id, item["target_type"], int(item["target_id"]),
                        float(item["multiplier"]),
                    )
                    for item in multipliers
                ],
            )
            await db.execute(
                "DELETE FROM level_blacklist WHERE guild_id = ?", (guild_id,)
            )
            await db.execute(
                "DELETE FROM level_user_blacklist WHERE guild_id = ?", (guild_id,)
            )
            await db.executemany(
                """
                INSERT INTO level_blacklist (guild_id, target_type, target_id)
                VALUES (?, ?, ?)
                """,
                [
                    (guild_id, item["target_type"], int(item["target_id"]))
                    for item in blacklist if item["target_type"] != "user"
                ],
            )
            await db.executemany(
                """
                INSERT INTO level_user_blacklist (guild_id, target_id)
                VALUES (?, ?)
                """,
                [
                    (guild_id, int(item["target_id"]))
                    for item in blacklist if item["target_type"] == "user"
                ],
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        async with db.execute(
            "SELECT * FROM level_settings WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
    result = _decode_level_settings(row)
    if result is None:
        raise RuntimeError("level settings row disappeared after dashboard save")
    return result


async def get_user_level(
    guild_id: int,
    user_id: int,
) -> Optional[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


def _level_text_values(guild_id, user_id, xp, row):
    """Shared text progression for every leveling XP source."""
    from level_progression import level_from_xp

    old_xp = int(row["text_xp"] or 0) if row else 0
    old_level = int(row["text_level"] or 0) if row else 0
    new_xp = old_xp + int(xp)
    return {
        "guild_id": int(guild_id), "user_id": int(user_id),
        "old_xp": old_xp, "old_level": old_level,
        "text_xp": new_xp, "text_level": level_from_xp(new_xp),
        "xp_awarded": int(xp),
    }


async def _level_text_overtakes(db, guild_id, user_id, old_xp, new_xp, enabled, projections=None):
    """Indexed crossed-range query; no full leaderboard materialization.

    New positive-XP participants enter without alerts. Ties follow the existing
    leaderboard's user-ID ordering. All comparisons share the award transaction.
    """
    if not enabled or old_xp <= 0 or new_xp <= old_xp:
        return []
    async with db.execute(
        """
        SELECT user_id FROM user_levels
        WHERE guild_id = ? AND user_id != ? AND text_xp > 0
          AND text_xp BETWEEN ? AND ?
          AND (text_xp > ? OR (text_xp = ? AND user_id < ?))
          AND (text_xp < ? OR (text_xp = ? AND user_id > ?))
        ORDER BY text_xp DESC, user_id ASC
        """,
        (int(guild_id), int(user_id), old_xp, new_xp,
         old_xp, old_xp, int(user_id), new_xp, new_xp, int(user_id)),
    ) as cur:
        passed = await cur.fetchall()
    if not passed:
        return []
    async with db.execute(
        """
        SELECT 1 + COUNT(*) FROM user_levels
        WHERE guild_id = ? AND
            (text_xp > ? OR (text_xp = ? AND user_id < ?))
        """,
        (int(guild_id), new_xp, new_xp, int(user_id)),
    ) as cur:
        new_rank = (await cur.fetchone())[0]
    previous_rank = new_rank + len(passed)
    if projections:
        # A reaction can credit two people atomically. Compare their final
        # positions, not temporary positions during sequential SQL updates.
        passed = [
            item for item in passed
            if item[0] not in projections
            or projections[item[0]][1] < new_xp
            or (projections[item[0]][1] == new_xp and item[0] > user_id)
        ]
        for other_id, (previous_xp, final_xp) in projections.items():
            if other_id != user_id:
                before_ahead = previous_xp > new_xp or (previous_xp == new_xp and other_id < user_id)
                after_ahead = final_xp > new_xp or (final_xp == new_xp and other_id < user_id)
                new_rank += int(after_ahead) - int(before_ahead)
    if new_rank >= previous_rank:
        return []
    return [
        {"passed_id": item[0], "new_rank": new_rank,
         "previous_rank": previous_rank}
        for item in passed
    ]


async def _add_level_text_credit(db, guild_id, user_id, xp, row, detect_overtakes):
    values = _level_text_values(guild_id, user_id, xp, row)
    values["overtakes"] = await _level_text_overtakes(
        db, guild_id, user_id, values["old_xp"], values["text_xp"], detect_overtakes)
    await db.execute(
        """
        INSERT INTO user_levels (guild_id, user_id, text_xp, text_level)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            text_xp = excluded.text_xp, text_level = excluded.text_level
        """,
        (int(guild_id), int(user_id), values["text_xp"], values["text_level"]),
    )
    return values


def _level_utc(value):
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _level_streak_local(value):
    """Normalize an instant to the fixed streak calendar (midnight Riyadh)."""
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(STREAK_TIMEZONE)


async def record_level_streak_activity(
    guild_id: int,
    user_id: int,
    channel_id: int,
    activity_at: datetime,
) -> Dict[str, Any]:
    """Atomically record one eligible member activity for a Riyadh calendar day.

    The ledger's UNIQUE key is the final duplicate guard. BEGIN IMMEDIATE keeps
    insertion, streak calculation, and state updates in one serialized SQLite
    transaction across bot instances and restarts.
    """
    guild_id, user_id, channel_id = int(guild_id), int(user_id), int(channel_id)
    local_activity = _level_streak_local(activity_at)
    activity_day = local_activity.date()
    activity_date = activity_day.isoformat()
    first_activity_at = local_activity.isoformat()

    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                """
                SELECT is_enabled, streak_enabled, streak_channel_id
                FROM level_settings WHERE guild_id = ?
                """,
                (guild_id,),
            ) as cur:
                settings = await cur.fetchone()
            if (
                settings is None
                or not settings["is_enabled"]
                or not settings["streak_enabled"]
            ):
                await db.rollback()
                return {"status": "disabled"}
            configured_channel = settings["streak_channel_id"]
            if configured_channel is None:
                await db.rollback()
                return {"status": "channel_not_configured"}
            if int(configured_channel) != channel_id:
                await db.rollback()
                return {"status": "wrong_channel"}

            async with db.execute(
                """
                SELECT current_streak, best_streak, last_daily_claim
                FROM user_levels WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ) as cur:
                user_row = await cur.fetchone()
            async with db.execute(
                """
                SELECT MAX(activity_date) FROM streak_daily_activity
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ) as cur:
                latest_ledger_row = await cur.fetchone()

            latest_ledger_day = (
                date.fromisoformat(latest_ledger_row[0])
                if latest_ledger_row and latest_ledger_row[0]
                else None
            )
            legacy_claim_day = (
                _level_streak_local(user_row["last_daily_claim"]).date()
                if user_row and user_row["last_daily_claim"]
                else None
            )
            latest_persisted_day = max(
                (day for day in (latest_ledger_day, legacy_claim_day) if day is not None),
                default=None,
            )

            # A legacy manual claim on this date still occupies the day, but it
            # must not manufacture a message-activity ledger row.
            if (
                legacy_claim_day is not None
                and legacy_claim_day == activity_day
                and latest_ledger_day != activity_day
            ):
                await db.rollback()
                return {
                    "status": "duplicate",
                    "reason": "already_claimed",
                    "current_streak": int(user_row["current_streak"] or 0) if user_row else 0,
                    "best_streak": int(user_row["best_streak"] or 0) if user_row else 0,
                    "activity_date": activity_date,
                }

            cursor = await db.execute(
                """
                INSERT INTO streak_daily_activity (
                    guild_id, user_id, activity_date, first_activity_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, activity_date) DO NOTHING
                """,
                (guild_id, user_id, activity_date, first_activity_at),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return {
                    "status": "duplicate",
                    "reason": "already_claimed",
                    "current_streak": int(user_row["current_streak"] or 0) if user_row else 0,
                    "best_streak": int(user_row["best_streak"] or 0) if user_row else 0,
                    "activity_date": activity_date,
                }

            current_streak = int(user_row["current_streak"] or 0) if user_row else 0
            previous_streak = current_streak
            best_streak = max(
                int(user_row["best_streak"] or 0) if user_row else 0,
                current_streak,
            )
            if latest_persisted_day is not None and latest_persisted_day > activity_day:
                # Delayed/replayed older events may fill history, but cannot
                # roll the live streak state backwards.
                await db.commit()
                return {
                    "status": "success",
                    "current_streak": current_streak,
                    "best_streak": best_streak,
                    "activity_date": activity_date,
                    "streak_updated": False,
                    "previous_streak": previous_streak,
                }

            if latest_persisted_day == activity_day - timedelta(days=1):
                current_streak = max(0, current_streak) + 1
            else:
                current_streak = 1
            best_streak = max(0, best_streak, current_streak)

            await db.execute(
                "INSERT OR IGNORE INTO user_levels (guild_id, user_id) VALUES (?, ?)",
                (guild_id, user_id),
            )
            await db.execute(
                """
                UPDATE user_levels
                SET current_streak = ?, best_streak = ?, last_daily_claim = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (
                    current_streak, best_streak, first_activity_at,
                    guild_id, user_id,
                ),
            )
            await db.commit()
            return {
                "status": "success",
                "current_streak": current_streak,
                "best_streak": best_streak,
                "activity_date": activity_date,
                "last_daily_claim": first_activity_at,
                "streak_updated": True,
                "previous_streak": previous_streak,
            }
        except BaseException:
            await db.rollback()
            raise


def _validated_streak_image(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("streak image must be a public HTTPS URL")
    parts = urlparse(value)
    if parts.scheme != "https":
        raise ValueError("streak image must use HTTPS")
    from cogs.card_images import validate_url
    return validate_url(value)


async def get_streak_stages(include_disabled: bool = False) -> list[Dict[str, Any]]:
    query = "SELECT * FROM streak_stages"
    if not include_disabled:
        query += " WHERE enabled = 1"
    query += " ORDER BY threshold ASC, stage_key ASC"
    async with connect(aiosqlite.Row) as db:
        async with db.execute(query) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def upsert_streak_stage(stage: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(stage, dict):
        raise ValueError("streak stage must be a mapping")
    key = str(stage.get("stage_key") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", key):
        raise ValueError("stage_key must be a short lowercase identifier")
    threshold = stage.get("threshold")
    if isinstance(threshold, bool):
        raise ValueError("threshold must be a positive whole number")
    try:
        threshold = int(threshold)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("threshold must be a positive whole number") from exc
    if threshold <= 0 or (isinstance(stage.get("threshold"), float)
                          and not stage["threshold"].is_integer()):
        raise ValueError("threshold must be a positive whole number")
    name = str(stage.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("stage name must contain 1 to 80 characters")
    message = stage.get("message")
    message = str(message).strip() if message is not None else None
    if message is not None and len(message) > 1900:
        raise ValueError("stage-up message must be at most 1900 characters")
    color = str(stage.get("color") or "")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("stage color must be a six-digit hex color")
    image = _validated_streak_image(stage.get("image"))
    reaction = stage.get("reaction")
    reaction = str(reaction).strip() if reaction is not None else None
    if reaction is not None and len(reaction) > 100:
        raise ValueError("stage reaction is too long")
    description = str(stage.get("description") or "").strip()
    if len(description) > 400:
        raise ValueError("stage description is too long")
    glow = stage.get("glow", 0)
    if isinstance(glow, bool):
        raise ValueError("glow must be an integer from 0 to 100")
    try:
        glow = int(glow)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("glow must be an integer from 0 to 100") from exc
    if not 0 <= glow <= 100 or (
        isinstance(stage.get("glow"), float) and not stage["glow"].is_integer()
    ):
        raise ValueError("glow must be an integer from 0 to 100")
    particle = str(stage.get("particle") or "none")
    if particle not in STREAK_PARTICLES:
        raise ValueError("unsupported streak particle effect")
    enabled = stage.get("enabled", True)
    if enabled not in (True, False, 0, 1):
        raise ValueError("enabled must be boolean")
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            INSERT INTO streak_stages
                (stage_key, threshold, name, message, image, color, reaction,
                 description, glow, particle, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stage_key) DO UPDATE SET
                threshold = excluded.threshold,
                name = excluded.name,
                message = excluded.message,
                image = excluded.image,
                color = excluded.color,
                reaction = excluded.reaction,
                description = excluded.description,
                glow = excluded.glow,
                particle = excluded.particle,
                enabled = excluded.enabled
            """,
            (
                key, threshold, name, message, image, color.upper(), reaction,
                description, glow, particle, int(bool(enabled)),
            ),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM streak_stages WHERE stage_key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise RuntimeError("streak stage disappeared after saving")
    return dict(row)


async def get_streak_milestones(include_disabled: bool = False) -> list[Dict[str, Any]]:
    query = "SELECT * FROM streak_milestones"
    if not include_disabled:
        query += " WHERE enabled = 1"
    query += " ORDER BY threshold ASC"
    async with connect(aiosqlite.Row) as db:
        async with db.execute(query) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_level_streak_experience_config(
    guild_id: int, settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return guild-scoped presentation rules, falling back to Phase 3 rows."""
    settings = settings if settings is not None else await get_level_settings(guild_id)
    from prime_level_controls import controls_with_defaults

    controls = controls_with_defaults(
        settings.get("prime_controls") if settings else None, settings or {},
    )
    streak = controls["streak"]
    stages = streak.get("stages")
    milestones = streak.get("milestones")
    if stages is None:
        stages = await get_streak_stages()
    else:
        stages = [
            dict(stage) for stage in stages
            if isinstance(stage, dict) and stage.get("enabled", True)
        ]
    if milestones is None:
        milestones = await get_streak_milestones()
    else:
        milestones = [
            dict(item) for item in milestones
            if isinstance(item, dict) and item.get("enabled", True)
        ]
    return {
        "controls": streak,
        "stages": stages,
        "milestones": milestones,
    }


async def upsert_streak_milestone(milestone: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(milestone, dict):
        raise ValueError("streak milestone must be a mapping")
    threshold = milestone.get("threshold")
    if isinstance(threshold, bool):
        raise ValueError("threshold must be a positive whole number")
    try:
        threshold = int(threshold)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("threshold must be a positive whole number") from exc
    if threshold <= 0 or (
        isinstance(milestone.get("threshold"), float)
        and not milestone["threshold"].is_integer()
    ):
        raise ValueError("threshold must be a positive whole number")
    message = str(milestone.get("message") or "").strip()
    if len(message) > 1900:
        raise ValueError("milestone message must be at most 1900 characters")
    image = _validated_streak_image(milestone.get("image"))
    reaction = milestone.get("reaction")
    reaction = str(reaction).strip() if reaction is not None else None
    if reaction is not None and len(reaction) > 100:
        raise ValueError("milestone reaction is too long")
    enabled = milestone.get("enabled", True)
    if enabled not in (True, False, 0, 1):
        raise ValueError("enabled must be boolean")
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            INSERT INTO streak_milestones
                (threshold, message, image, reaction, enabled)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(threshold) DO UPDATE SET
                message = excluded.message,
                image = excluded.image,
                reaction = excluded.reaction,
                enabled = excluded.enabled
            """,
            (threshold, message, image, reaction, int(bool(enabled))),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM streak_milestones WHERE threshold = ?", (threshold,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise RuntimeError("streak milestone disappeared after saving")
    return dict(row)


async def record_streak_experience_events(
    guild_id: int,
    user_id: int,
    previous_streak: int,
    current_streak: int,
    activity_date: str,
    stages: Optional[list[Dict[str, Any]]] = None,
    milestones: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, list[Dict[str, Any]]]:
    """Reserve each attained stage/milestone once per user, even across restarts."""
    previous_streak = max(0, int(previous_streak))
    current_streak = max(0, int(current_streak))
    if stages is None:
        stages = await get_streak_stages()
    if milestones is None:
        milestones = await get_streak_milestones()
    created = {"stages": [], "milestones": []}
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            for stage in stages:
                threshold = int(stage["threshold"])
                if previous_streak < threshold <= current_streak:
                    cursor = await db.execute(
                        """
                        INSERT OR IGNORE INTO streak_experience_events
                            (guild_id, user_id, event_key, event_type,
                             threshold, activity_date)
                        VALUES (?, ?, ?, 'stage', ?, ?)
                        """,
                        (
                            int(guild_id), int(user_id),
                            f"stage:{stage['stage_key']}", threshold,
                            str(activity_date),
                        ),
                    )
                    if cursor.rowcount == 1:
                        created["stages"].append(stage)
            for milestone in milestones:
                threshold = int(milestone["threshold"])
                if previous_streak < threshold <= current_streak:
                    cursor = await db.execute(
                        """
                        INSERT OR IGNORE INTO streak_experience_events
                            (guild_id, user_id, event_key, event_type,
                             threshold, activity_date)
                        VALUES (?, ?, ?, 'milestone', ?, ?)
                        """,
                        (
                            int(guild_id), int(user_id),
                            f"milestone:{threshold}", threshold,
                            str(activity_date),
                        ),
                    )
                    if cursor.rowcount == 1:
                        created["milestones"].append(milestone)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    return created


async def get_streak_ranks(guild_id: int, user_id: int) -> Dict[str, int]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT current_streak FROM user_levels WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
        score = max(0, int(row["current_streak"] or 0)) if row else 0
        async with db.execute(
            """
            SELECT COUNT(*) + 1 AS rank
            FROM (
                SELECT user_id, MAX(COALESCE(current_streak, 0)) AS best
                FROM user_levels WHERE guild_id = ? GROUP BY user_id
            )
            WHERE best > ?
            """,
            (int(guild_id), score),
        ) as cur:
            server_rank = int((await cur.fetchone())["rank"])
        async with db.execute(
            """
            SELECT COUNT(*) + 1 AS rank
            FROM (
                SELECT user_id, MAX(COALESCE(current_streak, 0)) AS best
                FROM user_levels GROUP BY user_id
            )
            WHERE best > ?
            """,
            (score,),
        ) as cur:
            global_rank = int((await cur.fetchone())["rank"])
    return {"server_rank": server_rank, "global_rank": global_rank}


async def get_streak_leaderboard(
    guild_id: int,
    limit: int = 10,
) -> List[Dict[str, int]]:
    """Read the existing server streak records without creating parallel state."""
    limit = max(1, min(20, int(limit)))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT user_id, MAX(COALESCE(current_streak, 0)) AS current_streak
            FROM user_levels
            WHERE guild_id = ? AND COALESCE(current_streak, 0) > 0
            GROUP BY user_id
            ORDER BY current_streak DESC, user_id ASC
            LIMIT ?
            """,
            (int(guild_id), limit),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"user_id": int(row["user_id"]), "current_streak": int(row["current_streak"])}
        for row in rows
    ]


async def get_streak_dashboard_analytics(guild_id: int) -> Dict[str, Any]:
    """Aggregate existing streak records without synthesizing member or history data."""
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT
                COUNT(*) AS tracked_members,
                COALESCE(SUM(CASE WHEN COALESCE(current_streak, 0) > 0 THEN 1 ELSE 0 END), 0)
                    AS current_streak_members,
                COALESCE(SUM(COALESCE(current_streak, 0)), 0)
                    AS total_current_streak_days,
                COALESCE(AVG(CASE WHEN COALESCE(current_streak, 0) > 0
                    THEN current_streak END), 0) AS average_current_streak,
                COALESCE(MAX(COALESCE(current_streak, 0)), 0)
                    AS highest_current_streak,
                COALESCE(SUM(CASE WHEN COALESCE(best_streak, 0) > 0 THEN 1 ELSE 0 END), 0)
                    AS members_with_best_streak,
                COALESCE(AVG(CASE WHEN COALESCE(best_streak, 0) > 0
                    THEN best_streak END), 0) AS average_best_streak,
                COALESCE(MAX(COALESCE(best_streak, 0)), 0) AS highest_best_streak
            FROM user_levels
            WHERE guild_id = ?
              AND (COALESCE(current_streak, 0) > 0 OR COALESCE(best_streak, 0) > 0)
            """,
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    return {
        "tracked_members": int(row["tracked_members"] or 0),
        "current_streak_members": int(row["current_streak_members"] or 0),
        "total_current_streak_days": int(row["total_current_streak_days"] or 0),
        "average_current_streak": round(float(row["average_current_streak"] or 0), 2),
        "highest_current_streak": int(row["highest_current_streak"] or 0),
        "members_with_best_streak": int(row["members_with_best_streak"] or 0),
        "average_best_streak": round(float(row["average_best_streak"] or 0), 2),
        "highest_best_streak": int(row["highest_best_streak"] or 0),
    }


async def set_streak_reminder(
    guild_id: int, user_id: int, enabled: bool
) -> Dict[str, Any]:
    if enabled not in (True, False, 0, 1):
        raise ValueError("enabled must be boolean")
    enabled_at = datetime.now(timezone.utc).isoformat()
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            INSERT INTO streak_reminder_settings
                (guild_id, user_id, enabled, reminder_time, enabled_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                enabled = excluded.enabled,
                reminder_time = excluded.reminder_time,
                enabled_at = excluded.enabled_at
            """,
            (
                int(guild_id), int(user_id), int(bool(enabled)),
                STREAK_DEFAULT_REMINDER_TIME, enabled_at,
            ),
        )
        await db.commit()
    settings = await get_level_settings(guild_id) or {}
    from prime_level_controls import controls_with_defaults

    controls = controls_with_defaults(
        settings.get("prime_controls"), settings,
    )
    reminder = controls["streak"]["messages"]["reminder"]
    reminder_time = reminder.get("time") or STREAK_DEFAULT_REMINDER_TIME
    delivery_enabled = bool(
        settings.get("is_enabled", True)
        and settings.get("streak_enabled", True)
        and settings.get("streak_channel_id")
        and reminder.get("enabled") is True
    )
    return {
        "enabled": bool(enabled),
        "reminder_time": reminder_time,
        "delivery_enabled": delivery_enabled,
        "timezone": "Asia/Riyadh",
    }


async def claim_due_streak_reminders(
    now: Optional[datetime] = None, limit: int = 100
) -> list[Dict[str, Any]]:
    """Atomically reserve opt-in reminders due today; never reserve a claimed day."""
    from prime_level_controls import controls_with_defaults

    local_now = _level_streak_local(now or datetime.now(timezone.utc))
    today = local_now.date().isoformat()
    max_results = max(1, min(int(limit), 1000))
    rows_out = []
    reminder_configs = {}
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                """
                SELECT r.guild_id, r.user_id, r.enabled_at, s.prime_controls,
                       u.current_streak, u.best_streak, u.last_daily_claim
                FROM streak_reminder_settings AS r
                JOIN level_settings AS s ON s.guild_id = r.guild_id
                JOIN user_levels AS u
                  ON u.guild_id = r.guild_id AND u.user_id = r.user_id
                WHERE r.enabled = 1 AND s.is_enabled = 1
                  AND s.streak_enabled = 1 AND s.streak_channel_id IS NOT NULL
                  AND COALESCE(u.current_streak, 0) > 0
                ORDER BY r.enabled_at, r.guild_id, r.user_id
                """
            ) as cur:
                subscriptions = await cur.fetchall()
            for subscription in subscriptions:
                if len(rows_out) >= max_results:
                    break
                guild_id = int(subscription["guild_id"])
                if guild_id not in reminder_configs:
                    try:
                        stored_controls = json.loads(
                            subscription["prime_controls"] or "{}"
                        )
                        if not isinstance(stored_controls, dict):
                            raise ValueError("controls are not an object")
                        controls = controls_with_defaults(stored_controls)
                        reminder_config = (
                            controls["streak"]["messages"]["reminder"]
                        )
                        if not isinstance(reminder_config, dict):
                            raise ValueError("reminder controls are not an object")
                        reminder_configs[guild_id] = {
                            "enabled": reminder_config.get("enabled") is True,
                            "time": reminder_config.get("time"),
                            "message": reminder_config.get("message"),
                        }
                    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                        logger.warning(
                            "Skipping invalid streak reminder controls guild=%s",
                            guild_id,
                            exc_info=True,
                        )
                        reminder_configs[guild_id] = {"enabled": False}
                reminder_config = reminder_configs[guild_id]
                if not reminder_config.get("enabled"):
                    continue
                reminder_time = reminder_config.get("time")
                reminder_message = reminder_config.get("message")
                if (
                    not isinstance(reminder_time, str)
                    or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", reminder_time)
                    or not isinstance(reminder_message, str)
                    or not reminder_message.strip()
                    or len(reminder_message) > 500
                ):
                    logger.warning(
                        "Skipping invalid streak reminder schedule guild=%s",
                        guild_id,
                    )
                    continue
                try:
                    hour, minute = (
                        int(part) for part in reminder_time.split(":", 1)
                    )
                    due_local = datetime(
                        local_now.year, local_now.month, local_now.day,
                        hour, minute, tzinfo=STREAK_TIMEZONE,
                    )
                    enabled_local = _level_streak_local(subscription["enabled_at"])
                except (TypeError, ValueError, OverflowError):
                    logger.warning(
                        "Skipping invalid streak reminder schedule guild=%s user=%s",
                        subscription["guild_id"], subscription["user_id"],
                    )
                    continue
                if local_now < due_local or enabled_local > due_local:
                    continue
                last_claim = subscription["last_daily_claim"]
                if last_claim and _level_streak_local(last_claim).date().isoformat() == today:
                    continue
                async with db.execute(
                    """
                    SELECT 1 FROM streak_daily_activity
                    WHERE guild_id = ? AND user_id = ? AND activity_date = ?
                    """,
                    (
                        int(subscription["guild_id"]),
                        int(subscription["user_id"]),
                        today,
                    ),
                ) as cur:
                    if await cur.fetchone():
                        continue
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO streak_reminder_deliveries
                        (guild_id, user_id, reminder_date)
                    VALUES (?, ?, ?)
                    """,
                    (
                        int(subscription["guild_id"]),
                        int(subscription["user_id"]),
                        today,
                    ),
                )
                if cursor.rowcount == 1:
                    rows_out.append(
                        {
                            "guild_id": int(subscription["guild_id"]),
                            "user_id": int(subscription["user_id"]),
                            "reminder_date": today,
                            "current_streak": int(subscription["current_streak"] or 0),
                            "best_streak": int(subscription["best_streak"] or 0),
                            "reminder_time": reminder_time,
                            "reminder_message": reminder_message,
                        }
                    )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    return rows_out


async def _record_level_daily_xp(
    db, guild_id, user_id, awarded_at, *, text_xp=0, voice_xp=0,
):
    text_xp, voice_xp = int(text_xp), int(voice_xp)
    if min(text_xp, voice_xp) < 0:
        raise ValueError("daily XP credits cannot be negative")
    if not (text_xp or voice_xp):
        return
    timestamp = _level_utc(awarded_at)
    day_utc = timestamp.date().isoformat()
    await db.execute(
        """
        INSERT INTO level_xp_daily (guild_id, user_id, day_utc, text_xp, voice_xp)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id, day_utc) DO UPDATE SET
            text_xp = level_xp_daily.text_xp + excluded.text_xp,
            voice_xp = level_xp_daily.voice_xp + excluded.voice_xp
        """,
        (int(guild_id), int(user_id), day_utc, text_xp, voice_xp),
    )
    await db.execute(
        """
        INSERT INTO level_xp_events (guild_id, user_id, awarded_at, text_xp, voice_xp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(guild_id), int(user_id), timestamp.isoformat(),
            text_xp, voice_xp,
        ),
    )


def _level_xp_period_bounds(period, now=None):
    if period not in {"daily", "weekly", "monthly"}:
        raise ValueError("period must be daily, weekly, monthly, or all_time")
    current = _level_utc(now or datetime.now(timezone.utc)).date()
    if period == "daily":
        start = current
        end = current + timedelta(days=1)
    elif period == "weekly":
        start = current - timedelta(days=current.weekday())
        end = start + timedelta(days=7)
    else:
        start = current.replace(day=1)
        end = (
            datetime(start.year + 1, 1, 1).date()
            if start.month == 12
            else datetime(start.year, start.month + 1, 1).date()
        )
    return start.isoformat(), end.isoformat()


async def award_text_xp(
    guild_id: int, user_id: int, xp: int, awarded_at: datetime,
    cooldown_seconds: int = 60,
    detect_overtakes: bool = False,
) -> Optional[Dict[str, Any]]:
    """Atomic text-only award; persisted cooldown protects restarts/races.

    None means cooldown suppression. No user record is created until an award.
    """
    xp = int(xp)
    if xp <= 0:
        raise ValueError("text XP award must be positive")
    if awarded_at.tzinfo is None:
        awarded_at = awarded_at.replace(tzinfo=timezone.utc)
    awarded_at = awarded_at.astimezone(timezone.utc)
    cooldown_seconds = max(0, int(cooldown_seconds))
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
                (int(guild_id), int(user_id)),
            ) as cur:
                row = await cur.fetchone()
            # A voice-created record with zero messages has no text cooldown.
            if row and row["total_messages"] and row["last_message_at"]:
                last = datetime.fromisoformat(str(row["last_message_at"]).replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (awarded_at - last).total_seconds() < cooldown_seconds:
                    await db.rollback()
                    return None
            result = await _add_level_text_credit(db, guild_id, user_id, xp, row, detect_overtakes)
            await _record_level_daily_xp(
                db, guild_id, user_id, awarded_at, text_xp=xp
            )
            await db.execute(
                """
                UPDATE user_levels SET
                    total_messages = COALESCE(user_levels.total_messages, 0) + 1,
                    last_message_at = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (awarded_at.isoformat(), int(guild_id), int(user_id)),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    result["total_messages"] = (int(row["total_messages"] or 0) if row else 0) + 1
    result["last_message_at"] = awarded_at.isoformat()
    return result


async def award_voice_xp(
    guild_id: int, user_id: int, voice_xp: int, text_xp: int,
    eligible_seconds: int,
    detect_overtakes: bool = False,
    awarded_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Commit one batched voice credit, preserving chat counts/timestamps.

    Separate and combined credits may coexist after a mid-session mode change.
    The transaction serializes with the chat award transaction.
    """
    from level_progression import level_from_xp

    voice_xp, text_xp, eligible_seconds = map(int, (voice_xp, text_xp, eligible_seconds))
    if min(voice_xp, text_xp, eligible_seconds) < 0:
        raise ValueError("voice credits cannot be negative")
    if not (voice_xp or text_xp or eligible_seconds):
        raise ValueError("empty voice credit")
    awarded_at = _level_utc(awarded_at or datetime.now(timezone.utc))
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
                (int(guild_id), int(user_id)),
            ) as cur:
                row = await cur.fetchone()
            old_voice_xp = int(row["voice_xp"] or 0) if row else 0
            old_text_xp = int(row["text_xp"] or 0) if row else 0
            old_voice_level = int(row["voice_level"] or 0) if row else 0
            old_text_level = int(row["text_level"] or 0) if row else 0
            new_voice_xp, new_text_xp = old_voice_xp + voice_xp, old_text_xp + text_xp
            new_voice_level = level_from_xp(new_voice_xp) if voice_xp else old_voice_level
            text_values = _level_text_values(guild_id, user_id, text_xp, row)
            new_text_level = text_values["text_level"] if text_xp else old_text_level
            overtakes = await _level_text_overtakes(
                db, guild_id, user_id, old_text_xp, new_text_xp, detect_overtakes)
            await db.execute(
                """
                INSERT INTO user_levels
                    (guild_id, user_id, voice_xp, voice_level, text_xp,
                     text_level, total_voice_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    voice_xp = excluded.voice_xp,
                    voice_level = excluded.voice_level,
                    text_xp = excluded.text_xp,
                    text_level = excluded.text_level,
                    total_voice_seconds =
                        COALESCE(user_levels.total_voice_seconds, 0) + excluded.total_voice_seconds
                """,
                (int(guild_id), int(user_id), new_voice_xp, new_voice_level,
                 new_text_xp, new_text_level, eligible_seconds),
            )
            await _record_level_daily_xp(
                db, guild_id, user_id, awarded_at,
                text_xp=text_xp, voice_xp=voice_xp,
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    return {
        "old_voice_level": old_voice_level, "voice_level": new_voice_level,
        "old_text_level": old_text_level, "text_level": new_text_level,
        "voice_xp": new_voice_xp, "text_xp": new_text_xp,
        "old_xp": old_text_xp, "old_level": old_text_level,
        "overtakes": overtakes, "xp_awarded": text_xp,
    }


async def award_reaction_xp(
    guild_id: int, message_id: int, reactor_id: int, emoji_key: str,
    awards: Dict[int, int], awarded_at: datetime, cooldown_seconds: int = 60,
    detect_overtakes: bool = True,
) -> Dict[str, Any]:
    """Atomic reaction event ledger, cooldowns, recipient credits and ranks."""
    awarded_at = _level_utc(awarded_at)
    cooldown_seconds = max(0, int(cooldown_seconds))
    recipients = {int(user_id): int(xp) for user_id, xp in awards.items() if int(xp) > 0}
    if not recipients:
        return {"status": "no_award", "awards": []}
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                """SELECT 1 FROM level_reaction_awards
                   WHERE guild_id = ? AND message_id = ? AND reactor_id = ? AND emoji_key = ?""",
                (int(guild_id), int(message_id), int(reactor_id), str(emoji_key)),
            ) as cur:
                if await cur.fetchone():
                    await db.rollback()
                    return {"status": "duplicate", "awards": []}
            async with db.execute(
                "SELECT last_award_at FROM level_reaction_cooldowns WHERE guild_id = ? AND user_id = ?",
                (int(guild_id), int(reactor_id)),
            ) as cur:
                previous = await cur.fetchone()
            if previous and (awarded_at - _level_utc(previous[0])).total_seconds() < cooldown_seconds:
                await db.rollback()
                remaining = cooldown_seconds - (awarded_at - _level_utc(previous[0])).total_seconds()
                return {"status": "cooldown", "awards": [], "remaining_seconds": remaining}
            eligible = []
            for user_id, xp in sorted(recipients.items()):
                async with db.execute(
                    "SELECT last_award_at FROM level_reaction_cooldowns WHERE guild_id = ? AND user_id = ?",
                    (int(guild_id), user_id),
                ) as cur:
                    previous = await cur.fetchone()
                if previous and (awarded_at - _level_utc(previous[0])).total_seconds() < cooldown_seconds:
                    continue
                async with db.execute(
                    "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
                    (int(guild_id), user_id),
                ) as cur:
                    row = await cur.fetchone()
                eligible.append((user_id, xp, row))
            if not eligible:
                await db.rollback()
                return {"status": "cooldown", "awards": []}
            projections = {
                user_id: (int(row["text_xp"] or 0) if row else 0,
                          (int(row["text_xp"] or 0) if row else 0) + xp)
                for user_id, xp, row in eligible
            }
            crossings = {
                user_id: await _level_text_overtakes(
                    db, guild_id, user_id, *projections[user_id], detect_overtakes, projections)
                for user_id, _, _ in eligible
            }
            results = []
            for user_id, xp, row in eligible:
                result = await _add_level_text_credit(db, guild_id, user_id, xp, row, False)
                await _record_level_daily_xp(
                    db, guild_id, user_id, awarded_at, text_xp=xp
                )
                result["overtakes"] = crossings[user_id]
                results.append(result)
            await db.execute(
                """INSERT INTO level_reaction_awards
                   (guild_id, message_id, reactor_id, emoji_key, awarded_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (int(guild_id), int(message_id), int(reactor_id), str(emoji_key), awarded_at.isoformat()),
            )
            # The initiating reactor is throttled even in author-only mode.
            cooldown_ids = {int(reactor_id)} | {result["user_id"] for result in results}
            for user_id in sorted(cooldown_ids):
                await db.execute(
                    """INSERT INTO level_reaction_cooldowns (guild_id, user_id, last_award_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(guild_id, user_id) DO UPDATE SET last_award_at = excluded.last_award_at""",
                    (int(guild_id), user_id, awarded_at.isoformat()),
                )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    return {"status": "awarded", "awards": results, "cooldown_ids": sorted(cooldown_ids)}


async def claim_level_streak(
    guild_id: int, user_id: int, claimed_at: datetime,
    multiplier: float = 1.0, detect_overtakes: bool = True,
) -> Dict[str, Any]:
    """Exactly one streak claim per Riyadh date, committed with its text XP.

    Bonus = min(streak_daily_xp * consecutive_days * multiplier, streak_max_cap).
    All gating and previous-claim comparisons happen under the write lock.
    """
    claimed_at = _level_utc(claimed_at)
    multiplier = float(multiplier)
    if not math.isfinite(multiplier) or multiplier < 0:
        raise ValueError("invalid streak multiplier")
    multiplier = min(multiplier, 100.0)
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute("SELECT * FROM level_settings WHERE guild_id = ?", (int(guild_id),)) as cur:
                settings = await cur.fetchone()
            if not settings or not settings["is_enabled"] or not settings["streak_enabled"]:
                await db.rollback()
                return {"status": "disabled"}
            async with db.execute(
                "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
                (int(guild_id), int(user_id)),
            ) as cur:
                row = await cur.fetchone()
            previous_day = (
                _level_streak_local(row["last_daily_claim"]).date()
                if row and row["last_daily_claim"] else None
            )
            today = _level_streak_local(claimed_at).date()
            if previous_day and previous_day >= today:
                await db.rollback()
                return {"status": "already_claimed"}
            consecutive = previous_day == today - timedelta(days=1)
            streak = (max(0, int(row["current_streak"] or 0)) + 1) if row and consecutive else 1
            previous_streak = int(row["current_streak"] or 0) if row else 0
            best_streak = max(
                int(row["best_streak"] or 0) if row else 0,
                previous_streak,
                streak,
            )
            base, cap = int(settings["streak_daily_xp"]), int(settings["streak_max_cap"])
            if base < 0 or cap < 0:
                raise ValueError("negative streak reward configuration")
            xp = int(min(cap, base * streak * multiplier))
            result = await _add_level_text_credit(
                db, guild_id, user_id, xp, row,
                detect_overtakes and bool(settings["overtake_alert_enabled"]))
            await _record_level_daily_xp(
                db, guild_id, user_id, claimed_at, text_xp=xp
            )
            await db.execute(
                """UPDATE user_levels
                   SET current_streak = ?, best_streak = ?, last_daily_claim = ?
                   WHERE guild_id = ? AND user_id = ?""",
                (
                    streak, best_streak, claimed_at.isoformat(),
                    int(guild_id), int(user_id),
                ),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    result.update(
        status="claimed",
        current_streak=streak,
        best_streak=best_streak,
        last_daily_claim=claimed_at.isoformat(),
    )
    return result


async def get_text_rank(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """On-demand rank among stored positive-XP participants, including ties.

    Equal XP is ordered by user ID, matching get_text_leaderboard. Eligibility
    here means a positive text XP record, not live Discord member presence.
    """
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT u.user_id, u.text_xp, u.text_level,
                CASE WHEN u.text_xp > 0 THEN 1 + (
                    SELECT COUNT(*) FROM user_levels p
                    WHERE p.guild_id = u.guild_id AND p.text_xp > 0
                    AND (p.text_xp > u.text_xp OR
                         (p.text_xp = u.text_xp AND p.user_id < u.user_id))
                ) ELSE NULL END AS rank,
                (SELECT COUNT(*) FROM user_levels p
                 WHERE p.guild_id = u.guild_id AND p.text_xp > 0)
                    AS total_eligible_members
            FROM user_levels u WHERE u.guild_id = ? AND u.user_id = ?
            """,
            (int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_user_level(
    guild_id: int,
    user_id: int,
) -> Dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            "INSERT OR IGNORE INTO user_levels (guild_id, user_id) VALUES (?, ?)",
            (int(guild_id), int(user_id)),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise RuntimeError("user level row disappeared after creation")
    return dict(row)


async def update_user_level(
    guild_id: int,
    user_id: int,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("user level data must be a mapping")
    unknown = set(data) - _USER_LEVEL_MUTABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown user level field: {sorted(unknown)[0]}")
    await create_user_level(guild_id, user_id)
    if data:
        async with connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                for new_col, value in data.items():
                    # new_col is checked against _USER_LEVEL_MUTABLE_FIELDS.
                    await db.execute(
                        f"UPDATE user_levels SET {new_col} = ? "
                        "WHERE guild_id = ? AND user_id = ?",
                        (value, int(guild_id), int(user_id)),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    result = await get_user_level(guild_id, user_id)
    if result is None:
        raise RuntimeError("user level row disappeared after update")
    return result


async def grant_text_levels(
    guild_id: int,
    user_id: int,
    levels: int,
) -> Dict[str, int]:
    """Atomically grant exact text levels while preserving current progress.

    Admin grants change lifetime text XP/level only; they are not message XP
    awards and do not fabricate message counts or period XP history.
    """
    if isinstance(levels, bool) or not isinstance(levels, int) or not 1 <= levels <= 100:
        raise ValueError("levels must be an integer from 1 to 100")
    from level_progression import level_from_xp, total_xp_for_level

    guild_id, user_id = int(guild_id), int(user_id)
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT text_xp FROM user_levels WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cur:
                row = await cur.fetchone()
            old_xp = max(0, int(row["text_xp"] or 0)) if row else 0
            old_level = level_from_xp(old_xp)
            current_progress = old_xp - total_xp_for_level(old_level)
            new_level = old_level + levels
            new_xp = total_xp_for_level(new_level) + current_progress
            if new_xp > 2**63 - 1:
                raise ValueError("resulting XP exceeds the supported storage limit")
            await db.execute(
                """
                INSERT INTO user_levels (guild_id, user_id, text_xp, text_level)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    text_xp = excluded.text_xp,
                    text_level = excluded.text_level
                """,
                (guild_id, user_id, new_xp, new_level),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    return {
        "guild_id": guild_id,
        "user_id": user_id,
        "old_xp": old_xp,
        "old_level": old_level,
        "text_xp": new_xp,
        "text_level": new_level,
        "xp_awarded": new_xp - old_xp,
        "levels_awarded": levels,
    }


async def take_text_levels(
    guild_id: int,
    user_id: int,
    levels: int,
) -> Dict[str, int]:
    """Atomically remove text levels while preserving as much level progress as fits."""
    if isinstance(levels, bool) or not isinstance(levels, int) or not 1 <= levels <= 100:
        raise ValueError("levels must be an integer from 1 to 100")
    from level_progression import level_from_xp, total_xp_for_level, xp_required

    guild_id, user_id = int(guild_id), int(user_id)
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT text_xp FROM user_levels WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cur:
                row = await cur.fetchone()
            old_xp = max(0, int(row["text_xp"] or 0)) if row else 0
            old_level = level_from_xp(old_xp)
            progress = old_xp - total_xp_for_level(old_level)
            new_level = max(0, old_level - levels)
            new_progress = min(progress, xp_required(new_level) - 1)
            new_xp = total_xp_for_level(new_level) + new_progress
            if row and new_xp != old_xp:
                await db.execute(
                    """
                    UPDATE user_levels
                    SET text_xp = ?, text_level = ?
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (new_xp, new_level, guild_id, user_id),
                )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    return {
        "guild_id": guild_id,
        "user_id": user_id,
        "old_xp": old_xp,
        "old_level": old_level,
        "text_xp": new_xp,
        "text_level": new_level,
        "xp_removed": old_xp - new_xp,
        "levels_removed": old_level - new_level,
    }


async def reset_level_progress(guild_id: int) -> Dict[str, int]:
    """Clear one guild's progression and XP history, retaining configuration."""
    guild_id = int(guild_id)
    if guild_id <= 0:
        raise ValueError("guild_id must be positive")

    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            deleted = {}
            for table in ("user_levels", "level_xp_daily", "level_xp_events"):
                cursor = await db.execute(
                    f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,)
                )
                deleted[table] = max(0, cursor.rowcount)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    return {
        "guild_id": guild_id,
        "members_reset": deleted["user_levels"],
        "daily_rows_removed": deleted["level_xp_daily"],
        "xp_events_removed": deleted["level_xp_events"],
    }


async def get_level_rewards(guild_id: int) -> List[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, reward_type, level_required, role_id
            FROM level_role_rewards
            WHERE guild_id = ?
            ORDER BY reward_type, level_required, id
            """,
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def add_level_reward(
    guild_id: int,
    reward_type: str,
    level_required: int,
    role_id: int,
) -> Dict[str, Any]:
    reward_type = str(reward_type).lower()
    if reward_type not in {"text", "voice"}:
        raise ValueError("reward_type must be 'text' or 'voice'")
    async with connect(aiosqlite.Row) as db:
        cur = await db.execute(
            """
            INSERT INTO level_role_rewards
                (guild_id, reward_type, level_required, role_id)
            VALUES (?, ?, ?, ?)
            RETURNING id, guild_id, reward_type, level_required, role_id
            """,
            (
                int(guild_id),
                reward_type,
                max(0, int(level_required)),
                int(role_id),
            ),
        )
        row = await cur.fetchone()
        await db.commit()
    if row is None:
        raise RuntimeError("level reward row was not returned")
    return dict(row)


async def delete_level_reward(guild_id: int, reward_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "DELETE FROM level_role_rewards WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(reward_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_level_multipliers(guild_id: int) -> List[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, target_type, target_id, multiplier
            FROM level_multipliers
            WHERE guild_id = ?
            ORDER BY target_type, target_id, id
            """,
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def add_level_multiplier(
    guild_id: int,
    target_type: str,
    target_id: int,
    multiplier: float = 1.5,
) -> Dict[str, Any]:
    target_type = str(target_type).lower()
    if target_type not in {"role", "channel"}:
        raise ValueError("target_type must be 'role' or 'channel'")
    multiplier = float(multiplier)
    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("multiplier must be a finite positive number")
    async with connect(aiosqlite.Row) as db:
        cur = await db.execute(
            """
            INSERT INTO level_multipliers
                (guild_id, target_type, target_id, multiplier)
            VALUES (?, ?, ?, ?)
            RETURNING id, guild_id, target_type, target_id, multiplier
            """,
            (int(guild_id), target_type, int(target_id), multiplier),
        )
        row = await cur.fetchone()
        await db.commit()
    if row is None:
        raise RuntimeError("level multiplier row was not returned")
    return dict(row)


async def delete_level_multiplier(guild_id: int, multiplier_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "DELETE FROM level_multipliers WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(multiplier_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_level_blacklist(guild_id: int) -> List[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, target_type, target_id
            FROM (
                SELECT id, guild_id, target_type, target_id
                FROM level_blacklist WHERE guild_id = ?
                UNION ALL
                SELECT id, guild_id, 'user' AS target_type, target_id
                FROM level_user_blacklist WHERE guild_id = ?
            )
            ORDER BY target_type, target_id, id
            """,
            (int(guild_id), int(guild_id)),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def add_level_blacklist(
    guild_id: int,
    target_type: str,
    target_id: int,
) -> Dict[str, Any]:
    target_type = str(target_type).lower()
    if target_type not in {"role", "channel", "user"}:
        raise ValueError("target_type must be 'role', 'channel' or 'user'")
    async with connect(aiosqlite.Row) as db:
        if target_type == "user":
            cur = await db.execute(
                """
                INSERT INTO level_user_blacklist (guild_id, target_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id, target_id) DO NOTHING
                """,
                (int(guild_id), int(target_id)),
            )
            async with db.execute(
                """
                SELECT id, guild_id, 'user' AS target_type, target_id
                FROM level_user_blacklist WHERE guild_id = ? AND target_id = ?
                """,
                (int(guild_id), int(target_id)),
            ) as lookup:
                row = await lookup.fetchone()
        else:
            cur = await db.execute(
                """
                INSERT INTO level_blacklist (guild_id, target_type, target_id)
                VALUES (?, ?, ?)
                RETURNING id, guild_id, target_type, target_id
                """,
                (int(guild_id), target_type, int(target_id)),
            )
            row = await cur.fetchone()
        await db.commit()
    if row is None:
        raise RuntimeError("level blacklist row was not returned")
    return dict(row)


async def delete_level_blacklist(guild_id: int, blacklist_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "DELETE FROM level_blacklist WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(blacklist_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def _get_level_leaderboard(
    guild_id: int,
    limit: int,
    xp_column: str,
    level_column: str,
) -> List[Dict[str, Any]]:
    queries = {
        ("text_xp", "text_level"): """
            SELECT guild_id, user_id, text_xp, text_level,
                   total_messages, total_voice_seconds, current_streak
            FROM user_levels
            WHERE guild_id = ?
            ORDER BY text_xp DESC, user_id ASC
            LIMIT ?
        """,
        ("voice_xp", "voice_level"): """
            SELECT guild_id, user_id, voice_xp, voice_level,
                   total_messages, total_voice_seconds, current_streak
            FROM user_levels
            WHERE guild_id = ?
            ORDER BY voice_xp DESC, user_id ASC
            LIMIT ?
        """,
    }
    query = queries.get((xp_column, level_column))
    if query is None:
        raise ValueError("invalid leaderboard column")
    limit = max(1, min(int(limit), 100))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            query,
            (int(guild_id), limit),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_text_leaderboard(
    guild_id: int,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    return await _get_level_leaderboard(
        guild_id, limit, "text_xp", "text_level"
    )


async def get_voice_leaderboard(
    guild_id: int,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    return await _get_level_leaderboard(
        guild_id, limit, "voice_xp", "voice_level"
    )


async def get_level_leaderboard_page(
    guild_id: int,
    mode: str = "text",
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return one bounded, indexed text or voice leaderboard page."""
    columns = {
        "text": ("text_xp", "text_level", "total_messages"),
        "voice": ("voice_xp", "voice_level", "total_voice_seconds"),
    }
    if mode not in columns:
        raise ValueError("mode must be 'text' or 'voice'")
    xp_column, level_column, total_column = columns[mode]
    limit = max(1, min(100, int(limit)))
    offset = max(0, min(1_000_000, int(offset)))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            f"SELECT COUNT(*) FROM user_levels WHERE guild_id = ? AND {xp_column} > 0",
            (int(guild_id),),
        ) as cur:
            count_row = await cur.fetchone()
        async with db.execute(
            f"""
            SELECT user_id, {xp_column} AS xp, {level_column} AS level,
                   {total_column} AS activity_total, total_messages,
                   total_voice_seconds, current_streak, last_message_at
            FROM user_levels
            WHERE guild_id = ? AND {xp_column} > 0
            ORDER BY {xp_column} DESC, user_id ASC
            LIMIT ? OFFSET ?
            """,
            (int(guild_id), limit, offset),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for index, row in enumerate(rows):
        row["rank"] = offset + index + 1
    return {
        "mode": mode,
        "limit": limit,
        "offset": offset,
        "total": int(count_row[0] or 0) if count_row else 0,
        "rows": rows,
    }


_PUBLIC_LEVEL_SLUG_RE = re.compile(r"^(?=.{3,40}$)[a-z0-9]+(?:-[a-z0-9]+)*$")


async def get_public_level_settings_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Resolve only canonical, enabled public leaderboard slugs."""
    if not isinstance(slug, str) or not _PUBLIC_LEVEL_SLUG_RE.fullmatch(slug):
        return None
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT guild_id
            FROM level_settings
            WHERE web_slug = ? AND web_leaderboard_enabled = 1
            ORDER BY guild_id
            LIMIT 2
            """,
            (slug,),
        ) as cur:
            rows = await cur.fetchall()
    if len(rows) != 1:
        return None
    return {"guild_id": int(rows[0]["guild_id"])}


async def get_public_level_summary(guild_id: int) -> Dict[str, int]:
    """Return compact public aggregates without materializing member rows."""
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT COUNT(*) AS active_members,
                   COALESCE(SUM(COALESCE(text_xp, 0) + COALESCE(voice_xp, 0)), 0)
                       AS total_xp
            FROM user_levels
            WHERE guild_id = ? AND (text_xp > 0 OR voice_xp > 0)
            """,
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    return {
        "active_members": int(row["active_members"] or 0) if row else 0,
        "total_xp": int(row["total_xp"] or 0) if row else 0,
    }


async def get_level_user_rank(
    guild_id: int,
    user_id: int,
    mode: str = "text",
) -> Optional[Dict[str, Any]]:
    """Return one user's rank using the same XP-desc/user-ID-asc order as pages."""
    columns = {
        "text": ("text_xp", "text_level", "total_messages"),
        "voice": ("voice_xp", "voice_level", "total_voice_seconds"),
    }
    if mode not in columns:
        raise ValueError("mode must be 'text' or 'voice'")
    xp_column, level_column, total_column = columns[mode]
    guild_id, user_id = int(guild_id), int(user_id)
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            f"""
            SELECT user_id, {xp_column} AS xp, {level_column} AS level,
                   {total_column} AS activity_total, total_messages,
                   total_voice_seconds, current_streak
            FROM user_levels
            WHERE guild_id = ? AND user_id = ? AND {xp_column} > 0
            """,
            (guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        xp = int(row["xp"] or 0)
        async with db.execute(
            f"""
            SELECT COUNT(*)
            FROM user_levels
            WHERE guild_id = ? AND {xp_column} > 0
              AND ({xp_column} > ? OR ({xp_column} = ? AND user_id < ?))
            """,
            (guild_id, xp, xp, user_id),
        ) as cur:
            count_row = await cur.fetchone()
    result = dict(row)
    result["rank"] = int(count_row[0] or 0) + 1 if count_row else 1
    return result


async def get_level_dashboard_analytics(guild_id: int) -> Dict[str, Any]:
    """Return live aggregates only; no daily history is synthesized."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT COUNT(*) AS participants,
                   COALESCE(SUM(text_xp), 0) AS text_xp,
                   COALESCE(SUM(voice_xp), 0) AS voice_xp,
                   COALESCE(SUM(total_messages), 0) AS total_messages,
                   COALESCE(SUM(total_voice_seconds), 0) AS total_voice_seconds,
                   COALESCE(MAX(text_level), 0) AS highest_text_level,
                   COALESCE(MAX(voice_level), 0) AS highest_voice_level,
                   SUM(CASE WHEN last_message_at >= ? THEN 1 ELSE 0 END)
                       AS active_members_7d
            FROM user_levels
            WHERE guild_id = ? AND (text_xp > 0 OR voice_xp > 0)
            """,
            (cutoff, int(guild_id)),
        ) as cur:
            row = await cur.fetchone()
        async with db.execute(
            """
            SELECT text_level AS level, COUNT(*) AS members
            FROM user_levels
            WHERE guild_id = ? AND text_xp > 0
            GROUP BY text_level
            ORDER BY text_level DESC
            LIMIT 20
            """,
            (int(guild_id),),
        ) as cur:
            distribution = [dict(item) for item in await cur.fetchall()]
    return {
        "totals": dict(row) if row else {
            "participants": 0, "text_xp": 0, "voice_xp": 0,
            "total_messages": 0, "total_voice_seconds": 0,
            "highest_text_level": 0, "highest_voice_level": 0,
            "active_members_7d": 0,
        },
        "distribution": distribution,
        "history_available": False,
    }


async def get_command_rank_snapshot(
    guild_id: int, user_id: int, human_ids: List[int], mode: str = "text",
) -> Dict[str, Any]:
    """Read-only card snapshot; rank excludes bots/departed members.

    Discord supplies current human IDs. One JSON parameter avoids SQLite's
    bound-parameter limit on large guilds. Legacy XP/rank helpers are unchanged.
    """
    columns = {
        "text": ("text_xp", "text_level"),
        "voice": ("voice_xp", "voice_level"),
    }
    if mode not in columns:
        raise ValueError("rank mode must be text or voice")
    xp_column, level_column = columns[mode]
    eligible = json.dumps(sorted({int(value) for value in human_ids}))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            f"""
            SELECT u.*,
                CASE WHEN u.{xp_column} > 0 THEN 1 + (
                    SELECT COUNT(*) FROM user_levels p
                    WHERE p.guild_id = u.guild_id AND p.{xp_column} > 0
                      AND p.user_id IN (SELECT value FROM json_each(?))
                      AND (p.{xp_column} > u.{xp_column} OR
                           (p.{xp_column} = u.{xp_column} AND p.user_id < u.user_id))
                ) ELSE NULL END AS rank
            FROM user_levels u WHERE u.guild_id = ? AND u.user_id = ?
            """,
            (eligible, int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
    result = dict(row) if row else {
        "text_level": 0, "text_xp": 0, "voice_level": 0, "voice_xp": 0,
        "rank": None, "total_messages": 0, "total_voice_seconds": 0,
        "current_streak": 0,
    }
    result["level"] = int(result.get(level_column) or 0)
    result["xp"] = int(result.get(xp_column) or 0)
    result["total_members"] = len(set(human_ids))
    return result


async def get_command_level_leaderboard(
    guild_id: int, human_ids: List[int], mode: str = "text",
    period: str = "all_time", now: Optional[datetime] = None, limit: int = 10,
) -> List[Dict[str, Any]]:
    """Return an indexed Top 10 for current humans and one UTC XP period.

    Period XP is additive only; permanent text/voice XP is never reset. The
    daily table begins recording at migration time and cannot infer old dates.
    """
    columns = {
        "text": ("text_xp", "text_level"),
        "voice": ("voice_xp", "voice_level"),
    }
    if mode not in columns:
        raise ValueError("leaderboard mode must be text or voice")
    if period not in {"daily", "weekly", "monthly", "all_time"}:
        raise ValueError("period must be daily, weekly, monthly, or all_time")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("leaderboard limit must be from 1 to 20")
    if not human_ids:
        return []
    xp_column, level_column = columns[mode]
    eligible = json.dumps(sorted({int(value) for value in human_ids}))
    if period == "all_time":
        query = f"""
            SELECT user_id, {level_column} AS level, {xp_column} AS xp,
                   {xp_column} AS total_xp
            FROM user_levels
            WHERE guild_id = ? AND {xp_column} > 0
              AND user_id IN (SELECT value FROM json_each(?))
              ORDER BY {xp_column} DESC, user_id ASC LIMIT ?
        """
        params = (int(guild_id), eligible, limit)
    else:
        start_day, end_day = _level_xp_period_bounds(period, now)
        query = f"""
            SELECT d.user_id, u.{level_column} AS level,
                   CAST(SUM(d.{xp_column}) AS INTEGER) AS xp,
                   u.{xp_column} AS total_xp
            FROM level_xp_daily d
            JOIN user_levels u
              ON u.guild_id = d.guild_id AND u.user_id = d.user_id
            WHERE d.guild_id = ? AND d.{xp_column} > 0
              AND d.day_utc >= ? AND d.day_utc < ?
              AND d.user_id IN (SELECT value FROM json_each(?))
            GROUP BY d.user_id
              ORDER BY xp DESC, d.user_id ASC LIMIT ?
        """
        params = (int(guild_id), start_day, end_day, eligible, limit)
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            query, params,
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_level_periodic_top_leaderboard(
    guild_id: int,
    human_ids: List[int],
    mode: str,
    start: datetime,
    end: datetime,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Rank period XP using exact UTC event times, without changing lifetime XP."""
    columns = {
        "text": ("text_xp", "text_level"),
        "voice": ("voice_xp", "voice_level"),
    }
    if mode not in {"text", "voice", "both"}:
        raise ValueError("leaderboard mode must be text, voice, or both")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("leaderboard limit must be from 1 to 20")
    if not human_ids:
        return []
    start_utc, end_utc = _level_utc(start), _level_utc(end)
    if end_utc <= start_utc:
        raise ValueError("leaderboard period end must be after start")
    eligible = json.dumps(sorted({int(value) for value in human_ids}))
    if mode == "both":
        select_xp = "SUM(e.text_xp + e.voice_xp)"
        where_xp = "(e.text_xp > 0 OR e.voice_xp > 0)"
        period_level = "MAX(u.text_level, u.voice_level)"
        lifetime_xp = "(u.text_xp + u.voice_xp)"
    else:
        xp_column, level_column = columns[mode]
        select_xp = f"SUM(e.{xp_column})"
        where_xp = f"e.{xp_column} > 0"
        period_level = f"u.{level_column}"
        lifetime_xp = f"u.{xp_column}"
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            f"""
            SELECT e.user_id,
                   {period_level} AS level,
                   CAST({select_xp} AS INTEGER) AS xp,
                   {lifetime_xp} AS total_xp,
                   COALESCE(u.total_messages, 0) AS total_messages,
                   COALESCE(u.total_voice_seconds, 0) AS total_voice_seconds,
                   COALESCE(u.current_streak, 0) AS current_streak
            FROM level_xp_events e
            JOIN user_levels u
              ON u.guild_id = e.guild_id AND u.user_id = e.user_id
            WHERE e.guild_id = ? AND {where_xp}
              AND e.awarded_at >= ? AND e.awarded_at < ?
              AND e.user_id IN (SELECT value FROM json_each(?))
            GROUP BY e.user_id
            ORDER BY xp DESC, e.user_id ASC LIMIT ?
            """,
            (
                int(guild_id), start_utc.isoformat(), end_utc.isoformat(),
                eligible, limit,
            ),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def claim_level_periodic_top_run(
    guild_id: int, period: str, period_key: str,
) -> bool:
    """Claim a scheduled TOP run once, with crash-recovery for stale claims."""
    if period not in {"daily", "weekly", "monthly"}:
        raise ValueError("invalid periodic TOP period")
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    stale_before = now - timedelta(minutes=15)
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO level_periodic_top_runs
                    (guild_id, period, period_key, claimed_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(guild_id), period, str(period_key), now_iso),
            )
            if cursor.rowcount == 1:
                await db.commit()
                return True
            async with db.execute(
                """
                SELECT claimed_at, completed_at
                FROM level_periodic_top_runs
                WHERE guild_id = ? AND period = ? AND period_key = ?
                """,
                (int(guild_id), period, str(period_key)),
            ) as cur:
                row = await cur.fetchone()
            if row is None or row["completed_at"]:
                await db.commit()
                return False
            claimed_at = datetime.fromisoformat(
                str(row["claimed_at"]).replace("Z", "+00:00")
            )
            if claimed_at.tzinfo is None:
                claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            if claimed_at > stale_before:
                await db.commit()
                return False
            await db.execute(
                """
                UPDATE level_periodic_top_runs
                SET claimed_at = ?, completed_at = NULL
                WHERE guild_id = ? AND period = ? AND period_key = ?
                """,
                (now_iso, int(guild_id), str(period), str(period_key)),
            )
            await db.commit()
            return True
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise


async def complete_level_periodic_top_run(
    guild_id: int, period: str, period_key: str,
) -> None:
    async with connect() as db:
        await db.execute(
            """
            UPDATE level_periodic_top_runs SET completed_at = ?
            WHERE guild_id = ? AND period = ? AND period_key = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                int(guild_id), period, str(period_key),
            ),
        )
        await db.commit()


# -------------------------------------------------------------
# إعدادات السيرفر (Guild Settings API) مع كاش LRU/TTL
# -------------------------------------------------------------
def _empty_log_routing() -> Dict[str, int]:
    return {key: 0 for key in LOG_ROUTING_ALL_KEYS}


def get_cached_logging_channels(guild_id: int) -> Dict[str, int]:
    """Return the last committed routing snapshot without touching SQLite."""
    snapshot = LOG_ROUTING_CACHE.get(int(guild_id))
    return dict(snapshot) if snapshot is not None else _empty_log_routing()


async def get_logging_channels(guild_id: int) -> Dict[str, int]:
    """Read all dedicated and legacy log destinations and warm the cache."""
    guild_id = int(guild_id)
    cached = LOG_ROUTING_CACHE.get(guild_id)
    if cached is not None:
        return dict(cached)
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT " + ", ".join(LOG_ROUTING_ALL_KEYS)
            + " FROM logging_channels WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
    snapshot = _empty_log_routing()
    if row:
        snapshot.update({
            key: int(row[key] or 0)
            for key in LOG_ROUTING_KEYS
        })
    LOG_ROUTING_CACHE[guild_id] = snapshot
    return dict(snapshot)


async def set_logging_channels(
    guild_id: int,
    channels_dict: Dict[str, Any],
) -> Dict[str, int]:
    """Atomically upsert routing and publish it only after commit."""
    guild_id = int(guild_id)
    snapshot = _empty_log_routing()
    for key in LOG_ROUTING_KEYS:
        value = channels_dict.get(key, 0)
        if not value:
            value = next(
                (
                    legacy
                    for legacy, dedicated in LOG_ROUTING_ALIASES.items()
                    if dedicated == key and channels_dict.get(legacy)
                ),
                0,
            )
        try:
            snapshot[key] = max(0, int(value or 0))
        except (TypeError, ValueError):
            raise ValueError(f"invalid logging channel for {key}") from None
    for legacy, dedicated in LOG_ROUTING_ALIASES.items():
        snapshot[legacy] = snapshot[dedicated]
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO logging_channels
                (guild_id, """ + ", ".join(LOG_ROUTING_ALL_KEYS) + """)
            VALUES (?, """ + ", ".join("?" for _ in LOG_ROUTING_ALL_KEYS) + """)
            ON CONFLICT(guild_id) DO UPDATE SET
                """ + ", ".join(
                    f"{key} = excluded.{key}" for key in LOG_ROUTING_ALL_KEYS
                ) + """
            """,
            (guild_id, *(snapshot[key] for key in LOG_ROUTING_ALL_KEYS)),
        )
        await db.commit()
    LOG_ROUTING_CACHE[guild_id] = snapshot
    return dict(snapshot)


async def record_security_incident(
    guild_id: int,
    culprit_id: int,
    culprit_name: str,
    action_type: str,
    mitigation_taken: str,
) -> Dict[str, Any]:
    """Persist one security event without changing legacy audit records."""
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            INSERT INTO security_incidents
                (guild_id, culprit_id, culprit_name, action_type, mitigation_taken)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(guild_id),
                int(culprit_id),
                str(culprit_name),
                str(action_type),
                str(mitigation_taken),
            ),
        )
        incident_id = int(cursor.lastrowid)
        await cursor.close()
        async with db.execute(
            "SELECT id, guild_id, culprit_id, culprit_name, action_type, "
            "mitigation_taken, timestamp FROM security_incidents WHERE id = ?",
            (incident_id,),
        ) as cursor:
            row = await cursor.fetchone()
        await db.commit()
    if row is None:
        raise RuntimeError("security incident disappeared after insertion")
    return dict(row)


async def get_security_incidents(
    guild_id: int,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return the newest persisted security events for one guild."""
    limit = max(1, min(int(limit), 1000))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, culprit_id, culprit_name, action_type,
                   mitigation_taken, timestamp
            FROM security_incidents
            WHERE guild_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(guild_id), limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_security_whitelist(guild_id: int) -> List[int]:
    async with connect() as db:
        async with db.execute(
            "SELECT user_id FROM security_whitelist "
            "WHERE guild_id = ? ORDER BY user_id",
            (int(guild_id),),
        ) as cursor:
            rows = await cursor.fetchall()
    return [int(row[0]) for row in rows]


async def get_all_security_whitelists() -> Dict[int, set[int]]:
    async with connect() as db:
        async with db.execute(
            "SELECT guild_id, user_id FROM security_whitelist "
            "ORDER BY guild_id, user_id"
        ) as cursor:
            rows = await cursor.fetchall()
    result: Dict[int, set[int]] = {}
    for guild_id, user_id in rows:
        result.setdefault(int(guild_id), set()).add(int(user_id))
    return result


async def set_security_whitelist_member(
    guild_id: int,
    user_id: int,
    allowed: bool,
) -> None:
    async with connect() as db:
        if allowed:
            await db.execute(
                "INSERT OR IGNORE INTO security_whitelist (guild_id, user_id) "
                "VALUES (?, ?)",
                (int(guild_id), int(user_id)),
            )
        else:
            await db.execute(
                "DELETE FROM security_whitelist WHERE guild_id = ? AND user_id = ?",
                (int(guild_id), int(user_id)),
            )
        await db.commit()


def _nullable_permission(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(bool(value))


async def save_security_lockdown_snapshots(
    guild_id: int,
    snapshots: List[Dict[str, Any]],
) -> None:
    """Capture the pre-lock values once; retries must not replace originals."""
    if not snapshots:
        return
    values = [
        (
            int(guild_id),
            int(item["channel_id"]),
            _nullable_permission(item.get("send_messages")),
            _nullable_permission(item.get("send_messages_in_threads")),
        )
        for item in snapshots
    ]
    async with connect() as db:
        await db.executemany(
            """
            INSERT OR IGNORE INTO security_lockdown_overwrites
                (guild_id, channel_id, send_messages, send_messages_in_threads)
            VALUES (?, ?, ?, ?)
            """,
            values,
        )
        await db.commit()


async def get_security_lockdown_snapshots(
    guild_id: int,
) -> Dict[int, Dict[str, Any]]:
    async with connect() as db:
        async with db.execute(
            """
            SELECT channel_id, send_messages, send_messages_in_threads
            FROM security_lockdown_overwrites
            WHERE guild_id = ?
            ORDER BY channel_id
            """,
            (int(guild_id),),
        ) as cursor:
            rows = await cursor.fetchall()
    return {
        int(channel_id): {
            "send_messages": None if send_messages is None else bool(send_messages),
            "send_messages_in_threads": (
                None
                if send_messages_in_threads is None
                else bool(send_messages_in_threads)
            ),
        }
        for channel_id, send_messages, send_messages_in_threads in rows
    }


async def set_security_lockdown_status(guild_id: int, status: str) -> None:
    status = str(status).strip().lower()
    if status not in {
        "unlocked",
        "locking",
        "locked",
        "unlocking",
        "lock_partial",
        "unlock_partial",
    }:
        raise ValueError("invalid security lockdown status")
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO security_lockdown_state (guild_id, status, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(guild_id), status),
        )
        await db.commit()


async def get_security_lockdown_status(guild_id: int) -> str:
    async with connect() as db:
        async with db.execute(
            "SELECT status FROM security_lockdown_state WHERE guild_id = ?",
            (int(guild_id),),
        ) as cursor:
            row = await cursor.fetchone()
    return str(row[0]) if row else "unlocked"


async def clear_security_lockdown_snapshots(guild_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "DELETE FROM security_lockdown_overwrites WHERE guild_id = ?",
            (int(guild_id),),
        )
        await db.commit()


async def complete_security_lockdown_unlock(guild_id: int) -> None:
    """Atomically mark an unlock complete and remove its restore snapshots."""
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "DELETE FROM security_lockdown_overwrites WHERE guild_id = ?",
            (int(guild_id),),
        )
        await db.execute(
            """
            INSERT INTO security_lockdown_state (guild_id, status, updated_at)
            VALUES (?, 'unlocked', CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                status = 'unlocked',
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(guild_id),),
        )
        await db.commit()


def _row_to_settings(guild_id: int, row: Optional[Any]) -> Dict[str, Any]:
    data = dict(row) if row is not None else {}
    settings: Dict[str, Any] = {}
    for key, (_, default, kind) in SETTINGS_SCHEMA.items():
        value = data.get(key)
        if value is None:
            value = default
        elif kind == "bool":
            value = bool(value)
        elif kind == "float":
            value = float(value)
        elif kind == "json_list":
            try:
                value = json.loads(value) if isinstance(value, str) else value
            except (TypeError, ValueError):
                value = []
            if not isinstance(value, list):
                value = []
            value = [str(item) for item in value if isinstance(item, str)]
        elif kind == "json_map":
            try:
                value = json.loads(value) if isinstance(value, str) else value
            except (TypeError, ValueError):
                value = {}
            if not isinstance(value, dict):
                value = {}
            if key == "management_role_ids":
                value = {
                    tier: str(value.get(tier, "") or "")
                    for tier in ("admin", "moderator", "staff")
                }
            else:
                value = {
                    str(key): float(multiplier)
                    for key, multiplier in value.items()
                    if str(key).isdigit()
                    and isinstance(multiplier, (int, float))
                    and 0.0 < float(multiplier) <= 10.0
                }
        elif kind in ("int", "id"):
            value = int(value)
        settings[key] = value
    return {
        "guild_id": guild_id,
        "revision": int(data.get("revision") or 0),
        "updated_at": data.get("updated_at"),
        "settings": settings,
    }


def _cache_get(guild_id: int) -> Optional[Dict[str, Any]]:
    entry = _settings_cache.get(guild_id)
    if not entry:
        return None
    loaded_at, snapshot = entry
    if time.monotonic() - loaded_at > CACHE_TTL:
        _settings_cache.pop(guild_id, None)
        return None
    _settings_cache.move_to_end(guild_id)
    return {**snapshot, "settings": dict(snapshot["settings"])}


def _cache_put(guild_id: int, snapshot: Dict[str, Any]) -> None:
    _settings_cache[guild_id] = (time.monotonic(), {**snapshot, "settings": dict(snapshot["settings"])})
    _settings_cache.move_to_end(guild_id)
    while len(_settings_cache) > CACHE_MAX:
        _settings_cache.popitem(last=False)


def invalidate_guild_settings(guild_id: Optional[int] = None) -> None:
    """إبطال الكاش لسيرفر واحد أو للجميع (مفيد للاختبارات والكتابات الخارجية)."""
    if guild_id is None:
        _settings_cache.clear()
    else:
        _settings_cache.pop(guild_id, None)


def _lock_for(guild_id: int) -> asyncio.Lock:
    lock = _guild_locks.get(guild_id)
    if lock is None:
        lock = _guild_locks[guild_id] = asyncio.Lock()
    return lock


def _release_lock(guild_id: int) -> None:
    lock = _guild_locks.get(guild_id)
    if lock is not None and not lock.locked() and not getattr(lock, "_waiters", None):
        _guild_locks.pop(guild_id, None)


def validate_setting(key: str, value: Any) -> Any:
    """تحويل القيمة والتحقق منها حسب المخطط؛ يرفع ValueError برسالة عربية."""
    if key not in SETTINGS_SCHEMA:
        raise ValueError("حقل غير معروف")
    kind = SETTINGS_SCHEMA[key][2]
    if isinstance(value, bool) and kind != "bool":
        raise ValueError("نوع القيمة غير صالح")
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("يجب أن تكون القيمة تشغيل/إيقاف")
        return value
    if kind == "id":
        if value in (None, ""):
            return None
        text = str(value)
        if key in {"leaderboard_channel_id", "leaderboard_message_id"} and text == "0":
            return 0
        if not text.isdigit() or not 15 <= len(text) <= 22:
            raise ValueError("معرّف ديسكورد غير صالح")
        return int(text)
    if kind == "int":
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("يجب أن تكون القيمة عدداً صحيحاً")
        if not isinstance(value, (int, float)):
            raise ValueError("يجب أن تكون القيمة رقماً")
        value = int(value)
        limits = {
            "anti_alt_days": (0, 365),
            "anti_spam_max_messages": (1, 100),
            "anti_spam_time_window_seconds": (1, 3600),
            "anti_spam_timeout_duration_minutes": (1, 10080),
            "anti_mention_max_per_message": (1, 100),
            "anti_mention_target_max_repeats": (1, 100),
            "anti_mention_target_time_window_seconds": (1, 3600),
            "anti_mention_timeout_duration_minutes": (1, 10080),
            "daily_amount": (0, 1_000_000),
            "daily_base_amount": (0, 1_000_000),
        }
        low, high = limits.get(key, (0, 2**31 - 1))
        if not low <= value <= high:
            raise ValueError(f"القيمة يجب أن تكون بين {low} و {high}")
        return value
    if kind == "float":
        if not isinstance(value, (int, float)):
            raise ValueError("يجب أن تكون القيمة رقماً")
        value = float(value)
        if value != value or not 0.0 <= value <= 100.0:
            raise ValueError("النسبة يجب أن تكون بين 0 و 100")
        return round(value, 2)
    if kind == "json_list":
        if not isinstance(value, list):
            raise ValueError("يجب أن تكون قائمة الكلمات نصية")
        words = []
        for item in value[:200]:
            if not isinstance(item, str):
                raise ValueError("كل كلمة محظورة يجب أن تكون نصاً")
            item = item.strip().replace("\x00", "")
            if item and len(item) <= 80:
                words.append(item)
        return list(dict.fromkeys(words))
    if kind == "json_map":
        if not isinstance(value, dict):
            raise ValueError("يجب أن تكون مضاعفات الرتب في صيغة JSON")
        if key == "management_role_ids":
            allowed_tiers = ("admin", "moderator", "staff")
            if set(value) - set(allowed_tiers):
                raise ValueError("مستوى الإدارة غير صالح")
            result = {tier: "" for tier in allowed_tiers}
            seen = set()
            for tier in allowed_tiers:
                raw_id = value.get(tier)
                if raw_id in (None, ""):
                    continue
                role_id = str(raw_id)
                if (
                    not role_id.isascii()
                    or not role_id.isdigit()
                    or not 15 <= len(role_id) <= 22
                ):
                    raise ValueError("معرّف رتبة الإدارة غير صالح")
                if role_id in seen:
                    raise ValueError("يجب اختيار رتبة مختلفة لكل مستوى")
                seen.add(role_id)
                result[tier] = role_id
            return result
        result = {}
        for role_id, multiplier in list(value.items())[:100]:
            if not str(role_id).isdigit():
                raise ValueError("معرّف الرتبة غير صالح")
            try:
                multiplier = float(multiplier)
            except (TypeError, ValueError):
                raise ValueError("قيمة المضاعف غير صالحة")
            if not 0.0 < multiplier <= 10.0:
                raise ValueError("المضاعف يجب أن يكون أكبر من صفر وحتى 10")
            result[str(role_id)] = round(multiplier, 3)
        return result
    # str
    if not isinstance(value, str):
        raise ValueError("يجب أن تكون القيمة نصاً")
    value = value.replace("\r\n", "\n").replace("\x00", "")
    if key == "prefix":
        value = value.strip()
        if not 1 <= len(value) <= 5 or any(ch.isspace() for ch in value):
            raise ValueError("البادئة يجب أن تكون من 1 إلى 5 أحرف بدون مسافات")
    elif key in {"welcome_message", "leave_message", "welcome_embed_description"} and len(value) > 1000:
        raise ValueError("رسالة الترحيب يجب ألا تتجاوز 1000 حرف")
    elif key == "welcome_embed_title" and len(value) > 256:
        raise ValueError("عنوان الـ Embed يجب ألا يتجاوز 256 حرفاً")
    elif key == "welcome_embed_footer" and len(value) > 2048:
        raise ValueError("تذييل الـ Embed طويل جداً")
    elif key == "welcome_embed_color":
        if not (len(value) == 7 and value.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in value[1:])):
            raise ValueError("لون الـ Embed يجب أن يكون بصيغة #RRGGBB")
    elif key == "welcome_embed_image_url":
        if value:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("رابط صورة الـ Embed غير صالح")
    elif key in {"anti_spam_action", "anti_mention_action"}:
        if value not in AUTOMOD_ACTIONS:
            raise ValueError("إجراء الحماية غير صالح")
    return value


async def get_guild_settings(guild_id: int) -> Dict[str, Any]:
    """جلب إعدادات السيرفر (مع القيم الافتراضية) من الكاش أو القرص؛ لا يُنشئ صفاً."""
    guild_id = int(guild_id)
    cached = _cache_get(guild_id)
    if cached is not None:
        return cached
    async with connect(aiosqlite.Row) as db:
        async with db.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)) as cur:
            row = await cur.fetchone()
    snapshot = _row_to_settings(guild_id, row)
    # لا نستبدل نسخة أحدث وصلت أثناء القراءة (الكتابة تنشر الإصدار الملتزم فقط)
    current = _settings_cache.get(guild_id)
    if current is None or current[1]["revision"] <= snapshot["revision"]:
        _cache_put(guild_id, snapshot)
    return {**snapshot, "settings": dict(snapshot["settings"])}


async def update_guild_settings(
    guild_id: int, expected_revision: Optional[int] = None, **kwargs: Any
) -> Dict[str, Any]:
    """تحديث الحقول المُمرّرة فقط ذرياً مع رفع updated_at والإصدار؛ يرفع SettingsConflict عند التعارض."""
    guild_id = int(guild_id)
    changes = {key: validate_setting(key, value) for key, value in kwargs.items()}
    if not changes:
        return await get_guild_settings(guild_id)
    # مزامنة الأعمدة القديمة مع الجديدة للحفاظ على التوافق
    for new_col, legacy in LEGACY_ALIASES.items():
        if new_col in changes:
            changes[legacy] = changes[new_col]
    columns = list(changes)
    values = [
        int(value)
        if isinstance(value, bool)
        else json.dumps(value, ensure_ascii=False)
        if SETTINGS_SCHEMA.get(key, (None, None, None))[2] in ("json_list", "json_map")
        else value
        for key, value in changes.items()
    ]
    now = _utc_now()
    assignments = ", ".join(f"{col} = excluded.{col}" for col in columns)
    sql = (
        f"INSERT INTO guild_settings (guild_id, {', '.join(columns)}, revision, updated_at) "
        f"VALUES (?, {', '.join('?' for _ in columns)}, 1, ?) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {assignments}, "
        "revision = guild_settings.revision + 1, updated_at = excluded.updated_at"
    )
    params: List[Any] = [guild_id, *values, now]
    if expected_revision is not None:
        expected_revision = int(expected_revision)
        if expected_revision < 0:
            raise ValueError("رقم الإصدار غير صالح")
        if expected_revision == 0:
            # صف جديد أو صف قائم لم يُعدَّل بعد
            sql += " WHERE guild_settings.revision = 0"
        else:
            # يجب أن يكون الصف موجوداً بالإصدار المتوقع؛ لا إدراج ضمني هنا
            sets = ", ".join(f"{col} = ?" for col in columns)
            sql = (
                f"UPDATE guild_settings SET {sets}, revision = revision + 1, updated_at = ? "
                "WHERE guild_id = ? AND revision = ?"
            )
            params = [*values, now, guild_id, expected_revision]
    lock = _lock_for(guild_id)
    try:
        async with lock:
            async with connect(aiosqlite.Row) as db:
                try:
                    cur = await db.execute(sql, params)
                    changed = cur.rowcount
                    await cur.close()
                    if changed == 0:
                        await db.rollback()
                        async with db.execute(
                            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
                        ) as cur:
                            current = _row_to_settings(guild_id, await cur.fetchone())
                        raise SettingsConflict(current)
                    async with db.execute(
                        "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
                    ) as cur:
                        row = await cur.fetchone()
                    await db.commit()
                except BaseException:
                    # فشل أو إلغاء: لا ننشر بيانات غير ملتزمة
                    invalidate_guild_settings(guild_id)
                    raise
            snapshot = _row_to_settings(guild_id, row)
            _cache_put(guild_id, snapshot)  # النشر بعد الالتزام فقط
            return {**snapshot, "settings": dict(snapshot["settings"])}
    finally:
        _release_lock(guild_id)  # بعد تحرير القفل فعلياً


# -------------------------------------------------------------
# دوال الاقتصاد والمستويات (Economy & Levels API)
# -------------------------------------------------------------
async def get_or_create_user(user_id: int, guild_id: int) -> Dict[str, Any]:
    """جلب بيانات العضو أو إنشاؤه بأمان عند الطلبات المتزامنة."""
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, guild_id) VALUES (?, ?)",
            (int(user_id), int(guild_id)),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ? AND guild_id = ?",
            (int(user_id), int(guild_id)),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("user row disappeared after atomic creation")
            return dict(row)


async def claim_daily_reward(
    user_id: int,
    guild_id: int,
    today: str,
    reward: int,
) -> bool:
    """صرف المكافأة اليومية مرة واحدة، مع إنشاء الحساب ضمن نفس المعاملة."""
    reward = max(0, int(reward))
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, guild_id) VALUES (?, ?)",
                (int(user_id), int(guild_id)),
            )
            cursor = await db.execute(
                """
                UPDATE users
                SET balance = balance + ?, last_daily = ?
                WHERE user_id = ? AND guild_id = ?
                  AND (last_daily IS NULL OR last_daily <> ?)
                """,
                (
                    reward,
                    str(today),
                    int(user_id),
                    int(guild_id),
                    str(today),
                ),
            )
            claimed = cursor.rowcount == 1
            await db.commit()
            return claimed
        except Exception:
            await db.rollback()
            raise


async def update_balance(
    user_id: int,
    guild_id: int,
    amount: int,
    account: str = "balance",
) -> int:
    """تعديل رصيد العضو (كاش أو بنك) بأمان وحماية من الرصيد السالب."""
    col = "bank" if account == "bank" else "balance"
    async with connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, guild_id) VALUES (?, ?)",
            (int(user_id), int(guild_id)),
        )
        # استخدام Parameterized Query لتجنب ثغرات حقن الاستعلامات
        query = f"UPDATE users SET {col} = MAX(0, {col} + ?) WHERE user_id = ? AND guild_id = ?"
        await db.execute(query, (int(amount), int(user_id), int(guild_id)))
        await db.commit()

        async with db.execute(
            f"SELECT {col} FROM users WHERE user_id = ? AND guild_id = ?",
            (int(user_id), int(guild_id)),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def adjust_user_balance(
    guild_id: int,
    user_id: int,
    wallet_delta: int = 0,
    bank_delta: int = 0,
) -> Optional[Dict[str, Any]]:
    """Atomically adjust both wallets while preventing either from going negative."""
    wallet_delta, bank_delta = int(wallet_delta), int(bank_delta)
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, guild_id) VALUES (?, ?)",
                (int(user_id), int(guild_id)),
            )
            cur = await db.execute(
                """
                UPDATE users
                SET balance = balance + ?, bank = bank + ?
                WHERE user_id = ? AND guild_id = ?
                  AND balance + ? >= 0 AND bank + ? >= 0
                """,
                (
                    wallet_delta,
                    bank_delta,
                    int(user_id),
                    int(guild_id),
                    wallet_delta,
                    bank_delta,
                ),
            )
            if cur.rowcount != 1:
                await db.rollback()
                return None
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ? AND guild_id = ?",
                (int(user_id), int(guild_id)),
            ) as cursor:
                row = await cursor.fetchone()
            await db.commit()
            return dict(row) if row else None
        except Exception:
            await db.rollback()
            raise


async def claim_scaled_daily_reward(
    user_id: int,
    guild_id: int,
    today: str,
    base_amount: int,
    role_multiplier: float,
) -> Optional[Dict[str, Any]]:
    """Calculate and claim a role-scaled daily reward in one transaction."""
    base_amount = max(0, int(base_amount))
    role_multiplier = max(0.0, float(role_multiplier))
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, guild_id) VALUES (?, ?)",
                (int(user_id), int(guild_id)),
            )
            async with db.execute(
                "SELECT last_daily FROM users WHERE user_id = ? AND guild_id = ?",
                (int(user_id), int(guild_id)),
            ) as cur:
                account = await cur.fetchone()
            if account is None or account["last_daily"] == str(today):
                await db.rollback()
                return None
            reward = max(0, round(base_amount * role_multiplier))
            cur = await db.execute(
                """
                UPDATE users SET balance = balance + ?, last_daily = ?
                WHERE user_id = ? AND guild_id = ?
                  AND (last_daily IS NULL OR last_daily <> ?)
                """,
                (reward, str(today), int(user_id), int(guild_id), str(today)),
            )
            if cur.rowcount != 1:
                await db.rollback()
                return None
            await db.commit()
            return {
                "reward": int(reward),
                "base_amount": base_amount,
                "role_multiplier": round(role_multiplier, 3),
            }
        except Exception:
            await db.rollback()
            raise


async def set_leaderboard_embed_target(
    guild_id: int,
    channel_id: int,
    message_id: int = 0,
) -> Dict[str, Any]:
    return await update_guild_settings(
        int(guild_id),
        leaderboard_channel_id=int(channel_id),
        leaderboard_message_id=int(message_id),
    )


async def get_role_multipliers(guild_id: int) -> Dict[str, float]:
    settings = await get_guild_settings(int(guild_id))
    return dict(settings["settings"].get("role_multipliers") or {})


async def set_role_multiplier(
    guild_id: int,
    role_id: int,
    multiplier: float,
) -> Dict[str, Any]:
    multiplier = float(multiplier)
    if not 0.0 < multiplier <= 10.0:
        raise ValueError("المضاعف يجب أن يكون أكبر من صفر وحتى 10")
    multipliers = await get_role_multipliers(int(guild_id))
    multipliers[str(int(role_id))] = round(multiplier, 3)
    return await update_guild_settings(
        int(guild_id),
        role_multipliers=multipliers,
    )


async def get_leaderboard_targets() -> list[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT guild_id, leaderboard_channel_id, leaderboard_message_id
            FROM guild_settings
            WHERE leaderboard_channel_id IS NOT NULL
              AND leaderboard_channel_id > 0
            """
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def add_economy_audit(
    guild_id: int,
    user_id: int,
    actor_id: int,
    action: str,
    wallet_delta: int = 0,
    details: str = "",
) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO economy_audit_logs
                (guild_id, user_id, actor_id, action, wallet_delta, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(guild_id),
                int(user_id),
                int(actor_id),
                str(action)[:80],
                int(wallet_delta),
                str(details)[:1000],
            ),
        )
        await db.commit()


async def move_balance(
    user_id: int,
    guild_id: int,
    amount: int,
    from_account: str,
    to_account: str,
) -> bool:
    """نقل رصيد بين الكاش والبنك دون نافذة سباق بين عمليتي خصم وإضافة."""
    amount = int(amount)
    columns = {"balance", "bank"}
    if amount <= 0 or from_account not in columns or to_account not in columns:
        return False
    if from_account == to_account:
        return False

    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, guild_id) VALUES (?, ?)",
                (int(user_id), int(guild_id)),
            )
            cursor = await db.execute(
                f"""
                UPDATE users SET {from_account} = {from_account} - ?
                WHERE user_id = ? AND guild_id = ? AND {from_account} >= ?
                """,
                (amount, int(user_id), int(guild_id), amount),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return False
            await db.execute(
                f"""
                UPDATE users SET {to_account} = {to_account} + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, int(user_id), int(guild_id)),
            )
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise


async def transfer_balance(
    guild_id: int,
    from_user_id: int,
    to_user_id: int,
    amount: int,
) -> bool:
    """تحويل كاش ذري بين عضوين مع تسجيل العملية."""
    amount = int(amount)
    if amount <= 0 or int(from_user_id) == int(to_user_id):
        return False
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, guild_id) VALUES (?, ?)",
            (int(from_user_id), int(guild_id)),
        )
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, guild_id) VALUES (?, ?)",
            (int(to_user_id), int(guild_id)),
        )
        cur = await db.execute(
            """
            UPDATE users SET balance = balance - ?
            WHERE user_id = ? AND guild_id = ? AND balance >= ?
            """,
            (amount, int(from_user_id), int(guild_id), amount),
        )
        if cur.rowcount != 1:
            await db.rollback()
            return False
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ? AND guild_id = ?",
            (amount, int(to_user_id), int(guild_id)),
        )
        await db.execute(
            """
            INSERT INTO economy_transactions
                (guild_id, from_user_id, to_user_id, amount, kind)
            VALUES (?, ?, ?, ?, 'transfer')
            """,
            (int(guild_id), int(from_user_id), int(to_user_id), amount),
        )
        await db.commit()
    return True


async def get_economy_leaderboard(
    guild_id: int,
    limit: int = 10,
) -> list[Dict[str, Any]]:
    limit = max(1, min(int(limit), 25))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT user_id, balance, bank, last_daily,
                   (balance + bank) AS total
            FROM users
            WHERE guild_id = ?
            ORDER BY total DESC, user_id ASC
            LIMIT ?
            """,
            (int(guild_id), limit),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def record_tournament_score(
    guild_id: int,
    winner_team: str,
    loser_team: str,
) -> None:
    async with connect() as db:
        for team, won in ((winner_team, True), (loser_team, False)):
            await db.execute(
                """
                INSERT INTO tournament_scores
                    (guild_id, team_name, points, wins, losses)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, team_name) DO UPDATE SET
                    points = tournament_scores.points + excluded.points,
                    wins = tournament_scores.wins + excluded.wins,
                    losses = tournament_scores.losses + excluded.losses,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(guild_id),
                    str(team)[:100],
                    3 if won else 0,
                    1 if won else 0,
                    0 if won else 1,
                ),
            )
        await db.commit()


async def get_tournament_scores(
    guild_id: int,
    limit: int = 25,
) -> list[Dict[str, Any]]:
    limit = max(1, min(int(limit), 50))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT team_name, points, wins, losses
            FROM tournament_scores
            WHERE guild_id = ?
            ORDER BY points DESC, wins DESC, team_name ASC
            LIMIT ?
            """,
            (int(guild_id), limit),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def create_giveaway(
    guild_id: int,
    channel_id: int,
    prize: str,
    ends_at: str,
    created_by: int,
) -> int:
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO giveaways
                (guild_id, channel_id, prize, ends_at, created_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(guild_id),
                int(channel_id),
                str(prize).strip()[:200],
                str(ends_at),
                int(created_by),
            ),
        )
        giveaway_id = cur.lastrowid
        await db.commit()
    return int(giveaway_id)


async def set_giveaway_message(giveaway_id: int, message_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?",
            (int(message_id), int(giveaway_id)),
        )
        await db.commit()


async def add_giveaway_entry(giveaway_id: int, user_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id)
            SELECT ?, ?
            WHERE EXISTS (
                SELECT 1 FROM giveaways
                WHERE id = ? AND status = 'open'
            )
            """,
            (int(giveaway_id), int(user_id), int(giveaway_id)),
        )
        changed = cur.rowcount > 0
        await db.commit()
    return changed


async def get_open_giveaways() -> list[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, channel_id, message_id, prize, ends_at
            FROM giveaways
            WHERE status = 'open' AND message_id > 0
            ORDER BY id ASC
            """
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_due_giveaways(now: Optional[str] = None) -> list[Dict[str, Any]]:
    current = now or _utc_now()
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, channel_id, message_id, prize, ends_at
            FROM giveaways
            WHERE status = 'open' AND message_id > 0 AND ends_at <= ?
            ORDER BY ends_at ASC, id ASC
            LIMIT 100
            """,
            (current,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_giveaway_entries(giveaway_id: int) -> list[int]:
    async with connect() as db:
        async with db.execute(
            """
            SELECT user_id FROM giveaway_entries
            WHERE giveaway_id = ? ORDER BY user_id ASC
            """,
            (int(giveaway_id),),
        ) as cur:
            return [int(row[0]) for row in await cur.fetchall()]


async def complete_giveaway(giveaway_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE giveaways SET status = 'completed'
            WHERE id = ? AND status = 'open'
            """,
            (int(giveaway_id),),
        )
        changed = cur.rowcount > 0
        await db.commit()
    return changed


async def cancel_giveaway(giveaway_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE giveaways SET status = 'cancelled'
            WHERE id = ? AND status = 'open'
            """,
            (int(giveaway_id),),
        )
        changed = cur.rowcount > 0
        await db.commit()
    return changed


async def create_tournament(
    guild_id: int,
    channel_id: int,
    title: str,
    max_players: int,
    created_by: int,
) -> int:
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO tournaments
                (guild_id, channel_id, title, max_players, created_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(guild_id),
                int(channel_id),
                str(title).strip()[:150],
                max(2, min(int(max_players), 100)),
                int(created_by),
            ),
        )
        tournament_id = cur.lastrowid
        await db.commit()
    return int(tournament_id)


async def set_tournament_message(tournament_id: int, message_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE tournaments SET message_id = ? WHERE id = ?",
            (int(message_id), int(tournament_id)),
        )
        await db.commit()


async def cancel_tournament(tournament_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE tournaments SET status = 'cancelled'
            WHERE id = ? AND status = 'open'
            """,
            (int(tournament_id),),
        )
        changed = cur.rowcount > 0
        await db.commit()
    return changed


async def add_tournament_entry(tournament_id: int, user_id: int) -> tuple[bool, int, int]:
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT max_players, status FROM tournaments WHERE id = ?",
            (int(tournament_id),),
        ) as cur:
            tournament = await cur.fetchone()
        if tournament is None or tournament["status"] != "open":
            return False, 0, 0
        async with db.execute(
            "SELECT COUNT(*) FROM tournament_entries WHERE tournament_id = ?",
            (int(tournament_id),),
        ) as cur:
            count = int((await cur.fetchone())[0])
        if count >= int(tournament["max_players"]):
            return False, count, int(tournament["max_players"])
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO tournament_entries (tournament_id, user_id)
            VALUES (?, ?)
            """,
            (int(tournament_id), int(user_id)),
        )
        if cur.rowcount > 0:
            count += 1
        await db.commit()
        return cur.rowcount > 0, count, int(tournament["max_players"])


async def get_open_tournaments() -> list[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, channel_id, message_id, title, max_players
            FROM tournaments
            WHERE status = 'open' AND message_id > 0
            ORDER BY id ASC
            """
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def create_scrim_config(
    guild_id: int,
    channel_id: int,
    title: str,
    game_type: str,
    team_size: int,
    max_slots: int,
) -> Dict[str, Any]:
    """Create an independent scrim lobby configuration."""
    team_size = max(1, min(int(team_size), 16))
    max_slots = max(1, min(int(max_slots), 128))
    async with connect(aiosqlite.Row) as db:
        cur = await db.execute(
            """
            INSERT INTO scrim_configs
                (guild_id, channel_id, title, game_type, team_size, max_slots)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(guild_id),
                int(channel_id),
                str(title).strip()[:150],
                str(game_type).strip()[:80],
                team_size,
                max_slots,
            ),
        )
        scrim_id = int(cur.lastrowid)
        await db.commit()
        async with db.execute(
            "SELECT * FROM scrim_configs WHERE id = ?", (scrim_id,)
        ) as row_cursor:
            row = await row_cursor.fetchone()
    return dict(row)


async def set_scrim_message(scrim_id: int, message_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE scrim_configs SET message_id = ? WHERE id = ?",
            (int(message_id), int(scrim_id)),
        )
        await db.commit()


async def close_scrim(scrim_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "UPDATE scrim_configs SET is_active = 0 WHERE id = ? AND is_active = 1",
            (int(scrim_id),),
        )
        await db.commit()
    return cur.rowcount > 0


async def get_active_scrims(guild_id: int) -> list[Dict[str, Any]]:
    """Return active scrims with occupancy and roster data for the dashboard."""
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT c.*,
                   COUNT(r.id) AS occupied_slots,
                   COALESCE(
                     json_group_array(
                       CASE WHEN r.id IS NULL THEN NULL ELSE json_object(
                         'id', r.id,
                         'slot_number', r.slot_number,
                         'team_name', r.team_name,
                         'leader_id', r.leader_id,
                         'members_json', r.members_json,
                         'checked_in', r.checked_in
                       ) END
                     ),
                     '[]'
                   ) AS registrations_json
            FROM scrim_configs AS c
            LEFT JOIN scrim_registrations AS r ON r.scrim_id = c.id
            WHERE c.guild_id = ? AND c.is_active = 1
            GROUP BY c.id
            ORDER BY c.created_at DESC, c.id DESC
            """,
            (int(guild_id),),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        registrations = []
        try:
            raw = json.loads(row.pop("registrations_json") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            try:
                item["members"] = json.loads(item.pop("members_json") or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                item["members"] = []
            item["checked_in"] = bool(item.get("checked_in"))
            registrations.append(item)
        row["occupied_slots"] = int(row.get("occupied_slots") or 0)
        row["registrations"] = registrations
    return rows


async def get_scrims(guild_id: int) -> list[Dict[str, Any]]:
    """Return active and closed scrims using the same dashboard payload shape."""
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT id FROM scrim_configs WHERE guild_id = ? ORDER BY id DESC",
            (int(guild_id),),
        ) as cur:
            ids = [int(row[0]) for row in await cur.fetchall()]
    # Keep one response contract and avoid duplicating roster decoding logic.
    result = []
    for scrim_id in ids:
        async with connect(aiosqlite.Row) as db:
            async with db.execute(
                """
                SELECT c.*, COUNT(r.id) AS occupied_slots
                FROM scrim_configs c
                LEFT JOIN scrim_registrations r ON r.scrim_id = c.id
                WHERE c.id = ? GROUP BY c.id
                """,
                (scrim_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                continue
            item = dict(row)
            async with db.execute(
                """
                SELECT id, slot_number, team_name, leader_id,
                       members_json, checked_in
                FROM scrim_registrations
                WHERE scrim_id = ? ORDER BY slot_number ASC
                """,
                (scrim_id,),
            ) as cur:
                registrations = []
                for registration in await cur.fetchall():
                    value = dict(registration)
                    try:
                        value["members"] = json.loads(value.pop("members_json") or "[]")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        value["members"] = []
                    value["checked_in"] = bool(value.get("checked_in"))
                    registrations.append(value)
            item["occupied_slots"] = int(item.get("occupied_slots") or 0)
            item["registrations"] = registrations
            result.append(item)
    return result


async def reserve_scrim_slot(
    scrim_id: int,
    team_name: str,
    leader_id: int,
    members_json: str | list[int] | None = None,
) -> Optional[Dict[str, Any]]:
    """Atomically reserve the first free slot, or return None if unavailable."""
    if isinstance(members_json, list):
        members_json = json.dumps(
            [int(member_id) for member_id in members_json if str(member_id).isdigit()]
        )
    members_json = str(members_json or "[]")
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT max_slots, is_active FROM scrim_configs WHERE id = ?",
            (int(scrim_id),),
        ) as cur:
            scrim = await cur.fetchone()
        if scrim is None or not bool(scrim["is_active"]):
            return None
        async with db.execute(
            "SELECT slot_number FROM scrim_registrations WHERE scrim_id = ?",
            (int(scrim_id),),
        ) as cur:
            used = {int(row[0]) for row in await cur.fetchall()}
        slot = next(
            (candidate for candidate in range(1, int(scrim["max_slots"]) + 1)
             if candidate not in used),
            None,
        )
        if slot is None:
            return None
        cur = await db.execute(
            """
            INSERT INTO scrim_registrations
                (scrim_id, slot_number, team_name, leader_id, members_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(scrim_id), slot, str(team_name).strip()[:100], int(leader_id), members_json),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM scrim_registrations WHERE id = ?", (int(cur.lastrowid),)
        ) as row_cursor:
            row = await row_cursor.fetchone()
    result = dict(row)
    try:
        result["members"] = json.loads(result.pop("members_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        result["members"] = []
    result["checked_in"] = bool(result.get("checked_in"))
    return result


async def cancel_scrim_slot(scrim_id: int, *, leader_id: int | None = None, slot_number: int | None = None) -> bool:
    """Release a reservation, scoped to its leader unless an admin caller bypasses it."""
    if leader_id is None and slot_number is None:
        return False
    async with connect() as db:
        conditions = ["scrim_id = ?"]
        params: list[Any] = [int(scrim_id)]
        if slot_number is not None:
            conditions.append("slot_number = ?")
            params.append(int(slot_number))
        if leader_id is not None:
            conditions.append("leader_id = ?")
            params.append(int(leader_id))
        cur = await db.execute(
            f"DELETE FROM scrim_registrations WHERE {' AND '.join(conditions)}",
            tuple(params),
        )
        await db.commit()
    return cur.rowcount > 0


async def toggle_scrim_checkin(
    scrim_id: int,
    *,
    leader_id: int | None = None,
    slot_number: int | None = None,
    checked_in: bool = True,
) -> Optional[Dict[str, Any]]:
    """Toggle check-in for one reservation and return the updated row."""
    if leader_id is None and slot_number is None:
        return None
    async with connect(aiosqlite.Row) as db:
        conditions = ["scrim_id = ?"]
        params: list[Any] = [int(scrim_id)]
        if slot_number is not None:
            conditions.append("slot_number = ?")
            params.append(int(slot_number))
        if leader_id is not None:
            conditions.append("leader_id = ?")
            params.append(int(leader_id))
        await db.execute(
            f"UPDATE scrim_registrations SET checked_in = ? WHERE {' AND '.join(conditions)}",
            (int(bool(checked_in)), *params),
        )
        await db.commit()
        async with db.execute(
            f"SELECT * FROM scrim_registrations WHERE {' AND '.join(conditions)}",
            tuple(params),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    result = dict(row)
    try:
        result["members"] = json.loads(result.pop("members_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        result["members"] = []
    result["checked_in"] = bool(result.get("checked_in"))
    return result


async def get_tournament_entries(tournament_id: int) -> list[int]:
    async with connect() as db:
        async with db.execute(
            """
            SELECT user_id FROM tournament_entries
            WHERE tournament_id = ? ORDER BY created_at ASC, user_id ASC
            """,
            (int(tournament_id),),
        ) as cur:
            return [int(row[0]) for row in await cur.fetchall()]


async def start_tournament(tournament_id: int) -> Optional[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT * FROM tournaments WHERE id = ? AND status = 'open'",
            (int(tournament_id),),
        ) as cur:
            tournament = await cur.fetchone()
        if tournament is None:
            return None
        async with db.execute(
            "SELECT user_id FROM tournament_entries WHERE tournament_id = ? ORDER BY created_at ASC",
            (int(tournament_id),),
        ) as cur:
            entries = [int(row[0]) for row in await cur.fetchall()]
        if len(entries) < 2:
            return None
        await db.execute(
            "UPDATE tournaments SET status = 'started' WHERE id = ? AND status = 'open'",
            (int(tournament_id),),
        )
        await db.commit()
        result = dict(tournament)
        result["entries"] = entries
        return result


# -------------------------------------------------------------
# دوال الإدارة والتحذيرات (Moderation API)
# -------------------------------------------------------------
async def add_warning(user_id: int, guild_id: int, mod_id: int, reason: str) -> int:
    """تسجيل تحذير وإرجاع إجمالي عدد تحذيرات العضو الحالية."""
    async with connect() as db:
        await db.execute(
            "INSERT INTO warnings (user_id, guild_id, moderator_id, reason) VALUES (?, ?, ?, ?)",
            (user_id, guild_id, mod_id, reason),
        )
        await db.commit()

        async with db.execute(
            "SELECT COUNT(*) FROM warnings WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ) as cur:
            count = (await cur.fetchone())[0]
            return count


async def get_warnings(user_id: int, guild_id: int) -> List[Tuple[int, str, str]]:
    """جلب أرشيف المخالفات (ID, Reason, Timestamp) الخاص بعضو معين."""
    async with connect() as db:
        async with db.execute(
            "SELECT id, reason, timestamp FROM warnings "
            "WHERE user_id = ? AND guild_id = ? ORDER BY id DESC LIMIT 10",
            (user_id, guild_id),
        ) as cur:
            return await cur.fetchall()


async def add_member_warning(
    guild_id: int,
    user_id: int,
    mod_id: int,
    reason: str,
) -> int:
    """Add a Step 4 warning without changing the legacy warnings contract."""
    async with connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO member_warnings (guild_id, user_id, moderator_id, reason)
            VALUES (?, ?, ?, ?)
            """,
            (int(guild_id), int(user_id), int(mod_id), str(reason).strip()[:2000]),
        )
        warning_id = int(cursor.lastrowid)
        await db.commit()
    return warning_id


async def get_member_warnings(guild_id: int, user_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, user_id, moderator_id, reason, created_at
            FROM member_warnings
            WHERE guild_id = ? AND user_id = ?
            ORDER BY id DESC
            """,
            (int(guild_id), int(user_id)),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def delete_member_warning(warning_id: int, guild_id: int | None = None) -> bool:
    async with connect() as db:
        if guild_id is None:
            cursor = await db.execute(
                "DELETE FROM member_warnings WHERE id = ?",
                (int(warning_id),),
            )
        else:
            cursor = await db.execute(
                "DELETE FROM member_warnings WHERE id = ? AND guild_id = ?",
                (int(warning_id), int(guild_id)),
            )
        changed = cursor.rowcount > 0
        await db.commit()
    return changed


async def clear_member_warnings(guild_id: int, user_id: int) -> int:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM member_warnings WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        )
        deleted = max(0, int(cursor.rowcount))
        await db.commit()
    return deleted


async def add_temp_role(
    guild_id: int,
    user_id: int,
    role_id: int,
    expires_at: Any,
) -> int:
    async with connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO temp_roles (guild_id, user_id, role_id, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                int(guild_id),
                int(user_id),
                int(role_id),
                _penalty_timestamp(expires_at),
            ),
        )
        entry_id = int(cursor.lastrowid)
        await db.commit()
    return entry_id


async def get_expired_temp_roles() -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, user_id, role_id, expires_at, created_at
            FROM temp_roles
            WHERE expires_at <= CURRENT_TIMESTAMP
            ORDER BY id ASC
            """
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def remove_temp_role_entry(entry_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM temp_roles WHERE id = ?",
            (int(entry_id),),
        )
        changed = cursor.rowcount > 0
        await db.commit()
    return changed


async def adjust_event_points(guild_id: int, user_id: int, delta: int) -> int:
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            INSERT INTO event_points (guild_id, user_id, points)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                points = event_points.points + excluded.points
            """,
            (int(guild_id), int(user_id), int(delta)),
        )
        async with db.execute(
            """
            SELECT points FROM event_points
            WHERE guild_id = ? AND user_id = ?
            """,
            (int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return int(row["points"]) if row else 0


async def reset_event_points(guild_id: int) -> int:
    async with connect() as db:
        cursor = await db.execute(
            "UPDATE event_points SET points = 0 WHERE guild_id = ?",
            (int(guild_id),),
        )
        changed = max(0, int(cursor.rowcount))
        await db.commit()
    return changed


async def get_event_leaderboard(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT guild_id, user_id, points
            FROM event_points
            WHERE guild_id = ?
            ORDER BY points DESC, user_id ASC
            """,
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def add_mod_note(
    guild_id: int,
    user_id: int,
    mod_id: int,
    text: str,
) -> int:
    async with connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO mod_notes (guild_id, user_id, moderator_id, note_text)
            VALUES (?, ?, ?, ?)
            """,
            (int(guild_id), int(user_id), int(mod_id), str(text).strip()[:4000]),
        )
        note_id = int(cursor.lastrowid)
        await db.commit()
    return note_id


async def get_mod_notes(guild_id: int, user_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, user_id, moderator_id, note_text, created_at
            FROM mod_notes
            WHERE guild_id = ? AND user_id = ?
            ORDER BY id DESC
            """,
            (int(guild_id), int(user_id)),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def delete_mod_note(note_id: int, guild_id: int | None = None) -> bool:
    async with connect() as db:
        if guild_id is None:
            cursor = await db.execute(
                "DELETE FROM mod_notes WHERE id = ?",
                (int(note_id),),
            )
        else:
            cursor = await db.execute(
                "DELETE FROM mod_notes WHERE id = ? AND guild_id = ?",
                (int(note_id), int(guild_id)),
            )
        changed = cursor.rowcount > 0
        await db.commit()
    return changed


def _penalty_timestamp(value: Any) -> str:
    """Normalize datetime-like expiry values to SQLite's sortable UTC format."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


async def add_temp_ban(guild_id: int, user_id: int, unban_at: Any) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO temp_bans (guild_id, user_id, unban_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                unban_at = excluded.unban_at
            """,
            (int(guild_id), int(user_id), _penalty_timestamp(unban_at)),
        )
        await db.commit()


async def get_expired_temp_bans() -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT guild_id, user_id, unban_at
            FROM temp_bans
            WHERE datetime(unban_at) <= CURRENT_TIMESTAMP
            ORDER BY unban_at ASC
            """
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def remove_temp_ban(guild_id: int, user_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM temp_bans WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        )
        await db.commit()
        return cursor.rowcount > 0


async def add_voice_ban(guild_id: int, user_id: int, mod_id: int) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO voice_bans (guild_id, user_id, banned_by)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                banned_by = excluded.banned_by,
                created_at = CURRENT_TIMESTAMP
            """,
            (int(guild_id), int(user_id), int(mod_id)),
        )
        await db.commit()


async def remove_voice_ban(guild_id: int, user_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM voice_bans WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        )
        await db.commit()
        return cursor.rowcount > 0


async def is_voice_banned(guild_id: int, user_id: int) -> bool:
    async with connect() as db:
        async with db.execute(
            "SELECT 1 FROM voice_bans WHERE guild_id = ? AND user_id = ? LIMIT 1",
            (int(guild_id), int(user_id)),
        ) as cur:
            return await cur.fetchone() is not None


async def add_text_mute(guild_id: int, user_id: int, mod_id: int) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO text_mutes (guild_id, user_id, muted_by)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                muted_by = excluded.muted_by,
                created_at = CURRENT_TIMESTAMP
            """,
            (int(guild_id), int(user_id), int(mod_id)),
        )
        await db.commit()


async def remove_text_mute(guild_id: int, user_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM text_mutes WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_text_mutes(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT guild_id, user_id, muted_by, created_at
            FROM text_mutes
            WHERE guild_id = ?
            ORDER BY created_at ASC
            """,
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def jail_user(
    guild_id: int,
    user_id: int,
    mod_id: int,
    saved_roles_json: str,
    jail_type: str = "general",
    private_channel_id: int = 0,
) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO jailed_users
                (guild_id, user_id, jailed_by, saved_roles, jail_type, private_channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                jailed_by = excluded.jailed_by,
                saved_roles = excluded.saved_roles,
                jail_type = excluded.jail_type,
                private_channel_id = excluded.private_channel_id,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                int(guild_id),
                int(user_id),
                int(mod_id),
                str(saved_roles_json),
                str(jail_type or "general"),
                int(private_channel_id or 0),
            ),
        )
        await db.commit()


async def get_jailed_user(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT guild_id, user_id, jailed_by, saved_roles, jail_type,
                   private_channel_id, created_at
            FROM jailed_users
            WHERE guild_id = ? AND user_id = ?
            """,
            (int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def unjail_user(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT guild_id, user_id, jailed_by, saved_roles, jail_type,
                   private_channel_id, created_at
            FROM jailed_users
            WHERE guild_id = ? AND user_id = ?
            """,
            (int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        await db.execute(
            "DELETE FROM jailed_users WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        )
        await db.commit()
        return dict(row)


async def add_channel_restriction(
    guild_id: int,
    channel_id: int,
    user_id: int,
    r_type: str,
) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO channel_blacklists
                (guild_id, channel_id, user_id, restriction_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, channel_id, user_id, restriction_type)
            DO UPDATE SET created_at = CURRENT_TIMESTAMP
            """,
            (int(guild_id), int(channel_id), int(user_id), str(r_type)),
        )
        await db.commit()


async def remove_channel_restriction(
    guild_id: int,
    channel_id: int,
    user_id: int,
    r_type: str,
) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            """
            DELETE FROM channel_blacklists
            WHERE guild_id = ? AND channel_id = ? AND user_id = ?
              AND restriction_type = ?
            """,
            (int(guild_id), int(channel_id), int(user_id), str(r_type)),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_channel_restrictions(
    guild_id: int,
    channel_id: int,
) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, channel_id, user_id, restriction_type, created_at
            FROM channel_blacklists
            WHERE guild_id = ? AND channel_id = ?
            ORDER BY id ASC
            """,
            (int(guild_id), int(channel_id)),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def record_invite_use(guild_id: int, inviter_id: int) -> int:
    """Atomically increment persistent invite usage and return the new total."""
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO invite_stats (guild_id, inviter_id, uses, last_used_at)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, inviter_id) DO UPDATE SET
                uses = invite_stats.uses + 1,
                last_used_at = CURRENT_TIMESTAMP
            """,
            (int(guild_id), int(inviter_id)),
        )
        await db.commit()
        async with db.execute(
            "SELECT uses FROM invite_stats WHERE guild_id = ? AND inviter_id = ?",
            (int(guild_id), int(inviter_id)),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


async def record_rules_agreement(
    guild_id: int,
    user_id: int,
    verified_role_id: Optional[int],
) -> str:
    """Upsert the agreement timestamp without creating duplicate rows."""
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO rules_agreements (guild_id, user_id, verified_role_id, agreed_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                verified_role_id = excluded.verified_role_id,
                agreed_at = CURRENT_TIMESTAMP
            """,
            (int(guild_id), int(user_id), verified_role_id),
        )
        await db.commit()
        async with db.execute(
            "SELECT agreed_at FROM rules_agreements WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
            return str(row[0]) if row else ""


async def save_role_panel(
    guild_id: int,
    channel_id: int,
    message_id: int,
    role_ids: list[int],
) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO role_panels (guild_id, channel_id, message_id, role_ids)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, channel_id, message_id) DO UPDATE SET
                role_ids = excluded.role_ids
            """,
            (int(guild_id), int(channel_id), int(message_id), json.dumps(role_ids)),
        )
        await db.commit()


async def get_role_panels(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT guild_id, channel_id, message_id, role_ids "
            "FROM role_panels WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            rows = []
            for row in await cur.fetchall():
                item = dict(row)
                try:
                    item["role_ids"] = [int(value) for value in json.loads(item["role_ids"])]
                except (TypeError, ValueError, json.JSONDecodeError):
                    item["role_ids"] = []
                rows.append(item)
            return rows


async def save_rules_panel(guild_id: int, channel_id: int, message_id: int) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO rules_panels (guild_id, channel_id, message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, channel_id, message_id) DO NOTHING
            """,
            (int(guild_id), int(channel_id), int(message_id)),
        )
        await db.commit()


async def get_rules_panels(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT guild_id, channel_id, message_id FROM rules_panels WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def save_self_role_panel(
    guild_id: int,
    channel_id: int,
    message_id: int,
    title: str,
    description: str,
    color: str,
    emoji: str,
    role_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    encoded_specs = json.dumps(role_specs, ensure_ascii=False)
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            INSERT INTO self_role_panels
                (guild_id, channel_id, message_id, title, description, color, emoji, role_specs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, message_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                title = excluded.title,
                description = excluded.description,
                color = excluded.color,
                emoji = excluded.emoji,
                role_specs = excluded.role_specs,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id, guild_id, channel_id, message_id, title, description, color, emoji,
                      role_specs, created_at, updated_at
            """,
            (
                int(guild_id),
                int(channel_id),
                int(message_id),
                str(title),
                str(description),
                str(color),
                str(emoji),
                encoded_specs,
            ),
        )
        row = await cursor.fetchone()
        await db.commit()
    result = dict(row) if row else {}
    try:
        result["role_specs"] = json.loads(result.get("role_specs") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        result["role_specs"] = []
    return result


async def get_self_role_panels(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, channel_id, message_id, title, description,
                   color, emoji, role_specs, created_at, updated_at
            FROM self_role_panels
            WHERE guild_id = ?
            ORDER BY id DESC
            """,
            (int(guild_id),),
        ) as cur:
            rows = []
            for row in await cur.fetchall():
                item = dict(row)
                try:
                    item["role_specs"] = json.loads(item.get("role_specs") or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    item["role_specs"] = []
                rows.append(item)
            return rows


def _json_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).isdigit()]


def _json_aliases(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = value.split(",")
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        alias = str(item or "").strip().lstrip("!/")
        if not alias or len(alias) > 80 or any(char.isspace() for char in alias):
            continue
        key = alias.casefold()
        if key not in seen:
            seen.add(key)
            result.append(alias)
    return result[:20]


async def get_command_controls(guild_id: int) -> dict[str, dict[str, Any]]:
    """Compatibility wrapper for the canonical command policy cache."""
    return await get_command_policies(guild_id)


async def get_command_policies(
    guild_id: int,
    *,
    refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    guild_id = int(guild_id)
    if not refresh and guild_id in COMMAND_CACHE:
        return {
            name: {**policy, "allowed_roles": list(policy["allowed_roles"]),
                   "allowed_channels": list(policy["allowed_channels"]),
                   "aliases": list(policy["aliases"])}
            for name, policy in COMMAND_CACHE[guild_id].items()
        }
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT command_name, is_enabled, aliases, allowed_roles,
                   allowed_channels, auto_delete_seconds, response_style,
                   response_template, updated_at
            FROM command_policies
            WHERE guild_id = ?
            ORDER BY command_name
            """,
            (guild_id,),
        ) as cur:
            result = {}
            for row in await cur.fetchall():
                item = dict(row)
                item["enabled"] = bool(item.pop("is_enabled"))
                item["aliases"] = _json_aliases(item.get("aliases"))
                item["allowed_roles"] = _json_ids(item["allowed_roles"])
                item["allowed_channels"] = _json_ids(item["allowed_channels"])
                try:
                    item["auto_delete_seconds"] = max(0, int(item.get("auto_delete_seconds") or 0))
                except (TypeError, ValueError):
                    item["auto_delete_seconds"] = 0
                item["response_style"] = str(item.get("response_style") or "default")
                item["response_template"] = str(item.get("response_template") or "")[:2000]
                item["response_mode"] = item["response_style"]
                item["custom_template"] = item["response_template"]
                result[item["command_name"]] = item
    COMMAND_CACHE[guild_id] = result
    return {
        name: {**policy, "allowed_roles": list(policy["allowed_roles"]),
               "allowed_channels": list(policy["allowed_channels"]),
               "aliases": list(policy["aliases"])}
        for name, policy in result.items()
    }


def invalidate_command_cache(guild_id: Optional[int] = None) -> None:
    if guild_id is None:
        COMMAND_CACHE.clear()
    else:
        COMMAND_CACHE.pop(int(guild_id), None)


async def save_command_control(
    guild_id: int,
    command_name: str,
    enabled: bool,
    allowed_roles: list[int | str] | None = None,
    allowed_channels: list[int | str] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper that preserves aliases already on the policy."""
    return await save_command_policy(
        guild_id,
        command_name,
        enabled,
        allowed_roles=allowed_roles,
        allowed_channels=allowed_channels,
    )


async def save_command_policy(
    guild_id: int,
    command_name: str,
    enabled: bool,
    allowed_roles: list[int | str] | None = None,
    allowed_channels: list[int | str] | None = None,
    aliases: list[str] | None = None,
    auto_delete_seconds: int | None = None,
    response_style: str | None = None,
    response_template: str | None = None,
) -> dict[str, Any]:
    guild_id = int(guild_id)
    name = str(command_name).strip().lower()
    roles = [str(role_id) for role_id in (allowed_roles or []) if str(role_id).isdigit()]
    channels = [str(channel_id) for channel_id in (allowed_channels or []) if str(channel_id).isdigit()]
    if (
        allowed_roles is None
        or allowed_channels is None
        or aliases is None
        or auto_delete_seconds is None
        or response_style is None
        or response_template is None
    ):
        async with connect(aiosqlite.Row) as db:
            async with db.execute(
                """
                SELECT aliases, allowed_roles, allowed_channels,
                       auto_delete_seconds, response_style, response_template
                FROM command_policies
                WHERE guild_id = ? AND command_name = ?
                """,
                (guild_id, name),
            ) as cur:
                existing = await cur.fetchone()
        if existing:
            if allowed_roles is None:
                roles = _json_ids(existing["allowed_roles"])
            if allowed_channels is None:
                channels = _json_ids(existing["allowed_channels"])
            if aliases is None:
                aliases = _json_aliases(existing["aliases"])
            if auto_delete_seconds is None:
                auto_delete_seconds = existing["auto_delete_seconds"]
            if response_style is None:
                response_style = existing["response_style"]
            if response_template is None:
                response_template = existing["response_template"]
    normalized_aliases = _json_aliases(aliases or [])
    try:
        auto_delete_seconds = int(auto_delete_seconds or 0)
    except (TypeError, ValueError):
        auto_delete_seconds = 0
    if auto_delete_seconds not in {0, 5, 10, 30, 60, 300}:
        auto_delete_seconds = 0
    response_style = str(response_style or "default").strip().lower()
    if response_style not in {"default", "embed", "compact", "silent"}:
        response_style = "default"
    response_template = str(response_template or "")[:2000]
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            INSERT INTO command_policies
                (guild_id, command_name, is_enabled, aliases,
                 allowed_roles, allowed_channels, auto_delete_seconds,
                 response_style, response_template, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, command_name) DO UPDATE SET
                is_enabled = excluded.is_enabled,
                aliases = excluded.aliases,
                allowed_roles = excluded.allowed_roles,
                allowed_channels = excluded.allowed_channels,
                auto_delete_seconds = excluded.auto_delete_seconds,
                response_style = excluded.response_style,
                response_template = excluded.response_template,
                updated_at = CURRENT_TIMESTAMP
            RETURNING command_name, is_enabled, aliases, allowed_roles,
                      allowed_channels, auto_delete_seconds, response_style,
                      response_template, updated_at
            """,
            (
                guild_id,
                name,
                int(bool(enabled)),
                json.dumps(normalized_aliases, ensure_ascii=False),
                json.dumps(roles, ensure_ascii=False),
                json.dumps(channels, ensure_ascii=False),
                auto_delete_seconds,
                response_style,
                response_template,
            ),
        )
        row = await cursor.fetchone()
        await db.commit()
    item = dict(row) if row else {
        "command_name": name,
        "is_enabled": int(bool(enabled)),
        "aliases": json.dumps(normalized_aliases, ensure_ascii=False),
        "allowed_roles": json.dumps(roles),
        "allowed_channels": json.dumps(channels),
        "auto_delete_seconds": auto_delete_seconds,
        "response_style": response_style,
        "response_template": response_template,
    }
    result = {
        "command_name": name,
        "enabled": bool(enabled),
        "aliases": _json_aliases(item.get("aliases")),
        "allowed_roles": _json_ids(item.get("allowed_roles")),
        "allowed_channels": _json_ids(item.get("allowed_channels")),
        "auto_delete_seconds": max(0, int(item.get("auto_delete_seconds") or 0)),
        "response_style": str(item.get("response_style") or "default"),
        "response_template": str(item.get("response_template") or "")[:2000],
        "response_mode": str(item.get("response_style") or "default"),
        "custom_template": str(item.get("response_template") or "")[:2000],
        "updated_at": item.get("updated_at"),
    }
    COMMAND_CACHE.setdefault(guild_id, {})[name] = result
    return result


async def get_auto_responders(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, trigger, match_type, response, enabled,
                   cooldown_seconds, bucket_capacity, channel_id,
                   target_type, target_id, reaction_emoji,
                   execution_count, updated_at
            FROM guild_auto_responders
            WHERE guild_id = ? AND enabled = 1
            ORDER BY id
            """,
            (int(guild_id),),
        ) as cur:
            rows = []
            for row in await cur.fetchall():
                item = dict(row)
                item["enabled"] = bool(item["enabled"])
                item["cooldown_seconds"] = max(0.0, float(item["cooldown_seconds"]))
                item["bucket_capacity"] = max(1, int(item["bucket_capacity"]))
                item["channel_id"] = (
                    str(item["channel_id"]) if item["channel_id"] is not None else None
                )
                item["target_type"] = (
                    item.get("target_type")
                    if item.get("target_type") in {"everyone", "role", "user"}
                    else "everyone"
                )
                item["target_id"] = max(0, int(item.get("target_id") or 0))
                item["reaction_emoji"] = str(item.get("reaction_emoji") or "")[:100]
                item["execution_count"] = max(0, int(item["execution_count"] or 0))
                rows.append(item)
            return rows


async def save_auto_responder(
    guild_id: int,
    trigger: str,
    match_type: str,
    response: str,
    *,
    enabled: bool = True,
    cooldown_seconds: float = 5.0,
    bucket_capacity: int = 1,
    channel_id: int | str | None = None,
    target_type: str = "everyone",
    target_id: int | str | None = 0,
    reaction_emoji: str = "",
) -> dict[str, Any]:
    target_type = str(target_type).strip().lower()
    if target_type not in {"everyone", "role", "user"}:
        raise ValueError("target_type must be one of everyone, role, user")
    try:
        target_id = max(0, int(target_id or 0))
    except (TypeError, ValueError) as error:
        raise ValueError("target_id must be a numeric Discord ID") from error
    reaction_emoji = str(reaction_emoji or "").strip()[:100]
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            INSERT INTO guild_auto_responders
                (guild_id, trigger, match_type, response, enabled,
                 cooldown_seconds, bucket_capacity, channel_id,
                 target_type, target_id, reaction_emoji, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, trigger, match_type, target_type, target_id) DO UPDATE SET
                response = excluded.response,
                enabled = excluded.enabled,
                cooldown_seconds = excluded.cooldown_seconds,
                bucket_capacity = excluded.bucket_capacity,
                channel_id = excluded.channel_id,
                target_type = excluded.target_type,
                target_id = excluded.target_id,
                reaction_emoji = excluded.reaction_emoji,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id, guild_id, trigger, match_type, response, enabled,
                      cooldown_seconds, bucket_capacity, channel_id,
                       target_type, target_id, reaction_emoji,
                       execution_count, updated_at
            """,
            (
                int(guild_id),
                str(trigger).strip(),
                str(match_type).strip().lower(),
                str(response)[:2000],
                int(bool(enabled)),
                max(0.0, float(cooldown_seconds)),
                max(1, int(bucket_capacity)),
                int(channel_id) if channel_id is not None else None,
                target_type,
                target_id,
                reaction_emoji,
            ),
        )
        row = await cursor.fetchone()
        await db.commit()
    item = dict(row) if row else {}
    item["enabled"] = bool(item.get("enabled", enabled))
    item["channel_id"] = (
        str(item["channel_id"]) if item.get("channel_id") is not None else None
    )
    item["execution_count"] = int(item.get("execution_count") or 0)
    return item


async def delete_auto_responder(guild_id: int, rule_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM guild_auto_responders WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(rule_id)),
        )
        deleted = cursor.rowcount > 0
        await db.commit()
    return deleted


async def record_auto_responder_execution(guild_id: int, rule_id: int) -> int:
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            UPDATE guild_auto_responders
            SET execution_count = execution_count + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ? AND id = ?
            """,
            (int(guild_id), int(rule_id)),
        )
        async with db.execute(
            "SELECT execution_count FROM guild_auto_responders WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(rule_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return int(row["execution_count"]) if row else 0


async def get_shortcuts(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, trigger, target_type, target, announcement,
                   enabled, updated_at
            FROM guild_shortcuts
            WHERE guild_id = ? AND enabled = 1
            ORDER BY id
            """,
            (int(guild_id),),
        ) as cur:
            rows = []
            for row in await cur.fetchall():
                item = dict(row)
                item["enabled"] = bool(item["enabled"])
                rows.append(item)
            return rows


async def save_shortcut(
    guild_id: int,
    trigger: str,
    target_type: str,
    *,
    target: str = "",
    announcement: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            INSERT INTO guild_shortcuts
                (guild_id, trigger, target_type, target, announcement,
                 enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, trigger) DO UPDATE SET
                target_type = excluded.target_type,
                target = excluded.target,
                announcement = excluded.announcement,
                enabled = excluded.enabled,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id, guild_id, trigger, target_type, target, announcement,
                      enabled, updated_at
            """,
            (
                int(guild_id),
                str(trigger).strip(),
                str(target_type).strip().lower(),
                str(target)[:100],
                str(announcement)[:2000],
                int(bool(enabled)),
            ),
        )
        row = await cursor.fetchone()
        await db.commit()
    item = dict(row) if row else {}
    item["enabled"] = bool(item.get("enabled", enabled))
    return item


async def delete_shortcut(guild_id: int, shortcut_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM guild_shortcuts WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(shortcut_id)),
        )
        await db.commit()
    return cursor.rowcount > 0


def _ticket_json_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = []
    return [str(item) for item in value] if isinstance(value, list) else []


async def save_ticket_panel(
    guild_id: int,
    channel_id: int,
    message_id: int,
    categories: list[dict[str, Any]],
    *,
    title: str = "مركز الدعم والتذاكر",
    description: str = "",
    color: int = 0x6366F1,
    mode: str = "dropdown",
) -> dict[str, Any]:
    encoded = json.dumps(categories, ensure_ascii=False)
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            INSERT INTO ticket_panels
                (guild_id, channel_id, message_id, categories, title,
                 description, color, mode, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, channel_id, message_id) DO UPDATE SET
                categories = excluded.categories,
                title = excluded.title,
                description = excluded.description,
                color = excluded.color,
                mode = excluded.mode,
                version = ticket_panels.version + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(guild_id),
                int(channel_id),
                int(message_id),
                encoded,
                str(title or "مركز الدعم والتذاكر")[:256],
                str(description or "")[:4096],
                max(0, min(0xFFFFFF, int(color))),
                "buttons" if mode == "buttons" else "dropdown",
            ),
        )
        await db.commit()
        async with db.execute(
            """
            SELECT rowid AS id, guild_id, channel_id, message_id, categories,
                   title, description, color, mode, version, updated_at
            FROM ticket_panels
            WHERE guild_id = ? AND channel_id = ? AND message_id = ?
            """,
            (int(guild_id), int(channel_id), int(message_id)),
        ) as cur:
            row = await cur.fetchone()
    result = dict(row) if row else {}
    try:
        result["categories"] = json.loads(result.get("categories") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        result["categories"] = categories
    for key in ("guild_id", "channel_id", "message_id", "id", "version"):
        if result.get(key) is not None:
            result[key] = int(result[key])
    result["color"] = int(result.get("color") or 0x6366F1)
    return result


async def get_ticket_panels() -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT rowid AS id, guild_id, channel_id, message_id, categories, "
            "title, description, color, mode, version, updated_at "
            "FROM ticket_panels ORDER BY updated_at DESC"
        ) as cur:
            rows = []
            for row in await cur.fetchall():
                item = dict(row)
                item["id"] = int(item["id"])
                item["guild_id"] = int(item["guild_id"])
                item["channel_id"] = int(item["channel_id"])
                if item.get("message_id") is not None:
                    item["message_id"] = int(item["message_id"])
                item["color"] = int(item.get("color") or 0x6366F1)
                item["version"] = int(item.get("version") or 1)
                try:
                    item["categories"] = json.loads(item["categories"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    item["categories"] = []
                rows.append(item)
            return rows


async def delete_ticket_panel(
    guild_id: int,
    channel_id: int,
    message_id: int,
) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            """
            DELETE FROM ticket_panels
            WHERE guild_id = ? AND channel_id = ? AND message_id = ?
            """,
            (int(guild_id), int(channel_id), int(message_id)),
        )
        await db.commit()
    return cursor.rowcount > 0


def _ticket_config_row(row) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    item["guild_id"] = int(item["guild_id"])
    for key in ("channel_id", "message_id"):
        if item.get(key) is not None:
            item[key] = int(item[key])
    item["embed_color"] = int(item.get("embed_color") or 0x5865F2)
    item["embed_title"] = str(item.get("embed_title") or "الدعم الفني")
    item["embed_description"] = str(item.get("embed_description") or "")
    item["footer_text"] = str(item.get("footer_text") or "PR1ME TEAM Support")
    for key in ("allow_user_close", "send_transcript_dm"):
        if key in item:
            item[key] = bool(item[key])
    for key in ("closed_category_id", "log_channel_id", "evaluation_channel_id"):
        if item.get(key) is not None:
            item[key] = int(item[key])
    for key in ("auto_close_minutes", "open_limit"):
        if key in item:
            item[key] = int(item.get(key) or 0)
    for key in ("permissions_json", "close_config_json"):
        raw = item.get(key)
        if isinstance(raw, str):
            try:
                item[key] = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                item[key] = {}
        elif not isinstance(raw, dict):
            item[key] = {}
    item["panel_mode"] = str(item.get("panel_mode") or "dropdown")
    item["select_placeholder"] = str(
        item.get("select_placeholder") or "اختر القسم المناسب لطلبك"
    )
    return item


def _ticket_settings_row(row) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    item["guild_id"] = int(item["guild_id"])
    for key in (
        "log_channel_id",
        "evaluation_channel_id",
        "default_open_category_id",
        "closed_category_id",
    ):
        if item.get(key) is not None:
            item[key] = int(item[key])
    item["allow_user_close"] = bool(item.get("allow_user_close", False))
    item["send_transcript_dm"] = bool(item.get("send_transcript_dm", True))
    return item


def _ticket_permissions_value(value: Any) -> dict[str, list[str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {}
    if not isinstance(value, dict):
        return {}
    allowed_actions = {
        "claim",
        "close",
        "rename",
        "priority",
        "transfer",
        "add_member",
        "remove_member",
        "private_ticket",
        "summon",
        "tag",
        "note",
        "reopen",
    }
    return {
        str(action): [str(role_id) for role_id in role_ids if str(role_id).isdigit()][:100]
        for action, role_ids in value.items()
        if str(action) in allowed_actions and isinstance(role_ids, list)
    }


async def get_ticket_settings(guild_id: int) -> dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM ticket_settings WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    if row:
        return _ticket_settings_row(row) or {}
    # Read-only compatibility fallback for installations where only the
    # original ticket_config row has been populated.
    config = await get_ticket_config(guild_id) or {}
    return {
        "guild_id": int(guild_id),
        "log_channel_id": config.get("log_channel_id"),
        "evaluation_channel_id": config.get("evaluation_channel_id"),
        "default_open_category_id": config.get("default_open_category_id"),
        "closed_category_id": config.get("closed_category_id"),
        "allow_user_close": bool(config.get("allow_user_close", False)),
        "send_transcript_dm": bool(config.get("send_transcript_dm", True)),
    }


async def save_ticket_settings(
    guild_id: int,
    *,
    log_channel_id: int | None | object = _UNSET,
    evaluation_channel_id: int | None | object = _UNSET,
    default_open_category_id: int | None | object = _UNSET,
    closed_category_id: int | None | object = _UNSET,
    allow_user_close: bool | object = _UNSET,
    send_transcript_dm: bool | object = _UNSET,
) -> dict[str, Any]:
    current = await get_ticket_settings(guild_id)
    values = {
        "log_channel_id": current.get("log_channel_id") if log_channel_id is _UNSET else log_channel_id,
        "evaluation_channel_id": current.get("evaluation_channel_id") if evaluation_channel_id is _UNSET else evaluation_channel_id,
        "default_open_category_id": current.get("default_open_category_id") if default_open_category_id is _UNSET else default_open_category_id,
        "closed_category_id": current.get("closed_category_id") if closed_category_id is _UNSET else closed_category_id,
        "allow_user_close": current.get("allow_user_close", False) if allow_user_close is _UNSET else bool(allow_user_close),
        "send_transcript_dm": current.get("send_transcript_dm", True) if send_transcript_dm is _UNSET else bool(send_transcript_dm),
    }
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            INSERT INTO ticket_settings
                (guild_id, log_channel_id, evaluation_channel_id,
                 default_open_category_id, closed_category_id,
                 allow_user_close, send_transcript_dm, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                log_channel_id = excluded.log_channel_id,
                evaluation_channel_id = excluded.evaluation_channel_id,
                default_open_category_id = excluded.default_open_category_id,
                closed_category_id = excluded.closed_category_id,
                allow_user_close = excluded.allow_user_close,
                send_transcript_dm = excluded.send_transcript_dm,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(guild_id),
                int(values["log_channel_id"]) if values["log_channel_id"] is not None else None,
                int(values["evaluation_channel_id"]) if values["evaluation_channel_id"] is not None else None,
                int(values["default_open_category_id"]) if values["default_open_category_id"] is not None else None,
                int(values["closed_category_id"]) if values["closed_category_id"] is not None else None,
                int(bool(values["allow_user_close"])),
                int(bool(values["send_transcript_dm"])),
            ),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM ticket_settings WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    return _ticket_settings_row(row) or {}


async def get_ticket_permissions(guild_id: int) -> dict[str, list[str]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT permissions FROM ticket_permissions WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    return _ticket_permissions_value(row["permissions"] if row else {})


async def save_ticket_permissions(
    guild_id: int, permissions: dict[str, Any]
) -> dict[str, list[str]]:
    normalized = _ticket_permissions_value(permissions)
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO ticket_permissions (guild_id, permissions, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                permissions = excluded.permissions,
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(guild_id), json.dumps(normalized, ensure_ascii=False)),
        )
        await db.commit()
    return normalized


def _ticket_option_row(row) -> dict[str, Any]:
    item = dict(row)
    for key in ("id", "guild_id", "role_id", "category_id"):
        if item.get(key) is not None:
            item[key] = int(item[key])
    for key, default in (
        ("label", ""),
        ("description", ""),
        ("emoji", "🎫"),
        ("welcome_msg", ""),
    ):
        item[key] = str(item.get(key) or default)
    return item


async def get_ticket_config(guild_id: int) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM ticket_config WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    return _ticket_config_row(row)


async def get_ticket_configs() -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM ticket_config ORDER BY guild_id"
        ) as cur:
            return [
                _ticket_config_row(row)
                for row in await cur.fetchall()
            ]


async def save_ticket_config(
    guild_id: int,
    channel_id: int | None,
    message_id: int | None,
    embed_title: str = "الدعم الفني",
    embed_description: str = "",
    embed_color: int = 0x5865F2,
    footer_text: str = "PR1ME TEAM Support",
) -> dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            INSERT INTO ticket_config
                (guild_id, channel_id, message_id, embed_title,
                 embed_description, embed_color, footer_text, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id,
                embed_title = excluded.embed_title,
                embed_description = excluded.embed_description,
                embed_color = excluded.embed_color,
                footer_text = excluded.footer_text,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(guild_id),
                int(channel_id) if channel_id is not None else None,
                int(message_id) if message_id is not None else None,
                str(embed_title)[:256],
                str(embed_description)[:4000],
                int(embed_color),
                str(footer_text)[:2048],
            ),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM ticket_config WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    return _ticket_config_row(row)


async def update_ticket_control_config(
    guild_id: int,
    *,
    closed_category_id: int | None | object = _UNSET,
    log_channel_id: int | None | object = _UNSET,
    evaluation_channel_id: int | None | object = _UNSET,
    allow_user_close: bool | None | object = _UNSET,
    send_transcript_dm: bool | None | object = _UNSET,
    auto_close_minutes: int | None | object = _UNSET,
    open_limit: int | None | object = _UNSET,
    panel_mode: str | None | object = _UNSET,
    select_placeholder: str | None | object = _UNSET,
    permissions: dict[str, Any] | None | object = _UNSET,
    close_config: dict[str, Any] | None | object = _UNSET,
) -> dict[str, Any]:
    config = await get_ticket_config(guild_id) or {}
    def preserved_snowflake(value: int | None | object, key: str) -> int | None:
        current = config.get(key) if value is _UNSET else value
        return int(current) if current is not None else None

    await save_ticket_config(
        guild_id,
        config.get("channel_id"),
        config.get("message_id"),
        embed_title=config.get("embed_title") or "الدعم الفني",
        embed_description=config.get("embed_description") or "",
        embed_color=int(config.get("embed_color") or 0x5865F2),
        footer_text=config.get("footer_text") or "PR1ME TEAM Support",
    )
    async with connect(aiosqlite.Row) as db:
        current_permissions = config.get("permissions_json") or config.get("permissions") or {}
        current_close_config = config.get("close_config_json") or config.get("close_config") or {}
        await db.execute(
            """
            UPDATE ticket_config
            SET closed_category_id = ?,
                log_channel_id = ?,
                evaluation_channel_id = ?,
                allow_user_close = ?,
                send_transcript_dm = ?,
                auto_close_minutes = ?,
                open_limit = ?,
                panel_mode = ?,
                select_placeholder = ?,
                permissions_json = ?,
                close_config_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ?
            """,
            (
                preserved_snowflake(closed_category_id, "closed_category_id"),
                preserved_snowflake(log_channel_id, "log_channel_id"),
                preserved_snowflake(evaluation_channel_id, "evaluation_channel_id"),
                int(bool(config.get("allow_user_close", False) if allow_user_close is _UNSET or allow_user_close is None else allow_user_close)),
                int(bool(config.get("send_transcript_dm", True) if send_transcript_dm is _UNSET or send_transcript_dm is None else send_transcript_dm)),
                max(0, min(10080, int(config.get("auto_close_minutes", 0) if auto_close_minutes is _UNSET or auto_close_minutes is None else auto_close_minutes))),
                max(1, min(20, int(config.get("open_limit", 1) if open_limit is _UNSET or open_limit is None else open_limit))),
                "buttons" if (config.get("panel_mode", "dropdown") if panel_mode is _UNSET or panel_mode is None else panel_mode) == "buttons" else "dropdown",
                str(config.get("select_placeholder") or "اختر القسم المناسب لطلبك" if select_placeholder is _UNSET or select_placeholder is None else select_placeholder)[:200],
                json.dumps(current_permissions if permissions is _UNSET or permissions is None else permissions, ensure_ascii=False),
                json.dumps(current_close_config if close_config is _UNSET or close_config is None else close_config, ensure_ascii=False),
                int(guild_id),
            ),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM ticket_config WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    return _ticket_config_row(row) or {}


async def get_ticket_options(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, label, description, emoji, role_id,
                   category_id, welcome_msg
            FROM ticket_options
            WHERE guild_id = ?
            ORDER BY id
            """,
            (int(guild_id),),
        ) as cur:
            return [_ticket_option_row(row) for row in await cur.fetchall()]


async def replace_ticket_options(
    guild_id: int,
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            "DELETE FROM ticket_options WHERE guild_id = ?",
            (int(guild_id),),
        )
        for option in options[:25]:
            await db.execute(
                """
                INSERT INTO ticket_options
                    (guild_id, label, description, emoji, role_id,
                     category_id, welcome_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(guild_id),
                    str(option.get("label") or "قسم دعم")[:100],
                    str(option.get("description") or "")[:100],
                    str(option.get("emoji") or "🎫").strip()[:100],
                    int(option["role_id"])
                    if option.get("role_id") not in (None, "")
                    else None,
                    int(option["category_id"])
                    if option.get("category_id") not in (None, "")
                    else None,
                    str(option.get("welcome_msg") or "")[:2000],
                ),
            )
        await db.execute(
            "DELETE FROM ticket_categories WHERE guild_id = ?",
            (int(guild_id),),
        )
        for index, option in enumerate(options[:25]):
            name = str(
                option.get("name")
                or option.get("label")
                or f"قسم دعم {index + 1}"
            ).strip()[:100]
            ping_role_ids = option.get("ping_role_ids")
            if not isinstance(ping_role_ids, list):
                ping_role_ids = option.get("support_role_ids")
            if not isinstance(ping_role_ids, list):
                ping_role_ids = (
                    [option.get("role_id")]
                    if option.get("role_id") not in (None, "")
                    else []
                )
            staff_role_ids = option.get("staff_role_ids")
            if not isinstance(staff_role_ids, list):
                staff_role_ids = option.get("support_role_ids", [])
            senior_role_ids = option.get("senior_role_ids", [])
            await db.execute(
                """
                INSERT INTO ticket_categories
                    (guild_id, name, ping_role_ids, staff_role_ids, description,
                     emoji, category_id, welcome_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, name) DO UPDATE SET
                    ping_role_ids = excluded.ping_role_ids,
                    staff_role_ids = excluded.staff_role_ids,
                    description = excluded.description,
                    emoji = excluded.emoji,
                    category_id = excluded.category_id,
                    welcome_msg = excluded.welcome_msg
                """,
                (
                    int(guild_id),
                    name or f"قسم دعم {index + 1}",
                    json.dumps([str(item) for item in ping_role_ids if str(item).isdigit()]),
                    json.dumps([str(item) for item in staff_role_ids if str(item).isdigit()]),
                    str(option.get("description") or "")[:100],
                    str(option.get("emoji") or "🎫")[:100],
                    int(option["category_id"])
                    if option.get("category_id") not in (None, "")
                    else None,
                    str(option.get("welcome_msg") or "")[:2000],
                ),
            )
        await db.commit()
    return await get_ticket_options(guild_id)


def _ticket_category_row(row) -> dict[str, Any]:
    item = dict(row)
    for key in (
        "id",
        "guild_id",
        "panel_id",
        "category_id",
        "open_category_id",
        "closed_category_id",
        "max_open_per_user",
        "auto_close_hours",
    ):
        if item.get(key) is not None:
            item[key] = int(item[key])
    for key in ("ping_role_ids", "staff_role_ids"):
        item[key] = _ticket_json_ids(item.get(key))
    item["label"] = str(item.get("label") or item.get("name") or "قسم دعم")
    item["name"] = str(item.get("name") or item["label"])
    item["button_color"] = str(item.get("button_color") or "primary")
    item["naming_format"] = str(item.get("naming_format") or "ticket-{count}")
    item["closed_naming_format"] = str(
        item.get("closed_naming_format") or "closed-{count}"
    )
    item["welcome_message"] = str(
        item.get("welcome_message") or item.get("welcome_msg") or ""
    )
    item["welcome_msg"] = str(item.get("welcome_msg") or item["welcome_message"])
    item["max_open_per_user"] = max(1, int(item.get("max_open_per_user") or 1))
    item["auto_close_hours"] = max(0, int(item.get("auto_close_hours") or 0))
    return item


async def get_ticket_categories(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM ticket_categories WHERE guild_id = ? ORDER BY id",
            (int(guild_id),),
        ) as cur:
            rows = [_ticket_category_row(row) for row in await cur.fetchall()]
    if rows:
        return rows
    # A read-only compatibility fallback for installations upgraded before
    # ticket_categories existed; the next panel save will backfill the table.
    return [
        {
            "id": option["id"],
            "guild_id": option["guild_id"],
            "name": option["label"],
            "ping_role_ids": [str(option["role_id"])] if option.get("role_id") else [],
            "staff_role_ids": [str(option["role_id"])] if option.get("role_id") else [],
            "description": option.get("description", ""),
            "emoji": option.get("emoji", "🎫"),
            "category_id": option.get("category_id"),
            "welcome_msg": option.get("welcome_msg", ""),
        }
        for option in await get_ticket_options(guild_id)
    ]


def _ticket_category_payload(data: dict[str, Any]) -> dict[str, Any]:
    label = str(data.get("label") or data.get("name") or "").strip()[:100]
    if not label:
        raise ValueError("category label is required")
    def ids(key: str) -> list[str]:
        value = data.get(key, [])
        if not isinstance(value, list):
            value = []
        return [str(item) for item in value if str(item).isdigit()][:50]
    return {
        "name": str(data.get("name") or label).strip()[:100] or label,
        "label": label,
        "panel_id": int(data["panel_id"]) if data.get("panel_id") not in (None, "") else None,
        "description": str(data.get("description") or "").strip()[:100],
        "emoji": str(data.get("emoji") or "🎫").strip()[:100],
        "button_color": str(data.get("button_color") or "primary").strip().lower()[:20],
        "naming_format": str(data.get("naming_format") or "ticket-{count}").strip()[:100],
        "closed_naming_format": str(
            data.get("closed_naming_format") or "closed-{count}"
        ).strip()[:100],
        "open_category_id": (
            int(data["open_category_id"])
            if data.get("open_category_id") not in (None, "")
            else None
        ),
        "closed_category_id": (
            int(data["closed_category_id"])
            if data.get("closed_category_id") not in (None, "")
            else None
        ),
        "category_id": (
            int(data["category_id"])
            if data.get("category_id") not in (None, "")
            else None
        ),
        "ping_role_ids": ids("ping_role_ids"),
        "staff_role_ids": ids("staff_role_ids"),
        "welcome_message": str(
            data.get("welcome_message", data.get("welcome_msg", ""))
        )[:2000],
        "max_open_per_user": max(1, min(20, int(data.get("max_open_per_user") or 1))),
        "auto_close_hours": max(0, min(8760, int(data.get("auto_close_hours") or 0))),
    }


async def save_ticket_category(
    guild_id: int,
    data: dict[str, Any],
    category_id: int | None = None,
) -> dict[str, Any]:
    payload = _ticket_category_payload(data)
    async with connect(aiosqlite.Row) as db:
        if category_id is None:
            cursor = await db.execute(
                """
                INSERT INTO ticket_categories
                    (guild_id, panel_id, name, label, ping_role_ids,
                     staff_role_ids, description, emoji, button_color,
                     naming_format, closed_naming_format, open_category_id,
                     closed_category_id, category_id, welcome_msg,
                     welcome_message, max_open_per_user, auto_close_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING *
                """,
                (
                    int(guild_id),
                    payload["panel_id"],
                    payload["name"],
                    payload["label"],
                    json.dumps(payload["ping_role_ids"]),
                    json.dumps(payload["staff_role_ids"]),
                    payload["description"],
                    payload["emoji"],
                    payload["button_color"],
                    payload["naming_format"],
                    payload["closed_naming_format"],
                    payload["open_category_id"],
                    payload["closed_category_id"],
                    payload["category_id"],
                    payload["welcome_message"],
                    payload["welcome_message"],
                    payload["max_open_per_user"],
                    payload["auto_close_hours"],
                ),
            )
        else:
            cursor = await db.execute(
                """
                UPDATE ticket_categories
                SET panel_id = ?, name = ?, label = ?, ping_role_ids = ?,
                    staff_role_ids = ?, description = ?, emoji = ?,
                    button_color = ?, naming_format = ?,
                    closed_naming_format = ?, open_category_id = ?,
                    closed_category_id = ?, category_id = ?, welcome_msg = ?,
                    welcome_message = ?, max_open_per_user = ?,
                    auto_close_hours = ?
                WHERE guild_id = ? AND id = ?
                RETURNING *
                """,
                (
                    payload["panel_id"],
                    payload["name"],
                    payload["label"],
                    json.dumps(payload["ping_role_ids"]),
                    json.dumps(payload["staff_role_ids"]),
                    payload["description"],
                    payload["emoji"],
                    payload["button_color"],
                    payload["naming_format"],
                    payload["closed_naming_format"],
                    payload["open_category_id"],
                    payload["closed_category_id"],
                    payload["category_id"],
                    payload["welcome_message"],
                    payload["welcome_message"],
                    payload["max_open_per_user"],
                    payload["auto_close_hours"],
                    int(guild_id),
                    int(category_id),
                ),
            )
        row = await cursor.fetchone()
        if not row:
            await db.rollback()
            raise LookupError("ticket category not found")
        await db.commit()
    return _ticket_category_row(row)


async def delete_ticket_category(
    guild_id: int, category_id: int
) -> dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT name, label FROM ticket_categories WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(category_id)),
        ) as cur:
            category = await cur.fetchone()
        if not category:
            return {"deleted": False, "found": False, "in_use": False}
        async with db.execute(
            """
            SELECT COUNT(*) AS count
            FROM tickets
            WHERE guild_id = ? AND status != 'closed'
              AND (category_key = ? OR category_label = ?)
            """,
            (int(guild_id), str(category["name"]), str(category["label"])),
        ) as cur:
            active_count = int((await cur.fetchone())["count"] or 0)
        if active_count:
            await db.rollback()
            return {
                "deleted": False,
                "found": True,
                "in_use": True,
                "active_tickets": active_count,
            }
        cursor = await db.execute(
            "DELETE FROM ticket_categories WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(category_id)),
        )
        await db.commit()
    return {"deleted": cursor.rowcount > 0, "found": True, "in_use": False}


async def save_ticket_log(
    ticket_id: int,
    guild_id: int,
    action: str,
    *,
    staff_id: int | None = None,
    target_user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT channel_id, status, claimed_by, closed_by
            FROM tickets
            WHERE guild_id = ? AND id = ?
            """,
            (int(guild_id), int(ticket_id)),
        ) as ticket_cur:
            ticket = await ticket_cur.fetchone()
        cursor = await db.execute(
            """
            INSERT INTO ticket_logs
                (ticket_id, guild_id, channel_id, action, status, claimed_by,
                 closed_by, staff_id, target_user_id, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                int(ticket_id),
                int(guild_id),
                int(ticket["channel_id"]) if ticket and ticket["channel_id"] is not None else None,
                str(action)[:80],
                str(ticket["status"]) if ticket and ticket["status"] is not None else None,
                int(ticket["claimed_by"]) if ticket and ticket["claimed_by"] is not None else None,
                int(ticket["closed_by"]) if ticket and ticket["closed_by"] is not None else None,
                int(staff_id) if staff_id is not None else None,
                int(target_user_id) if target_user_id is not None else None,
                json.dumps(metadata or {}, ensure_ascii=False)[:4000],
            ),
        )
        row = await cursor.fetchone()
        await db.commit()
    return dict(row) if row else {}


def _ticket_blacklist_row(row) -> dict[str, Any]:
    item = dict(row)
    for key in ("id", "guild_id", "user_id", "created_by"):
        if item.get(key) is not None:
            item[key] = int(item[key])
    if item.get("expiration") is None:
        item["expiration"] = item.get("expires_at")
    if item.get("expires_at") is None:
        item["expires_at"] = item.get("expiration")
    return item


async def get_ticket_blacklist(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM ticket_blacklist
            WHERE guild_id = ?
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            ORDER BY created_at DESC
            """,
            (int(guild_id),),
        ) as cur:
            return [_ticket_blacklist_row(row) for row in await cur.fetchall()]


async def is_ticket_user_blacklisted(guild_id: int, user_id: int) -> bool:
    async with connect() as db:
        async with db.execute(
            """
            SELECT 1 FROM ticket_blacklist
            WHERE guild_id = ? AND user_id = ?
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            LIMIT 1
            """,
            (int(guild_id), int(user_id)),
        ) as cur:
            return await cur.fetchone() is not None


async def save_ticket_blacklist(
    guild_id: int,
    user_id: int,
    *,
    reason: str = "",
    duration_days: int | None = None,
    expiration: str | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    expires_at = None
    if duration_days is not None and int(duration_days) > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(days=min(int(duration_days), 3650))
        expires_at = expires_at.strftime("%Y-%m-%d %H:%M:%S")
    elif expiration:
        expires_at = str(expiration).strip()[:40]
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            INSERT INTO ticket_blacklist
                (guild_id, user_id, expires_at, expiration, reason, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                expires_at = excluded.expires_at,
                expiration = excluded.expiration,
                reason = excluded.reason,
                created_by = excluded.created_by,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                int(guild_id),
                int(user_id),
                expires_at,
                expires_at,
                str(reason or "").strip()[:500],
                int(created_by) if created_by is not None else None,
            ),
        )
        await db.commit()
        async with db.execute(
            """
            SELECT * FROM ticket_blacklist
            WHERE guild_id = ? AND user_id = ?
            """,
            (int(guild_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
    return _ticket_blacklist_row(row)


async def delete_ticket_blacklist(guild_id: int, user_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        )
        await db.commit()
    return cursor.rowcount > 0


async def get_ticket_dashboard_analytics(guild_id: int) -> dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status != 'closed' THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
                AVG(CASE WHEN first_response_at IS NOT NULL
                    THEN (julianday(first_response_at) - julianday(opened_at)) * 86400 END) AS avg_response
            FROM tickets WHERE guild_id = ?
            """,
            (int(guild_id),),
        ) as cur:
            overview = dict(await cur.fetchone())
        async with db.execute(
            """
            SELECT COALESCE(priority, 'normal') AS priority, COUNT(*) AS count
            FROM tickets
            WHERE guild_id = ? AND status != 'closed'
            GROUP BY COALESCE(priority, 'normal')
            """,
            (int(guild_id),),
        ) as cur:
            priorities = [dict(row) for row in await cur.fetchall()]
        async with db.execute(
            """
            SELECT stars AS rating, COUNT(*) AS count
            FROM ticket_ratings
            WHERE guild_id = ?
            GROUP BY rating ORDER BY rating
            """,
            (int(guild_id),),
        ) as cur:
            ratings = [dict(row) for row in await cur.fetchall()]
        async with db.execute(
            """
            SELECT
                COALESCE(t.closed_by, t.claimed_by) AS staff_id,
                COUNT(*) AS resolved,
            AVG(r.stars) AS avg_rating
            FROM tickets t
            LEFT JOIN ticket_ratings r ON r.ticket_id = t.id
            WHERE t.guild_id = ?
              AND t.status = 'closed'
              AND COALESCE(t.closed_by, t.claimed_by) IS NOT NULL
            GROUP BY COALESCE(t.closed_by, t.claimed_by)
            ORDER BY resolved DESC, avg_rating DESC
            LIMIT 8
            """,
            (int(guild_id),),
        ) as cur:
            staff = [dict(row) for row in await cur.fetchall()]
        async with db.execute(
            """
            SELECT
                t.id, t.subject, t.category_label, t.status, t.priority,
                t.user_id, t.claimed_by, t.opened_at, t.closed_at,
                r.stars AS rating
            FROM tickets t
            LEFT JOIN ticket_ratings r ON r.ticket_id = t.id
            WHERE t.guild_id = ?
            ORDER BY COALESCE(t.closed_at, t.opened_at) DESC, t.id DESC
            LIMIT 12
            """,
            (int(guild_id),),
        ) as cur:
            activity = [dict(row) for row in await cur.fetchall()]
    overview["total"] = int(overview.get("total") or 0)
    overview["active"] = int(overview.get("active") or 0)
    overview["closed"] = int(overview.get("closed") or 0)
    overview["avg_response"] = float(overview["avg_response"]) if overview.get("avg_response") is not None else None
    for item in priorities + ratings + staff:
        for key in ("count", "resolved", "staff_id", "rating"):
            if item.get(key) is not None and key != "rating":
                item[key] = int(item[key])
    for item in ratings + staff + activity:
        if item.get("rating") is not None:
            item["rating"] = float(item["rating"])
    return {
        "overview": overview,
        "priorities": priorities,
        "ratings": ratings,
        "staff": staff,
        "activity": activity,
    }


async def get_ticket_overview_metrics(guild_id: int) -> dict[str, Any]:
    """Return the CRM overview contract without changing the legacy analytics shape."""
    analytics = await get_ticket_dashboard_analytics(guild_id)
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT COUNT(*) AS count FROM ticket_panels WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            panels = int((await cur.fetchone())["count"] or 0)
        async with db.execute(
            """
            SELECT COUNT(*) AS count
            FROM ticket_blacklist
            WHERE guild_id = ?
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            """,
            (int(guild_id),),
        ) as cur:
            blacklist = int((await cur.fetchone())["count"] or 0)
        async with db.execute(
            """
            SELECT AVG(stars) AS average
            FROM ticket_ratings
            WHERE guild_id = ?
            """,
            (int(guild_id),),
        ) as cur:
            average_row = await cur.fetchone()
        async with db.execute(
            """
            SELECT id, ticket_id, action, staff_id, target_user_id,
                   metadata, created_at
            FROM ticket_logs
            WHERE guild_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 10
            """,
            (int(guild_id),),
        ) as cur:
            activity_logs = []
            for row in await cur.fetchall():
                item = dict(row)
                try:
                    item["metadata"] = json.loads(item.get("metadata") or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    item["metadata"] = {}
                activity_logs.append(item)
        async with db.execute(
            """
            SELECT stars AS rating, COUNT(*) AS count
            FROM ticket_ratings
            WHERE guild_id = ?
            GROUP BY stars
            """,
            (int(guild_id),),
        ) as cur:
            rating_rows = {int(row["rating"]): int(row["count"]) for row in await cur.fetchall()}
    rating_distribution = [
        {"rating": stars, "count": rating_rows.get(stars, 0)}
        for stars in range(1, 6)
    ]
    overview = {
        **analytics.get("overview", {}),
        "panels": panels,
        "blacklist": blacklist,
        "average_rating": (
            round(float(average_row["average"]), 2)
            if average_row and average_row["average"] is not None
            else None
        ),
    }
    return {
        "overview": overview,
        "panels": panels,
        "total_panels": panels,
        "active_tickets": int(overview.get("active") or 0),
        "total_tickets": int(overview.get("total") or 0),
        "average_rating": overview["average_rating"],
        "priority_distribution": analytics.get("priorities", []),
        "priorities_donut": analytics.get("priorities", []),
        "rating_distribution": rating_distribution,
        "ratings_bar": rating_distribution,
        "staff_leaderboard": analytics.get("staff", []),
        "last_activity_logs": activity_logs,
        # Keep the existing dashboard consumer compatible during rollout.
        **analytics,
    }


async def get_ticket_ratings(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT r.*, t.subject, t.category_label, t.closed_at
            FROM ticket_ratings r
            LEFT JOIN tickets t ON t.id = r.ticket_id
            WHERE r.guild_id = ?
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT 100
            """,
            (int(guild_id),),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for item in rows:
        for key in ("id", "ticket_id", "guild_id", "staff_id", "user_id", "stars"):
            if item.get(key) is not None:
                item[key] = int(item[key])
    return rows


async def get_active_ticket_for_user_category(
    guild_id: int,
    user_id: int,
    category_key: str,
) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM tickets
            WHERE guild_id = ? AND user_id = ? AND category_key = ?
              AND status != 'closed'
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(guild_id), int(user_id), str(category_key)[:80]),
        ) as cur:
            row = await cur.fetchone()
    return _ticket_row(dict(row)) if row else None


async def create_ticket(
    guild_id: int,
    channel_id: int,
    user_id: int,
    category_key: str,
    category_label: str,
    subject: str,
    details: str,
    support_role_ids: list[int | str] | None = None,
    senior_role_ids: list[int | str] | None = None,
    intake_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    support = [str(item) for item in (support_role_ids or [])]
    senior = [str(item) for item in (senior_role_ids or [])]
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            INSERT INTO tickets
                (guild_id, channel_id, user_id, category_key, category_label,
                 subject, details, support_role_ids, senior_role_ids, intake_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                int(guild_id), int(channel_id), int(user_id),
                str(category_key)[:80], str(category_label)[:120],
                str(subject)[:200], str(details)[:4000],
                json.dumps(support), json.dumps(senior),
                json.dumps(intake_data or {}, ensure_ascii=False),
            ),
        )
        row = await cursor.fetchone()
        await db.commit()
    return _ticket_row(dict(row))


def _ticket_row(item: dict[str, Any]) -> dict[str, Any]:
    for key in ("support_role_ids", "senior_role_ids"):
        item[key] = _ticket_json_ids(item.get(key))
    raw_intake = item.get("intake_data")
    if isinstance(raw_intake, str):
        try:
            raw_intake = json.loads(raw_intake)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_intake = {}
    item["intake_data"] = raw_intake if isinstance(raw_intake, dict) else {}
    for key in ("guild_id", "channel_id", "user_id", "id", "claimed_by", "closed_by"):
        if item.get(key) is not None:
            item[key] = int(item[key])
    return item


async def get_ticket_by_channel(channel_id: int) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM tickets WHERE channel_id = ?",
            (int(channel_id),),
        ) as cur:
            row = await cur.fetchone()
    return _ticket_row(dict(row)) if row else None


async def get_ticket(guild_id: int, ticket_id: int) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(ticket_id)),
        ) as cur:
            row = await cur.fetchone()
    return _ticket_row(dict(row)) if row else None


async def get_active_tickets(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM tickets
            WHERE guild_id = ? AND status != 'closed'
            ORDER BY
                CASE priority WHEN 'management' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                CASE status WHEN 'waiting_staff' THEN 0 ELSE 1 END,
                opened_at ASC, id ASC
            """,
            (int(guild_id),),
        ) as cur:
            return [_ticket_row(dict(row)) for row in await cur.fetchall()]


async def claim_ticket(
    guild_id: int,
    ticket_id: int,
    staff_id: int,
    *,
    expected_claimed_by: int | None = None,
) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        if expected_claimed_by is None:
            cursor = await db.execute(
                """
                UPDATE tickets SET claimed_by = ?, status = 'active', waiting_since = NULL
                WHERE guild_id = ? AND id = ? AND status != 'closed'
                  AND claimed_by IS NULL
                """,
                (int(staff_id), int(guild_id), int(ticket_id)),
            )
        else:
            cursor = await db.execute(
                """
                UPDATE tickets SET claimed_by = ?, status = 'active', waiting_since = NULL
                WHERE guild_id = ? AND id = ? AND status != 'closed'
                  AND (claimed_by IS NULL OR claimed_by = ?)
                """,
                (
                    int(staff_id),
                    int(guild_id),
                    int(ticket_id),
                    int(expected_claimed_by),
                ),
            )
        if cursor.rowcount <= 0:
            await db.rollback()
            return None
        async with db.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(ticket_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return _ticket_row(dict(row)) if row else None


async def unclaim_ticket(
    guild_id: int, ticket_id: int, staff_id: int
) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET claimed_by = NULL,
                status = 'waiting_staff',
                waiting_since = COALESCE(waiting_since, CURRENT_TIMESTAMP)
            WHERE guild_id = ? AND id = ? AND claimed_by = ? AND status != 'closed'
            """,
            (int(guild_id), int(ticket_id), int(staff_id)),
        )
        if cursor.rowcount <= 0:
            await db.rollback()
            return None
        async with db.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(ticket_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return _ticket_row(dict(row)) if row else None


async def escalate_ticket(
    guild_id: int,
    ticket_id: int,
    priority: str,
) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            UPDATE tickets SET priority = ?, escalated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ? AND id = ? AND status != 'closed'
            """,
            (str(priority), int(guild_id), int(ticket_id)),
        )
        if cursor.rowcount <= 0:
            await db.rollback()
            return None
        async with db.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(ticket_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return _ticket_row(dict(row)) if row else None


async def record_ticket_response(guild_id: int, ticket_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET first_response_at = COALESCE(first_response_at, CURRENT_TIMESTAMP),
                status = 'active', waiting_since = NULL
            WHERE guild_id = ? AND id = ? AND status != 'closed'
            """,
            (int(guild_id), int(ticket_id)),
        )
        await db.commit()
    return cursor.rowcount > 0


async def record_ticket_user_message(guild_id: int, ticket_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET status = 'waiting_staff',
                waiting_since = COALESCE(waiting_since, CURRENT_TIMESTAMP),
                last_user_message_at = CURRENT_TIMESTAMP
            WHERE guild_id = ? AND id = ? AND status != 'closed'
            """,
            (int(guild_id), int(ticket_id)),
        )
        await db.commit()
    return cursor.rowcount > 0


async def set_ticket_status(
    guild_id: int,
    ticket_id: int,
    status: str,
    *,
    staff_id: int | None = None,
) -> dict[str, Any] | None:
    allowed = {"active", "waiting_user", "waiting_staff"}
    if status not in allowed:
        raise ValueError("invalid ticket status")
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET status = ?,
                waiting_since = CASE WHEN ? = 'active' THEN NULL ELSE CURRENT_TIMESTAMP END,
                claimed_by = COALESCE(?, claimed_by)
            WHERE guild_id = ? AND id = ? AND status != 'closed'
            """,
            (
                status,
                status,
                int(staff_id) if staff_id is not None else None,
                int(guild_id),
                int(ticket_id),
            ),
        )
        if cursor.rowcount <= 0:
            await db.rollback()
            return None
        async with db.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(ticket_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return _ticket_row(dict(row)) if row else None


async def set_ticket_priority(
    guild_id: int, ticket_id: int, priority: str
) -> dict[str, Any] | None:
    allowed = {"normal", "high", "management"}
    if priority not in allowed:
        raise ValueError("invalid ticket priority")
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            UPDATE tickets SET priority = ?,
                escalated_at = CASE WHEN ? = 'management' THEN CURRENT_TIMESTAMP ELSE escalated_at END
            WHERE guild_id = ? AND id = ? AND status != 'closed'
            """,
            (priority, priority, int(guild_id), int(ticket_id)),
        )
        if cursor.rowcount <= 0:
            await db.rollback()
            return None
        async with db.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(ticket_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return _ticket_row(dict(row)) if row else None


async def reopen_ticket(
    guild_id: int, ticket_id: int, staff_id: int
) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET status = 'active', closed_at = NULL, closed_by = NULL,
                close_reason = '', waiting_since = NULL, claimed_by = ?
            WHERE guild_id = ? AND id = ? AND status = 'closed'
            """,
            (int(staff_id), int(guild_id), int(ticket_id)),
        )
        if cursor.rowcount <= 0:
            await db.rollback()
            return None
        async with db.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(ticket_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return _ticket_row(dict(row)) if row else None


async def add_ticket_note(
    guild_id: int,
    ticket_id: int,
    staff_id: int,
    content: str,
) -> dict[str, Any] | None:
    text = str(content).strip()[:2000]
    if not text:
        return None
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            INSERT INTO ticket_notes (ticket_id, guild_id, staff_id, content)
            SELECT ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM tickets
                WHERE id = ? AND guild_id = ?
            )
            RETURNING *
            """,
            (int(ticket_id), int(guild_id), int(staff_id), text, int(ticket_id), int(guild_id)),
        )
        row = await cursor.fetchone()
        await db.commit()
    return dict(row) if row else None


async def get_ticket_notes(guild_id: int, ticket_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM ticket_notes
            WHERE guild_id = ? AND ticket_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 100
            """,
            (int(guild_id), int(ticket_id)),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def close_ticket(
    guild_id: int,
    ticket_id: int,
    staff_id: int,
    reason: str,
) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET status = 'closed', closed_at = CURRENT_TIMESTAMP,
                closed_by = ?, close_reason = ?
            WHERE guild_id = ? AND id = ? AND status != 'closed'
            """,
            (int(staff_id), str(reason)[:1000], int(guild_id), int(ticket_id)),
        )
        if cursor.rowcount <= 0:
            await db.rollback()
            return None
        async with db.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(ticket_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return _ticket_row(dict(row)) if row else None


async def save_ticket_transcript(
    ticket_id: int,
    guild_id: int,
    channel_id: int,
    content_text: str,
    content_html: str,
) -> dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            INSERT INTO ticket_transcripts
                (ticket_id, guild_id, channel_id, content_text, content_html)
            VALUES (?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                int(ticket_id), int(guild_id), int(channel_id),
                str(content_text), str(content_html),
            ),
        )
        row = await cursor.fetchone()
        await db.commit()
    return dict(row)


async def get_ticket_transcripts(
    guild_id: int,
    query: str = "",
) -> list[dict[str, Any]]:
    pattern = f"%{str(query).strip()}%"
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT tt.*, t.user_id, t.category_label, t.subject, t.closed_by
            FROM ticket_transcripts tt
            JOIN tickets t ON t.id = tt.ticket_id
            WHERE tt.guild_id = ?
              AND (
                ? = '' OR t.subject LIKE ? OR t.category_label LIKE ?
                OR tt.content_text LIKE ?
              )
            ORDER BY tt.created_at DESC, tt.id DESC
            LIMIT 100
            """,
            (int(guild_id), str(query).strip(), pattern, pattern, pattern),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_ticket_archive(
    guild_id: int,
    query: str = "",
) -> list[dict[str, Any]]:
    value = str(query).strip()
    pattern = f"%{value}%"
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT t.*, tt.id AS transcript_id, tt.created_at AS transcript_created_at
            FROM tickets t
            LEFT JOIN ticket_transcripts tt ON tt.ticket_id = t.id
            WHERE t.guild_id = ? AND t.status = 'closed'
              AND (
                ? = '' OR t.subject LIKE ? OR t.category_label LIKE ?
                OR t.close_reason LIKE ?
              )
            ORDER BY t.closed_at DESC, t.id DESC
            LIMIT 200
            """,
            (int(guild_id), value, pattern, pattern, pattern),
        ) as cur:
            return [_ticket_row(dict(row)) for row in await cur.fetchall()]


async def get_ticket_transcript(
    guild_id: int,
    ticket_id: int,
) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT tt.*, t.user_id, t.category_label, t.subject, t.closed_by,
                   t.close_reason
            FROM ticket_transcripts tt
            JOIN tickets t ON t.id = tt.ticket_id
            WHERE tt.guild_id = ? AND tt.ticket_id = ?
            ORDER BY tt.id DESC
            LIMIT 1
            """,
            (int(guild_id), int(ticket_id)),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def save_ticket_rating(
    ticket_id: int,
    guild_id: int,
    user_id: int,
    stars: int,
    feedback: str = "",
) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT closed_by, status FROM tickets WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(ticket_id)),
        ) as cur:
            ticket = await cur.fetchone()
        if not ticket or ticket["status"] != "closed":
            await db.rollback()
            return None
        staff_id = ticket["closed_by"] if ticket else None
        cursor = await db.execute(
            """
            INSERT INTO ticket_ratings
                (ticket_id, guild_id, staff_id, user_id, stars, feedback)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket_id) DO UPDATE SET
                stars = excluded.stars,
                feedback = excluded.feedback,
                staff_id = excluded.staff_id
            RETURNING *
            """,
            (
                int(ticket_id), int(guild_id),
                int(staff_id) if staff_id is not None else None,
                int(user_id), max(1, min(5, int(stars))), str(feedback)[:1000],
            ),
        )
        row = await cursor.fetchone()
        await db.execute(
            "UPDATE tickets SET rating = ? WHERE guild_id = ? AND id = ?",
            (max(1, min(5, int(stars))), int(guild_id), int(ticket_id)),
        )
        await db.commit()
    return dict(row)


async def get_staff_kpis(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT
                COALESCE(t.claimed_by, t.closed_by) AS staff_id,
                COUNT(t.id) AS tickets_handled,
                ROUND(AVG(
                    CASE WHEN t.first_response_at IS NOT NULL
                    THEN (julianday(t.first_response_at) - julianday(t.opened_at)) * 86400
                    END
                ), 1) AS avg_response_seconds,
                ROUND(AVG(
                    CASE WHEN t.closed_at IS NOT NULL
                    THEN (julianday(t.closed_at) - julianday(t.opened_at)) * 86400
                    END
                ), 1) AS avg_resolution_seconds,
                COUNT(r.id) AS ratings_count,
                ROUND(AVG(r.stars), 2) AS avg_rating
            FROM tickets t
            LEFT JOIN ticket_ratings r ON r.ticket_id = t.id
            WHERE t.guild_id = ? AND COALESCE(t.claimed_by, t.closed_by) IS NOT NULL
            GROUP BY COALESCE(t.claimed_by, t.closed_by)
            ORDER BY tickets_handled DESC, avg_rating DESC
            """,
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_canned_responses(guild_id: int) -> list[dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, title, content, category, shortcut, sticker_id,
                   created_by, updated_at
            FROM canned_responses
            WHERE guild_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def save_canned_response(
    guild_id: int,
    title: str,
    content: str,
    category: str = "عام",
    created_by: int | str | None = None,
    response_id: int | None = None,
    shortcut: str | None = None,
    sticker_id: int | str | None = None,
) -> dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        if response_id is not None:
            cursor = await db.execute(
                """
                UPDATE canned_responses
                SET title = ?, content = ?, category = ?, shortcut = ?, sticker_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE guild_id = ? AND id = ?
                """,
                (
                    str(title)[:120], str(content)[:2000], str(category)[:80],
                    str(shortcut).strip()[:80] if shortcut else None,
                    int(sticker_id) if sticker_id not in (None, "") else None,
                    int(guild_id), int(response_id),
                ),
            )
        else:
            cursor = await db.execute(
                """
                INSERT INTO canned_responses
                    (guild_id, title, content, category, shortcut, sticker_id, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, title) DO UPDATE SET
                    content = excluded.content,
                    category = excluded.category,
                    shortcut = excluded.shortcut,
                    sticker_id = excluded.sticker_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(guild_id), str(title)[:120], str(content)[:2000],
                    str(category)[:80],
                    str(shortcut).strip()[:80] if shortcut else None,
                    int(sticker_id) if sticker_id not in (None, "") else None,
                    int(created_by) if created_by is not None else None,
                ),
            )
        if response_id is not None and cursor.rowcount == 0:
            await db.commit()
            return {}
        async with db.execute(
            """
            SELECT id, guild_id, title, content, category, shortcut, sticker_id,
                   created_by, updated_at
            FROM canned_responses
            WHERE guild_id = ? AND title = ?
            """,
            (int(guild_id), str(title)[:120]),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return dict(row) if row else {}


async def delete_canned_response(guild_id: int, response_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM canned_responses WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(response_id)),
        )
        deleted = cursor.rowcount > 0
        await db.commit()
    return deleted


async def get_recent_warnings(guild_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """جلب أحدث مخالفات السيرفر بصيغة مناسبة للـ API."""
    limit = max(1, min(int(limit), 100))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT id, user_id, guild_id, moderator_id, reason, timestamp "
            "FROM warnings WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (int(guild_id), limit),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_dashboard_stats(
    guild_id: int,
    *,
    member_count: int | None = None,
    latency_series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the shared dashboard snapshot used by REST and live charts."""
    guild_id = int(guild_id)
    now = time.monotonic()
    cached = _stats_cache.get(guild_id)
    if cached and now - cached[0] < 5:
        snapshot = dict(cached[1])
        snapshot["series"] = list(latency_series or snapshot.get("series", []))
        if member_count is not None:
            snapshot["guild"]["members"] = int(member_count)
        return snapshot

    async with connect(aiosqlite.Row) as db:
        queries = {
            "tickets_active": (
                "SELECT COUNT(*) AS value FROM tickets "
                "WHERE guild_id = ? AND status NOT IN ('closed', 'archived')"
            ),
            "tickets_archive": (
                "SELECT COUNT(*) AS value FROM tickets "
                "WHERE guild_id = ? AND status IN ('closed', 'archived')"
            ),
            "infractions": "SELECT COUNT(*) AS value FROM warnings WHERE guild_id = ?",
            "auto_responses": (
                "SELECT COUNT(*) AS value FROM guild_auto_responders "
                "WHERE guild_id = ? AND enabled = 1"
            ),
            "commands_enabled": (
                "SELECT COUNT(*) AS value FROM guild_command_controls "
                "WHERE guild_id = ? AND enabled = 1"
            ),
            "economy_accounts": (
                "SELECT COUNT(*) AS value FROM users WHERE guild_id = ?"
            ),
        }
        counts: dict[str, int] = {}
        for key, query in queries.items():
            async with db.execute(query, (guild_id,)) as cursor:
                row = await cursor.fetchone()
            counts[key] = int(row["value"]) if row else 0

    snapshot = {
        "guild": {"id": str(guild_id), "members": member_count},
        "counts": counts,
        "series": list(latency_series or []),
        "updated_at": _utc_now(),
    }
    _stats_cache[guild_id] = (now, snapshot)
    while len(_stats_cache) > 256:
        _stats_cache.popitem(last=False)
    return snapshot


ANALYTICS_RANGE_DAYS = {
    "today": 1,
    "7d": 7,
    "30d": 30,
    "3m": 90,
    "90d": 90,
    "year": 365,
}


def _analytics_window(timeframe: str) -> tuple[str, datetime, datetime, datetime]:
    key = str(timeframe or "7d").strip().lower()
    if key not in ANALYTICS_RANGE_DAYS:
        key = "7d"
    end = datetime.now(timezone.utc)
    if key == "today":
        start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = end - timedelta(days=ANALYTICS_RANGE_DAYS[key])
    previous_start = start - (end - start)
    return key, start, end, previous_start


def _analytics_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def record_analytics_events(events: list[dict[str, Any]]) -> int:
    """Persist a small batch without blocking Discord's event listener."""
    if not events:
        return 0
    rows = []
    for event in events:
        try:
            guild_id = int(event["guild_id"])
            channel_id = int(event["channel_id"]) if event.get("channel_id") else None
            user_id = int(event["user_id"]) if event.get("user_id") else None
        except (KeyError, TypeError, ValueError):
            continue
        timestamp = event.get("timestamp")
        if isinstance(timestamp, (int, float)):
            timestamp = _analytics_iso(datetime.fromtimestamp(timestamp, timezone.utc))
        rows.append((
            guild_id,
            channel_id,
            user_id,
            1 if event.get("is_voice") else 0,
            timestamp or _utc_now(),
        ))
    if not rows:
        return 0
    async with connect() as db:
        await db.executemany(
            """
            INSERT INTO analytics_messages
                (guild_id, channel_id, user_id, is_voice, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        await db.commit()
    return len(rows)


async def record_analytics_message(
    guild_id: int,
    channel_id: int | None,
    user_id: int | None,
    *,
    timestamp: float | str | None = None,
) -> int:
    return await record_analytics_events([{
        "guild_id": guild_id,
        "channel_id": channel_id,
        "user_id": user_id,
        "timestamp": timestamp,
    }])


async def record_analytics_voice_session(
    guild_id: int,
    channel_id: int | None,
    user_id: int | None,
    started_at: float | str,
    ended_at: float | str,
    duration_seconds: int,
) -> None:
    def normalize(value: float | str) -> str:
        if isinstance(value, (int, float)):
            return _analytics_iso(datetime.fromtimestamp(value, timezone.utc))
        return str(value)

    started = normalize(started_at)
    ended = normalize(ended_at)
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO analytics_voice_sessions
                (guild_id, channel_id, user_id, started_at, ended_at, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(guild_id),
                int(channel_id) if channel_id else None,
                int(user_id) if user_id else None,
                started,
                ended,
                max(0, int(duration_seconds)),
            ),
        )
        await db.commit()


async def get_analytics_summary(guild_id: int, timeframe: str = "7d") -> dict[str, Any]:
    _, start, end, previous_start = _analytics_window(timeframe)
    current_start, current_end = _analytics_iso(start), _analytics_iso(end)
    previous_start_text = _analytics_iso(previous_start)
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT COUNT(*) AS total_messages,
                   COUNT(DISTINCT user_id) AS active_chatters
            FROM analytics_messages
            WHERE guild_id = ? AND is_voice = 0
              AND timestamp >= ? AND timestamp < ?
            """,
            (int(guild_id), current_start, current_end),
        ) as cur:
            current = dict(await cur.fetchone() or {})
        async with db.execute(
            """
            SELECT COUNT(*) AS total_messages,
                   COUNT(DISTINCT user_id) AS active_chatters
            FROM analytics_messages
            WHERE guild_id = ? AND is_voice = 0
              AND timestamp >= ? AND timestamp < ?
            """,
            (int(guild_id), previous_start_text, current_start),
        ) as cur:
            previous = dict(await cur.fetchone() or {})
        async with db.execute(
            """
            SELECT DISTINCT user_id
            FROM analytics_messages
            WHERE guild_id = ? AND is_voice = 0
              AND timestamp >= ? AND timestamp < ?
            """,
            (int(guild_id), current_start, current_end),
        ) as cur:
            current_users = {
                int(row["user_id"]) for row in await cur.fetchall()
                if row["user_id"] is not None
            }
        async with db.execute(
            """
            SELECT DISTINCT user_id
            FROM analytics_messages
            WHERE guild_id = ? AND is_voice = 0
              AND timestamp >= ? AND timestamp < ?
            """,
            (int(guild_id), previous_start_text, current_start),
        ) as cur:
            previous_users = {
                int(row["user_id"]) for row in await cur.fetchall()
                if row["user_id"] is not None
            }
        async with db.execute(
            """
            SELECT COALESCE(SUM(duration_seconds), 0) AS total_voice_seconds
            FROM analytics_voice_sessions
            WHERE guild_id = ? AND started_at >= ? AND started_at < ?
            """,
            (int(guild_id), current_start, current_end),
        ) as cur:
            voice = dict(await cur.fetchone() or {})

    total_messages = int(current.get("total_messages", 0) or 0)
    previous_messages = int(previous.get("total_messages", 0) or 0)
    active_chatters = int(current.get("active_chatters", 0) or 0)
    previous_count = len(previous_users)
    retained = len(current_users & previous_users)
    return {
        "total_messages": total_messages,
        "active_chatters": active_chatters,
        "activity_trend_pct": round(
            ((total_messages - previous_messages) / previous_messages) * 100
        ) if previous_messages else 0,
        "retention_pct": round((retained / previous_count) * 100) if previous_count else 0,
        "total_voice_seconds": int(voice.get("total_voice_seconds", 0) or 0),
    }


async def get_channel_traffic(guild_id: int, timeframe: str = "7d") -> list[dict[str, Any]]:
    _, start, end, _ = _analytics_window(timeframe)
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT channel_id, COUNT(*) AS count
            FROM analytics_messages
            WHERE guild_id = ? AND is_voice = 0
              AND channel_id IS NOT NULL
              AND timestamp >= ? AND timestamp < ?
            GROUP BY channel_id
            ORDER BY count DESC
            """,
            (int(guild_id), _analytics_iso(start), _analytics_iso(end)),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    total = sum(int(row["count"] or 0) for row in rows)
    return [
        {
            "id": str(row["channel_id"]),
            "count": int(row["count"] or 0),
            "percentage": round((int(row["count"] or 0) / total) * 100, 1) if total else 0,
        }
        for row in rows
    ]


async def get_dead_channels(
    guild_id: int,
    timeframe: str = "7d",
    channel_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    if not channel_ids:
        return []
    traffic = await get_channel_traffic(guild_id, timeframe)
    counts = {str(row["id"]): int(row["count"]) for row in traffic}
    return [
        {"id": str(channel_id), "count": counts.get(str(channel_id), 0)}
        for channel_id in channel_ids
        if counts.get(str(channel_id), 0) == 0
    ]


async def get_top_messenger(guild_id: int, timeframe: str = "7d") -> dict[str, Any] | None:
    _, start, end, _ = _analytics_window(timeframe)
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT user_id, COUNT(*) AS message_count
            FROM analytics_messages
            WHERE guild_id = ? AND is_voice = 0 AND user_id IS NOT NULL
              AND timestamp >= ? AND timestamp < ?
            GROUP BY user_id
            ORDER BY message_count DESC, user_id ASC
            LIMIT 1
            """,
            (int(guild_id), _analytics_iso(start), _analytics_iso(end)),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "user_id": str(row["user_id"]),
        "message_count": int(row["message_count"] or 0),
    }


async def get_hourly_heatmap(guild_id: int, timeframe: str = "7d") -> dict[str, list[list[int]]]:
    _, start, end, _ = _analytics_window(timeframe)
    heatmap = {
        "written": [[0 for _ in range(24)] for _ in range(7)],
        "voice": [[0 for _ in range(24)] for _ in range(7)],
    }
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT CAST(strftime('%w', timestamp) AS INTEGER) AS day,
                   CAST(strftime('%H', timestamp) AS INTEGER) AS hour,
                   is_voice, COUNT(*) AS amount
            FROM analytics_messages
            WHERE guild_id = ? AND timestamp >= ? AND timestamp < ?
            GROUP BY day, hour, is_voice
            """,
            (int(guild_id), _analytics_iso(start), _analytics_iso(end)),
        ) as cur:
            rows = await cur.fetchall()
    for row in rows:
        day, hour = int(row["day"]), int(row["hour"])
        if 0 <= day < 7 and 0 <= hour < 24:
            heatmap["voice" if int(row["is_voice"] or 0) else "written"][day][hour] = int(row["amount"] or 0)
    for mode, matrix in heatmap.items():
        maximum = max((value for row in matrix for value in row), default=0)
        heatmap[mode] = [
            [round((value / maximum) * 100) if maximum else 0 for value in row]
            for row in matrix
        ]
    return heatmap


async def get_golden_hour(guild_id: int, timeframe: str = "7d") -> dict[str, Any]:
    heatmap = await get_hourly_heatmap(guild_id, timeframe)
    combined = [
        [heatmap["written"][day][hour] + heatmap["voice"][day][hour] for hour in range(24)]
        for day in range(7)
    ]
    best_day, best_start, best_value = 0, 0, 0
    windows = []
    for day, row in enumerate(combined):
        for start_hour in range(22):
            value = sum(row[start_hour:start_hour + 3])
            windows.append(value)
            if value > best_value:
                best_day, best_start, best_value = day, start_hour, value
    average = (sum(windows) / len(windows)) if windows else 0
    multiplier = round(best_value / average, 1) if average else 0
    day_names = ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]

    def hour_text(hour: int) -> str:
        suffix = "ص" if hour < 12 else "م"
        display = hour % 12 or 12
        return f"{display}{suffix}"

    return {
        "day": day_names[best_day] if best_value else "—",
        "window": f"من {hour_text(best_start)} إلى {hour_text((best_start + 3) % 24)}" if best_value else "لا توجد بيانات كافية",
        "multiplier": multiplier,
        "tip": "أفضل وقت تنشر فيه إعلاناً أو تفتح فعالية أو سكريم تنافسي!" if best_value else "ستظهر التوصية بعد تسجيل نشاط كافٍ.",
    }


async def get_warning(warning_id: int) -> Optional[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT id, user_id, guild_id, moderator_id, reason, timestamp "
            "FROM warnings WHERE id = ?",
            (int(warning_id),),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_warning(guild_id: int, warning_id: int) -> bool:
    """حذف إنذار واحد مع تقييد العملية بسيرفره الأصلي."""
    async with connect() as db:
        cur = await db.execute(
            "DELETE FROM warnings WHERE id = ? AND guild_id = ?",
            (int(warning_id), int(guild_id)),
        )
        changed = cur.rowcount > 0
        await cur.close()
        await db.commit()
        return changed


# -------------------------------------------------------------
# التذكيرات الدائمة (Persistent reminders)
# -------------------------------------------------------------
async def create_reminder(
    guild_id: int,
    user_id: int,
    channel_id: int,
    reminder: str,
    due_at: str,
) -> int:
    return await add_reminder(
        guild_id,
        user_id,
        channel_id,
        reminder,
        due_at,
    )


async def add_reminder(
    guild_id: int,
    user_id: int,
    channel_id: int,
    text: str,
    remind_at: str,
) -> int:
    """Persist a Step 5 user reminder and return its database id.

    This intentionally targets ``user_reminders`` rather than the legacy
    ``reminders`` table used by the existing community cog.
    """
    async with connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO user_reminders
                (guild_id, user_id, channel_id, reminder_text, remind_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(guild_id),
                int(user_id),
                int(channel_id),
                str(text).strip()[:1000],
                str(remind_at),
            ),
        )
        reminder_id = cursor.lastrowid
        await db.commit()
    return int(reminder_id)


async def get_due_user_reminders(
    now: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """Atomically claim due user reminders for one delivery worker."""
    current = now or _utc_now()
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT id, guild_id, user_id, channel_id, reminder_text, remind_at,
                   status, claimed_at, created_at
            FROM user_reminders
            WHERE remind_at <= ?
              AND (
                  status = 'pending'
                  OR (
                      status = 'processing'
                      AND (
                          claimed_at IS NULL
                          OR claimed_at <= datetime('now', '-5 minutes')
                      )
                  )
              )
            ORDER BY remind_at ASC, id ASC
            LIMIT 100
            """,
            (str(current),),
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
        if rows:
            claim_time = _utc_now()
            placeholders = ", ".join("?" for _ in rows)
            await db.execute(
                f"""
                UPDATE user_reminders
                SET status = 'processing', claimed_at = ?
                WHERE id IN ({placeholders})
                  AND (
                      status = 'pending'
                      OR (
                          status = 'processing'
                          AND (
                              claimed_at IS NULL
                              OR claimed_at <= datetime('now', '-5 minutes')
                          )
                      )
                  )
                """,
                (claim_time, *(int(row["id"]) for row in rows)),
            )
            for row in rows:
                row["status"] = "processing"
                row["claimed_at"] = claim_time
        await db.commit()
        return rows


async def delete_reminder(reminder_id: int) -> bool:
    """Delete one Step 5 reminder, returning whether a row was removed."""
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM user_reminders WHERE id = ?",
            (int(reminder_id),),
        )
        deleted = cursor.rowcount > 0
        await db.commit()
    return deleted


# -------------------------------------------------------------
# Clan operations and dashboard ticket dropdown helpers
# -------------------------------------------------------------
async def get_clan_applications(
    guild_id: int,
    status: str | None = None,
) -> list[Dict[str, Any]]:
    query = (
        "SELECT id, guild_id, user_id, username, kd_ratio, device, notes, "
        "status, created_at FROM clan_applications WHERE guild_id = ?"
    )
    params: list[Any] = [int(guild_id)]
    if status and status != "all":
        query += " AND status = ?"
        params.append(str(status))
    query += " ORDER BY created_at DESC, id DESC"
    async with connect(aiosqlite.Row) as db:
        async with db.execute(query, tuple(params)) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def update_clan_application(
    guild_id: int,
    application_id: int,
    status: str,
) -> Optional[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            UPDATE clan_applications
            SET status = ?
            WHERE guild_id = ? AND id = ?
            """,
            (str(status), int(guild_id), int(application_id)),
        )
        async with db.execute(
            "SELECT * FROM clan_applications WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(application_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return dict(row) if row else None


async def get_clan_roster(guild_id: int) -> list[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, lineup_name, player_id, player_name,
                   role_title, display_order
            FROM clan_rosters
            WHERE guild_id = ?
            ORDER BY lineup_name ASC, display_order ASC, id ASC
            """,
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def save_clan_roster_player(
    guild_id: int,
    lineup_name: str,
    player_id: int,
    player_name: str,
    role_title: str = "",
    display_order: int = 0,
    roster_id: int | None = None,
) -> Optional[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        if roster_id is None:
            cur = await db.execute(
                """
                INSERT INTO clan_rosters
                    (guild_id, lineup_name, player_id, player_name,
                     role_title, display_order)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(guild_id),
                    str(lineup_name),
                    int(player_id),
                    str(player_name)[:100],
                    str(role_title)[:80],
                    int(display_order),
                ),
            )
            roster_id = cur.lastrowid
        else:
            await db.execute(
                """
                UPDATE clan_rosters
                SET lineup_name = ?, player_id = ?, player_name = ?,
                    role_title = ?, display_order = ?
                WHERE guild_id = ? AND id = ?
                """,
                (
                    str(lineup_name),
                    int(player_id),
                    str(player_name)[:100],
                    str(role_title)[:80],
                    int(display_order),
                    int(guild_id),
                    int(roster_id),
                ),
            )
        async with db.execute(
            "SELECT * FROM clan_rosters WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(roster_id)),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return dict(row) if row else None


async def delete_clan_roster_player(guild_id: int, roster_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "DELETE FROM clan_rosters WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(roster_id)),
        )
        await db.commit()
    return cur.rowcount > 0


async def get_scrim_logs(guild_id: int) -> list[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, opponent_name, score_prime, score_enemy,
                   map_name, result, logged_by, timestamp
            FROM scrim_logs
            WHERE guild_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT 200
            """,
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def add_scrim_log(
    guild_id: int,
    opponent_name: str,
    score_prime: int,
    score_enemy: int,
    map_name: str,
    result: str,
    logged_by: int,
) -> Dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        cur = await db.execute(
            """
            INSERT INTO scrim_logs
                (guild_id, opponent_name, score_prime, score_enemy,
                 map_name, result, logged_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(guild_id),
                str(opponent_name)[:120],
                int(score_prime),
                int(score_enemy),
                str(map_name)[:80],
                str(result),
                int(logged_by),
            ),
        )
        async with db.execute(
            "SELECT * FROM scrim_logs WHERE id = ?",
            (int(cur.lastrowid),),
        ) as row_cur:
            row = await row_cur.fetchone()
        await db.commit()
    return dict(row)


async def get_ticket_dropdown_config(guild_id: int) -> Optional[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM ticket_dropdown_configs WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def save_ticket_dropdown_config(
    guild_id: int,
    channel_id: int | None,
    message_id: int | None,
    embed_title: str,
    embed_description: str,
    embed_color: str,
    footer_text: str,
) -> Dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            """
            INSERT INTO ticket_dropdown_configs
                (guild_id, channel_id, message_id, embed_title,
                 embed_description, embed_color, footer_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id,
                embed_title = excluded.embed_title,
                embed_description = excluded.embed_description,
                embed_color = excluded.embed_color,
                footer_text = excluded.footer_text
            """,
            (
                int(guild_id),
                int(channel_id) if channel_id is not None else None,
                int(message_id) if message_id is not None else None,
                str(embed_title)[:256],
                str(embed_description)[:4096],
                str(embed_color)[:7],
                str(footer_text)[:2048],
            ),
        )
        async with db.execute(
            "SELECT * FROM ticket_dropdown_configs WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return dict(row)


async def get_ticket_dropdown_categories(guild_id: int) -> list[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, label, description, emoji, role_id, category_id
            FROM ticket_dropdown_categories
            WHERE guild_id = ?
            ORDER BY id ASC
            """,
            (int(guild_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def replace_ticket_dropdown_categories(
    guild_id: int,
    categories: list[dict],
) -> list[Dict[str, Any]]:
    async with connect(aiosqlite.Row) as db:
        await db.execute(
            "DELETE FROM ticket_dropdown_categories WHERE guild_id = ?",
            (int(guild_id),),
        )
        for category in categories[:25]:
            await db.execute(
                """
                INSERT INTO ticket_dropdown_categories
                    (guild_id, label, description, emoji, role_id, category_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(guild_id),
                    str(category.get("label") or "")[:80],
                    str(category.get("description") or "")[:100],
                    str(category.get("emoji") or "🎫").strip()[:100],
                    int(category["role_id"]) if category.get("role_id") else None,
                    int(category["category_id"]) if category.get("category_id") else None,
                ),
            )
        async with db.execute(
            """
            SELECT id, guild_id, label, description, emoji, role_id, category_id
            FROM ticket_dropdown_categories
            WHERE guild_id = ? ORDER BY id ASC
            """,
            (int(guild_id),),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
        await db.commit()
    return rows


async def add_broadcast_log(
    guild_id: int,
    channel_id: int,
    author_id: int,
    message_type: str,
    title: str,
    content: str,
    color: str,
    description: str = "",
) -> Dict[str, Any]:
    async with connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            INSERT INTO broadcast_logs
                (guild_id, channel_id, author_id, message_type, title, content,
                 description, color)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(guild_id),
                int(channel_id),
                int(author_id),
                str(message_type)[:20],
                str(title)[:256],
                str(content)[:4000],
                str(description)[:4096],
                str(color)[:7],
            ),
        )
        async with db.execute(
            "SELECT * FROM broadcast_logs WHERE id = ?",
            (int(cursor.lastrowid),),
        ) as row_cursor:
            row = await row_cursor.fetchone()
        await db.commit()
    return dict(row)


async def get_recent_broadcast_logs(
    guild_id: int,
    limit: int = 10,
) -> list[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 50))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, guild_id, channel_id, author_id, message_type, title,
                   content, description, color, sent_at
            FROM broadcast_logs
            WHERE guild_id = ?
            ORDER BY sent_at DESC, id DESC
            LIMIT ?
            """,
            (int(guild_id), safe_limit),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_due_reminders(now: Optional[str] = None) -> list[Dict[str, Any]]:
    rows = await get_due_user_reminders(now)
    return [
        {
            "id": row["id"],
            "guild_id": row["guild_id"],
            "user_id": row["user_id"],
            "channel_id": row["channel_id"],
            "reminder": row["reminder_text"],
            "due_at": row["remind_at"],
        }
        for row in rows
    ]


async def get_user_reminders(
    guild_id: int,
    user_id: int,
    limit: int = 20,
) -> list[Dict[str, Any]]:
    limit = max(1, min(int(limit), 50))
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT id, channel_id, reminder_text AS reminder,
                   remind_at AS due_at, status
            FROM user_reminders
            WHERE guild_id = ? AND user_id = ? AND status = 'pending'
            ORDER BY remind_at ASC
            LIMIT ?
            """,
            (int(guild_id), int(user_id), limit),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def cancel_reminder(guild_id: int, user_id: int, reminder_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE user_reminders SET status = 'cancelled', claimed_at = NULL
            WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'pending'
            """,
            (int(reminder_id), int(guild_id), int(user_id)),
        )
        changed = cur.rowcount > 0
        await db.commit()
    return changed


async def complete_reminder(reminder_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE user_reminders SET status = 'completed', claimed_at = NULL
            WHERE id = ? AND status IN ('pending', 'processing')
            """,
            (int(reminder_id),),
        )
        changed = cur.rowcount > 0
        await db.commit()
    return changed

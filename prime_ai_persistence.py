"""Durable storage for selected PRIME AI context and memory records.

Discord server data remains in the existing SQLite database. Only PRIME AI
conversation turns and mirrors of its user-owned memory/profile records are
stored in PostgreSQL when DATABASE_URL is configured.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import aiosqlite

import database

LOGGER = logging.getLogger("PRIME.AI.Persistence")
_SECRET_VALUE_PATTERNS = (
    re.compile(
        r"(?i)(?:api[\s_-]*key|password|passwd|secret|private[\s_-]*key|"
        r"access[\s_-]*token|auth(?:entication)?[\s_-]*token|كلمة\s*المرور|"
        r"كلمة\s*السر|مفتاح\s*(?:سري|خاص)|رمز\s*الدخول)\s*(?:[:=]|\bis\b)\s*\S+"
    ),
    re.compile(r"\b(?:M[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{25,}|AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9_-]{20,})\b"),
)
_FOLLOW_UP_PATTERN = re.compile(
    r"(?i)(?:\b(?:it|that|this|they|those|same|previous|there|and|also|"
    r"what about|how about|which one|the second)\b|"
    r"(?<![\u0600-\u06ff])(?:هذا|هذه|هذي|ذلك|ذالك|هو|هي|هم|نفس(?:ه|ها)|"
    r"هناك|طيب|تمام|بعدها|نكمل|كمل(?:ها)?|كيف ذلك|وش|ايش|كم|وين)"
    r"(?![\u0600-\u06ff]))"
)
_SAFE_TOPIC_KEYS = {
    "general",
    "design",
    "discord moderation",
    "discord roles",
    "discord channels",
    "games and events",
    "subscriptions and economy",
    "dashboard settings",
    "PRIME AI",
}

_pool: asyncpg.Pool | None = None
_DISABLED_FOR_TESTS = os.environ.get("PRIME_AI_DISABLE_DURABLE_STORE") == "1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value or "")
    if not text:
        return _now()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_durable_timestamp") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stamp(value: Any) -> str:
    return _datetime(value).isoformat()


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _redact_context_text(value: Any) -> str:
    result = str(value or "")[:12000]
    for pattern in _SECRET_VALUE_PATTERNS:
        result = pattern.sub("[محذوفة بيانات سرية]", result)
    return result


async def start_durable_store() -> bool:
    """Open the PostgreSQL pool and fail clearly if its published schema is absent."""
    global _pool
    if _DISABLED_FOR_TESTS or not os.environ.get("DATABASE_URL"):
        return False
    if _pool is not None:
        return True
    _pool = await asyncpg.create_pool(
        dsn=os.environ["DATABASE_URL"],
        min_size=1,
        max_size=5,
        command_timeout=15,
    )
    required = (
        "prime_ai_conversation_turns",
        "prime_ai_durable_memories",
        "prime_ai_durable_profiles",
        "prime_ai_durable_memory_revisions",
    )
    async with _pool.acquire() as conn:
        found = await conn.fetch(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename = ANY($1::text[])",
            list(required),
        )
    found_names = {row["tablename"] for row in found}
    missing = [name for name in required if name not in found_names]
    if missing:
        await _pool.close()
        _pool = None
        raise RuntimeError(
            "PRIME AI durable schema is missing: "
            + ", ".join(missing)
            + ". Apply schemas/prime_ai_persistence.sql to the development database "
            "and publish its schema before starting this version."
        )
    return True


async def close_durable_store() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.close()


def durable_store_enabled() -> bool:
    return _pool is not None


async def _sqlite_rows(query: str, params: tuple = ()) -> list[dict]:
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def _upsert_pg_memory(conn: asyncpg.Connection, row: dict) -> None:
    related = _json_list(row.get("related_user_ids_json"))
    await conn.execute(
        """
        INSERT INTO prime_ai_durable_memories (
            guild_id, memory_id, content, created_by, created_at, scope, scope_id,
            enabled, expires_at, updated_at, source, confidence, status, owner_user_id,
            candidate_expires_at, confirmation_message_id, pinned, memory_type,
            importance, source_channel_id, source_message_id, related_user_ids
        ) VALUES (
            $1, $2, $3, $4, $5::timestamptz, $6, $7, $8, $9::timestamptz,
            $10::timestamptz, $11, $12, $13, $14, $15::timestamptz, $16,
            $17, $18, $19, $20, $21, $22::jsonb
        )
        ON CONFLICT (guild_id, memory_id) DO UPDATE SET
            content=EXCLUDED.content, created_by=EXCLUDED.created_by,
            created_at=EXCLUDED.created_at, scope=EXCLUDED.scope,
            scope_id=EXCLUDED.scope_id, enabled=EXCLUDED.enabled,
            expires_at=EXCLUDED.expires_at, updated_at=EXCLUDED.updated_at,
            source=EXCLUDED.source, confidence=EXCLUDED.confidence,
            status=EXCLUDED.status, owner_user_id=EXCLUDED.owner_user_id,
            candidate_expires_at=EXCLUDED.candidate_expires_at,
            confirmation_message_id=EXCLUDED.confirmation_message_id,
            pinned=EXCLUDED.pinned, memory_type=EXCLUDED.memory_type,
            importance=EXCLUDED.importance, source_channel_id=EXCLUDED.source_channel_id,
            source_message_id=EXCLUDED.source_message_id,
            related_user_ids=EXCLUDED.related_user_ids
        """,
        int(row["guild_id"]),
        int(row["memory_id"]),
        str(row["content"]),
        int(row.get("created_by") or 0),
        _datetime(row.get("created_at")),
        str(row.get("scope") or "SERVER"),
        str(row.get("scope_id") or ""),
        bool(row.get("enabled", 1)),
        _datetime(row["expires_at"]) if row.get("expires_at") else None,
        _datetime(row.get("updated_at")),
        str(row.get("source") or "ADMIN"),
        float(row.get("confidence") or 1.0),
        str(row.get("status") or "ACTIVE"),
        int(row["owner_user_id"]) if row.get("owner_user_id") is not None else None,
        _datetime(row["candidate_expires_at"])
        if row.get("candidate_expires_at") else None,
        int(row["confirmation_message_id"])
        if row.get("confirmation_message_id") is not None
        else None,
        bool(row.get("pinned", 0)),
        str(row.get("memory_type") or "FACT"),
        max(1, min(5, int(row.get("importance") or 3))),
        int(row["source_channel_id"]) if row.get("source_channel_id") else None,
        int(row["source_message_id"]) if row.get("source_message_id") else None,
        json.dumps(related, ensure_ascii=False),
    )


async def sync_memory_snapshot(guild_id: int | None = None) -> None:
    """Mirror the current SQLite memory snapshot without touching unrelated guilds."""
    if _pool is None:
        return
    where = "WHERE guild_id = ?" if guild_id is not None else ""
    params = (int(guild_id),) if guild_id is not None else ()
    memories = await _sqlite_rows(
        f"SELECT * FROM prime_ai_memories {where} ORDER BY guild_id, memory_id",
        params,
    )
    revisions = await _sqlite_rows(
        "SELECT * FROM prime_ai_memory_revisions "
        + ("WHERE guild_id = ? " if guild_id is not None else "")
        + "ORDER BY guild_id, revision_id",
        params,
    )
    rows_by_guild: dict[int, list[int]] = {}
    revisions_by_guild: dict[int, list[int]] = {}
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for row in memories:
                await _upsert_pg_memory(conn, row)
                rows_by_guild.setdefault(int(row["guild_id"]), []).append(
                    int(row["memory_id"])
                )
            if guild_id is not None:
                ids = rows_by_guild.get(int(guild_id), [])
                await conn.execute(
                    "DELETE FROM prime_ai_durable_memories WHERE guild_id=$1 "
                    "AND NOT (memory_id = ANY($2::bigint[]))",
                    int(guild_id),
                    ids,
                )
            else:
                for gid in rows_by_guild:
                    ids = rows_by_guild[gid]
                    await conn.execute(
                        "DELETE FROM prime_ai_durable_memories WHERE guild_id=$1 "
                        "AND NOT (memory_id = ANY($2::bigint[]))",
                        gid,
                        ids,
                    )
            for row in revisions:
                revisions_by_guild.setdefault(int(row["guild_id"]), []).append(
                    int(row["revision_id"])
                )
                await conn.execute(
                    """
                    INSERT INTO prime_ai_durable_memory_revisions (
                        guild_id, memory_id, revision_id, before_content, after_content,
                        changed_by, changed_at, source_channel_id, source_message_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7::timestamptz,$8,$9)
                    ON CONFLICT (guild_id, revision_id) DO UPDATE SET
                        memory_id=EXCLUDED.memory_id,
                        before_content=EXCLUDED.before_content,
                        after_content=EXCLUDED.after_content,
                        changed_by=EXCLUDED.changed_by,
                        changed_at=EXCLUDED.changed_at,
                        source_channel_id=EXCLUDED.source_channel_id,
                        source_message_id=EXCLUDED.source_message_id
                    """,
                    int(row["guild_id"]),
                    int(row["memory_id"]),
                    int(row["revision_id"]),
                    row["before_content"],
                    row["after_content"],
                    int(row["changed_by"]),
                    _datetime(row["changed_at"]),
                    row.get("source_channel_id"),
                    row.get("source_message_id"),
                )
            if guild_id is not None:
                revision_ids = revisions_by_guild.get(int(guild_id), [])
                await conn.execute(
                    "DELETE FROM prime_ai_durable_memory_revisions WHERE guild_id=$1 "
                    "AND NOT (revision_id = ANY($2::bigint[]))",
                    int(guild_id),
                    revision_ids,
                )


async def sync_profile_snapshot(guild_id: int | None = None) -> None:
    if _pool is None:
        return
    where = "WHERE guild_id = ?" if guild_id is not None else ""
    params = (int(guild_id),) if guild_id is not None else ()
    profiles = await _sqlite_rows(
        f"SELECT * FROM prime_ai_user_profiles {where} ORDER BY guild_id, user_id",
        params,
    )
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for row in profiles:
                preferences = row.get("preferences_json") or "{}"
                try:
                    preferences = json.dumps(
                        json.loads(preferences), ensure_ascii=False
                    )
                except (TypeError, ValueError):
                    preferences = "{}"
                await conn.execute(
                    """
                    INSERT INTO prime_ai_durable_profiles (
                        guild_id,user_id,preferences_json,interaction_count,last_intent,
                        last_topic,last_channel_id,last_seen_at,updated_at
                    ) VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7,$8::timestamptz,$9::timestamptz)
                    ON CONFLICT (guild_id,user_id) DO UPDATE SET
                        preferences_json=EXCLUDED.preferences_json,
                        interaction_count=EXCLUDED.interaction_count,
                        last_intent=EXCLUDED.last_intent,last_topic=EXCLUDED.last_topic,
                        last_channel_id=EXCLUDED.last_channel_id,
                        last_seen_at=EXCLUDED.last_seen_at,updated_at=EXCLUDED.updated_at
                    """,
                    int(row["guild_id"]),
                    int(row["user_id"]),
                    preferences,
                    int(row.get("interaction_count") or 0),
                    str(row.get("last_intent") or ""),
                    str(row.get("last_topic") or ""),
                    row.get("last_channel_id"),
                    _datetime(row.get("last_seen_at")),
                    _datetime(row.get("updated_at")),
                )


async def sync_profile_record(guild_id: int, user_id: int) -> None:
    if _pool is None:
        return
    rows = await _sqlite_rows(
        "SELECT * FROM prime_ai_user_profiles WHERE guild_id=? AND user_id=?",
        (int(guild_id), int(user_id)),
    )
    if not rows:
        async with _pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM prime_ai_durable_profiles WHERE guild_id=$1 AND user_id=$2",
                int(guild_id), int(user_id),
            )
        return
    row = rows[0]
    preferences = row.get("preferences_json") or "{}"
    try:
        preferences = json.dumps(json.loads(preferences), ensure_ascii=False)
    except (TypeError, ValueError):
        preferences = "{}"
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO prime_ai_durable_profiles (
                guild_id,user_id,preferences_json,interaction_count,last_intent,
                last_topic,last_channel_id,last_seen_at,updated_at
            ) VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7,$8::timestamptz,$9::timestamptz)
            ON CONFLICT (guild_id,user_id) DO UPDATE SET
                preferences_json=EXCLUDED.preferences_json,
                interaction_count=EXCLUDED.interaction_count,
                last_intent=EXCLUDED.last_intent,last_topic=EXCLUDED.last_topic,
                last_channel_id=EXCLUDED.last_channel_id,
                last_seen_at=EXCLUDED.last_seen_at,updated_at=EXCLUDED.updated_at
            """,
            int(row["guild_id"]), int(row["user_id"]), preferences,
            int(row.get("interaction_count") or 0), str(row.get("last_intent") or ""),
            str(row.get("last_topic") or ""), row.get("last_channel_id"),
            _datetime(row.get("last_seen_at")), _datetime(row.get("updated_at")),
        )


async def restore_from_durable_store() -> dict[str, int]:
    """Restore missing SQLite copies from PostgreSQL, then refresh durable mirrors."""
    if _pool is None:
        return {"memories": 0, "profiles": 0}
    async with _pool.acquire() as conn:
        memory_rows = await conn.fetch(
            "SELECT * FROM prime_ai_durable_memories ORDER BY guild_id,memory_id"
        )
        profile_rows = await conn.fetch(
            "SELECT * FROM prime_ai_durable_profiles ORDER BY guild_id,user_id"
        )
        revision_rows = await conn.fetch(
            "SELECT * FROM prime_ai_durable_memory_revisions "
            "ORDER BY guild_id,revision_id"
        )

    async with database.connect() as db:
        for row in memory_rows:
            await db.execute(
                """
                INSERT OR IGNORE INTO prime_ai_memories (
                    memory_id,guild_id,content,created_by,created_at,scope,scope_id,
                    enabled,expires_at,updated_at,source,confidence,status,owner_user_id,
                    candidate_expires_at,confirmation_message_id,pinned,memory_type,
                    importance,source_channel_id,source_message_id,related_user_ids_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(row["memory_id"]), int(row["guild_id"]), row["content"],
                    int(row["created_by"]), _stamp(row["created_at"]), row["scope"],
                    row["scope_id"], int(row["enabled"]), _stamp(row["expires_at"])
                    if row["expires_at"] else None, _stamp(row["updated_at"]), row["source"],
                    float(row["confidence"]), row["status"],
                    int(row["owner_user_id"]) if row["owner_user_id"] is not None else None,
                    _stamp(row["candidate_expires_at"])
                    if row["candidate_expires_at"] else None,
                    int(row["confirmation_message_id"])
                    if row["confirmation_message_id"] is not None else None,
                    int(row["pinned"]), row["memory_type"], int(row["importance"]),
                    row["source_channel_id"], row["source_message_id"],
                    json.dumps(_json_list(row["related_user_ids"]), ensure_ascii=False),
                ),
            )
        for row in profile_rows:
            await db.execute(
                """
                INSERT INTO prime_ai_user_profiles (
                    guild_id,user_id,preferences_json,interaction_count,last_intent,
                    last_topic,last_channel_id,last_seen_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET
                    preferences_json=excluded.preferences_json,
                    interaction_count=excluded.interaction_count,
                    last_intent=excluded.last_intent,last_topic=excluded.last_topic,
                    last_channel_id=excluded.last_channel_id,
                    last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at
                WHERE excluded.updated_at > prime_ai_user_profiles.updated_at
                """,
                (
                    int(row["guild_id"]), int(row["user_id"]),
                    json.dumps(row["preferences_json"], ensure_ascii=False)
                    if isinstance(row["preferences_json"], (dict, list))
                    else str(row["preferences_json"] or "{}"),
                    int(row["interaction_count"]), row["last_intent"], row["last_topic"],
                    row["last_channel_id"], _stamp(row["last_seen_at"]),
                    _stamp(row["updated_at"]),
                ),
            )
        for row in revision_rows:
            await db.execute(
                """
                INSERT OR IGNORE INTO prime_ai_memory_revisions (
                    revision_id,guild_id,memory_id,before_content,after_content,
                    changed_by,changed_at,source_channel_id,source_message_id
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(row["revision_id"]), int(row["guild_id"]), int(row["memory_id"]),
                    row["before_content"], row["after_content"], int(row["changed_by"]),
                    _stamp(row["changed_at"]), row["source_channel_id"],
                    row["source_message_id"],
                ),
            )
        await db.commit()

    await sync_memory_snapshot()
    await sync_profile_snapshot()
    return {"memories": len(memory_rows), "profiles": len(profile_rows)}


async def _latest_topic(
    guild_id: int,
    channel_id: int,
    user_id: int,
    reference_message_id: int | None,
) -> str | None:
    if _pool is not None:
        async with _pool.acquire() as conn:
            if reference_message_id:
                topic = await conn.fetchval(
                    "SELECT topic_key FROM prime_ai_conversation_turns "
                    "WHERE guild_id=$1 AND channel_id=$2 AND user_id=$3 "
                    "AND assistant_message_id=$4 AND expires_at>NOW() LIMIT 1",
                    guild_id, channel_id, user_id, reference_message_id,
                )
                if topic:
                    return str(topic)
            topic = await conn.fetchval(
                "SELECT topic_key FROM prime_ai_conversation_turns "
                "WHERE guild_id=$1 AND channel_id=$2 AND user_id=$3 "
                "AND expires_at>NOW() ORDER BY created_at DESC LIMIT 1",
                guild_id, channel_id, user_id,
            )
            return str(topic) if topic else None
    async with database.connect(aiosqlite.Row) as db:
        if reference_message_id:
            async with db.execute(
                "SELECT topic_key FROM prime_ai_conversation_turns "
                "WHERE guild_id=? AND channel_id=? AND user_id=? "
                "AND assistant_message_id=? AND expires_at>? LIMIT 1",
                (guild_id, channel_id, user_id, reference_message_id, _now().isoformat()),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return str(row["topic_key"])
        async with db.execute(
            "SELECT topic_key FROM prime_ai_conversation_turns "
            "WHERE guild_id=? AND channel_id=? AND user_id=? AND expires_at>? "
            "ORDER BY created_at DESC LIMIT 1",
            (guild_id, channel_id, user_id, _now().isoformat()),
        ) as cursor:
            row = await cursor.fetchone()
    return str(row["topic_key"]) if row else None


async def resolve_topic_key(
    guild_id: int,
    channel_id: int,
    user_id: int,
    detected_topic: str,
    reference_message_id: int | None = None,
    *,
    allow_inherit: bool = False,
) -> str:
    detected = str(detected_topic or "").strip()
    if detected in _SAFE_TOPIC_KEYS and detected != "general":
        return detected
    if reference_message_id or allow_inherit:
        inherited = (
            await _latest_topic(
                int(guild_id),
                int(channel_id),
                int(user_id),
                reference_message_id,
            )
        )
        return inherited if inherited in _SAFE_TOPIC_KEYS else "general"
    return "general"


def is_follow_up(text: Any) -> bool:
    return bool(_FOLLOW_UP_PATTERN.search(str(text or "")[:500]))


async def record_turn(
    *,
    turn_key: str,
    guild_id: int,
    channel_id: int,
    user_id: int,
    topic_key: str,
    user_content: str,
    assistant_content: str,
    retention_days: int = 7,
    thread_id: int | None = None,
    user_message_id: int | None = None,
    assistant_message_id: int | None = None,
    reference_message_id: int | None = None,
    mentioned_user_ids: list[int] | None = None,
) -> str | None:
    if retention_days <= 0:
        return None
    now = _now()
    expires = now + timedelta(days=min(int(retention_days), 3650))
    key = str(turn_key or f"{guild_id}:{channel_id}:{user_id}:{int(now.timestamp() * 1000)}")
    topic = str(topic_key or "general")
    if topic not in _SAFE_TOPIC_KEYS:
        topic = "general"
    mentioned = [int(value) for value in (mentioned_user_ids or [])][:20]
    values = (
        key, int(guild_id), int(channel_id), thread_id, int(user_id),
        topic, user_message_id, assistant_message_id,
        reference_message_id, json.dumps(mentioned, ensure_ascii=False),
        _redact_context_text(user_content), _redact_context_text(assistant_content),
        now.isoformat(), expires.isoformat(),
    )
    if _pool is not None:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO prime_ai_conversation_turns (
                    turn_key,guild_id,channel_id,thread_id,user_id,topic_key,
                    user_message_id,assistant_message_id,reference_message_id,
                    mentioned_user_ids,user_content,assistant_content,created_at,expires_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13::timestamptz,$14::timestamptz)
                ON CONFLICT (turn_key) DO UPDATE SET
                    assistant_message_id=EXCLUDED.assistant_message_id,
                    assistant_content=EXCLUDED.assistant_content,
                    expires_at=EXCLUDED.expires_at
                """,
                *values[:12], now, expires,
            )
    else:
        async with database.connect() as db:
            await db.execute(
                """
                INSERT INTO prime_ai_conversation_turns (
                    turn_key,guild_id,channel_id,thread_id,user_id,topic_key,
                    user_message_id,assistant_message_id,reference_message_id,
                    mentioned_user_ids_json,user_content,assistant_content,created_at,expires_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(turn_key) DO UPDATE SET
                    assistant_message_id=excluded.assistant_message_id,
                    assistant_content=excluded.assistant_content,
                    expires_at=excluded.expires_at
                """,
                values,
            )
            await db.commit()
    return key


async def set_assistant_message_id(turn_key: str, message_id: int) -> None:
    if not turn_key or not message_id:
        return
    if _pool is not None:
        async with _pool.acquire() as conn:
            await conn.execute(
                "UPDATE prime_ai_conversation_turns SET assistant_message_id=$2 WHERE turn_key=$1",
                str(turn_key), int(message_id),
            )
    else:
        async with database.connect() as db:
            await db.execute(
                "UPDATE prime_ai_conversation_turns SET assistant_message_id=? WHERE turn_key=?",
                (int(message_id), str(turn_key)),
            )
            await db.commit()


async def load_turns(
    guild_id: int,
    channel_id: int,
    user_id: int,
    topic_key: str = "general",
    *,
    reference_message_id: int | None = None,
    limit: int = 8,
) -> list[dict[str, str]]:
    gid, cid, uid = int(guild_id), int(channel_id), int(user_id)
    topic = str(topic_key or "general")
    if topic not in _SAFE_TOPIC_KEYS:
        topic = "general"
    selected_topic = topic
    if reference_message_id:
        selected_topic = await _latest_topic(gid, cid, uid, reference_message_id) or topic
    count = max(1, min(int(limit), 20))
    if _pool is not None:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_content,assistant_content
                FROM prime_ai_conversation_turns
                WHERE guild_id=$1 AND channel_id=$2 AND user_id=$3
                  AND topic_key=$4 AND expires_at>NOW()
                ORDER BY created_at DESC LIMIT $5
                """,
                gid, cid, uid, selected_topic, count,
            )
    else:
        async with database.connect(aiosqlite.Row) as db:
            async with db.execute(
                """
                SELECT user_content,assistant_content
                FROM prime_ai_conversation_turns
                WHERE guild_id=? AND channel_id=? AND user_id=? AND topic_key=?
                  AND expires_at>?
                ORDER BY created_at DESC LIMIT ?
                """,
                (gid, cid, uid, selected_topic, _now().isoformat(), count),
            ) as cursor:
                rows = await cursor.fetchall()
    messages: list[dict[str, str]] = []
    for row in reversed(rows):
        messages.extend(
            [
                {"role": "user", "content": str(row["user_content"])},
                {"role": "assistant", "content": str(row["assistant_content"])},
            ]
        )
    return messages


async def prune_conversation_turns(
    retention_days: int, guild_id: int | None = None
) -> int:
    if _pool is not None:
        async with _pool.acquire() as conn:
            if retention_days <= 0:
                result = await conn.execute(
                    "DELETE FROM prime_ai_conversation_turns "
                    + ("WHERE guild_id=$1" if guild_id is not None else ""),
                    *([int(guild_id)] if guild_id is not None else []),
                )
            else:
                cutoff_days = min(int(retention_days), 3650)
                if guild_id is None:
                    result = await conn.execute(
                        "DELETE FROM prime_ai_conversation_turns WHERE expires_at <= NOW() "
                        "OR created_at < NOW() - ($1::int * INTERVAL '1 day')",
                        cutoff_days,
                    )
                else:
                    result = await conn.execute(
                        "DELETE FROM prime_ai_conversation_turns WHERE guild_id=$1 "
                        "AND (expires_at <= NOW() OR created_at < NOW() - ($2::int * INTERVAL '1 day'))",
                        int(guild_id), cutoff_days,
                    )
        try:
            return int(result.rsplit(" ", 1)[-1])
        except (TypeError, ValueError):
            return 0
    async with database.connect() as db:
        if retention_days <= 0:
            cursor = await db.execute(
                "DELETE FROM prime_ai_conversation_turns "
                + ("WHERE guild_id=?" if guild_id is not None else ""),
                (int(guild_id),) if guild_id is not None else (),
            )
        else:
            expiry = _now().isoformat()
            cutoff = (_now() - timedelta(days=min(int(retention_days), 3650))).isoformat()
            if guild_id is None:
                cursor = await db.execute(
                    "DELETE FROM prime_ai_conversation_turns WHERE expires_at<=? OR created_at<=?",
                    (expiry, cutoff),
                )
            else:
                cursor = await db.execute(
                    "DELETE FROM prime_ai_conversation_turns WHERE guild_id=? "
                    "AND (expires_at<=? OR created_at<=?)",
                    (int(guild_id), expiry, cutoff),
                )
        await db.commit()
        return max(0, int(cursor.rowcount or 0))


async def forget_conversation_user(
    guild_id: int, user_id: int, channel_id: int | None = None
) -> int:
    if _pool is not None:
        async with _pool.acquire() as conn:
            if channel_id is None:
                result = await conn.execute(
                    "DELETE FROM prime_ai_conversation_turns WHERE guild_id=$1 AND user_id=$2",
                    int(guild_id), int(user_id),
                )
            else:
                result = await conn.execute(
                    "DELETE FROM prime_ai_conversation_turns "
                    "WHERE guild_id=$1 AND user_id=$2 AND channel_id=$3",
                    int(guild_id), int(user_id), int(channel_id),
                )
        try:
            return int(result.rsplit(" ", 1)[-1])
        except (TypeError, ValueError):
            return 0
    async with database.connect() as db:
        if channel_id is None:
            cursor = await db.execute(
                "DELETE FROM prime_ai_conversation_turns WHERE guild_id=? AND user_id=?",
                (int(guild_id), int(user_id)),
            )
        else:
            cursor = await db.execute(
                "DELETE FROM prime_ai_conversation_turns "
                "WHERE guild_id=? AND user_id=? AND channel_id=?",
                (int(guild_id), int(user_id), int(channel_id)),
            )
        await db.commit()
        return max(0, int(cursor.rowcount or 0))

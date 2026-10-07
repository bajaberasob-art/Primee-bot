"""Durable subscription lifecycle built on the existing PRIME text XP engine."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from string import Formatter
from typing import Any

import aiosqlite

import database

logger = logging.getLogger("SubscriptionService")

LEVEL_STEP_XP = 5
NEW_SUBSCRIPTION_JITTER = 20
RENEWAL_JITTER = 30
NEW_SUBSCRIPTION_XP_CAP = 2_000
RENEWAL_XP_CAP = 3_000
MAX_DURATION_DAYS = 36_500
NOTIFICATION_BATCH_SIZE = 100
STALE_NOTIFICATION_MINUTES = 10
MAX_REMINDER_RULES = 32
MAX_REMINDER_HOURS = MAX_DURATION_DAYS * 24
NOTIFICATION_EVENTS = ("created", "renewal", "expiring", "expired")
EXPIRY_REMINDERS = (
    (168, "7d"),
    (72, "3d"),
    (24, "24h"),
)
TEMPLATE_FIELDS = {
    "user",
    "name",
    "server",
    "plan",
    "start_date",
    "end_date",
    "days_remaining",
    "subscription_id",
    "xp",
    "level",
    "message",
    "remaining",
}
DEFAULT_NOTIFICATION_TEMPLATES = {
    "created": "تم تفعيل اشتراكك في {server} حتى {end_date}. حصلت على {xp} XP.",
    "renewal": "تم تجديد اشتراكك في {server} حتى {end_date}. حصلت على {xp} XP.",
    "expiring": "ينتهي اشتراكك في {server} بعد {days_remaining}، بتاريخ {end_date}.",
    "expired": "انتهى اشتراكك في {server} بتاريخ {end_date}.",
}
DEFAULT_TEMPLATE_NAMES = {
    "created": "تفعيل اشتراك",
    "renewal": "تجديد اشتراك",
    "expiring": "تذكير انتهاء",
    "expired": "انتهاء اشتراك",
}


class SubscriptionSettingsConflict(RuntimeError):
    def __init__(self, current: dict[str, Any]):
        super().__init__("subscription settings were changed by another dashboard session")
        self.current = current


def _utc(value: datetime | str | None = None) -> datetime:
    if value is None:
        value = datetime.now(timezone.utc)
    elif not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | str | None = None) -> str:
    return _utc(value).isoformat()


def _positive_id(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _actor_id(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("actor_id must be a non-negative integer") from error
    if parsed < 0:
        raise ValueError("actor_id must be a non-negative integer")
    return parsed


def _normalized_name(value: Any, field: str = "name") -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    name = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if not name or len(name) > 80:
        raise ValueError(f"{field} must contain 1 to 80 characters")
    return name, name.casefold()


def _operation_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key or len(key) > 200:
        raise ValueError("idempotency_key must contain 1 to 200 characters")
    return key


def _duration(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("duration_days must be an integer")
    try:
        days = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("duration_days must be an integer") from error
    if days < 1 or days > MAX_DURATION_DAYS:
        raise ValueError(f"duration_days must be between 1 and {MAX_DURATION_DAYS}")
    return days


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _templates_from_row(row) -> dict[str, str]:
    try:
        stored = json.loads(row["notification_templates"] or "{}") if row else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        stored = {}
    templates = dict(DEFAULT_NOTIFICATION_TEMPLATES)
    if isinstance(stored, dict):
        for name in templates:
            value = stored.get(name)
            if isinstance(value, str) and value.strip():
                templates[name] = value.strip()[:1000]
    return templates


def _default_notification_rules(guild_id: int) -> dict[str, dict[str, Any]]:
    return {
        event: {
            "enabled": True,
            "dm_enabled": True,
            "channel_enabled": False,
            "channel_id": None,
            "template_id": f"subtpl-{guild_id}-{event}",
        }
        for event in NOTIFICATION_EVENTS
    }


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _notification_rules_from_row(row, guild_id: int) -> dict[str, dict[str, Any]]:
    rules = _default_notification_rules(guild_id)
    if row is None:
        return rules
    supplied = _json_object(row["notification_rules_json"])
    for event in NOTIFICATION_EVENTS:
        current = supplied.get(event)
        if isinstance(current, dict):
            rules[event].update(
                {key: value for key, value in current.items() if key in rules[event]}
            )
    return rules


def _settings_from_row(row, guild_id: int) -> dict[str, Any]:
    return {
        "guild_id": int(guild_id),
        "xp_enabled": bool(row["xp_enabled"]) if row else True,
        "notifications_enabled": bool(row["notifications_enabled"]) if row else True,
        "new_xp_base": int(row["new_xp_base"]) if row else 100,
        "renewal_xp_base": int(row["renewal_xp_base"]) if row else 150,
        "enabled": bool(row["enabled"]) if row else True,
        "new_subscription_enabled": bool(row["new_subscription_enabled"]) if row else True,
        "renewal_enabled": bool(row["renewal_enabled"]) if row else True,
        "expiry_enabled": bool(row["expiry_enabled"]) if row else True,
        "expiry_detection_enabled": bool(row["expiry_detection_enabled"]) if row else True,
        "reminders_enabled": bool(row["reminders_enabled"]) if row else True,
        "default_duration_days": int(row["default_duration_days"]) if row else 30,
        "renewal_duration_days": int(row["renewal_duration_days"]) if row else 30,
        "default_plan_id": row["default_plan_id"] if row else None,
        "level_step_xp": int(row["level_step_xp"]) if row else LEVEL_STEP_XP,
        "new_xp_jitter": int(row["new_xp_jitter"]) if row else NEW_SUBSCRIPTION_JITTER,
        "renewal_xp_jitter": int(row["renewal_xp_jitter"]) if row else RENEWAL_JITTER,
        "new_xp_cap": int(row["new_xp_cap"]) if row else NEW_SUBSCRIPTION_XP_CAP,
        "renewal_xp_cap": int(row["renewal_xp_cap"]) if row else RENEWAL_XP_CAP,
        "xp_multiplier": float(row["xp_multiplier"]) if row else 1.0,
        "expiry_action": row["expiry_action"] if row else "expire",
        "notification_claim_timeout_minutes": (
            int(row["notification_claim_timeout_minutes"])
            if row else STALE_NOTIFICATION_MINUTES
        ),
        "notification_templates": _templates_from_row(row),
        "notification_rules": _notification_rules_from_row(row, guild_id),
        "revision": int(row["revision"]) if row else 0,
        "created_at": row["created_at"] if row else None,
        "updated_at": row["updated_at"] if row else None,
    }


async def _ensure_settings(db, guild_id: int, now: str) -> dict[str, Any]:
    await db.execute(
        """
        INSERT OR IGNORE INTO subscription_guild_settings
            (guild_id, notification_templates, notification_rules_json,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(guild_id),
            json.dumps(DEFAULT_NOTIFICATION_TEMPLATES, ensure_ascii=False, sort_keys=True),
            json.dumps(_default_notification_rules(int(guild_id)), ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )
    async with db.execute(
        "SELECT * FROM subscription_guild_settings WHERE guild_id = ?",
        (int(guild_id),),
    ) as cursor:
        row = await cursor.fetchone()
    settings = _settings_from_row(row, guild_id)
    # Backfill Phase 5 rows with durable defaults once. These values are then
    # read from SQLite by both Discord commands and the dashboard runtime.
    stored_templates = _json_object(row["notification_templates"])
    stored_rules = _json_object(row["notification_rules_json"])
    if not stored_templates or not stored_rules:
        await db.execute(
            """
            UPDATE subscription_guild_settings
            SET notification_templates = ?, notification_rules_json = ?
            WHERE guild_id = ?
            """,
            (
                json.dumps(settings["notification_templates"], ensure_ascii=False, sort_keys=True),
                json.dumps(settings["notification_rules"], ensure_ascii=False, sort_keys=True),
                int(guild_id),
            ),
        )

    for event in NOTIFICATION_EVENTS:
        template_id = f"subtpl-{int(guild_id)}-{event}"
        await db.execute(
            """
            INSERT OR IGNORE INTO subscription_templates
                (template_id, guild_id, name, normalized_name, event_type,
                 content, enabled, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
            """,
            (
                template_id,
                int(guild_id),
                DEFAULT_TEMPLATE_NAMES[event],
                f"default-{event}",
                event,
                settings["notification_templates"][event],
                now,
                now,
            ),
        )
    for hours, label in EXPIRY_REMINDERS:
        name = {"7d": "قبل 7 أيام", "3d": "قبل 3 أيام", "24h": "قبل 24 ساعة"}[label]
        await db.execute(
            """
            INSERT OR IGNORE INTO subscription_reminder_rules
                (reminder_id, guild_id, name, hours_before, enabled,
                 dm_enabled, channel_enabled, conditions_json,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, 1, 0, '{}', ?, ?)
            """,
            (f"subrem-{int(guild_id)}-{hours}", int(guild_id), name, hours, now, now),
        )
    return settings


async def get_subscription_settings(guild_id: int) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            settings = await _ensure_settings(db, guild_id, _iso())
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
    return settings


def _validate_template(template: Any) -> str:
    if not isinstance(template, str):
        raise ValueError("template must be text")
    value = template.strip()
    if not value or len(value) > 1000:
        raise ValueError("template must contain 1 to 1000 characters")
    try:
        parsed_fields = list(Formatter().parse(value))
    except ValueError as error:
        raise ValueError("template contains invalid braces") from error
    fields = set()
    for _, field_name, format_spec, conversion in parsed_fields:
        if field_name is None:
            continue
        fields.add(field_name)
        if format_spec or conversion:
            raise ValueError("template formatting options are not supported")
    unknown = fields - TEMPLATE_FIELDS
    if unknown:
        raise ValueError("unsupported template fields: " + ", ".join(sorted(unknown)))
    return value


async def _write_control_audit(
    db,
    *,
    guild_id: int,
    entity_type: str,
    entity_id: str,
    operation: str,
    actor_id: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    now: str,
) -> None:
    await db.execute(
        """
        INSERT INTO subscription_control_audit
            (guild_id, entity_type, entity_id, operation, actor_id,
             before_json, after_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(guild_id),
            entity_type,
            str(entity_id),
            operation,
            int(actor_id),
            json.dumps(before or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(after or {}, ensure_ascii=False, sort_keys=True),
            now,
        ),
    )


async def _write_admin_audit(
    db,
    *,
    event_key: str,
    idempotency_key: str | None,
    request_hash: str,
    subscription: dict[str, Any],
    actor_id: int,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    now: str,
) -> None:
    await db.execute(
        """
        INSERT OR IGNORE INTO subscription_admin_audit
            (event_key, idempotency_key, request_hash, subscription_id,
             guild_id, user_id, event_type, actor_id, before_json, after_json,
             created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'admin_update', ?, ?, ?, ?)
        """,
        (
            event_key,
            idempotency_key,
            request_hash,
            subscription["subscription_id"],
            int(subscription["guild_id"]),
            int(subscription["user_id"]),
            int(actor_id),
            json.dumps(before or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(after, ensure_ascii=False, sort_keys=True),
            now,
        ),
    )


async def update_subscription_settings(
    guild_id: int,
    changes: dict[str, Any],
    *,
    expected_revision: int | None = None,
    actor_id: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    if not isinstance(changes, dict):
        raise ValueError("settings changes must be an object")
    allowed = {
        "xp_enabled",
        "notifications_enabled",
        "enabled",
        "new_subscription_enabled",
        "renewal_enabled",
        "expiry_enabled",
        "expiry_detection_enabled",
        "reminders_enabled",
        "new_xp_base",
        "renewal_xp_base",
        "default_duration_days",
        "renewal_duration_days",
        "default_plan_id",
        "level_step_xp",
        "new_xp_jitter",
        "renewal_xp_jitter",
        "new_xp_cap",
        "renewal_xp_cap",
        "xp_multiplier",
        "expiry_action",
        "notification_claim_timeout_minutes",
        "notification_rules",
        "templates",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("unsupported subscription settings: " + ", ".join(sorted(unknown)))
    if expected_revision is not None and (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise ValueError("expected_revision must be a non-negative integer")
    actor_id = _actor_id(actor_id) if actor_id is not None else None

    now_text = _iso(now)
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            current = await _ensure_settings(db, guild_id, now_text)
            before_settings = json.loads(
                json.dumps(current, ensure_ascii=False, sort_keys=True)
            )
            if (
                expected_revision is not None
                and expected_revision != current["revision"]
            ):
                raise SubscriptionSettingsConflict(current)
            values = {
                key: current[key]
                for key in (
                    "xp_enabled",
                    "notifications_enabled",
                    "enabled",
                    "new_subscription_enabled",
                    "renewal_enabled",
                    "expiry_enabled",
                    "expiry_detection_enabled",
                    "reminders_enabled",
                    "new_xp_base",
                    "renewal_xp_base",
                    "default_duration_days",
                    "renewal_duration_days",
                    "default_plan_id",
                    "level_step_xp",
                    "new_xp_jitter",
                    "renewal_xp_jitter",
                    "new_xp_cap",
                    "renewal_xp_cap",
                    "xp_multiplier",
                    "expiry_action",
                    "notification_claim_timeout_minutes",
                )
            }
            bool_keys = {
                "xp_enabled",
                "notifications_enabled",
                "enabled",
                "new_subscription_enabled",
                "renewal_enabled",
                "expiry_enabled",
                "expiry_detection_enabled",
                "reminders_enabled",
            }
            int_limits = {
                "new_xp_base": (0, 100_000),
                "renewal_xp_base": (0, 100_000),
                "default_duration_days": (1, MAX_DURATION_DAYS),
                "renewal_duration_days": (1, MAX_DURATION_DAYS),
                "level_step_xp": (0, 100_000),
                "new_xp_jitter": (0, 100_000),
                "renewal_xp_jitter": (0, 100_000),
                "new_xp_cap": (0, 100_000),
                "renewal_xp_cap": (0, 100_000),
                "notification_claim_timeout_minutes": (1, 120),
            }
            for key in bool_keys & changes.keys():
                if not isinstance(changes[key], bool):
                    raise ValueError(f"{key} must be boolean")
                values[key] = changes[key]
            for key, (minimum, maximum) in int_limits.items():
                if key not in changes:
                    continue
                value = changes[key]
                if isinstance(value, bool):
                    raise ValueError(f"{key} must be an integer")
                try:
                    value = int(value)
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError(f"{key} must be an integer") from error
                if value < minimum or value > maximum:
                    raise ValueError(f"{key} must be between {minimum} and {maximum}")
                values[key] = value
            if "xp_multiplier" in changes:
                value = changes["xp_multiplier"]
                if isinstance(value, bool):
                    raise ValueError("xp_multiplier must be a number")
                try:
                    value = float(value)
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError("xp_multiplier must be a number") from error
                if not 0 <= value <= 100:
                    raise ValueError("xp_multiplier must be between 0 and 100")
                values["xp_multiplier"] = value
            if "expiry_action" in changes:
                action = str(changes["expiry_action"])
                if action not in {"expire", "cancel", "keep_active"}:
                    raise ValueError("expiry_action must be expire, cancel, or keep_active")
                values["expiry_action"] = action
            if "default_plan_id" in changes:
                plan_id = str(changes["default_plan_id"] or "").strip() or None
                if plan_id and len(plan_id) > 100:
                    raise ValueError("default_plan_id cannot exceed 100 characters")
                if plan_id:
                    async with db.execute(
                        """
                        SELECT 1 FROM subscription_plans
                        WHERE guild_id = ? AND plan_id = ? AND enabled = 1
                        """,
                        (guild_id, plan_id),
                    ) as cursor:
                        if not await cursor.fetchone():
                            raise ValueError("default_plan_id must reference an enabled plan")
                values["default_plan_id"] = plan_id

            notification_rules = json.loads(
                json.dumps(current["notification_rules"], ensure_ascii=False)
            )
            if "notification_rules" in changes:
                supplied_rules = changes["notification_rules"]
                if not isinstance(supplied_rules, dict):
                    raise ValueError("notification_rules must be an object")
                unknown_events = set(supplied_rules) - set(NOTIFICATION_EVENTS)
                if unknown_events:
                    raise ValueError(
                        "unsupported notification event(s): "
                        + ", ".join(sorted(unknown_events))
                    )
                for event, supplied in supplied_rules.items():
                    if not isinstance(supplied, dict):
                        raise ValueError(f"notification_rules.{event} must be an object")
                    allowed_rule_keys = {
                        "enabled", "dm_enabled", "channel_enabled", "channel_id",
                        "template_id",
                    }
                    unknown_rule_keys = set(supplied) - allowed_rule_keys
                    if unknown_rule_keys:
                        raise ValueError(
                            f"unsupported {event} notification setting(s): "
                            + ", ".join(sorted(unknown_rule_keys))
                        )
                    rule = notification_rules[event]
                    for key in ("enabled", "dm_enabled", "channel_enabled"):
                        if key in supplied:
                            if not isinstance(supplied[key], bool):
                                raise ValueError(f"{event}.{key} must be boolean")
                            rule[key] = supplied[key]
                    if "channel_id" in supplied:
                        raw_channel_id = supplied["channel_id"]
                        if raw_channel_id in (None, ""):
                            rule["channel_id"] = None
                        else:
                            text_id = str(raw_channel_id).strip()
                            if not text_id.isdigit() or not 15 <= len(text_id) <= 22:
                                raise ValueError(f"{event}.channel_id must be a Discord channel ID")
                            rule["channel_id"] = text_id
                    if "template_id" in supplied:
                        template_id = str(supplied["template_id"] or "").strip() or None
                        if template_id and len(template_id) > 100:
                            raise ValueError(f"{event}.template_id cannot exceed 100 characters")
                        if template_id:
                            async with db.execute(
                                """
                                SELECT 1 FROM subscription_templates
                                WHERE guild_id = ? AND template_id = ? AND enabled = 1
                                """,
                                (guild_id, template_id),
                            ) as cursor:
                                if not await cursor.fetchone():
                                    raise ValueError(f"{event}.template_id must reference an enabled template")
                        rule["template_id"] = template_id

            if "templates" in changes:
                supplied = changes["templates"]
                if not isinstance(supplied, dict):
                    raise ValueError("templates must be an object")
                unknown_templates = set(supplied) - set(DEFAULT_NOTIFICATION_TEMPLATES)
                if unknown_templates:
                    raise ValueError(
                        "unsupported notification templates: "
                        + ", ".join(sorted(unknown_templates))
                    )
                for key, value in supplied.items():
                    content = _validate_template(value)
                    current["notification_templates"][key] = content
                    await db.execute(
                        """
                        UPDATE subscription_templates
                        SET content = ?, updated_at = ?
                        WHERE guild_id = ? AND event_type = ? AND is_default = 1
                        """,
                        (content, now_text, guild_id, key),
                    )
            if changes:
                await db.execute(
                    """
                    UPDATE subscription_guild_settings
                    SET xp_enabled = ?, notifications_enabled = ?, enabled = ?,
                        new_subscription_enabled = ?, renewal_enabled = ?,
                        expiry_enabled = ?, expiry_detection_enabled = ?,
                        reminders_enabled = ?, new_xp_base = ?,
                        renewal_xp_base = ?, default_duration_days = ?,
                        renewal_duration_days = ?, default_plan_id = ?,
                        level_step_xp = ?, new_xp_jitter = ?,
                        renewal_xp_jitter = ?, new_xp_cap = ?,
                        renewal_xp_cap = ?, xp_multiplier = ?, expiry_action = ?,
                        notification_claim_timeout_minutes = ?,
                        notification_templates = ?, notification_rules_json = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE guild_id = ?
                    """,
                    (
                        int(values["xp_enabled"]),
                        int(values["notifications_enabled"]),
                        int(values["enabled"]),
                        int(values["new_subscription_enabled"]),
                        int(values["renewal_enabled"]),
                        int(values["expiry_enabled"]),
                        int(values["expiry_detection_enabled"]),
                        int(values["reminders_enabled"]),
                        values["new_xp_base"],
                        values["renewal_xp_base"],
                        values["default_duration_days"],
                        values["renewal_duration_days"],
                        values["default_plan_id"],
                        values["level_step_xp"],
                        values["new_xp_jitter"],
                        values["renewal_xp_jitter"],
                        values["new_xp_cap"],
                        values["renewal_xp_cap"],
                        values["xp_multiplier"],
                        values["expiry_action"],
                        values["notification_claim_timeout_minutes"],
                        json.dumps(current["notification_templates"], ensure_ascii=False, sort_keys=True),
                        json.dumps(notification_rules, ensure_ascii=False, sort_keys=True),
                        now_text,
                        guild_id,
                    ),
                )
                if actor_id is not None:
                    audited_after = {
                        **current,
                        **values,
                        "notification_templates": current["notification_templates"],
                        "notification_rules": notification_rules,
                        "revision": current["revision"] + 1,
                        "updated_at": now_text,
                    }
                    await _write_control_audit(
                        db,
                        guild_id=guild_id,
                        entity_type="settings",
                        entity_id=str(guild_id),
                        operation="update",
                        actor_id=actor_id,
                        before=before_settings,
                        after=audited_after,
                        now=now_text,
                    )
            await db.commit()
            if not changes:
                return current
        except BaseException:
            await db.rollback()
            raise
    return await get_subscription_settings(guild_id)


async def list_subscription_plans(guild_id: int) -> list[dict[str, Any]]:
    guild_id = _positive_id(guild_id, "guild_id")
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM subscription_plans
            WHERE guild_id = ?
            ORDER BY enabled DESC, name COLLATE NOCASE ASC
            """,
            (guild_id,),
        ) as cursor:
            return [_plan_from_row(row) for row in await cursor.fetchall()]


async def _validate_plan_notification_overrides(db, guild_id: int, value: Any):
    if not isinstance(value, dict):
        raise ValueError("notification_overrides must be an object")
    result: dict[str, dict[str, Any]] = {}
    for event, override in value.items():
        if event not in NOTIFICATION_EVENTS or not isinstance(override, dict):
            raise ValueError("notification_overrides contains an unsupported event")
        allowed = {
            "enabled", "dm_enabled", "channel_enabled", "channel_id", "template_id"
        }
        unknown = set(override) - allowed
        if unknown:
            raise ValueError(
                "unsupported plan notification setting(s): "
                + ", ".join(sorted(unknown))
            )
        clean: dict[str, Any] = {}
        for key in ("enabled", "dm_enabled", "channel_enabled"):
            if key in override:
                if not isinstance(override[key], bool):
                    raise ValueError(f"{event}.{key} must be boolean")
                clean[key] = override[key]
        if "channel_id" in override:
            raw = override["channel_id"]
            if raw in (None, ""):
                clean["channel_id"] = None
            else:
                text = str(raw).strip()
                if not text.isdigit() or not 15 <= len(text) <= 22:
                    raise ValueError(f"{event}.channel_id must be a Discord channel ID")
                clean["channel_id"] = text
        if "template_id" in override:
            template_id = str(override["template_id"] or "").strip() or None
            if template_id:
                async with db.execute(
                    """
                    SELECT 1 FROM subscription_templates
                    WHERE guild_id = ? AND template_id = ? AND event_type = ?
                      AND enabled = 1
                    """,
                    (guild_id, template_id, event),
                ) as cursor:
                    if not await cursor.fetchone():
                        raise ValueError(f"{event}.template_id must reference an enabled template")
            clean["template_id"] = template_id
        result[event] = clean
    return result


async def save_subscription_plan(
    guild_id: int,
    changes: dict[str, Any],
    *,
    actor_id: int,
    plan_id: str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    actor_id = _actor_id(actor_id)
    if not isinstance(changes, dict):
        raise ValueError("plan changes must be an object")
    allowed = {
        "name", "description", "duration_days", "price_cents", "currency",
        "xp_enabled", "new_xp_base", "renewal_xp_base", "xp_multiplier",
        "notifications_enabled", "reminders_enabled", "expiry_action",
        "notification_overrides", "enabled",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("unsupported plan fields: " + ", ".join(sorted(unknown)))
    if not changes and not plan_id:
        raise ValueError("plan changes cannot be empty")
    now_text = _iso(now)
    plan_id = str(plan_id or f"plan_{uuid.uuid4().hex}").strip()
    if not plan_id or len(plan_id) > 100:
        raise ValueError("plan_id must contain 1 to 100 characters")
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT * FROM subscription_plans WHERE guild_id = ? AND plan_id = ?",
                (guild_id, plan_id),
            ) as cursor:
                existing_row = await cursor.fetchone()
            before = _plan_from_row(existing_row)
            if not before:
                async with db.execute(
                    "SELECT 1 FROM subscription_plans WHERE plan_id = ?",
                    (plan_id,),
                ) as cursor:
                    if await cursor.fetchone():
                        raise ValueError("plan_id already belongs to another server")
                values: dict[str, Any] = {
                    "name": "New plan",
                    "description": "",
                    "duration_days": 30,
                    "price_cents": None,
                    "currency": "USD",
                    "xp_enabled": True,
                    "new_xp_base": None,
                    "renewal_xp_base": None,
                    "xp_multiplier": None,
                    "notifications_enabled": True,
                    "reminders_enabled": True,
                    "expiry_action": None,
                    "notification_overrides": {},
                    "enabled": True,
                }
            else:
                values = {key: before[key] for key in allowed}
            values.update(changes)

            name, normalized_name = _normalized_name(values["name"])
            description = values["description"]
            if not isinstance(description, str) or len(description) > 500:
                raise ValueError("description cannot exceed 500 characters")
            duration_days = _duration(values["duration_days"])
            price_cents = values["price_cents"]
            if price_cents is not None:
                if isinstance(price_cents, bool):
                    raise ValueError("price_cents must be a non-negative integer or null")
                try:
                    price_cents = int(price_cents)
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError("price_cents must be a non-negative integer or null") from error
                if price_cents < 0 or price_cents > 1_000_000_000:
                    raise ValueError("price_cents must be between 0 and 1000000000")
            currency = str(values["currency"] or "").strip().upper()
            if len(currency) != 3 or not currency.isalpha() or not currency.isascii():
                raise ValueError("currency must be a three-letter code")
            bool_values = {}
            for key in ("xp_enabled", "notifications_enabled", "reminders_enabled", "enabled"):
                if not isinstance(values[key], bool):
                    raise ValueError(f"{key} must be boolean")
                bool_values[key] = values[key]
            xp_values = {}
            for key in ("new_xp_base", "renewal_xp_base"):
                value = values[key]
                if value is None:
                    xp_values[key] = None
                    continue
                if isinstance(value, bool):
                    raise ValueError(f"{key} must be an integer or null")
                try:
                    value = int(value)
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError(f"{key} must be an integer or null") from error
                if value < 0 or value > 100_000:
                    raise ValueError(f"{key} must be between 0 and 100000")
                xp_values[key] = value
            multiplier = values["xp_multiplier"]
            if multiplier is not None:
                if isinstance(multiplier, bool):
                    raise ValueError("xp_multiplier must be a number or null")
                try:
                    multiplier = float(multiplier)
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError("xp_multiplier must be a number or null") from error
                if not 0 <= multiplier <= 100:
                    raise ValueError("xp_multiplier must be between 0 and 100")
            expiry_action = values["expiry_action"]
            if expiry_action not in (None, "", "expire", "cancel", "keep_active"):
                raise ValueError("expiry_action must be expire, cancel, keep_active, or null")
            expiry_action = expiry_action or None
            overrides = await _validate_plan_notification_overrides(
                db, guild_id, values["notification_overrides"]
            )
            async with db.execute(
                """
                SELECT 1 FROM subscription_plans
                WHERE guild_id = ? AND normalized_name = ? AND plan_id != ?
                """,
                (guild_id, normalized_name, plan_id),
            ) as cursor:
                if await cursor.fetchone():
                    raise ValueError("a plan with this name already exists")
            await db.execute(
                """
                INSERT INTO subscription_plans
                    (plan_id, guild_id, name, normalized_name, description,
                     duration_days, price_cents, currency, xp_enabled,
                     new_xp_base, renewal_xp_base, xp_multiplier,
                     notifications_enabled, reminders_enabled, expiry_action,
                     notification_overrides_json, enabled, created_by,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    name = excluded.name,
                    normalized_name = excluded.normalized_name,
                    description = excluded.description,
                    duration_days = excluded.duration_days,
                    price_cents = excluded.price_cents,
                    currency = excluded.currency,
                    xp_enabled = excluded.xp_enabled,
                    new_xp_base = excluded.new_xp_base,
                    renewal_xp_base = excluded.renewal_xp_base,
                    xp_multiplier = excluded.xp_multiplier,
                    notifications_enabled = excluded.notifications_enabled,
                    reminders_enabled = excluded.reminders_enabled,
                    expiry_action = excluded.expiry_action,
                    notification_overrides_json = excluded.notification_overrides_json,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    plan_id, guild_id, name, normalized_name, description,
                    duration_days, price_cents, currency, int(bool_values["xp_enabled"]),
                    xp_values["new_xp_base"], xp_values["renewal_xp_base"], multiplier,
                    int(bool_values["notifications_enabled"]),
                    int(bool_values["reminders_enabled"]), expiry_action,
                    json.dumps(overrides, ensure_ascii=False, sort_keys=True),
                    int(bool_values["enabled"]), actor_id, now_text, now_text,
                ),
            )
            async with db.execute(
                "SELECT * FROM subscription_plans WHERE guild_id = ? AND plan_id = ?",
                (guild_id, plan_id),
            ) as cursor:
                after = _plan_from_row(await cursor.fetchone())
            await _write_control_audit(
                db,
                guild_id=guild_id,
                entity_type="plan",
                entity_id=plan_id,
                operation="update" if before else "create",
                actor_id=actor_id,
                before=before,
                after=after,
                now=now_text,
            )
            await db.commit()
            return after
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise


async def disable_subscription_plan(
    guild_id: int,
    plan_id: str,
    *,
    actor_id: int,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    actor_id = _actor_id(actor_id)
    plan_id = str(plan_id or "").strip()
    now_text = _iso(now)
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT * FROM subscription_plans WHERE guild_id = ? AND plan_id = ?",
                (guild_id, plan_id),
            ) as cursor:
                before = _plan_from_row(await cursor.fetchone())
            if not before:
                raise ValueError("plan not found in this server")
            if before["enabled"]:
                await db.execute(
                    "UPDATE subscription_plans SET enabled = 0, updated_at = ? WHERE guild_id = ? AND plan_id = ?",
                    (now_text, guild_id, plan_id),
                )
            async with db.execute(
                "SELECT * FROM subscription_plans WHERE guild_id = ? AND plan_id = ?",
                (guild_id, plan_id),
            ) as cursor:
                after = _plan_from_row(await cursor.fetchone())
            await _write_control_audit(
                db, guild_id=guild_id, entity_type="plan", entity_id=plan_id,
                operation="disable", actor_id=actor_id, before=before, after=after,
                now=now_text,
            )
            async with db.execute(
                "SELECT default_plan_id FROM subscription_guild_settings WHERE guild_id = ?",
                (guild_id,),
            ) as cursor:
                settings_row = await cursor.fetchone()
            if settings_row and settings_row["default_plan_id"] == plan_id:
                await db.execute(
                    """
                    UPDATE subscription_guild_settings
                    SET default_plan_id = NULL, revision = revision + 1, updated_at = ?
                    WHERE guild_id = ?
                    """,
                    (now_text, guild_id),
                )
            await db.commit()
            return after
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise


async def list_subscription_reminders(guild_id: int) -> list[dict[str, Any]]:
    guild_id = _positive_id(guild_id, "guild_id")
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM subscription_reminder_rules
            WHERE guild_id = ?
            ORDER BY enabled DESC, hours_before DESC, name COLLATE NOCASE
            """,
            (guild_id,),
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        row["enabled"] = bool(row["enabled"])
        row["dm_enabled"] = bool(row["dm_enabled"])
        row["channel_enabled"] = bool(row["channel_enabled"])
        row["conditions"] = _json_object(row.pop("conditions_json", "{}"))
    return rows


async def save_subscription_reminder(
    guild_id: int,
    changes: dict[str, Any],
    *,
    actor_id: int,
    reminder_id: str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    actor_id = _actor_id(actor_id)
    if not isinstance(changes, dict):
        raise ValueError("reminder changes must be an object")
    allowed = {
        "name", "hours_before", "enabled", "dm_enabled", "channel_enabled",
        "channel_id", "template_id", "conditions",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("unsupported reminder fields: " + ", ".join(sorted(unknown)))
    reminder_id = str(reminder_id or f"rem_{uuid.uuid4().hex}").strip()
    if not reminder_id or len(reminder_id) > 100:
        raise ValueError("reminder_id must contain 1 to 100 characters")
    now_text = _iso(now)
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT * FROM subscription_reminder_rules WHERE guild_id = ? AND reminder_id = ?",
                (guild_id, reminder_id),
            ) as cursor:
                old = await cursor.fetchone()
            before = dict(old) if old else None
            if not before:
                async with db.execute(
                    "SELECT 1 FROM subscription_reminder_rules WHERE reminder_id = ?",
                    (reminder_id,),
                ) as cursor:
                    if await cursor.fetchone():
                        raise ValueError("reminder_id already belongs to another server")
                values = {
                    "name": "New reminder", "hours_before": 24, "enabled": True,
                    "dm_enabled": True, "channel_enabled": False,
                    "channel_id": None, "template_id": None, "conditions": {},
                }
            else:
                values = {
                    "name": before["name"], "hours_before": before["hours_before"],
                    "enabled": bool(before["enabled"]),
                    "dm_enabled": bool(before["dm_enabled"]),
                    "channel_enabled": bool(before["channel_enabled"]),
                    "channel_id": before["channel_id"],
                    "template_id": before["template_id"],
                    "conditions": _json_object(before["conditions_json"]),
                }
            values.update(changes)
            name, _ = _normalized_name(values["name"])
            hours = values["hours_before"]
            if isinstance(hours, bool):
                raise ValueError("hours_before must be an integer")
            try:
                hours = int(hours)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError("hours_before must be an integer") from error
            if not 1 <= hours <= MAX_REMINDER_HOURS:
                raise ValueError(f"hours_before must be between 1 and {MAX_REMINDER_HOURS}")
            for key in ("enabled", "dm_enabled", "channel_enabled"):
                if not isinstance(values[key], bool):
                    raise ValueError(f"{key} must be boolean")
            channel_id = values["channel_id"]
            if channel_id in (None, ""):
                channel_id = None
            else:
                channel_id = str(channel_id).strip()
                if not channel_id.isdigit() or not 15 <= len(channel_id) <= 22:
                    raise ValueError("channel_id must be a Discord channel ID")
            if values["channel_enabled"] and not channel_id:
                raise ValueError("channel_id is required when channel delivery is enabled")
            template_id = str(values["template_id"] or "").strip() or None
            if template_id:
                async with db.execute(
                    """
                    SELECT 1 FROM subscription_templates
                    WHERE guild_id = ? AND template_id = ? AND event_type = 'expiring'
                      AND enabled = 1
                    """,
                    (guild_id, template_id),
                ) as cursor:
                    if not await cursor.fetchone():
                        raise ValueError("template_id must reference an enabled expiry template")
            conditions = values["conditions"]
            if not isinstance(conditions, dict) or set(conditions) - {"plan_ids"}:
                raise ValueError("conditions may contain only plan_ids")
            plan_ids = conditions.get("plan_ids", [])
            if not isinstance(plan_ids, list) or len(plan_ids) > 50:
                raise ValueError("conditions.plan_ids must be a list of at most 50 plan IDs")
            clean_plan_ids = []
            for value in plan_ids:
                item = str(value).strip()
                if not item or len(item) > 100:
                    raise ValueError("conditions.plan_ids contains an invalid plan ID")
                async with db.execute(
                    "SELECT 1 FROM subscription_plans WHERE guild_id = ? AND plan_id = ?",
                    (guild_id, item),
                ) as cursor:
                    if not await cursor.fetchone():
                        raise ValueError("conditions.plan_ids must reference plans in this server")
                clean_plan_ids.append(item)
            conditions = {"plan_ids": clean_plan_ids} if clean_plan_ids else {}
            if values["enabled"]:
                async with db.execute(
                    """
                    SELECT COUNT(*) FROM subscription_reminder_rules
                    WHERE guild_id = ? AND enabled = 1 AND reminder_id != ?
                    """,
                    (guild_id, reminder_id),
                ) as cursor:
                    count = int((await cursor.fetchone())[0])
                if count >= MAX_REMINDER_RULES:
                    raise ValueError(f"at most {MAX_REMINDER_RULES} enabled reminder rules are allowed")
            await db.execute(
                """
                INSERT INTO subscription_reminder_rules
                    (reminder_id, guild_id, name, hours_before, enabled, dm_enabled,
                     channel_enabled, channel_id, template_id, conditions_json,
                     created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reminder_id) DO UPDATE SET
                    name = excluded.name, hours_before = excluded.hours_before,
                    enabled = excluded.enabled, dm_enabled = excluded.dm_enabled,
                    channel_enabled = excluded.channel_enabled,
                    channel_id = excluded.channel_id, template_id = excluded.template_id,
                    conditions_json = excluded.conditions_json, updated_at = excluded.updated_at
                """,
                (
                    reminder_id, guild_id, name, hours, int(values["enabled"]),
                    int(values["dm_enabled"]), int(values["channel_enabled"]),
                    channel_id, template_id,
                    json.dumps(conditions, ensure_ascii=False, sort_keys=True),
                    actor_id, now_text, now_text,
                ),
            )
            async with db.execute(
                "SELECT * FROM subscription_reminder_rules WHERE guild_id = ? AND reminder_id = ?",
                (guild_id, reminder_id),
            ) as cursor:
                after_row = dict(await cursor.fetchone())
            await _write_control_audit(
                db, guild_id=guild_id, entity_type="reminder", entity_id=reminder_id,
                operation="update" if before else "create", actor_id=actor_id,
                before=before, after=after_row, now=now_text,
            )
            await db.commit()
            result = after_row
            result["enabled"] = bool(result["enabled"])
            result["dm_enabled"] = bool(result["dm_enabled"])
            result["channel_enabled"] = bool(result["channel_enabled"])
            result["conditions"] = _json_object(result.pop("conditions_json", "{}"))
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise


async def disable_subscription_reminder(
    guild_id: int,
    reminder_id: str,
    *,
    actor_id: int,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    actor_id = _actor_id(actor_id)
    reminder_id = str(reminder_id or "").strip()
    now_text = _iso(now)
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT * FROM subscription_reminder_rules WHERE guild_id = ? AND reminder_id = ?",
                (guild_id, reminder_id),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                raise ValueError("reminder not found in this server")
            before = dict(row)
            await db.execute(
                "UPDATE subscription_reminder_rules SET enabled = 0, updated_at = ? WHERE guild_id = ? AND reminder_id = ?",
                (now_text, guild_id, reminder_id),
            )
            async with db.execute(
                "SELECT * FROM subscription_reminder_rules WHERE guild_id = ? AND reminder_id = ?",
                (guild_id, reminder_id),
            ) as cursor:
                after = dict(await cursor.fetchone())
            await _write_control_audit(
                db, guild_id=guild_id, entity_type="reminder", entity_id=reminder_id,
                operation="disable", actor_id=actor_id, before=before, after=after,
                now=now_text,
            )
            await db.commit()
            after["enabled"] = bool(after["enabled"])
            after["dm_enabled"] = bool(after["dm_enabled"])
            after["channel_enabled"] = bool(after["channel_enabled"])
            after["conditions"] = _json_object(after.pop("conditions_json", "{}"))
            return after
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise


async def list_subscription_templates(guild_id: int) -> list[dict[str, Any]]:
    guild_id = _positive_id(guild_id, "guild_id")
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM subscription_templates
            WHERE guild_id = ?
            ORDER BY event_type, is_default DESC, name COLLATE NOCASE
            """,
            (guild_id,),
        ) as cursor:
            return [
                {**dict(row), "enabled": bool(row["enabled"]), "is_default": bool(row["is_default"])}
                for row in await cursor.fetchall()
            ]


async def get_subscription_template(
    guild_id: int, template_id: str | None
) -> dict[str, Any] | None:
    guild_id = _positive_id(guild_id, "guild_id")
    if not template_id:
        return None
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM subscription_templates
            WHERE guild_id = ? AND template_id = ? AND enabled = 1
            """,
            (guild_id, str(template_id)),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["is_default"] = bool(result["is_default"])
    return result


async def save_subscription_template(
    guild_id: int,
    changes: dict[str, Any],
    *,
    actor_id: int,
    template_id: str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    actor_id = _actor_id(actor_id)
    if not isinstance(changes, dict):
        raise ValueError("template changes must be an object")
    allowed = {"name", "event_type", "content", "enabled"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("unsupported template fields: " + ", ".join(sorted(unknown)))
    template_id = str(template_id or f"tpl_{uuid.uuid4().hex}").strip()
    if not template_id or len(template_id) > 100:
        raise ValueError("template_id must contain 1 to 100 characters")
    now_text = _iso(now)
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT * FROM subscription_templates WHERE guild_id = ? AND template_id = ?",
                (guild_id, template_id),
            ) as cursor:
                old_row = await cursor.fetchone()
            before = dict(old_row) if old_row else None
            if not before:
                async with db.execute(
                    "SELECT 1 FROM subscription_templates WHERE template_id = ?",
                    (template_id,),
                ) as cursor:
                    if await cursor.fetchone():
                        raise ValueError("template_id already belongs to another server")
                values = {
                    "name": "New template", "event_type": "created",
                    "content": DEFAULT_NOTIFICATION_TEMPLATES["created"], "enabled": True,
                }
            else:
                values = {
                    "name": before["name"], "event_type": before["event_type"],
                    "content": before["content"], "enabled": bool(before["enabled"]),
                }
            values.update(changes)
            name, normalized_name = _normalized_name(values["name"])
            event_type = str(values["event_type"])
            if event_type not in NOTIFICATION_EVENTS:
                raise ValueError("event_type must be one of " + ", ".join(NOTIFICATION_EVENTS))
            content = _validate_template(values["content"])
            if not isinstance(values["enabled"], bool):
                raise ValueError("enabled must be boolean")
            if before and before["event_type"] != event_type:
                if before["is_default"]:
                    raise ValueError("default template event_type cannot be changed")
                async with db.execute(
                    """
                    SELECT 1 FROM subscription_templates
                    WHERE guild_id = ? AND template_id = ? AND enabled = 1
                    """,
                    (guild_id, template_id),
                ) as cursor:
                    pass
                # Keep event type stable once the template has been selected by
                # a notification rule or queued notification.
                if await _template_is_referenced(db, guild_id, template_id):
                    raise ValueError("template event_type cannot change while it is in use")
            if before and before["is_default"]:
                name = before["name"]
                normalized_name = before["normalized_name"]
            async with db.execute(
                """
                SELECT 1 FROM subscription_templates
                WHERE guild_id = ? AND normalized_name = ? AND template_id != ?
                """,
                (guild_id, normalized_name, template_id),
            ) as cursor:
                if await cursor.fetchone():
                    raise ValueError("a template with this name already exists")
            if before and before["is_default"] and not values["enabled"]:
                raise ValueError("default templates cannot be disabled")
            await db.execute(
                """
                INSERT INTO subscription_templates
                    (template_id, guild_id, name, normalized_name, event_type,
                     content, enabled, is_default, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(template_id) DO UPDATE SET
                    name = excluded.name, normalized_name = excluded.normalized_name,
                    event_type = excluded.event_type, content = excluded.content,
                    enabled = excluded.enabled, updated_at = excluded.updated_at
                """,
                (
                    template_id, guild_id, name, normalized_name, event_type,
                    content, int(values["enabled"]), actor_id, now_text, now_text,
                ),
            )
            if before and before["is_default"]:
                settings = await _ensure_settings(db, guild_id, now_text)
                templates = dict(settings["notification_templates"])
                templates[event_type] = content
                await db.execute(
                    """
                    UPDATE subscription_guild_settings
                    SET notification_templates = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE guild_id = ?
                    """,
                    (
                        json.dumps(templates, ensure_ascii=False, sort_keys=True),
                        now_text, guild_id,
                    ),
                )
            async with db.execute(
                "SELECT * FROM subscription_templates WHERE guild_id = ? AND template_id = ?",
                (guild_id, template_id),
            ) as cursor:
                after = dict(await cursor.fetchone())
            await _write_control_audit(
                db, guild_id=guild_id, entity_type="template", entity_id=template_id,
                operation="update" if before else "create", actor_id=actor_id,
                before=before, after=after, now=now_text,
            )
            await db.commit()
            after["enabled"] = bool(after["enabled"])
            after["is_default"] = bool(after["is_default"])
            return after
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise


async def _template_is_referenced(db, guild_id: int, template_id: str) -> bool:
    async with db.execute(
        "SELECT notification_rules_json FROM subscription_guild_settings WHERE guild_id = ?",
        (guild_id,),
    ) as cursor:
        settings_row = await cursor.fetchone()
    rules = _json_object(settings_row["notification_rules_json"]) if settings_row else {}
    if any(
        isinstance(rule, dict) and rule.get("template_id") == template_id
        for rule in rules.values()
    ):
        return True
    async with db.execute(
        """
        SELECT 1 FROM subscription_reminder_rules
        WHERE guild_id = ? AND enabled = 1 AND template_id = ?
        LIMIT 1
        """,
        (guild_id, template_id),
    ) as cursor:
        if await cursor.fetchone():
            return True
    async with db.execute(
        """
        SELECT 1 FROM subscription_plans
        WHERE guild_id = ? AND enabled = 1 AND notification_overrides_json LIKE ?
        LIMIT 1
        """,
        (guild_id, f'%"{template_id}"%'),
    ) as cursor:
        return bool(await cursor.fetchone())


async def disable_subscription_template(
    guild_id: int,
    template_id: str,
    *,
    actor_id: int,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    actor_id = _actor_id(actor_id)
    template_id = str(template_id or "").strip()
    now_text = _iso(now)
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT * FROM subscription_templates WHERE guild_id = ? AND template_id = ?",
                (guild_id, template_id),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                raise ValueError("template not found in this server")
            before = dict(row)
            if before["is_default"]:
                raise ValueError("default templates cannot be disabled")
            if await _template_is_referenced(db, guild_id, template_id):
                raise ValueError("template is selected by an active notification rule")
            await db.execute(
                "UPDATE subscription_templates SET enabled = 0, updated_at = ? WHERE guild_id = ? AND template_id = ?",
                (now_text, guild_id, template_id),
            )
            async with db.execute(
                "SELECT * FROM subscription_templates WHERE guild_id = ? AND template_id = ?",
                (guild_id, template_id),
            ) as cursor:
                after = dict(await cursor.fetchone())
            await _write_control_audit(
                db, guild_id=guild_id, entity_type="template", entity_id=template_id,
                operation="disable", actor_id=actor_id, before=before, after=after,
                now=now_text,
            )
            await db.commit()
            after["enabled"] = bool(after["enabled"])
            after["is_default"] = bool(after["is_default"])
            return after
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise


async def _get_subscription(db, subscription_id: str, guild_id: int | None = None):
    if guild_id is None:
        query = "SELECT * FROM subscriptions WHERE subscription_id = ?"
        params = (str(subscription_id),)
    else:
        query = (
            "SELECT * FROM subscriptions "
            "WHERE subscription_id = ? AND guild_id = ?"
        )
        params = (str(subscription_id), int(guild_id))
    async with db.execute(query, params) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else None


def _plan_from_row(row) -> dict[str, Any] | None:
    if row is None:
        return None
    plan = dict(row)
    plan["enabled"] = bool(plan["enabled"])
    plan["xp_enabled"] = bool(plan["xp_enabled"])
    plan["notifications_enabled"] = bool(plan["notifications_enabled"])
    plan["reminders_enabled"] = bool(plan["reminders_enabled"])
    plan["notification_overrides"] = _json_object(
        plan.pop("notification_overrides_json", "{}")
    )
    return plan


async def _get_plan(
    db, guild_id: int, plan_id: str | None, *, enabled_only: bool = False
) -> dict[str, Any] | None:
    if not plan_id:
        return None
    query = (
        "SELECT * FROM subscription_plans WHERE guild_id = ? AND plan_id = ?"
    )
    params: tuple[Any, ...] = (int(guild_id), str(plan_id))
    if enabled_only:
        query += " AND enabled = 1"
    async with db.execute(query, params) as cursor:
        return _plan_from_row(await cursor.fetchone())


def _effective_notification_rule(
    settings: dict[str, Any],
    event_type: str,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rule = dict(settings["notification_rules"].get(event_type, {}))
    if plan:
        if not plan["notifications_enabled"]:
            rule["enabled"] = False
        if event_type == "expiring" and not plan["reminders_enabled"]:
            rule["enabled"] = False
        overrides = plan.get("notification_overrides") or {}
        event_override = overrides.get(event_type)
        if isinstance(event_override, dict):
            rule.update(event_override)
    return rule


async def _find_operation(db, idempotency_key: str, request_hash: str):
    async with db.execute(
        "SELECT * FROM subscription_history WHERE idempotency_key = ?",
        (idempotency_key,),
    ) as cursor:
        history = await cursor.fetchone()
    if not history:
        return None
    if history["request_hash"] and history["request_hash"] != request_hash:
        raise ValueError("idempotency key was already used for a different request")
    return dict(history)


async def _xp_for_event(db, subscription_id: str, event_type: str):
    async with db.execute(
        """
        SELECT * FROM subscription_xp_transactions
        WHERE subscription_id = ? AND event_type = ?
        ORDER BY timestamp ASC LIMIT 1
        """,
        (str(subscription_id), str(event_type)),
    ) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else None


async def _xp_for_source(db, source_id: str, event_type: str):
    async with db.execute(
        """
        SELECT * FROM subscription_xp_transactions
        WHERE source_id = ? AND event_type = ?
        """,
        (str(source_id), str(event_type)),
    ) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else None


async def _write_history(
    db,
    *,
    event_key: str,
    idempotency_key: str | None,
    request_hash: str = "",
    subscription: dict[str, Any],
    event_type: str,
    actor_id: int | None,
    previous_status: str | None,
    details: dict[str, Any] | None,
    now: str,
) -> None:
    await db.execute(
        """
        INSERT OR IGNORE INTO subscription_history
            (event_key, idempotency_key, request_hash, subscription_id,
             guild_id, user_id, event_type, actor_id, previous_status, status,
             start_date, end_date, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            idempotency_key,
            request_hash,
            subscription["subscription_id"],
            int(subscription["guild_id"]),
            int(subscription["user_id"]),
            event_type,
            actor_id,
            previous_status,
            subscription["status"],
            subscription["start_date"],
            subscription["end_date"],
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
            now,
        ),
    )


async def _queue_notification(
    db,
    *,
    event_key: str,
    subscription: dict[str, Any],
    event_type: str,
    now: str,
    settings: dict[str, Any],
    plan: dict[str, Any] | None = None,
    reminder_hours: int | None = None,
    reminder_id: str | None = None,
    rule_override: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    rule = _effective_notification_rule(settings, event_type, plan)
    if rule_override:
        rule.update(rule_override)
    if (
        not settings["enabled"]
        or not settings["notifications_enabled"]
        or not rule.get("enabled", True)
        or (event_type == "expiring" and not settings["reminders_enabled"])
        or not (rule.get("dm_enabled") or rule.get("channel_enabled"))
        or (rule.get("channel_enabled") and not rule.get("channel_id"))
    ):
        return False
    notification_payload = dict(payload or {})
    notification_payload.setdefault("template_id", rule.get("template_id"))
    notification_payload.setdefault("channel_id", rule.get("channel_id"))
    notification_payload.setdefault("dm_enabled", bool(rule.get("dm_enabled", True)))
    notification_payload.setdefault(
        "channel_enabled", bool(rule.get("channel_enabled", False))
    )
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO subscription_notifications
            (notification_id, event_key, subscription_id, guild_id, user_id,
             event_type, reference_end_date, reminder_hours, reminder_id,
             payload_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            uuid.uuid4().hex,
            event_key,
            subscription["subscription_id"],
            int(subscription["guild_id"]),
            int(subscription["user_id"]),
            event_type,
            subscription["end_date"],
            reminder_hours,
            reminder_id or notification_payload.get("reminder_id"),
            json.dumps(notification_payload, ensure_ascii=False, sort_keys=True),
            now,
        ),
    )
    return cursor.rowcount == 1


async def _award_subscription_xp(
    db,
    *,
    subscription: dict[str, Any],
    event_type: str,
    source_id: str,
    now: str,
    settings: dict[str, Any],
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from level_progression import level_from_xp

    guild_id = int(subscription["guild_id"])
    user_id = int(subscription["user_id"])
    async with db.execute(
        "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ) as cursor:
        level_row = await cursor.fetchone()
    old_xp = int(level_row["text_xp"] or 0) if level_row else 0
    level = max(0, int(level_from_xp(old_xp)))
    is_new = event_type == "created"
    plan_base = (
        plan.get("new_xp_base") if is_new else plan.get("renewal_xp_base")
    ) if plan else None
    base = int(
        plan_base if plan_base is not None
        else settings["new_xp_base"] if is_new
        else settings["renewal_xp_base"]
    )
    level_step = int(settings["level_step_xp"])
    jitter_limit = int(
        settings["new_xp_jitter"] if is_new else settings["renewal_xp_jitter"]
    )
    cap = int(settings["new_xp_cap"] if is_new else settings["renewal_xp_cap"])
    xp_enabled = bool(settings["xp_enabled"]) and (
        plan is None or bool(plan["xp_enabled"])
    )
    multiplier = float(settings["xp_multiplier"]) * (
        float(plan["xp_multiplier"])
        if plan and plan.get("xp_multiplier") is not None
        else 1.0
    )
    random_bonus = secrets.randbelow(jitter_limit + 1) if xp_enabled else 0
    amount = (
        min(cap, round((base + level * level_step + random_bonus) * multiplier))
        if xp_enabled
        else 0
    )
    transaction_id = uuid.uuid4().hex
    ledger_key = f"subscription-xp:{event_type}:{source_id}"
    if amount:
        async with db.execute(
            "SELECT overtake_alert_enabled FROM level_settings WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            level_settings = await cursor.fetchone()
        detect_overtakes = (
            bool(level_settings["overtake_alert_enabled"])
            if level_settings else False
        )
        result = await database._add_level_text_credit(
            db, guild_id, user_id, amount, level_row, detect_overtakes
        )
        await database._record_level_daily_xp(
            db, guild_id, user_id, _utc(now), text_xp=amount
        )
    else:
        current_level = int(level_from_xp(old_xp))
        result = {
            "guild_id": guild_id,
            "user_id": user_id,
            "old_xp": old_xp,
            "old_level": current_level,
            "text_xp": old_xp,
            "text_level": current_level,
            "xp_awarded": 0,
            "overtakes": [],
        }
    await db.execute(
        """
        INSERT INTO subscription_xp_transactions
            (transaction_id, idempotency_key, subscription_id, guild_id,
             user_id, source, source_id, event_type, amount, base_amount,
             level_basis, level_step, random_bonus, cap_amount, timestamp)
        VALUES (?, ?, ?, ?, ?, 'subscription', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            ledger_key,
            subscription["subscription_id"],
            guild_id,
            user_id,
            str(source_id),
            event_type,
            amount,
            base,
            level,
            level_step,
            random_bonus,
            cap,
            now,
        ),
    )
    return {
        **result,
        "transaction_id": transaction_id,
        "idempotency_key": ledger_key,
        "subscription_id": subscription["subscription_id"],
        "source": "subscription",
        "source_id": str(source_id),
        "event_type": event_type,
        "amount": amount,
        "base_amount": base,
        "level_basis": level,
        "level_step": level_step,
        "random_bonus": random_bonus,
        "cap_amount": cap,
        "timestamp": now,
    }


async def _expire_one(
    db, subscription: dict[str, Any], now: str
) -> bool:
    if subscription["status"] != "active" or subscription["end_date"] > now:
        return False
    settings = await _ensure_settings(db, int(subscription["guild_id"]), now)
    if (
        not settings["enabled"]
        or not settings["expiry_enabled"]
        or not settings["expiry_detection_enabled"]
    ):
        return False
    plan = await _get_plan(
        db,
        int(subscription["guild_id"]),
        subscription.get("plan_id"),
    )
    expiry_action = (
        plan.get("expiry_action") if plan and plan.get("expiry_action")
        else settings["expiry_action"]
    )
    if expiry_action == "keep_active":
        return False
    new_status = "cancelled" if expiry_action == "cancel" else "expired"
    previous_status = subscription["status"]
    cursor = await db.execute(
        """
        UPDATE subscriptions
        SET status = ?, updated_at = ?
        WHERE subscription_id = ? AND status = 'active' AND end_date = ?
        """,
        (new_status, now, subscription["subscription_id"], subscription["end_date"]),
    )
    if cursor.rowcount != 1:
        return False
    subscription["status"] = new_status
    subscription["updated_at"] = now
    await _write_history(
        db,
        event_key=f"expired:{subscription['subscription_id']}:{subscription['end_date']}",
        idempotency_key=None,
        subscription=subscription,
        event_type="cancelled" if new_status == "cancelled" else "expired",
        actor_id=None,
        previous_status=previous_status,
        details={
            "end_date": subscription["end_date"],
            "expiry_action": expiry_action,
        },
        now=now,
    )
    await _queue_notification(
        db,
        event_key=f"expired:{subscription['subscription_id']}:{subscription['end_date']}",
        subscription=subscription,
        event_type="expired",
        now=now,
        settings=settings,
        plan=plan,
        payload={"end_date": subscription["end_date"]},
    )
    return True


async def create_subscription(
    guild_id: int,
    user_id: int,
    duration_days: int | None = None,
    *,
    idempotency_key: str,
    actor_id: int | None = None,
    plan_id: str | None = None,
    subscription_id: str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    user_id = _positive_id(user_id, "user_id")
    actor_id = _actor_id(actor_id) if actor_id is not None else None
    duration_days = _duration(duration_days) if duration_days is not None else None
    key = _operation_key(idempotency_key)
    plan_id = str(plan_id).strip() if plan_id is not None else None
    if plan_id and len(plan_id) > 100:
        raise ValueError("plan_id cannot exceed 100 characters")
    plan_id = plan_id or None
    requested_id = str(subscription_id).strip() if subscription_id else None
    if requested_id and len(requested_id) > 100:
        raise ValueError("subscription_id cannot exceed 100 characters")
    now_dt = _utc(now)
    now_text = now_dt.isoformat()
    request_hash = _fingerprint({
        "operation": "create",
        "guild_id": guild_id,
        "user_id": user_id,
        "duration_days": duration_days,
        "plan_id": plan_id,
        "subscription_id": requested_id,
    })
    generated_id = requested_id or f"sub_{uuid.uuid4().hex}"

    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            prior = await _find_operation(db, key, request_hash)
            if prior:
                subscription = await _get_subscription(
                    db, prior["subscription_id"], guild_id
                )
                xp = await _xp_for_event(db, prior["subscription_id"], "created")
                await db.rollback()
                if subscription is None:
                    raise RuntimeError("idempotent subscription record is missing")
                return {
                    "status": "duplicate",
                    "subscription": subscription,
                    "xp": xp,
                    "idempotent": True,
                }
            existing = await _get_subscription(db, generated_id)
            if existing:
                await db.rollback()
                if existing["guild_id"] != guild_id or existing["user_id"] != user_id:
                    raise ValueError("subscription_id already belongs to another member")
                return {
                    "status": "duplicate",
                    "subscription": existing,
                    "xp": await _xp_for_event(db, generated_id, "created"),
                    "idempotent": True,
                }

            settings = await _ensure_settings(db, guild_id, now_text)
            if not settings["enabled"] or not settings["new_subscription_enabled"]:
                raise ValueError("new subscriptions are disabled for this server")
            if plan_id is None:
                plan_id = settings["default_plan_id"]
            plan = await _get_plan(db, guild_id, plan_id, enabled_only=True)
            if plan_id and plan is None:
                raise ValueError("plan is not available for new subscriptions")
            if duration_days is None:
                duration_days = (
                    int(plan["duration_days"])
                    if plan else int(settings["default_duration_days"])
                )
            duration_days = _duration(duration_days)
            end_text = (now_dt + timedelta(days=duration_days)).isoformat()
            subscription = {
                "subscription_id": generated_id,
                "guild_id": guild_id,
                "user_id": user_id,
                "plan_id": plan_id,
                "start_date": now_text,
                "end_date": end_text,
                "status": "active",
                "created_at": now_text,
                "updated_at": now_text,
            }
            await db.execute(
                """
                INSERT INTO subscriptions
                    (subscription_id, guild_id, user_id, plan_id, start_date,
                     end_date, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    generated_id, guild_id, user_id, plan_id, now_text,
                    end_text, now_text, now_text,
                ),
            )
            xp = await _award_subscription_xp(
                db,
                subscription=subscription,
                event_type="created",
                source_id=generated_id,
                now=now_text,
                settings=settings,
                plan=plan,
            )
            await _write_history(
                db,
                event_key=f"created:{generated_id}",
                idempotency_key=key,
                request_hash=request_hash,
                subscription=subscription,
                event_type="created",
                actor_id=actor_id,
                previous_status=None,
                details={
                    "duration_days": duration_days,
                    "plan_id": plan_id,
                    "xp_transaction_id": xp["transaction_id"],
                },
                now=now_text,
            )
            if actor_id is not None:
                await _write_admin_audit(
                    db,
                    event_key=f"created:{generated_id}",
                    idempotency_key=f"subscription:{key}",
                    request_hash=request_hash,
                    subscription=subscription,
                    actor_id=actor_id,
                    before=None,
                    after=subscription,
                    now=now_text,
                )
            await _queue_notification(
                db,
                event_key=f"created:{generated_id}",
                subscription=subscription,
                event_type="created",
                now=now_text,
                settings=settings,
                plan=plan,
                payload={"xp": int(xp["amount"])},
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
    return {
        "status": "created",
        "subscription": subscription,
        "xp": xp,
        "idempotent": False,
    }


async def renew_subscription(
    guild_id: int,
    subscription_id: str,
    duration_days: int | None = None,
    *,
    idempotency_key: str,
    actor_id: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    subscription_id = str(subscription_id or "").strip()
    if not subscription_id or len(subscription_id) > 100:
        raise ValueError("subscription_id must contain 1 to 100 characters")
    duration_days = _duration(duration_days) if duration_days is not None else None
    key = _operation_key(idempotency_key)
    actor_id = _actor_id(actor_id) if actor_id is not None else None
    now_dt = _utc(now)
    now_text = now_dt.isoformat()
    request_hash = _fingerprint({
        "operation": "renew",
        "guild_id": guild_id,
        "subscription_id": subscription_id,
        "duration_days": duration_days,
    })

    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            prior = await _find_operation(db, key, request_hash)
            if prior:
                subscription = await _get_subscription(
                    db, prior["subscription_id"], guild_id
                )
                async with db.execute(
                    "SELECT * FROM subscription_renewals WHERE idempotency_key = ?",
                    (key,),
                ) as cursor:
                    renewal = await cursor.fetchone()
                xp = await _xp_for_source(
                    db, renewal["transaction_id"], "renewal"
                ) if renewal else None
                await db.rollback()
                return {
                    "status": "duplicate",
                    "subscription": subscription,
                    "renewal": dict(renewal) if renewal else None,
                    "xp": xp,
                    "idempotent": True,
                }
            subscription = await _get_subscription(db, subscription_id, guild_id)
            if subscription is None:
                raise ValueError("subscription not found in this server")
            before_admin = dict(subscription)
            if subscription["status"] == "cancelled":
                raise ValueError("cancelled subscriptions cannot be renewed")
            settings = await _ensure_settings(db, guild_id, now_text)
            if not settings["enabled"] or not settings["renewal_enabled"]:
                raise ValueError("subscription renewals are disabled for this server")
            plan = await _get_plan(
                db, guild_id, subscription.get("plan_id")
            )
            if duration_days is None:
                duration_days = (
                    int(plan["duration_days"])
                    if plan else int(settings["renewal_duration_days"])
                )
            duration_days = _duration(duration_days)
            previous_status = subscription["status"]
            expired_before_renewal = await _expire_one(db, subscription, now_text)
            if expired_before_renewal:
                previous_status = "expired"
            previous_end = _utc(subscription["end_date"])
            renewal_start = max(previous_end, now_dt)
            new_end = (renewal_start + timedelta(days=duration_days)).isoformat()
            transaction_id = uuid.uuid4().hex
            subscription["status"] = "active"
            subscription["end_date"] = new_end
            subscription["updated_at"] = now_text
            await db.execute(
                """
                UPDATE subscriptions
                SET end_date = ?, status = 'active', updated_at = ?
                WHERE subscription_id = ? AND guild_id = ?
                """,
                (new_end, now_text, subscription_id, guild_id),
            )
            # An expiry/reminder can be produced by this same transaction when
            # an admin renews after the recorded end date. Do not DM a stale
            # expiry after the renewal has already committed.
            await db.execute(
                """
                UPDATE subscription_notifications
                SET status = 'cancelled', completed_at = ?,
                    last_error = 'subscription renewed before notification delivery'
                WHERE subscription_id = ? AND status = 'pending'
                  AND event_type IN ('expiring', 'expired')
                """,
                (now_text, subscription_id),
            )
            await db.execute(
                """
                INSERT INTO subscription_renewals
                    (transaction_id, subscription_id, guild_id, user_id,
                     idempotency_key, previous_end_date, new_end_date,
                     duration_days, actor_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id, subscription_id, guild_id,
                    int(subscription["user_id"]), key, previous_end.isoformat(),
                    new_end, duration_days, actor_id, now_text,
                ),
            )
            xp = await _award_subscription_xp(
                db,
                subscription=subscription,
                event_type="renewal",
                source_id=transaction_id,
                now=now_text,
                settings=settings,
                plan=plan,
            )
            renewal = {
                "transaction_id": transaction_id,
                "subscription_id": subscription_id,
                "guild_id": guild_id,
                "user_id": int(subscription["user_id"]),
                "idempotency_key": key,
                "previous_end_date": previous_end.isoformat(),
                "new_end_date": new_end,
                "duration_days": duration_days,
                "actor_id": actor_id,
                "created_at": now_text,
            }
            await _write_history(
                db,
                event_key=f"renewed:{transaction_id}",
                idempotency_key=key,
                request_hash=request_hash,
                subscription=subscription,
                event_type="renewed",
                actor_id=actor_id,
                previous_status=previous_status,
                details={
                    "transaction_id": transaction_id,
                    "previous_end_date": previous_end.isoformat(),
                    "new_end_date": new_end,
                    "duration_days": duration_days,
                    "xp_transaction_id": xp["transaction_id"],
                    "expired_before_renewal": expired_before_renewal,
                },
                now=now_text,
            )
            if actor_id is not None:
                await _write_admin_audit(
                    db,
                    event_key=f"renewed:{transaction_id}",
                    idempotency_key=f"subscription:{key}",
                    request_hash=request_hash,
                    subscription=subscription,
                    actor_id=actor_id,
                    before=before_admin,
                    after=subscription,
                    now=now_text,
                )
            await _queue_notification(
                db,
                event_key=f"renewal:{transaction_id}",
                subscription=subscription,
                event_type="renewal",
                now=now_text,
                settings=settings,
                plan=plan,
                payload={"xp": int(xp["amount"])},
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
    return {
        "status": "renewed",
        "subscription": subscription,
        "renewal": renewal,
        "xp": xp,
        "idempotent": False,
    }


async def cancel_subscription(
    guild_id: int,
    subscription_id: str,
    *,
    idempotency_key: str,
    actor_id: int | None = None,
    reason: str = "",
    now: datetime | str | None = None,
) -> dict[str, Any]:
    guild_id = _positive_id(guild_id, "guild_id")
    subscription_id = str(subscription_id or "").strip()
    if not subscription_id or len(subscription_id) > 100:
        raise ValueError("subscription_id must contain 1 to 100 characters")
    key = _operation_key(idempotency_key)
    actor_id = _actor_id(actor_id) if actor_id is not None else None
    reason = str(reason or "").strip()[:500]
    now_text = _iso(now)
    request_hash = _fingerprint({
        "operation": "cancel",
        "guild_id": guild_id,
        "subscription_id": subscription_id,
        "reason": reason,
    })

    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            prior = await _find_operation(db, key, request_hash)
            if prior:
                subscription = await _get_subscription(
                    db, prior["subscription_id"], guild_id
                )
                await db.rollback()
                return {
                    "status": "duplicate",
                    "subscription": subscription,
                    "idempotent": True,
                }
            subscription = await _get_subscription(db, subscription_id, guild_id)
            if subscription is None:
                raise ValueError("subscription not found in this server")
            before_admin = dict(subscription)
            if await _expire_one(db, subscription, now_text):
                await db.commit()
                return {
                    "status": "expired",
                    "subscription": subscription,
                    "idempotent": False,
                }
            if subscription["status"] == "expired":
                await db.commit()
                return {
                    "status": "expired",
                    "subscription": subscription,
                    "idempotent": False,
                }
            if subscription["status"] == "cancelled":
                await db.rollback()
                return {
                    "status": "already_cancelled",
                    "subscription": subscription,
                    "idempotent": True,
                }
            previous_status = subscription["status"]
            subscription["status"] = "cancelled"
            subscription["updated_at"] = now_text
            await db.execute(
                """
                UPDATE subscriptions
                SET status = 'cancelled', updated_at = ?
                WHERE subscription_id = ? AND guild_id = ? AND status = 'active'
                """,
                (now_text, subscription_id, guild_id),
            )
            await db.execute(
                """
                UPDATE subscription_notifications
                SET status = 'cancelled', completed_at = ?,
                    last_error = 'subscription cancelled before reminder delivery'
                WHERE subscription_id = ? AND status = 'pending'
                  AND event_type = 'expiring'
                """,
                (now_text, subscription_id),
            )
            await _write_history(
                db,
                event_key=f"cancelled:{key}",
                idempotency_key=key,
                request_hash=request_hash,
                subscription=subscription,
                event_type="cancelled",
                actor_id=actor_id,
                previous_status=previous_status,
                details={"reason": reason},
                now=now_text,
            )
            if actor_id is not None:
                await _write_admin_audit(
                    db,
                    event_key=f"cancelled:{key}",
                    idempotency_key=f"subscription:{key}",
                    request_hash=request_hash,
                    subscription=subscription,
                    actor_id=actor_id,
                    before=before_admin,
                    after=subscription,
                    now=now_text,
                )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
    return {
        "status": "cancelled",
        "subscription": subscription,
        "idempotent": False,
    }


async def adjust_subscription(
    guild_id: int,
    subscription_id: str,
    changes: dict[str, Any],
    *,
    actor_id: int,
    idempotency_key: str,
    reason: str = "",
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Apply an auditable dashboard-only subscription correction."""
    guild_id = _positive_id(guild_id, "guild_id")
    actor_id = _actor_id(actor_id)
    subscription_id = str(subscription_id or "").strip()
    if not subscription_id or len(subscription_id) > 100:
        raise ValueError("subscription_id must contain 1 to 100 characters")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("subscription changes must be a non-empty object")
    allowed = {"end_date", "status", "plan_id"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("unsupported subscription fields: " + ", ".join(sorted(unknown)))
    key = _operation_key(idempotency_key)
    reason = str(reason or "").strip()[:500]
    now_dt = _utc(now)
    now_text = now_dt.isoformat()
    request_hash = _fingerprint({
        "operation": "adjust",
        "guild_id": guild_id,
        "subscription_id": subscription_id,
        "changes": changes,
        "reason": reason,
    })
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                """
                SELECT * FROM subscription_admin_audit
                WHERE idempotency_key = ?
                """,
                (f"subscription:{key}",),
            ) as cursor:
                prior = await cursor.fetchone()
            if prior:
                if prior["request_hash"] and prior["request_hash"] != request_hash:
                    raise ValueError("idempotency key was already used for a different request")
                subscription = await _get_subscription(
                    db, prior["subscription_id"], guild_id
                )
                await db.rollback()
                if subscription is None:
                    raise RuntimeError("audited subscription record is missing")
                return {
                    "status": "duplicate",
                    "subscription": subscription,
                    "idempotent": True,
                }
            subscription = await _get_subscription(db, subscription_id, guild_id)
            if subscription is None:
                raise ValueError("subscription not found in this server")
            before = dict(subscription)
            if "end_date" in changes:
                end_date = _iso(changes["end_date"])
                if end_date <= subscription["start_date"]:
                    raise ValueError("end_date must be later than the subscription start")
                subscription["end_date"] = end_date
            if "plan_id" in changes:
                plan_id = str(changes["plan_id"] or "").strip() or None
                if plan_id:
                    if len(plan_id) > 100 or not await _get_plan(
                        db, guild_id, plan_id
                    ):
                        raise ValueError("plan_id must reference a plan in this server")
                subscription["plan_id"] = plan_id
            if "status" in changes:
                status = str(changes["status"] or "").strip()
                if status not in {"active", "expired", "cancelled"}:
                    raise ValueError("status must be active, expired, or cancelled")
                subscription["status"] = status
            if (
                subscription["status"] == "active"
                and subscription["end_date"] <= now_text
            ):
                raise ValueError("active subscriptions must end in the future")
            if all(
                before.get(key) == subscription.get(key)
                for key in ("end_date", "status", "plan_id")
            ):
                await db.rollback()
                return {
                    "status": "unchanged",
                    "subscription": before,
                    "idempotent": True,
                }
            subscription["updated_at"] = now_text
            await db.execute(
                """
                UPDATE subscriptions
                SET end_date = ?, status = ?, plan_id = ?, updated_at = ?
                WHERE subscription_id = ? AND guild_id = ?
                """,
                (
                    subscription["end_date"], subscription["status"],
                    subscription["plan_id"], now_text, subscription_id, guild_id,
                ),
            )
            await db.execute(
                """
                UPDATE subscription_notifications
                SET status = 'cancelled', completed_at = ?,
                    last_error = 'subscription was manually adjusted'
                WHERE subscription_id = ? AND status = 'pending'
                  AND event_type IN ('expiring', 'expired')
                """,
                (now_text, subscription_id),
            )
            await _write_admin_audit(
                db,
                event_key=f"admin-adjust:{key}",
                idempotency_key=f"subscription:{key}",
                request_hash=request_hash,
                subscription=subscription,
                actor_id=actor_id,
                before=before,
                after={**subscription, "reason": reason},
                now=now_text,
            )
            await db.commit()
            return {
                "status": "adjusted",
                "subscription": subscription,
                "idempotent": False,
            }
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise


async def process_due_subscriptions(
    now: datetime | str | None = None,
    *,
    guild_id: int | None = None,
    limit: int = 500,
) -> dict[str, int]:
    now_dt = _utc(now)
    now_text = now_dt.isoformat()
    limit = max(1, min(int(limit), 2_000))
    expired_count = 0
    reminders_queued = 0

    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            guild_filter = " AND guild_id = ?" if guild_id is not None else ""
            reminder_filter_params: tuple[Any, ...] = (
                (int(guild_id),) if guild_id is not None else ()
            )
            async with db.execute(
                f"""
                SELECT COALESCE(MAX(hours_before), 0)
                FROM subscription_reminder_rules
                WHERE enabled = 1{guild_filter}
                """,
                reminder_filter_params,
            ) as cursor:
                maximum_reminder_hours = int((await cursor.fetchone())[0] or 0)
            async with db.execute(
                f"""
                SELECT * FROM subscriptions
                WHERE status = 'active' AND end_date <= ? AND end_date > ?{guild_filter}
                ORDER BY end_date ASC LIMIT ?
                """,
                (
                    (now_dt + timedelta(hours=maximum_reminder_hours)).isoformat(),
                    now_text,
                    int(guild_id),
                    limit,
                )
                if guild_id is not None
                else (
                    (now_dt + timedelta(hours=maximum_reminder_hours)).isoformat(),
                    now_text,
                    limit,
                ),
            ) as cursor:
                upcoming = [dict(row) for row in await cursor.fetchall()]
            # Expiry and reminders are isolated so a server with reminders
            # disabled still has expired subscriptions processed.
            async with db.execute(
                f"""
                SELECT * FROM subscriptions
                WHERE status = 'active' AND end_date <= ?{guild_filter}
                ORDER BY end_date ASC LIMIT ?
                """,
                (
                    (now_text, int(guild_id), limit)
                    if guild_id is not None
                    else (now_text, limit)
                ),
            ) as cursor:
                due = [dict(row) for row in await cursor.fetchall()]
            for subscription in due:
                if await _expire_one(db, subscription, now_text):
                    expired_count += 1

            reminders_by_guild: dict[int, list[dict[str, Any]]] = {}
            for subscription in upcoming:
                target_guild_id = int(subscription["guild_id"])
                settings = await _ensure_settings(db, target_guild_id, now_text)
                if (
                    not settings["enabled"]
                    or not settings["notifications_enabled"]
                    or not settings["reminders_enabled"]
                ):
                    continue
                if target_guild_id not in reminders_by_guild:
                    async with db.execute(
                        """
                        SELECT * FROM subscription_reminder_rules
                        WHERE guild_id = ? AND enabled = 1
                        ORDER BY hours_before DESC, reminder_id
                        """,
                        (target_guild_id,),
                    ) as cursor:
                        reminders_by_guild[target_guild_id] = [
                            dict(row) for row in await cursor.fetchall()
                        ]
                plan = await _get_plan(
                    db, target_guild_id, subscription.get("plan_id")
                )
                end_date = _utc(subscription["end_date"])
                due_reminders = []
                for rule in reminders_by_guild[target_guild_id]:
                    conditions = _json_object(rule.get("conditions_json"))
                    selected_plans = conditions.get("plan_ids")
                    if selected_plans and subscription.get("plan_id") not in selected_plans:
                        continue
                    hours = int(rule["hours_before"])
                    if end_date <= now_dt + timedelta(hours=hours):
                        due_reminders.append(rule)
                # If the worker was delayed, send only the nearest missed
                # checkpoint rather than a burst of obsolete reminders.
                if due_reminders:
                    reminder_rule = min(
                        due_reminders, key=lambda item: int(item["hours_before"])
                    )
                    hours = int(reminder_rule["hours_before"])
                    queued = await _queue_notification(
                        db,
                        event_key=(
                            f"expiring:{subscription['subscription_id']}:"
                            f"{subscription['end_date']}:{reminder_rule['reminder_id']}"
                        ),
                        subscription=subscription,
                        event_type="expiring",
                        now=now_text,
                        settings=settings,
                        plan=plan,
                        reminder_hours=hours,
                        reminder_id=reminder_rule["reminder_id"],
                        rule_override={
                            "dm_enabled": bool(reminder_rule["dm_enabled"]),
                            "channel_enabled": bool(reminder_rule["channel_enabled"]),
                            "channel_id": reminder_rule["channel_id"],
                            "template_id": reminder_rule["template_id"],
                        },
                        payload={
                            "reminder": reminder_rule["name"],
                            "end_date": subscription["end_date"],
                        },
                    )
                    reminders_queued += int(queued)
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
    return {"expired": expired_count, "reminders_queued": reminders_queued}


async def get_subscription(
    guild_id: int,
    subscription_id: str,
    *,
    now: datetime | str | None = None,
) -> dict[str, Any] | None:
    guild_id = _positive_id(guild_id, "guild_id")
    await process_due_subscriptions(now, guild_id=guild_id)
    async with database.connect(aiosqlite.Row) as db:
        return await _get_subscription(db, subscription_id, guild_id)


async def list_subscriptions(
    guild_id: int,
    *,
    user_id: int | None = None,
    limit: int = 20,
    now: datetime | str | None = None,
    process_due: bool = True,
) -> list[dict[str, Any]]:
    guild_id = _positive_id(guild_id, "guild_id")
    if user_id is not None:
        user_id = _positive_id(user_id, "user_id")
    limit = max(1, min(int(limit), 100))
    if process_due:
        await process_due_subscriptions(now, guild_id=guild_id)
    query = "SELECT * FROM subscriptions WHERE guild_id = ?"
    params: list[Any] = [guild_id]
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(query, tuple(params)) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_subscription_history(
    guild_id: int,
    subscription_id: str,
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    guild_id = _positive_id(guild_id, "guild_id")
    limit = max(1, min(int(limit), 100))
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM subscription_history
            WHERE guild_id = ? AND subscription_id = ?
            ORDER BY history_id DESC LIMIT ?
            """,
            (guild_id, str(subscription_id), limit),
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        try:
            row["details"] = json.loads(row.pop("details_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            row["details"] = {}
    return rows


async def list_subscription_admin_audit(
    guild_id: int,
    *,
    subscription_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    guild_id = _positive_id(guild_id, "guild_id")
    limit = max(1, min(int(limit), 250))
    query = "SELECT * FROM subscription_admin_audit WHERE guild_id = ?"
    params: list[Any] = [guild_id]
    if subscription_id:
        query += " AND subscription_id = ?"
        params.append(str(subscription_id))
    query += " ORDER BY created_at DESC, audit_id DESC LIMIT ?"
    params.append(limit)
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(query, tuple(params)) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        for source, target in (("before_json", "before"), ("after_json", "after")):
            row[target] = _json_object(row.pop(source, "{}"))
    return rows


async def list_subscription_control_audit(
    guild_id: int, *, limit: int = 100
) -> list[dict[str, Any]]:
    guild_id = _positive_id(guild_id, "guild_id")
    limit = max(1, min(int(limit), 250))
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM subscription_control_audit
            WHERE guild_id = ?
            ORDER BY created_at DESC, audit_id DESC LIMIT ?
            """,
            (guild_id, limit),
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        for source, target in (("before_json", "before"), ("after_json", "after")):
            row[target] = _json_object(row.pop(source, "{}"))
    return rows


async def list_subscription_notifications(
    guild_id: int, *, limit: int = 100
) -> list[dict[str, Any]]:
    guild_id = _positive_id(guild_id, "guild_id")
    limit = max(1, min(int(limit), 250))
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM subscription_notifications
            WHERE guild_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (guild_id, limit),
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        row["payload"] = _json_object(row.pop("payload_json", "{}"))
    return rows


async def get_subscription_analytics(
    guild_id: int,
    *,
    now: datetime | str | None = None,
    process_due: bool = True,
) -> dict[str, int]:
    guild_id = _positive_id(guild_id, "guild_id")
    now_dt = _utc(now)
    now_text = now_dt.isoformat()
    month_start = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_end = (
        now_dt.replace(year=now_dt.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if now_dt.month == 12
        else now_dt.replace(month=now_dt.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    ).isoformat()
    tomorrow = (now_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()
    expiring_horizon = (now_dt + timedelta(days=7)).isoformat()
    if process_due:
        await process_due_subscriptions(now_dt, guild_id=guild_id)
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT
              SUM(CASE WHEN status = 'active' AND end_date > ? THEN 1 ELSE 0 END) AS active_count,
              SUM(CASE WHEN status = 'expired' OR (status = 'active' AND end_date <= ?) THEN 1 ELSE 0 END) AS expired_count,
              SUM(CASE WHEN status = 'active' AND end_date > ? AND end_date <= ? THEN 1 ELSE 0 END) AS expiring_count
            FROM subscriptions WHERE guild_id = ?
            """,
            (now_text, now_text, now_text, expiring_horizon, guild_id),
        ) as cursor:
            state = await cursor.fetchone()
        async with db.execute(
            """
            SELECT
              SUM(CASE WHEN event_type = 'created' THEN 1 ELSE 0 END) AS new_count,
              SUM(CASE WHEN event_type = 'created' AND created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) AS new_today,
              SUM(CASE WHEN event_type = 'created' AND created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) AS new_month
            FROM subscription_history WHERE guild_id = ?
            """,
            (day_start, tomorrow, month_start, month_end, guild_id),
        ) as cursor:
            new_rows = await cursor.fetchone()
        async with db.execute(
            """
            SELECT
              COUNT(*) AS renewal_count,
              SUM(CASE WHEN created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) AS renewals_today,
              SUM(CASE WHEN created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) AS renewals_month
            FROM subscription_renewals WHERE guild_id = ?
            """,
            (day_start, tomorrow, month_start, month_end, guild_id),
        ) as cursor:
            renewal_rows = await cursor.fetchone()
        async with db.execute(
            """
            SELECT
              COALESCE(SUM(amount), 0) AS total_xp,
              COALESCE(SUM(CASE WHEN timestamp >= ? AND timestamp < ? THEN amount ELSE 0 END), 0) AS xp_today,
              COALESCE(SUM(CASE WHEN timestamp >= ? AND timestamp < ? THEN amount ELSE 0 END), 0) AS xp_month
            FROM subscription_xp_transactions WHERE guild_id = ?
            """,
            (day_start, tomorrow, month_start, month_end, guild_id),
        ) as cursor:
            xp_rows = await cursor.fetchone()
    return {
        "active_subscriptions": int(state["active_count"] or 0),
        "expired_subscriptions": int(state["expired_count"] or 0),
        "new_subscriptions": int(new_rows["new_count"] or 0),
        "new_subscriptions_today": int(new_rows["new_today"] or 0),
        "new_subscriptions_this_month": int(new_rows["new_month"] or 0),
        "renewals": int(renewal_rows["renewal_count"] or 0),
        "renewals_today": int(renewal_rows["renewals_today"] or 0),
        "renewals_this_month": int(renewal_rows["renewals_month"] or 0),
        "expiring_soon": int(state["expiring_count"] or 0),
        "total_subscription_xp": int(xp_rows["total_xp"] or 0),
        "xp_today": int(xp_rows["xp_today"] or 0),
        "xp_this_month": int(xp_rows["xp_month"] or 0),
    }


async def claim_due_notifications(
    *,
    now: datetime | str | None = None,
    limit: int = NOTIFICATION_BATCH_SIZE,
) -> list[dict[str, Any]]:
    now_text = _iso(now)
    limit = max(1, min(int(limit), 500))
    claimed: list[dict[str, Any]] = []
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            # Recover rows left in `sending` if the process died after claiming
            # but before recording a delivery result. This is at-least-once:
            # a crash after Discord accepted a DM can result in a duplicate.
            async with db.execute(
                """
                SELECT notification_id, guild_id, claimed_at
                FROM subscription_notifications WHERE status = 'sending'
                """
            ) as cursor:
                sending = await cursor.fetchall()
            for row in sending:
                settings = await _ensure_settings(
                    db, int(row["guild_id"]), now_text
                )
                stale_before = (
                    _utc(now)
                    - timedelta(minutes=settings["notification_claim_timeout_minutes"])
                ).isoformat()
                if row["claimed_at"] and row["claimed_at"] <= stale_before:
                    await db.execute(
                        """
                        UPDATE subscription_notifications
                        SET status = 'pending', claimed_at = NULL,
                            last_error = 'recovered stale notification claim'
                        WHERE notification_id = ? AND status = 'sending'
                        """,
                        (row["notification_id"],),
                    )
            async with db.execute(
                """
                SELECT * FROM subscription_notifications
                WHERE status = 'pending'
                ORDER BY created_at ASC LIMIT ?
                """,
                (limit,),
            ) as cursor:
                pending = [dict(row) for row in await cursor.fetchall()]
            for notification in pending:
                settings = await _ensure_settings(
                    db, int(notification["guild_id"]), now_text
                )
                if not settings["notifications_enabled"]:
                    await db.execute(
                        """
                        UPDATE subscription_notifications
                        SET status = 'cancelled', completed_at = ?,
                            last_error = 'notifications disabled'
                        WHERE notification_id = ? AND status = 'pending'
                        """,
                        (now_text, notification["notification_id"]),
                    )
                    continue
                rule = settings["notification_rules"].get(
                    notification["event_type"], {}
                )
                if (
                    not settings["enabled"]
                    or not rule.get("enabled", True)
                    or (
                        notification["event_type"] == "expiring"
                        and not settings["reminders_enabled"]
                    )
                ):
                    await db.execute(
                        """
                        UPDATE subscription_notifications
                        SET status = 'cancelled', completed_at = ?,
                            last_error = 'notification rule disabled'
                        WHERE notification_id = ? AND status = 'pending'
                        """,
                        (now_text, notification["notification_id"]),
                    )
                    continue
                if notification["event_type"] == "expiring":
                    subscription = await _get_subscription(
                        db, notification["subscription_id"], notification["guild_id"]
                    )
                    reminder_is_enabled = True
                    if notification.get("reminder_id"):
                        async with db.execute(
                            """
                            SELECT enabled FROM subscription_reminder_rules
                            WHERE guild_id = ? AND reminder_id = ?
                            """,
                            (notification["guild_id"], notification["reminder_id"]),
                        ) as cursor:
                            reminder_row = await cursor.fetchone()
                        reminder_is_enabled = bool(reminder_row and reminder_row["enabled"])
                    if (
                        subscription is None
                        or subscription["status"] != "active"
                        or subscription["end_date"] != notification["reference_end_date"]
                        or subscription["end_date"] <= now_text
                        or not reminder_is_enabled
                    ):
                        await db.execute(
                            """
                            UPDATE subscription_notifications
                            SET status = 'cancelled', completed_at = ?,
                                last_error = 'reminder no longer matches active subscription'
                            WHERE notification_id = ? AND status = 'pending'
                            """,
                            (now_text, notification["notification_id"]),
                        )
                        continue
                cursor = await db.execute(
                    """
                    UPDATE subscription_notifications
                    SET status = 'sending', claimed_at = ?
                    WHERE notification_id = ? AND status = 'pending'
                    """,
                    (now_text, notification["notification_id"]),
                )
                if cursor.rowcount != 1:
                    continue
                try:
                    notification["payload"] = json.loads(
                        notification.pop("payload_json") or "{}"
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    notification["payload"] = {}
                claimed.append(notification)
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
    return claimed


async def notification_is_current(
    notification_id: str,
    *,
    now: datetime | str | None = None,
) -> bool:
    now_text = _iso(now)
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT * FROM subscription_notifications WHERE notification_id = ?",
                (str(notification_id),),
            ) as cursor:
                notification = await cursor.fetchone()
            if not notification or notification["status"] != "sending":
                await db.rollback()
                return False
            if notification["event_type"] not in {"expiring", "expired"}:
                await db.rollback()
                return True
            subscription = await _get_subscription(
                db, notification["subscription_id"], notification["guild_id"]
            )
            if notification["event_type"] == "expiring":
                is_current = bool(
                    subscription
                    and subscription["status"] == "active"
                    and subscription["end_date"] == notification["reference_end_date"]
                    and subscription["end_date"] > now_text
                )
            else:
                is_current = bool(
                    subscription
                    and subscription["status"] in {"expired", "cancelled"}
                    and subscription["end_date"] == notification["reference_end_date"]
                )
            if not is_current:
                await db.execute(
                    """
                    UPDATE subscription_notifications
                    SET status = 'cancelled', completed_at = ?,
                        last_error = 'subscription changed before notification delivery'
                    WHERE notification_id = ? AND status = 'sending'
                    """,
                    (now_text, str(notification_id)),
                )
            await db.commit()
            return is_current
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise


async def complete_notification(
    notification_id: str,
    *,
    delivered: bool,
    error: str | None = None,
    now: datetime | str | None = None,
) -> bool:
    now_text = _iso(now)
    status = "sent" if delivered else "failed"
    message = str(error)[:1000] if error else (
        None if delivered else "notification delivery failed"
    )
    async with database.connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                """
                UPDATE subscription_notifications
                SET status = ?, completed_at = ?, last_error = ?
                WHERE notification_id = ? AND status = 'sending'
                """,
                (status, now_text, message, str(notification_id)),
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
    return cursor.rowcount == 1
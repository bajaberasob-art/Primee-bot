"""Persistent, per-guild announcement reactions; never read message history."""
import json
import time

import aiosqlite
import discord

import database


class SettingsConflict(Exception):
    def __init__(self, current):
        self.current = current


def decode(row, guild_id):
    if row is None:
        return {
            "guild_id": str(guild_id), "channel_id": None, "emoji_ids": [],
            "enabled": False, "revision": 0, "activated_at": None,
            "last_error": None, "last_error_at": None,
            "second_channel_id": None, "line_enabled": False, "line_channel_ids": [],
            "line_image_id": None, "line_activated_at": None,
            "line_last_error": None, "line_last_error_at": None,
        }
    result = dict(row)
    result["guild_id"] = str(result["guild_id"])
    result["emoji_ids"] = json.loads(result["emoji_ids"])
    result["enabled"] = bool(result["enabled"])
    result["line_enabled"] = bool(result["line_enabled"])
    result["line_channel_ids"] = json.loads(result["line_channel_ids"])
    return result


async def get_settings(guild_id):
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM announcement_reaction_settings WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            return decode(await cursor.fetchone(), guild_id)


async def enabled_settings():
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM announcement_reaction_settings WHERE enabled = 1 OR line_enabled = 1"
        ) as cursor:
            return [decode(row, row["guild_id"]) for row in await cursor.fetchall()]


async def save_settings(guild_id, changes, revision):
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT * FROM announcement_reaction_settings WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            old = decode(await cursor.fetchone(), guild_id)
        if old["revision"] != revision:
            raise SettingsConflict(old)
        changes = {**old, **changes}
        # Re-enabling/changing the target starts a new live-only activation boundary.
        changed = any(old[key] != changes[key] for key in ("enabled", "channel_id", "second_channel_id", "emoji_ids"))
        activated = (time.time() if changed else old["activated_at"]) if changes["enabled"] else None
        line_changed = any(old[key] != changes[key] for key in ("line_enabled", "line_channel_ids", "line_image_id"))
        line_activated = (time.time() if line_changed else old["line_activated_at"]) if changes["line_enabled"] else None
        await db.execute(
            """INSERT INTO announcement_reaction_settings
               (guild_id, channel_id, emoji_ids, enabled, revision, activated_at, updated_at,
                second_channel_id,line_enabled,line_channel_ids,line_image_id,line_activated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET
                 channel_id=excluded.channel_id, emoji_ids=excluded.emoji_ids,
                 enabled=excluded.enabled, revision=excluded.revision,
                 activated_at=excluded.activated_at, updated_at=excluded.updated_at,
                  second_channel_id=excluded.second_channel_id,
                  line_enabled=excluded.line_enabled,line_channel_ids=excluded.line_channel_ids,
                  line_image_id=excluded.line_image_id,line_activated_at=excluded.line_activated_at,
                  last_error=NULL, last_error_at=NULL,
                  line_last_error=NULL,line_last_error_at=NULL""",
            (guild_id, changes["channel_id"], json.dumps(changes["emoji_ids"]),
             int(changes["enabled"]), revision + 1, activated, time.time(),
             changes["second_channel_id"], int(changes["line_enabled"]),
             json.dumps(changes["line_channel_ids"]), changes["line_image_id"], line_activated),
        )
        await db.commit()
    return await get_settings(guild_id)


async def record_error(guild_id, revision, message, kind="reactions"):
    error, error_at, enabled = (
        ("line_last_error", "line_last_error_at", "line_enabled") if kind == "line"
        else ("last_error", "last_error_at", "enabled")
    )
    async with database.connect() as db:
        await db.execute(
            f"""UPDATE announcement_reaction_settings SET {error}=?, {error_at}=?
               WHERE guild_id=? AND revision=? AND {enabled}=1""",
            (message[:500], time.time(), guild_id, revision),
        )
        await db.commit()


async def clear_error(guild_id, revision, kind="reactions"):
    error, error_at = (
        ("line_last_error", "line_last_error_at") if kind == "line" else ("last_error", "last_error_at")
    )
    async with database.connect() as db:
        await db.execute(
            f"""UPDATE announcement_reaction_settings SET {error}=NULL, {error_at}=NULL
                WHERE guild_id=? AND revision=?""", (guild_id, revision),
        )
        await db.commit()


def text_channel(channel):
    return channel is not None and getattr(channel, "type", None) in (
        discord.ChannelType.text, discord.ChannelType.news,
    )


def emoji_usable(emoji):
    if not getattr(emoji, "available", True):
        return False
    try:
        return bool(emoji.is_usable())
    except (AttributeError, TypeError):
        return False


def permissions(guild, channel):
    names = ("view_channel", "read_message_history", "add_reactions", "use_external_emojis")
    member = getattr(guild, "me", None)
    actual = channel.permissions_for(member) if channel and member else None
    result = {name: bool(getattr(actual, name, False)) for name in names}
    # Only this guild's custom emojis are accepted; external permission isn't required.
    result["external_required"] = False
    return result


def reaction_channels(config):
    return [key for key in (config["channel_id"], config.get("second_channel_id")) if key]


def inspect_configuration(guild, config, worker_ready=True, channel_id=None):
    channel_id = channel_id or config["channel_id"]
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    perms = permissions(guild, channel)
    missing = [
        key for key in ("view_channel", "read_message_history", "add_reactions") if not perms[key]
    ] if channel and getattr(guild, "me", None) else []
    emojis = {str(emoji.id): emoji for emoji in guild.emojis}
    invalid = [key for key in config["emoji_ids"] if key not in emojis or not emoji_usable(emojis[key])]
    code, message = "ready", "النظام جاهز للتفاعل مع الرسائل الجديدة فقط."
    if not config["enabled"]:
        code, message = "disabled", "النظام متوقف؛ لن تضاف تفاعلات تلقائية."
    elif not worker_ready or not getattr(guild, "me", None):
        code, message = "bot_unavailable", "محرك التفاعلات غير جاهز. تحقق من اتصال البوت."
    elif not text_channel(channel):
        code, message = "missing_channel", "قناة الإعلانات غير موجودة أو لم تعد قناة نصية."
    elif missing:
        code, message = "missing_permissions", "البوت يفتقد صلاحيات في قناة الإعلانات: " + ", ".join(missing)
    elif invalid or not 4 <= len(config["emoji_ids"]) <= 5:
        code, message = "unavailable_emojis", "بعض الإيموجيات حُذفت أو لا يستطيع البوت استخدامها. حدّث الاختيار."
    elif config.get("last_error"):
        code, message = "runtime_error", config["last_error"]
    return {
        "code": code, "message": message, "missing_permissions": missing,
        "invalid_emoji_ids": invalid, "permissions": perms, "worker_ready": worker_ready,
    }


def validate_changes(guild, body, previous):
    if set(body) - {"channel_id", "emoji_ids", "enabled", "revision", "second_channel_id",
                    "line_enabled", "line_channel_ids", "line_image_id"}:
        raise ValueError("حقول إعدادات غير معروفة.")
    if not isinstance(body.get("enabled"), bool):
        raise ValueError("حالة التشغيل يجب أن تكون تشغيل أو إيقاف.")
    revision = body.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("مراجعة الإعدادات غير صحيحة؛ أعد تحميل البيانات.")
    channel_id = body.get("channel_id")
    if channel_id is not None:
        if not isinstance(channel_id, str) or not channel_id.isdigit() or not 15 <= len(channel_id) <= 22:
            raise ValueError("اختر قناة صحيحة من السيرفر.")
        if not text_channel(guild.get_channel(int(channel_id))):
            # Permit switching off an existing configuration whose channel was deleted.
            if body["enabled"] or channel_id != previous["channel_id"]:
                raise ValueError("القناة المختارة ليست قناة نصية موجودة في هذا السيرفر.")
    ids = body.get("emoji_ids")
    if not isinstance(ids, list) or len(ids) > 5:
        raise ValueError("يمكن اختيار خمسة إيموجيات كحد أقصى.")
    if any(not isinstance(key, str) or not key.isdigit() or not 15 <= len(key) <= 22 for key in ids):
        raise ValueError("اختر الإيموجيات من قائمة السيرفر فقط.")
    if len(set(ids)) != len(ids):
        raise ValueError("لا يمكن اختيار الإيموجي نفسه مرتين.")
    current = {str(emoji.id): emoji for emoji in guild.emojis}
    for key in ids:
        if key not in current and (body["enabled"] or key not in previous["emoji_ids"]):
            raise ValueError("الإيموجي غير موجود في هذا السيرفر.")
        if body["enabled"] and not emoji_usable(current[key]):
            raise ValueError("أحد الإيموجيات غير متاح للبوت؛ اختر إيموجي آخر.")
    if body["enabled"] and (not channel_id or not 4 <= len(ids) <= 5):
        raise ValueError("لتشغيل النظام اختر قناة ومن أربعة إلى خمسة إيموجيات.")
    second = body.get("second_channel_id", previous.get("second_channel_id"))
    validate_channel(guild, second, body["enabled"], previous.get("second_channel_id"))
    if second and (not channel_id or second == channel_id):
        raise ValueError("القناة الثانية اختيارية ويجب أن تختلف عن القناة الأولى.")
    line_enabled = body.get("line_enabled", previous.get("line_enabled", False))
    if not isinstance(line_enabled, bool):
        raise ValueError("حالة أوتو لاين يجب أن تكون تشغيل أو إيقاف.")
    line_channels = body.get("line_channel_ids", previous.get("line_channel_ids", []))
    if not isinstance(line_channels, list) or len(line_channels) > 2:
        raise ValueError("اختر قناة أو قناتين لأوتو لاين.")
    if any(not isinstance(key, str) for key in line_channels) or len(set(line_channels)) != len(line_channels):
        raise ValueError("قنوات أوتو لاين يجب أن تكون مختلفة ومن قائمة السيرفر.")
    for key in line_channels:
        validate_channel(guild, key, line_enabled, key if key in previous.get("line_channel_ids", []) else None)
        if key is None:
            raise ValueError("اختر قناة سليمة لأوتو لاين.")
    image_id = body.get("line_image_id", previous.get("line_image_id"))
    if image_id is not None and (
        not isinstance(image_id, str) or len(image_id) != 64
        or any(c not in "0123456789abcdef" for c in image_id)
    ):
        raise ValueError("اختر صورة فاصلة مرفوعة من هذه اللوحة.")
    if line_enabled and (not line_channels or not image_id):
        raise ValueError("لتشغيل أوتو لاين اختر قناة وارفع صورة فاصلة.")
    return {
        "channel_id": channel_id, "second_channel_id": second, "emoji_ids": ids,
        "enabled": body["enabled"], "line_enabled": line_enabled,
        "line_channel_ids": line_channels, "line_image_id": image_id,
    }, revision


def validate_channel(guild, key, enabled, old_key):
    if key is None:
        return
    if not isinstance(key, str) or not key.isdigit() or not 15 <= len(key) <= 22:
        raise ValueError("اختر قناة صحيحة من هذا السيرفر.")
    if not text_channel(guild.get_channel(int(key))) and (enabled or key != old_key):
        raise ValueError("القناة المختارة ليست قناة نصية موجودة في هذا السيرفر.")


def inspect_line(guild, config, worker_ready=True, image_exists=True, channel_id=None):
    keys = config.get("line_channel_ids", [])
    key = channel_id or (keys[0] if keys else None)
    channel = guild.get_channel(int(key)) if key else None
    member = getattr(guild, "me", None)
    actual = channel.permissions_for(member) if channel and member else None
    perms = {name: bool(getattr(actual, name, False)) for name in ("view_channel", "send_messages", "attach_files")}
    missing = [name for name, allowed in perms.items() if not allowed] if channel and member else []
    code, message = "ready", "أوتو لاين جاهز لإرسال الفاصل بعد الرسائل النصية الجديدة فقط."
    if not config.get("line_enabled"):
        code, message = "disabled", "أوتو لاين متوقف؛ لن يرسل البوت صورًا فاصلة."
    elif not worker_ready or not member:
        code, message = "bot_unavailable", "محرك أوتو لاين غير جاهز؛ تحقق من اتصال البوت."
    elif not text_channel(channel):
        code, message = "missing_channel", "إحدى قنوات أوتو لاين غير موجودة."
    elif missing:
        code, message = "missing_permissions", "أوتو لاين يفتقد صلاحيات: " + ", ".join(missing)
    elif not config.get("line_image_id") or not image_exists:
        code, message = "missing_image", "صورة الفاصل غير متاحة؛ ارفع صورة جديدة واحفظ."
    elif config.get("line_last_error"):
        code, message = "runtime_error", config["line_last_error"]
    return {"code": code, "message": message, "permissions": perms,
            "missing_permissions": missing, "worker_ready": worker_ready}

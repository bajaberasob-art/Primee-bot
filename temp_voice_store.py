"""Additive, per-guild temporary voice configuration and durable room state."""
import copy
import json
import re
import time

import aiosqlite

import database

BUTTONS = {
    "rename": "اسم الروم", "limit": "حد الأعضاء", "privacy": "الخصوصية",
    "waiting": "غرفة الانتظار", "kick": "طرد", "invite": "دعوة",
    "trust": "ثقة", "untrust": "سحب الثقة", "status": "حالة الروم",
    "lock": "قفل", "unlock": "فتح", "ban": "حظر", "unban": "فك الحظر",
    "region": "المنطقة", "mute": "كتم عضو", "deafen": "إصمام عضو",
    "color": "لون الحاوية", "meeting": "وضع الاجتماع", "claim": "أخذ الملكية",
    "transfer": "نقل الملكية", "delete": "حذف الروم", "pin": "تثبيت الروم",
    "activity": "نشاط", "quick_lock": "قفل سريع", "quick_unlock": "فتح سريع",
    "age": "تقييد عمري", "emergency": "قفل الطوارئ", "slowmode": "الوضع البطيء",
    "report": "إبلاغ الإدارة",
}
DEFAULTS = {
    "enabled": False, "category_id": None, "panel_channel_id": None,
    "creation_channel_id": None, "panel_message_id": None,
    "name_template": "🔊・{OWNER_NAME}", "user_limit": 10, "bitrate": 64,
    "cooldown": 30, "privacy": "public",
    "welcome_template": "أهلاً {OWNER_MENTION}، هذا رومك الخاص. تحكم فيه من الأزرار.",
    "panel_title": "لوحة التحكم الصوتية",
    "panel_description": "ادخل روم الإنشاء ليُنشأ رومك الخاص، ثم استخدم الأزرار للتحكم.",
    "embed_color": "#8b5cf6", "theme_color": "#8b5cf6",
    "banner_url": "", "banner_image_id": None,
    "buttons": list(BUTTONS)[:20], "button_settings": {},
    "permanent_memory": True, "in_room_interface": True, "ownership_claim": True,
    "owner_embed_color": True, "waiting_room": True, "meeting_mode": True,
    "owner_manage_channel": False, "voice_analytics": True,
    "blacklisted_role_ids": [], "whitelisted_role_ids": [], "admin_role_ids": [],
    "admin_protection": True,
}
BOOL_FIELDS = {key for key, value in DEFAULTS.items() if isinstance(value, bool)}
CHANNEL_FIELDS = {"category_id", "panel_channel_id", "creation_channel_id"}
ROLE_FIELDS = {"blacklisted_role_ids", "whitelisted_role_ids", "admin_role_ids"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS temp_voice_settings (
 guild_id INTEGER PRIMARY KEY, config TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS temp_voice_rooms (
 channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, last_tick REAL NOT NULL);
CREATE INDEX IF NOT EXISTS temp_voice_rooms_guild ON temp_voice_rooms(guild_id);
CREATE TABLE IF NOT EXISTS temp_voice_profiles (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, preferences TEXT NOT NULL DEFAULT '{}',
 last_created REAL NOT NULL DEFAULT 0, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS temp_voice_stats (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, rooms_created INTEGER NOT NULL DEFAULT 0,
 voice_seconds REAL NOT NULL DEFAULT 0, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS temp_voice_images (
 guild_id INTEGER NOT NULL, image_id TEXT NOT NULL, mime TEXT NOT NULL,
 payload BLOB NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(guild_id,image_id));
"""


class Conflict(Exception):
    def __init__(self, current):
        self.current = current


async def init_schema(db):
    await db.executescript(SCHEMA)


def decode(row):
    result = copy.deepcopy(DEFAULTS)
    if row:
        result.update(json.loads(row["config"]))
    return result


async def get_config(guild_id):
    async with database.connect(aiosqlite.Row) as db:
        row = await (await db.execute(
            "SELECT * FROM temp_voice_settings WHERE guild_id=?", (guild_id,)
        )).fetchone()
    return {"config": decode(row), "revision": row["revision"] if row else 0}


def snowflake(value):
    return isinstance(value, str) and value.isdigit() and 15 <= len(value) <= 22


def channel_valid(guild, key, value):
    import discord
    if value is None:
        return True
    if not snowflake(value):
        return False
    ch = guild.get_channel(int(value))
    kind = {"category_id": discord.CategoryChannel, "panel_channel_id": discord.TextChannel,
            "creation_channel_id": discord.VoiceChannel}[key]
    return isinstance(ch, kind)


def validate_patch(guild, body, old):
    if not isinstance(body, dict) or set(body) - (set(DEFAULTS) - {"panel_message_id"} | {"revision"}):
        raise ValueError("حقول غير معروفة في إعدادات الرومات.")
    revision = body.get("revision")
    if type(revision) is not int or revision < 0:
        raise ValueError("رقم مراجعة الإعدادات مطلوب.")
    config = copy.deepcopy(old)
    for key, value in body.items():
        if key == "revision":
            continue
        if key in BOOL_FIELDS and type(value) is not bool:
            raise ValueError("قيمة التفعيل يجب أن تكون true أو false.")
        if key in CHANNEL_FIELDS and not channel_valid(guild, key, value):
            raise ValueError("القناة غير موجودة في السيرفر أو نوعها غير صحيح.")
        if key in ROLE_FIELDS:
            validate_roles(guild, value)
        if key in {"embed_color", "theme_color"} and (not isinstance(value, str) or not HEX.fullmatch(value)):
            raise ValueError("اكتب لونًا بصيغة #RRGGBB.")
        limits = {"user_limit": (0, 99), "bitrate": (8, min(384, guild.bitrate_limit // 1000)),
                  "cooldown": (0, 3600)}
        if key in limits and (type(value) is not int or not limits[key][0] <= value <= limits[key][1]):
            raise ValueError(f"قيمة {key} خارج الحدود المسموحة: {limits[key]}.")
        lengths = {"name_template": 100, "welcome_template": 1500,
                   "panel_title": 256, "panel_description": 3500, "banner_url": 1000}
        if key in lengths and (not isinstance(value, str) or len(value) > lengths[key]):
            raise ValueError(f"نص {key} أطول من الحد المسموح.")
        if key in {"name_template", "panel_title"} and not value.strip():
            raise ValueError("اسم الروم وعنوان البانل لا يمكن أن يكونا فارغين.")
        if key in {"name_template", "welcome_template"}:
            if any(x not in {"OWNER_NAME", "OWNER_MENTION", "COUNT"} for x in re.findall(r"\{([^{}]+)\}", value)):
                raise ValueError("متغيرات القالب: {OWNER_NAME} و{OWNER_MENTION} و{COUNT} فقط.")
        if key == "privacy" and value not in ("public", "private"):
            raise ValueError("الخصوصية public أو private.")
        if key == "banner_url" and value:
            from urllib.parse import urlparse
            parsed = urlparse(value)
            # Discord loads this image, not our server: no server-side URL fetching.
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("رابط البانر يجب أن يكون HTTPS دون بيانات دخول.")
        if key == "banner_image_id" and value is not None:
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError("معرّف صورة البانر غير صحيح.")
        if key == "buttons":
            if (not isinstance(value, list) or not 1 <= len(value) <= 20
                    or any(not isinstance(x, str) or x not in BUTTONS for x in value)
                    or len(set(value)) != len(value)):
                raise ValueError("اختر من زر واحد إلى 20 زرًا مختلفًا.")
        if key == "button_settings":
            validate_button_settings(guild, value)
        config[key] = value
    if config["enabled"]:
        for key in ("category_id", "panel_channel_id", "creation_channel_id"):
            if config[key] is None or not channel_valid(guild, key, config[key]):
                raise ValueError("اختر الفئة وقناة اللوحة وقناة الإنشاء قبل تفعيل النظام.")
        hub = guild.get_channel(int(config["creation_channel_id"]))
        if hub.category_id != int(config["category_id"]):
            raise ValueError("يجب أن تكون قناة الإنشاء داخل الفئة المحددة.")
    return config, revision


def validate_roles(guild, roles):
    if (not isinstance(roles, list) or len(roles) > 25
            or any(not snowflake(x) or not guild.get_role(int(x)) or int(x) == guild.default_role.id for x in roles)
            or len(set(roles)) != len(roles)):
        raise ValueError("اختر رولات موجودة من السيرفر، دون @everyone.")


def validate_button_settings(guild, settings):
    if not isinstance(settings, dict) or set(settings) - set(BUTTONS):
        raise ValueError("إعدادات أزرار غير معروفة.")
    for key, values in settings.items():
        if not isinstance(values, dict) or set(values) - {"log_channel_id", "alert_role_ids", "application_id", "seconds"}:
            raise ValueError("حقول إعداد الزر غير صحيحة.")
        if values.get("log_channel_id") is not None and not channel_valid(guild, "panel_channel_id", values["log_channel_id"]):
            raise ValueError("قناة السجل يجب أن تكون قناة نصية من السيرفر.")
        if "alert_role_ids" in values:
            validate_roles(guild, values["alert_role_ids"])
        if "application_id" in values and values["application_id"] is not None and not snowflake(values["application_id"]):
            raise ValueError("معرّف تطبيق النشاط غير صحيح.")
        if "seconds" in values and (type(values["seconds"]) is not int or not 0 <= values["seconds"] <= 21600):
            raise ValueError("الوضع البطيء بين 0 و21600 ثانية.")


async def save_config(guild_id, config, revision):
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT * FROM temp_voice_settings WHERE guild_id=?", (guild_id,))).fetchone()
        actual = row["revision"] if row else 0
        if actual != revision:
            raise Conflict({"config": decode(row), "revision": actual})
        await db.execute(
            "INSERT INTO temp_voice_settings VALUES (?,?,?) ON CONFLICT(guild_id) DO UPDATE SET config=excluded.config,revision=excluded.revision",
            (guild_id, json.dumps(config, ensure_ascii=False), actual + 1),
        )
        await db.commit()
    return {"config": config, "revision": actual + 1}


async def rooms(guild_id=None):
    async with database.connect(aiosqlite.Row) as db:
        query = "SELECT * FROM temp_voice_rooms"
        rows = await (await db.execute(query + (" WHERE guild_id=?" if guild_id is not None else ""),
                                      (guild_id,) if guild_id is not None else ())).fetchall()
    return [{**dict(row), "state": json.loads(row["state"])} for row in rows]


async def add_room(guild_id, channel_id, owner_id, state, count_created=True):
    now = time.time()
    async with database.connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute("INSERT INTO temp_voice_rooms VALUES (?,?,?,?,?,?)",
                         (channel_id, guild_id, owner_id, json.dumps(state), now, now))
        if count_created:
            await db.execute("INSERT INTO temp_voice_stats(guild_id,user_id,rooms_created) VALUES (?,?,1) ON CONFLICT(guild_id,user_id) DO UPDATE SET rooms_created=rooms_created+1", (guild_id, owner_id))
            await db.execute("INSERT INTO temp_voice_profiles(guild_id,user_id,last_created) VALUES (?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET last_created=excluded.last_created", (guild_id, owner_id, now))
        await db.commit()


async def update_room(room):
    async with database.connect() as db:
        await db.execute("UPDATE temp_voice_rooms SET owner_id=?,state=?,last_tick=? WHERE guild_id=? AND channel_id=?",
                         (room["owner_id"], json.dumps(room["state"]), room["last_tick"], room["guild_id"], room["channel_id"]))
        await db.commit()


async def delete_room(guild_id, channel_id):
    async with database.connect() as db:
        await db.execute("DELETE FROM temp_voice_rooms WHERE guild_id=? AND channel_id=?", (guild_id, channel_id))
        await db.commit()


async def profile(guild_id, user_id):
    async with database.connect(aiosqlite.Row) as db:
        row = await (await db.execute("SELECT * FROM temp_voice_profiles WHERE guild_id=? AND user_id=?", (guild_id, user_id))).fetchone()
    return {"preferences": json.loads(row["preferences"]), "last_created": row["last_created"]} if row else {"preferences": {}, "last_created": 0}


async def save_profile(guild_id, user_id, prefs):
    async with database.connect() as db:
        await db.execute("INSERT INTO temp_voice_profiles(guild_id,user_id,preferences) VALUES (?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET preferences=excluded.preferences", (guild_id, user_id, json.dumps(prefs)))
        await db.commit()


async def accrue(room, seconds):
    async with database.connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute("INSERT INTO temp_voice_stats(guild_id,user_id,voice_seconds) VALUES (?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET voice_seconds=voice_seconds+excluded.voice_seconds", (room["guild_id"], room["owner_id"], seconds))
        await db.execute("UPDATE temp_voice_rooms SET last_tick=? WHERE channel_id=?", (room["last_tick"], room["channel_id"]))
        await db.commit()


async def metrics(guild_id):
    async with database.connect(aiosqlite.Row) as db:
        total = await (await db.execute("SELECT COALESCE(SUM(voice_seconds),0) seconds,COALESCE(SUM(rooms_created),0) rooms FROM temp_voice_stats WHERE guild_id=?", (guild_id,))).fetchone()
        presets = await (await db.execute("SELECT COUNT(*) n FROM temp_voice_profiles WHERE guild_id=? AND preferences!='{}'", (guild_id,))).fetchone()
        top = await (await db.execute("SELECT user_id,rooms_created,voice_seconds FROM temp_voice_stats WHERE guild_id=? ORDER BY voice_seconds DESC,rooms_created DESC LIMIT 10", (guild_id,))).fetchall()
    return {"total_minutes": int(total["seconds"] // 60), "rooms_created": total["rooms"],
            "saved_profiles": presets["n"]}, [dict(x) for x in top]

"""Shared defaults and safe message formatting for PRIME level controls."""
from copy import deepcopy
import re
from string import Formatter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from streak_experience import (
    DEFAULT_DUPLICATE_TEMPLATE,
    DEFAULT_MILESTONE_TEMPLATE,
    DEFAULT_REMINDER_TEMPLATE,
    DEFAULT_STAGE_UP_TEMPLATE,
)


STREAK_PARTICLES = {"none", "sparks", "shine", "embers", "snow", "petals", "neon"}
STREAK_MESSAGE_VARIABLES = {
    "duplicate": {
        "user", "username", "mention", "streak", "current_streak", "best",
        "best_streak", "stage", "stage_name", "next_stage", "remaining",
        "progress", "time_remaining", "server", "server_rank", "global_rank",
    },
    "success": {
        "user", "username", "mention", "streak", "current_streak", "best",
        "best_streak", "stage", "stage_name", "next_stage", "remaining",
        "progress", "time_remaining", "server", "server_rank", "global_rank",
    },
    "stageUp": {
        "user", "username", "mention", "streak", "current_streak", "best",
        "best_streak", "stage", "stage_name", "next_stage", "remaining",
        "progress", "time_remaining", "server", "server_rank", "global_rank",
    },
    "milestone": {
        "user", "username", "mention", "streak", "current_streak", "best",
        "best_streak", "stage", "stage_name", "next_stage", "remaining",
        "progress", "time_remaining", "server", "server_rank", "global_rank",
        "threshold",
    },
    "reminder": {
        "user", "username", "mention", "streak", "current_streak", "best",
        "best_streak", "stage", "stage_name", "next_stage", "remaining",
        "progress", "time_remaining", "server", "server_rank", "global_rank",
    },
}

TEMPLATE_VARIABLES = frozenset({
    "user", "username", "mention", "level", "old_level", "xp",
    "required_xp", "progress", "rank", "total_members", "messages",
    "voice_time", "streak", "server", "period", "message",
    "passer", "passed", "role",
    "name", "current_streak", "best", "best_streak", "stage",
    "stage_name", "next_stage", "remaining", "time_remaining",
    "server_rank", "global_rank", "server_streak", "global_streak",
    "threshold",
})

DEFAULT_CONTROLS = {
    "rank": {
        "enabled": True,
        "imageOnly": True,
        "sendEmbed": False,
        "showCustomMessage": False,
        "customMessage": "",
        "showCard": True,
        "channels": [],
    },
    "levelup": {
        "sendNotification": True,
        "sendAsEmbed": True,
        "showRankCard": True,
        "mentionUser": True,
        "mentionRole": "",
        "channel": "",
        "message": "مبروك {mention}! وصلت إلى المستوى {level} في {server}.",
        "messages": [{"id": "default", "name": "الافتراضي", "template": "مبروك {mention}! وصلت إلى المستوى {level} في {server}."}],
        "activeMessageId": "default",
        "embedTitle": "🎉 Level Up!",
        "embedColor": "#12D6FF",
        "embedFooter": "",
        "embedThumbnail": "",
        "embedImage": "",
        "timestamp": False,
    },
    "top": {
        "enabled": True,
        "defaultMode": "text",
        "count": 10,
        "showAvatar": True,
        "showProgress": True,
        "embed": True,
        "embedTitle": "🏆 PRIME TOP",
        "embedMessage": "ترتيب XP الدائم حسب النمط المختار.",
        "embedColor": "#12D6FF",
    },
    "notifications": {
        "milestone": {
            "enabled": True,
            "channel": "",
            "message": "{mention} حقق إنجازاً جديداً عند المستوى {level}.",
            "sendAsEmbed": False,
            "mentionUser": True,
            "mentionRole": "",
            "embedTitle": "إنجاز جديد",
            "embedDescription": "{message}",
            "messages": [{"id": "default", "name": "الافتراضي", "template": "{mention} حقق إنجازاً جديداً عند المستوى {level}."}],
            "activeMessageId": "default",
            "embedColor": "#12D6FF",
            "embedFooter": "",
            "embedImage": "",
            "timestamp": False,
        },
        "overtake": {
            "enabled": False,
            "channel": "",
            "message": "{mention} تجاوز {passed} وأصبح في المركز {rank}.",
            "sendAsEmbed": False,
            "mentionUser": True,
            "mentionRole": "",
            "embedTitle": "تجاوز في PRIME TOP",
            "embedDescription": "{message}",
            "messages": [{"id": "default", "name": "الافتراضي", "template": "{mention} تجاوز {passed} وأصبح في المركز {rank}."}],
            "activeMessageId": "default",
            "embedColor": "#12D6FF",
            "embedFooter": "",
            "embedImage": "",
            "timestamp": False,
        },
        "role_promotion": {
            "enabled": False,
            "channel": "",
            "message": "مبروك {mention}! حصلت على رتبة {role}.",
            "sendAsEmbed": True,
            "mentionUser": True,
            "mentionRole": "",
            "embedTitle": "🎖️ ترقية رتبة",
            "embedDescription": "{message}",
            "messages": [{"id": "default", "name": "الافتراضي", "template": "مبروك {mention}! حصلت على رتبة {role}."}],
            "activeMessageId": "default",
            "embedColor": "#6366F1",
            "embedFooter": "",
            "embedImage": "",
            "timestamp": False,
        },
    },
    "periodic": {
        "daily": {
            "enabled": False, "channel": "", "time": "09:00", "timezone": "UTC",
            "rewardRole": "", "winners": 1, "mode": "both", "message": "🏆 الفائز بـ TOP اليوم: {mention} · {xp} XP",
            "embed": True, "embedTitle": "🏆 PRIME Daily TOP",
            "embedDescription": "{message}", "embedColor": "#12D6FF",
            "mentionWinners": True, "showXp": True, "showRank": True,
        },
        "weekly": {
            "enabled": False, "channel": "", "time": "18:00", "timezone": "UTC",
            "weekday": 4, "rewardRole": "", "winners": 1, "mode": "both",
            "message": "🏆 الفائز بـ TOP الأسبوعي: {mention} · {xp} XP",
            "embed": True, "embedTitle": "🏆 PRIME Weekly TOP",
            "embedDescription": "{message}", "embedColor": "#4263EB",
            "mentionWinners": True, "showXp": True, "showRank": True,
        },
        "monthly": {
            "enabled": False, "channel": "", "time": "20:00", "timezone": "UTC",
            "dayOfMonth": 1, "rewardRole": "", "winners": 1, "mode": "both",
            "message": "🏆 الفائز بـ TOP الشهري: {mention} · {xp} XP",
            "embed": True, "embedTitle": "🏆 PRIME Monthly TOP",
            "embedDescription": "{message}", "embedColor": "#8B5CF6",
            "mentionWinners": True, "showXp": True, "showRank": True,
        },
    },
    "streak": {
        "progressCardEnabled": True,
        "successReaction": "🔥",
        # None means: continue using the existing shared Phase 3 definitions.
        "stages": None,
        "milestones": None,
        "messages": {
            "duplicate": {
                "enabled": True,
                "message": DEFAULT_DUPLICATE_TEMPLATE,
            },
            "success": {
                "enabled": True,
                "message": "🔥 {streak} يوم · {stage_name}",
            },
            "stageUp": {
                "enabled": True,
                "message": DEFAULT_STAGE_UP_TEMPLATE,
            },
            "milestone": {
                "enabled": True,
                "message": DEFAULT_MILESTONE_TEMPLATE,
            },
            "reminder": {
                "enabled": True,
                "time": "21:00",
                "message": DEFAULT_REMINDER_TEMPLATE,
            },
        },
    },
}


def controls_with_defaults(value=None, settings=None):
    """Merge persisted controls over defaults and migrate legacy level-up values."""
    result = deepcopy(DEFAULT_CONTROLS)
    if isinstance(value, dict):
        for section, defaults in DEFAULT_CONTROLS.items():
            source = value.get(section)
            if isinstance(defaults, dict) and isinstance(source, dict):
                if section == "periodic" or section == "notifications":
                    for key, nested_defaults in defaults.items():
                        nested_source = source.get(key)
                        if isinstance(nested_source, dict):
                            result[section][key].update(nested_source)
                elif section == "streak":
                    for key, nested_default in defaults.items():
                        nested_source = source.get(key)
                        if key == "messages" and isinstance(nested_source, dict):
                            for message_key, message_default in nested_default.items():
                                message_source = nested_source.get(message_key)
                                if isinstance(message_source, dict):
                                    result[section][key][message_key].update(message_source)
                        elif key in source:
                            result[section][key] = deepcopy(nested_source)
                else:
                    result[section].update(source)
    legacy_duplicate_template = (
        "🔥 تم تسجيل ستريكك اليوم بالفعل.\n"
        "ستريكك الحالي: {streak} يوم\n"
        "أفضل ستريك: {best} يوم\n"
        "المرحلة: {stage_name}\n"
        "ارجع غدًا لتسجيل اليوم التالي."
    )
    duplicate_message = result["streak"]["messages"]["duplicate"]
    if str(duplicate_message.get("message") or "").strip() == legacy_duplicate_template:
        duplicate_message["message"] = DEFAULT_DUPLICATE_TEMPLATE
    settings = settings or {}
    # Migrate legacy single-message controls into the new preset manager
    # without changing the currently active message.
    for name in ("levelup", "milestone", "overtake", "role_promotion"):
        item = result["levelup"] if name == "levelup" else result["notifications"][name]
        source = value.get("levelup" if name == "levelup" else "notifications", {}).get(name) if isinstance(value, dict) else None
        if not isinstance(source, dict) or not isinstance(source.get("messages"), list):
            current_message = str(item.get("message") or "")
            item["messages"] = [{
                "id": "default",
                "name": "الافتراضي",
                "template": current_message,
            }]
            item["activeMessageId"] = "default"
    if not isinstance(value, dict) or "channels" not in value.get("rank", {}):
        result["rank"]["channels"] = [
            str(item) for item in settings.get("command_rank_channels", [])
        ]
    if not isinstance(value, dict) or "embedTitle" not in value.get("levelup", {}):
        result["levelup"]["embedTitle"] = settings.get("levelup_title") or "🎉 Level Up!"
    if not isinstance(value, dict) or "channel" not in value.get("levelup", {}):
        legacy_channel = settings.get("levelup_channel_id")
        if legacy_channel:
            result["levelup"]["channel"] = str(legacy_channel)
    if not isinstance(value, dict) or "message" not in value.get("levelup", {}):
        result["levelup"]["message"] = settings.get("levelup_template") or result["levelup"]["message"]
    for name, enabled_field, channel_field, template_field in (
        ("milestone", "milestone_alert_enabled", "milestone_channel_id", "milestone_template"),
        ("overtake", "overtake_alert_enabled", "overtake_channel_id", "overtake_template"),
    ):
        item = result["notifications"][name]
        source = value.get("notifications", {}).get(name) if isinstance(value, dict) else None
        if not isinstance(source, dict) or "enabled" not in source:
            item["enabled"] = bool(settings.get(enabled_field, item["enabled"]))
        if not isinstance(source, dict) or "channel" not in source:
            legacy_channel = settings.get(channel_field)
            if legacy_channel:
                item["channel"] = str(legacy_channel)
        if not isinstance(source, dict) or "message" not in source:
            item["message"] = settings.get(template_field) or item["message"]
    return result


class SafeTemplateValues(dict):
    """Unknown placeholders remain visible instead of raising in event listeners."""
    def __missing__(self, key):
        return "{" + str(key) + "}"


def render_template(template, values):
    try:
        return str(template or "").format_map(SafeTemplateValues(values)).strip()
    except (ValueError, IndexError, AttributeError):
        return str(template or "").strip()


def _bool(value, name):
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _text(value, name, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{name} must be text under {maximum + 1} characters")
    return value.strip()


def _color(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError(f"invalid {name}")
    return value.lower()


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the allowed range")
    return value


def _streak_template(value, name, allowed):
    value = _text(value, name, 500)
    try:
        for _, field, spec, conversion in Formatter().parse(value):
            if field is None:
                continue
            if (
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field)
                or field not in allowed
                or spec
                or conversion
            ):
                raise ValueError(f"unsupported variable in {name}")
    except ValueError as error:
        raise ValueError(f"invalid {name} template") from error
    return value


def _optional_streak_image(value, name, validate_image):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError(f"{name} must be a public HTTPS URL")
    value = value.strip()
    if not value.startswith("https://"):
        raise ValueError(f"{name} must use HTTPS")
    return validate_image(value) if validate_image else value


def _validate_streak_controls(streak, validate_image):
    streak["progressCardEnabled"] = _bool(
        streak.get("progressCardEnabled"), "streak.progressCardEnabled",
    )
    streak["successReaction"] = _text(
        streak.get("successReaction"), "streak.successReaction", 100,
    )

    stages = streak.get("stages")
    if stages is not None:
        if not isinstance(stages, list) or len(stages) > 100:
            raise ValueError("streak.stages must contain at most 100 entries")
        clean_stages, keys, thresholds = [], set(), set()
        for stage in stages:
            if not isinstance(stage, dict):
                raise ValueError("invalid streak stage")
            key = str(stage.get("stage_key") or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", key) or key in keys:
                raise ValueError("invalid or duplicate streak stage key")
            threshold = _integer(
                stage.get("threshold"), "streak stage threshold", 1, 2_147_483_647,
            )
            if threshold in thresholds:
                raise ValueError("streak stage thresholds must be unique")
            name = _text(stage.get("name"), "streak stage name", 80)
            if not name:
                raise ValueError("streak stage name is required")
            message = stage.get("message")
            message = (
                _streak_template(message, "streak stage message", STREAK_MESSAGE_VARIABLES["stageUp"])
                if message not in (None, "") else None
            )
            color = _color(stage.get("color"), "streak stage color")
            reaction = stage.get("reaction")
            reaction = _text(reaction, "streak stage reaction", 100) if reaction else None
            description = _text(stage.get("description", ""), "streak stage description", 400)
            glow = _integer(stage.get("glow", 0), "streak stage glow", 0, 100)
            particle = stage.get("particle", "none")
            if particle not in STREAK_PARTICLES:
                raise ValueError("unsupported streak stage particle")
            image = _optional_streak_image(
                stage.get("image"), "streak stage image", validate_image,
            )
            enabled = _bool(stage.get("enabled"), "streak stage enabled")
            clean_stages.append({
                "stage_key": key, "threshold": threshold, "name": name,
                "message": message, "image": image, "color": color,
                "reaction": reaction, "description": description, "glow": glow,
                "particle": particle, "enabled": enabled,
            })
            keys.add(key)
            thresholds.add(threshold)
        streak["stages"] = clean_stages

    milestones = streak.get("milestones")
    if milestones is not None:
        if not isinstance(milestones, list) or len(milestones) > 200:
            raise ValueError("streak.milestones must contain at most 200 entries")
        clean_milestones, thresholds = [], set()
        for milestone in milestones:
            if not isinstance(milestone, dict):
                raise ValueError("invalid streak milestone")
            threshold = _integer(
                milestone.get("threshold"), "streak milestone threshold",
                1, 2_147_483_647,
            )
            if threshold in thresholds:
                raise ValueError("streak milestone thresholds must be unique")
            message = milestone.get("message", "")
            message = (
                _streak_template(
                    message, "streak milestone message",
                    STREAK_MESSAGE_VARIABLES["milestone"],
                )
                if message else ""
            )
            image = _optional_streak_image(
                milestone.get("image"), "streak milestone image", validate_image,
            )
            reaction = milestone.get("reaction")
            reaction = _text(reaction, "streak milestone reaction", 100) if reaction else None
            enabled = _bool(milestone.get("enabled"), "streak milestone enabled")
            clean_milestones.append({
                "threshold": threshold, "message": message, "image": image,
                "reaction": reaction, "enabled": enabled,
            })
            thresholds.add(threshold)
        streak["milestones"] = clean_milestones

    messages = streak.get("messages")
    if not isinstance(messages, dict):
        raise ValueError("invalid streak messages")
    for key, allowed in STREAK_MESSAGE_VARIABLES.items():
        item = messages.get(key)
        if not isinstance(item, dict):
            raise ValueError(f"invalid streak {key} message settings")
        item["enabled"] = _bool(item.get("enabled"), f"streak.messages.{key}.enabled")
        item["message"] = _streak_template(
            item.get("message"), f"streak.messages.{key}.message", allowed,
        )
        if key == "reminder":
            reminder_time = item.get("time")
            if not isinstance(reminder_time, str) or not re.fullmatch(
                r"(?:[01]\d|2[0-3]):[0-5]\d", reminder_time,
            ):
                raise ValueError("streak reminder time must use HH:MM")
            item["time"] = reminder_time
    return streak


def validate_controls(
    value, legacy_settings, *, validate_channel, validate_role,
    validate_assignable_role, validate_image=None,
):
    result = controls_with_defaults(value, legacy_settings)
    rank = result["rank"]
    for key in ("enabled", "imageOnly", "sendEmbed", "showCustomMessage", "showCard"):
        rank[key] = _bool(rank.get(key), f"rank.{key}")
    rank["customMessage"] = _text(rank.get("customMessage"), "rank.customMessage", 500)
    if not isinstance(rank.get("channels"), list) or len(rank["channels"]) > 200:
        raise ValueError("rank.channels must be an array with at most 200 entries")
    rank["channels"] = list(dict.fromkeys(
        str(validate_channel(item)) for item in rank["channels"] if item
    ))

    levelup = result["levelup"]
    for key in ("sendNotification", "sendAsEmbed", "showRankCard", "mentionUser", "timestamp"):
        levelup[key] = _bool(levelup.get(key), f"levelup.{key}")
    channel = levelup.get("channel")
    levelup["channel"] = str(validate_channel(channel, messageable=True)) if channel else ""
    levelup["message"] = _text(levelup.get("message"), "levelup.message", 1000)
    profiles = levelup.get("messages")
    if not isinstance(profiles, list) or not profiles or len(profiles) > 20:
        raise ValueError("levelup.messages must contain 1-20 presets")
    seen_message_ids = set()
    active_id = str(levelup.get("activeMessageId") or "")
    active_profile = None
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError("invalid levelup message preset")
        profile_id = str(profile.get("id") or "").strip()
        profile_name = str(profile.get("name") or "").strip()
        profile_template = _text(profile.get("template"), "levelup.preset.template", 500)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", profile_id) or profile_id in seen_message_ids:
            raise ValueError("invalid or duplicate levelup message preset id")
        if not profile_name or len(profile_name) > 80:
            raise ValueError("levelup preset name must contain 1-80 characters")
        for token in re.findall(r"\{[^{}]+\}", profile_template):
            if token[1:-1] not in TEMPLATE_VARIABLES:
                raise ValueError(f"unsupported variable {token} in levelup preset")
        seen_message_ids.add(profile_id)
        profile["id"] = profile_id
        profile["name"] = profile_name
        profile["template"] = profile_template
        if profile_id == active_id:
            active_profile = profile
    if active_profile is None:
        active_profile = profiles[0]
        levelup["activeMessageId"] = active_profile["id"]
    levelup["message"] = active_profile["template"]
    role = levelup.get("mentionRole")
    levelup["mentionRole"] = str(validate_role(role)) if role else ""
    levelup["embedTitle"] = _text(levelup.get("embedTitle"), "levelup.embedTitle", 256)
    levelup["embedFooter"] = _text(levelup.get("embedFooter"), "levelup.embedFooter", 2048)
    levelup["embedColor"] = _color(levelup.get("embedColor"), "levelup.embedColor")
    for key in ("embedThumbnail", "embedImage"):
        url = _text(levelup.get(key, ""), f"levelup.{key}", 400)
        if url and not url.startswith("https://"):
            raise ValueError(f"levelup.{key} must use HTTPS")
        levelup[key] = url

    top = result["top"]
    for key in ("enabled", "showAvatar", "showProgress", "embed"):
        top[key] = _bool(top.get(key), f"top.{key}")
    if top.get("defaultMode") not in {"text", "voice"}:
        raise ValueError("top.defaultMode must be text or voice")
    top["count"] = _integer(top.get("count"), "top.count", 1, 20)
    top["embedTitle"] = _text(top.get("embedTitle"), "top.embedTitle", 256)
    top["embedMessage"] = _text(top.get("embedMessage"), "top.embedMessage", 1000)
    top["embedColor"] = _color(top.get("embedColor"), "top.embedColor")

    for name in ("milestone", "overtake", "role_promotion"):
        item = result["notifications"][name]
        for key in ("enabled", "sendAsEmbed", "mentionUser", "timestamp"):
            item[key] = _bool(item.get(key), f"notifications.{name}.{key}")
        channel = item.get("channel")
        item["channel"] = str(validate_channel(channel, messageable=True)) if channel else ""
        item["message"] = _text(item.get("message"), f"notifications.{name}.message", 1000)
        item["embedDescription"] = _text(item.get("embedDescription"), f"notifications.{name}.embedDescription", 4000)
        item["embedImage"] = _text(item.get("embedImage", ""), f"notifications.{name}.embedImage", 400)
        if item["embedImage"] and not item["embedImage"].startswith("https://"):
            raise ValueError(f"notifications.{name}.embedImage must use HTTPS")
        profiles = item.get("messages")
        if not isinstance(profiles, list) or not profiles or len(profiles) > 20:
            raise ValueError(f"notifications.{name}.messages must contain 1-20 presets")
        seen_message_ids = set()
        active_id = str(item.get("activeMessageId") or "")
        active_profile = None
        for profile in profiles:
            if not isinstance(profile, dict):
                raise ValueError(f"invalid notifications.{name} message preset")
            profile_id = str(profile.get("id") or "").strip()
            profile_name = str(profile.get("name") or "").strip()
            profile_template = profile.get("template")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", profile_id) or profile_id in seen_message_ids:
                raise ValueError(f"invalid or duplicate notifications.{name} message preset id")
            if not profile_name or len(profile_name) > 80:
                raise ValueError(f"notifications.{name} preset name must contain 1-80 characters")
            profile_template = _text(profile_template, f"notifications.{name}.preset.template", 500)
            if profile_id not in seen_message_ids:
                seen_message_ids.add(profile_id)
            profile["id"] = profile_id
            profile["name"] = profile_name
            profile["template"] = profile_template
            for token in re.findall(r"\{[^{}]+\}", profile_template):
                if token[1:-1] not in TEMPLATE_VARIABLES:
                    raise ValueError(f"unsupported variable {token} in notifications.{name} preset")
            if profile_id == active_id:
                active_profile = profile
        if active_profile is None:
            active_profile = profiles[0]
            item["activeMessageId"] = active_profile["id"]
        item["message"] = active_profile["template"]
        role = item.get("mentionRole")
        item["mentionRole"] = str(validate_role(role)) if role else ""
        item["embedTitle"] = _text(item.get("embedTitle"), f"{name}.embedTitle", 256)
        item["embedFooter"] = _text(item.get("embedFooter"), f"{name}.embedFooter", 2048)
        item["embedColor"] = _color(item.get("embedColor"), f"{name}.embedColor")

    for period in ("daily", "weekly", "monthly"):
        item = result["periodic"][period]
        item["enabled"] = _bool(item.get("enabled"), f"{period}.enabled")
        item["mode"] = item.get("mode", "both")
        if item["mode"] not in {"text", "voice", "both"}:
            raise ValueError(f"{period}.mode must be text, voice, or both")
        for key in ("embed", "mentionWinners", "showXp", "showRank"):
            item[key] = _bool(item.get(key), f"{period}.{key}")
        channel = item.get("channel")
        item["channel"] = str(validate_channel(channel, messageable=True)) if channel else ""
        role = item.get("rewardRole")
        item["rewardRole"] = str(validate_assignable_role(role)) if role else ""
        if not isinstance(item.get("time"), str) or not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", item["time"],
        ):
            raise ValueError(f"{period}.time must use HH:MM")
        zone = _text(item.get("timezone"), f"{period}.timezone", 64)
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"{period}.timezone is not supported")
        item["timezone"] = zone
        item["winners"] = _integer(item.get("winners"), f"{period}.winners", 1, 20)
        if period == "weekly":
            item["weekday"] = _integer(item.get("weekday"), "weekly.weekday", 0, 6)
        if period == "monthly":
            item["dayOfMonth"] = _integer(item.get("dayOfMonth"), "monthly.dayOfMonth", 1, 28)
        item["message"] = _text(item.get("message"), f"{period}.message", 1000)
        item["embedTitle"] = _text(item.get("embedTitle"), f"{period}.embedTitle", 256)
        item["embedDescription"] = _text(
            item.get("embedDescription"), f"{period}.embedDescription", 4000,
        )
        item["embedColor"] = _color(item.get("embedColor"), f"{period}.embedColor")
        if item["enabled"] and not item["channel"]:
            raise ValueError(f"{period} TOP requires a channel")
    result["streak"] = _validate_streak_controls(
        result["streak"], validate_image,
    )
    return result
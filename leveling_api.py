"""Authenticated dashboard API for the existing PRIME leveling system."""
import math
import re
import secrets
import sqlite3
import string
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from aiohttp import web

import database
from cogs.card_generator import (
    generate_level_up_gif,
    generate_rank_card,
    has_animated_background,
)
from cogs.card_images import validate_url
from level_progression import text_progress, xp_required
from prime_level_controls import TEMPLATE_VARIABLES, controls_with_defaults, validate_controls


SNOWFLAKE_RE = re.compile(r"^\d{15,22}$")
PUBLIC_SLUG_RE = re.compile(r"^(?=.{3,40}$)[a-z0-9]+(?:-[a-z0-9]+)*$")
LAYOUTS = {"vertical", "stats", "minimal", "ring", "classic", "banner", "square", "spotlight"}
PARTICLES = {"none", "sparks", "shine", "embers", "snow", "petals", "neon"}
CARD_DESIGN_DEFAULTS = {
    "glowStrength": 54, "particleDensity": 46, "particleColor": "accent",
    "barStyle": "gradient", "frame": "auto", "bgOverlay": 28, "bgBlur": 4,
    "animationEnabled": True, "animationStyle": "beam", "animationIntensity": 62,
    "stats": {"messages": True, "voice": True, "streak": True, "serverRank": True},
}
CARD_BAR_STYLES = {"gradient", "solid", "segmented", "neon"}
CARD_FRAMES = {"auto", "none", "bronze", "silver", "gold", "diamond"}
CARD_ANIMATION_STYLES = {"beam", "aurora", "burst"}
TEMPLATE_FIELDS = {
    "levelup": set(TEMPLATE_VARIABLES),
    "milestone": set(TEMPLATE_VARIABLES),
    "overtake": set(TEMPLATE_VARIABLES),
    "role_promotion": set(TEMPLATE_VARIABLES),
}
BOOL_FIELDS = {
    "enabled", "text", "reaction", "streak",
    "voiceEnabled", "muteBlock", "deafBlock", "dimEnabled", "separate",
    "highestOnly", "animated", "showStats",
}


def _snowflake(value):
    if isinstance(value, bool) or not SNOWFLAKE_RE.fullmatch(str(value or "")):
        raise ValueError("invalid Discord ID")
    return int(value)


def _bool(value, name):
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the allowed range")
    return value


def _number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} is outside the allowed range")
    return result


def _clean_card_design(card):
    """Validate and normalize the additive, JSON-backed card design contract."""
    design = {
        "glowStrength": _integer(card.get("glowStrength", 54), "glowStrength", 0, 100),
        "particleDensity": _integer(card.get("particleDensity", 46), "particleDensity", 0, 100),
        "particleColor": card.get("particleColor", "accent"),
        "barStyle": card.get("barStyle", "gradient"),
        "frame": card.get("frame", "auto"),
        "bgOverlay": _integer(card.get("bgOverlay", 28), "bgOverlay", 0, 85),
        "bgBlur": _integer(card.get("bgBlur", 4), "bgBlur", 0, 18),
        "animationEnabled": _bool(card.get("animationEnabled", True), "animationEnabled"),
        "animationStyle": card.get("animationStyle", "beam"),
        "animationIntensity": _integer(card.get("animationIntensity", 62), "animationIntensity", 0, 100),
    }
    if design["particleColor"] != "accent" and (
        not isinstance(design["particleColor"], str)
        or not re.fullmatch(r"#[0-9a-fA-F]{6}", design["particleColor"])
    ):
        raise ValueError("invalid particle color")
    if design["barStyle"] not in CARD_BAR_STYLES:
        raise ValueError("invalid progress bar style")
    if design["frame"] not in CARD_FRAMES:
        raise ValueError("invalid rank frame")
    if design["animationStyle"] not in CARD_ANIMATION_STYLES:
        raise ValueError("invalid level-up animation style")
    stats = card.get("stats", CARD_DESIGN_DEFAULTS["stats"])
    if not isinstance(stats, dict):
        raise ValueError("card stats must be an object")
    design["stats"] = {
        key: _bool(stats.get(key, True), f"stats.{key}")
        for key in ("messages", "voice", "streak", "serverRank")
    }

    presets = card.get("presets", [])
    if not isinstance(presets, list) or len(presets) > 8:
        raise ValueError("card presets must be an array with at most 8 entries")
    clean_presets = []
    seen_ids = set()
    for preset in presets:
        if not isinstance(preset, dict):
            raise ValueError("invalid card preset")
        preset_id, name = preset.get("id"), preset.get("name")
        if not isinstance(preset_id, str) or not preset_id or len(preset_id) > 80:
            raise ValueError("invalid card preset id")
        if preset_id in seen_ids:
            raise ValueError("duplicate card preset id")
        seen_ids.add(preset_id)
        if not isinstance(name, str) or not name.strip() or len(name) > 40:
            raise ValueError("invalid card preset name")
        layout, particles = preset.get("layout"), preset.get("particles")
        color, bg = preset.get("color"), preset.get("bg", "")
        if layout not in LAYOUTS or particles not in PARTICLES:
            raise ValueError("invalid card preset layout or particle effect")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("invalid card preset color")
        if not isinstance(bg, str) or len(bg) > 400:
            raise ValueError("invalid card preset background")
        bg = bg.strip()
        if bg:
            if not bg.startswith("https://"):
                raise ValueError("card preset background must use HTTPS")
            validate_url(bg)
        preset_design = _clean_card_design({**preset, "presets": []})
        preset_design.pop("presets", None)
        clean_presets.append({
            "id": preset_id, "name": name.strip(), "layout": layout,
            "particles": particles, "color": color.lower(), "bg": bg,
            "animated": _bool(preset.get("animated", True), "preset.animated"),
            "showStats": _bool(preset.get("showStats", True), "preset.showStats"),
            **preset_design,
        })
    design["presets"] = clean_presets
    return design


def _owned_role(guild, raw_id, *, assignable=False):
    role_id = _snowflake(raw_id)
    role = guild.get_role(role_id)
    if role is None or getattr(getattr(role, "guild", None), "id", guild.id) != guild.id:
        raise ValueError("role does not belong to this server")
    if assignable:
        bot_member = getattr(guild, "me", None)
        if bot_member is None:
            bot = getattr(guild, "_state", None)
            bot_member = getattr(bot, "user", None)
            bot_member = guild.get_member(getattr(bot_member, "id", 0)) if bot_member else None
        permissions = getattr(bot_member, "guild_permissions", None)
        top_role = getattr(bot_member, "top_role", None)
        if (
            role_id == guild.id or getattr(role, "managed", False)
            or not getattr(permissions, "manage_roles", False)
            or top_role is None or not role < top_role
        ):
            raise ValueError("reward role cannot be assigned by the bot")
    return role_id


def _owned_channel(guild, raw_id, *, messageable=False):
    channel_id = _snowflake(raw_id)
    channel = guild.get_channel(channel_id)
    if channel is None or getattr(getattr(channel, "guild", None), "id", guild.id) != guild.id:
        raise ValueError("channel does not belong to this server")
    if messageable:
        channel_type = str(getattr(channel, "type", ""))
        valid_type = isinstance(channel, (discord.TextChannel, discord.Thread)) or channel_type in {
            "text", "news", "public_thread", "private_thread", "news_thread",
        }
        if not valid_type or not callable(getattr(channel, "send", None)):
            raise ValueError("announcement channel must be text-based")
    return channel_id


def _id_list(guild, values, name, *, kind):
    if not isinstance(values, list) or len(values) > 200:
        raise ValueError(f"{name} must be an array with at most 200 entries")
    result = []
    seen = set()
    for raw_id in values:
        item_id = (
            _owned_role(guild, raw_id) if kind == "role"
            else _owned_channel(guild, raw_id)
        )
        if item_id not in seen:
            seen.add(item_id)
            result.append(item_id)
    return result


def _format_template(value, key):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError(f"{key} template must contain 1-500 characters")
    allowed = TEMPLATE_FIELDS.get(key, set(TEMPLATE_VARIABLES))
    try:
        parsed = string.Formatter().parse(value)
        for _, field, spec, conversion in parsed:
            if field is not None and (
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field)
                or field not in allowed
                or spec or conversion
            ):
                raise ValueError(f"invalid placeholder in {key} template")
    except (ValueError, KeyError) as error:
        raise ValueError(f"invalid {key} template") from error
    return value.strip()


def _parse_boosts(guild_settings, draft_items, now):
    if not isinstance(draft_items, list) or len(draft_items) > 10:
        raise ValueError("boosts must be an array with at most 10 entries")
    stored = {
        str(item.get("id")): item
        for item in (guild_settings or {}).get("timed_xp_boosts", [])
        if isinstance(item, dict) and item.get("id")
    }
    result, seen = [], set()
    for item in draft_items:
        if not isinstance(item, dict):
            raise ValueError("invalid boost")
        label = item.get("label")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 40:
            raise ValueError("boost name must contain 1-40 characters")
        multiplier = _number(item.get("mult"), "boost multiplier", 1, 10)
        hours = _integer(item.get("hours"), "boost hours", 1, 168)
        boost_id = str(item.get("id") or secrets.token_hex(8))
        if len(boost_id) > 64 or boost_id in seen:
            raise ValueError("duplicate or invalid boost ID")
        seen.add(boost_id)
        previous = stored.get(boost_id)
        expiry = None
        starts = None
        if previous:
            old_expiry = previous.get("expires_at")
            if item.get("expiresAt") == old_expiry:
                try:
                    parsed_expiry = datetime.fromisoformat(
                        str(old_expiry).replace("Z", "+00:00")
                    )
                    if parsed_expiry.tzinfo is None:
                        parsed_expiry = parsed_expiry.replace(tzinfo=timezone.utc)
                    remaining = max(0, math.ceil((parsed_expiry - now).total_seconds() / 3600))
                    if hours == remaining:
                        expiry = parsed_expiry.astimezone(timezone.utc).isoformat()
                        starts = previous.get("starts_at")
                except (TypeError, ValueError):
                    pass
        if expiry is None:
            starts = now.isoformat()
            expiry = (now + timedelta(hours=hours)).isoformat()
        result.append({
            "id": boost_id,
            "label": label.strip(),
            "multiplier": multiplier,
            "starts_at": starts,
            "expires_at": expiry,
        })
    return result


def _serialize_boosts(settings, now):
    result = []
    for item in settings.get("timed_xp_boosts", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            expiry = datetime.fromisoformat(
                str(item.get("expires_at", "")).replace("Z", "+00:00")
            )
            starts = datetime.fromisoformat(
                str(item.get("starts_at", "")).replace("Z", "+00:00")
            )
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if not starts <= now < expiry:
                continue
            hours = max(1, math.ceil((expiry - now).total_seconds() / 3600))
            result.append({
                "id": str(item.get("id") or ""),
                "label": str(item.get("label") or ""),
                "mult": float(item.get("multiplier", 1)),
                "hours": min(hours, 168),
                "expiresAt": expiry.astimezone(timezone.utc).isoformat(),
            })
        except (TypeError, ValueError, OverflowError):
            continue
    return result


def _level_value(settings, key, default):
    value = settings.get(key, default)
    return bool(value)


async def _dashboard_snapshot(guild_id):
    settings = await database.get_level_settings(guild_id) or {}
    rewards = await database.get_level_rewards(guild_id)
    multipliers = await database.get_level_multipliers(guild_id)
    blacklist = await database.get_level_blacklist(guild_id)
    now = datetime.now(timezone.utc)
    draft = {
        "general": {
            "enabled": _level_value(settings, "is_enabled", True),
            "text": _level_value(settings, "text_xp_enabled", True),
            "reaction": _level_value(settings, "reaction_xp_enabled", True),
            "streak": _level_value(settings, "streak_enabled", True),
        },
        "public": {
            "enabled": (
                _level_value(settings, "web_leaderboard_enabled", False)
                and bool(settings.get("web_slug"))
            ),
            "slug": str(settings.get("web_slug") or ""),
        },
        "points": {
            "xpMultiplier": float(settings.get("xp_multiplier", 1) or 0),
            "minXp": int(settings.get("text_xp_min", 15) or 0),
            "maxXp": int(settings.get("text_xp_max", 25) or 0),
            "cooldown": int(settings.get("message_cooldown_seconds", 60) or 0),
            "roleMult": [
                {"id": str(row["target_id"]), "mult": float(row["multiplier"])}
                for row in multipliers if row["target_type"] == "role"
            ],
            "chanMult": [
                {"id": str(row["target_id"]), "mult": float(row["multiplier"])}
                for row in multipliers if row["target_type"] == "channel"
            ],
            "boosts": _serialize_boosts(settings, now),
            "allowedChannels": [str(value) for value in settings.get("text_allowed_channels", [])],
            "bl": {
                "channels": [str(row["target_id"]) for row in blacklist if row["target_type"] == "channel"],
                "users": [str(row["target_id"]) for row in blacklist if row["target_type"] == "user"],
                "roles": [str(row["target_id"]) for row in blacklist if row["target_type"] == "role"],
            },
        },
        "voice": {
            "enabled": _level_value(settings, "voice_xp_enabled", True),
            "xpPerMin": int(settings.get("voice_xp_per_minute", 20) or 0),
            "muteBlock": _level_value(settings, "voice_mute_no_xp", True),
            "deafBlock": _level_value(settings, "voice_deafen_no_xp", True),
            "minMembers": int(settings.get("voice_min_two_members", 1) and settings.get("voice_min_members", 2) or 1),
            "dimEnabled": _level_value(settings, "voice_diminishing_enabled", False),
            "dimThreshold": int(settings.get("voice_diminishing_mins", 60) or 0),
            "dimRate": round(float(settings.get("voice_diminishing_rate", 0.5) or 0) * 100),
            "separate": _level_value(settings, "voice_separate_levels", True),
        },
        "rewards": {
            "highestOnly": _level_value(settings, "rewards_single_highest", True),
            "list": [
                {"level": int(row["level_required"]), "role": str(row["role_id"]), "type": row["reward_type"]}
                for row in rewards
            ],
        },
        "card": {
            "layout": settings.get("card_layout", "vertical") or "vertical",
            "particles": settings.get("card_particles", "none") or "none",
            "color": settings.get("card_color", "#1E293B") or "#1E293B",
            "bg": settings.get("card_bg_url", "") or "",
            "animated": _level_value(settings, "card_animated_bar", True),
            "showStats": _level_value(settings, "card_show_stats", True),
            **{
                **CARD_DESIGN_DEFAULTS,
                **(settings.get("card_design") if isinstance(settings.get("card_design"), dict) else {}),
                "stats": {
                    **CARD_DESIGN_DEFAULTS["stats"],
                    **((settings.get("card_design") or {}).get("stats", {})
                       if isinstance(settings.get("card_design"), dict) else {}),
                },
            },
        },
        "messages": {
            "levelup": {
                "on": _level_value(settings, "levelup_enabled", True),
                "channel": str(settings.get("levelup_channel_id") or ""),
                "tpl": settings.get("levelup_template") or "مبروك {user}! وصلت إلى المستوى {level} في {server}.",
            },
            "milestone": {
                "on": _level_value(settings, "milestone_alert_enabled", True),
                "channel": str(settings.get("milestone_channel_id") or ""),
                "tpl": settings.get("milestone_template") or "{user} حقق إنجازاً جديداً عند المستوى {level}.",
            },
            "overtake": {
                "on": _level_value(settings, "overtake_alert_enabled", False),
                "channel": str(settings.get("overtake_channel_id") or ""),
                "tpl": settings.get("overtake_template") or "{passer} تجاوز {passed} وأصبح في المركز {rank}.",
            },
        },
    }
    draft["prime"] = controls_with_defaults(settings.get("prime_controls"), settings)
    prime = draft["prime"]
    streak_controls = prime["streak"]
    configured_stages = streak_controls.get("stages")
    if configured_stages is None:
        configured_stages = await database.get_streak_stages(include_disabled=True)
    configured_milestones = streak_controls.get("milestones")
    if configured_milestones is None:
        configured_milestones = await database.get_streak_milestones(include_disabled=True)
    draft["streak"] = {
        "enabled": bool(_level_value(settings, "streak_enabled", True)),
        "channel": str(settings.get("streak_channel_id") or ""),
        "dailyXp": int(settings.get("streak_daily_xp", 50) or 0),
        "maxCap": int(settings.get("streak_max_cap", 500) or 0),
        "timezone": "Asia/Riyadh",
        "resetTime": "00:00",
        "progressCardEnabled": bool(streak_controls.get("progressCardEnabled", True)),
        "successReaction": str(streak_controls.get("successReaction") or ""),
        "messages": streak_controls["messages"],
        "stages": [
            {
                **stage,
                "threshold": int(stage["threshold"]),
                "enabled": bool(stage.get("enabled", True)),
                "glow": int(stage.get("glow", 0) or 0),
                "message": stage.get("message"),
                "image": stage.get("image"),
                "reaction": stage.get("reaction"),
            }
            for stage in configured_stages
        ],
        "milestones": [
            {
                **milestone,
                "threshold": int(milestone["threshold"]),
                "enabled": bool(milestone.get("enabled", True)),
                "image": milestone.get("image"),
                "reaction": milestone.get("reaction"),
            }
            for milestone in configured_milestones
        ],
    }
    levelup = prime["levelup"]
    draft["messages"]["levelup"] = {
        "on": bool(levelup.get("sendNotification", True)),
        "channel": str(levelup.get("channel") or ""),
        "tpl": str(levelup.get("message") or ""),
    }
    for key in ("milestone", "overtake", "role_promotion"):
        item = prime["notifications"].get(key, {})
        draft["messages"][key] = {
            "on": bool(item.get("enabled", False)),
            "channel": str(item.get("channel") or ""),
            "tpl": str(item.get("message") or ""),
        }
    return {
        "revision": int(settings.get("revision", 0) or 0),
        "draft": draft,
        "configured": bool(settings),
    }


def _validate_draft(guild, draft, current_settings):
    if not isinstance(draft, dict):
        raise ValueError("draft must be an object")
    general = draft.get("general")
    public = draft.get("public")
    if public is None:
        legacy_slug = str(current_settings.get("web_slug") or "")
        public = {
            "enabled": (
                _level_value(current_settings, "web_leaderboard_enabled", False)
                and bool(legacy_slug)
            ),
            "slug": legacy_slug,
        }
    points = draft.get("points")
    voice = draft.get("voice")
    rewards_section = draft.get("rewards")
    card = draft.get("card")
    messages = draft.get("messages")
    if not all(isinstance(value, dict) for value in (
        general, public, points, voice, rewards_section, card, messages,
    )):
        raise ValueError("all leveling settings sections are required")

    public_enabled = _bool(public.get("enabled"), "public.enabled")
    public_slug = public.get("slug", "")
    if not isinstance(public_slug, str):
        raise ValueError("public.slug must be text")
    public_slug = public_slug.strip().lower()
    if public_slug and not PUBLIC_SLUG_RE.fullmatch(public_slug):
        raise ValueError("public.slug must be 3–40 lowercase letters, numbers, or single hyphens")
    if public_enabled and not public_slug:
        raise ValueError("public.slug is required when the public leaderboard is enabled")

    settings = {
        "is_enabled": int(_bool(general.get("enabled"), "enabled")),
        "text_xp_enabled": int(_bool(general.get("text"), "text")),
        "reaction_xp_enabled": int(_bool(general.get("reaction"), "reaction")),
        "streak_enabled": int(_bool(general.get("streak"), "streak")),
        "web_leaderboard_enabled": int(public_enabled),
        "web_slug": public_slug or None,
        "text_xp_min": _integer(points.get("minXp"), "minXp", 0, 1000),
        "text_xp_max": _integer(points.get("maxXp"), "maxXp", 0, 1000),
        "xp_multiplier": _number(points.get("xpMultiplier"), "xpMultiplier", 0, 10),
        "message_cooldown_seconds": _integer(points.get("cooldown"), "cooldown", 0, 3600),
        "text_allowed_channels": [
            str(value) for value in _id_list(
                guild, points.get("allowedChannels"), "allowedChannels", kind="channel"
            )
        ],
        "voice_xp_enabled": int(_bool(voice.get("enabled"), "voiceEnabled")),
        "voice_xp_per_minute": _integer(voice.get("xpPerMin"), "xpPerMin", 0, 500),
        "voice_mute_no_xp": int(_bool(voice.get("muteBlock"), "muteBlock")),
        "voice_deafen_no_xp": int(_bool(voice.get("deafBlock"), "deafBlock")),
        "voice_min_two_members": int(_bool(voice.get("minMembers", 2) > 1, "minMembersEnabled")),
        "voice_diminishing_enabled": int(_bool(voice.get("dimEnabled"), "dimEnabled")),
        "voice_diminishing_mins": _integer(voice.get("dimThreshold"), "dimThreshold", 0, 1440),
        "voice_diminishing_rate": _number(voice.get("dimRate"), "dimRate", 0, 100) / 100,
        "voice_separate_levels": int(_bool(voice.get("separate"), "separate")),
        "rewards_single_highest": int(_bool(rewards_section.get("highestOnly"), "highestOnly")),
        "card_animated_bar": int(_bool(card.get("animated"), "animated")),
        "card_show_stats": int(_bool(card.get("showStats"), "showStats")),
    }
    minimum, maximum = settings["text_xp_min"], settings["text_xp_max"]
    if minimum > maximum:
        raise ValueError("minXp cannot exceed maxXp")
    if not isinstance(voice.get("minMembers"), int) or isinstance(voice.get("minMembers"), bool):
        raise ValueError("minMembers must be an integer")
    min_members = _integer(voice["minMembers"], "minMembers", 1, 99)
    settings["voice_min_two_members"] = int(min_members >= 2)
    settings["voice_min_members"] = min_members

    if card.get("layout") not in LAYOUTS:
        raise ValueError("invalid card layout")
    if card.get("particles") not in PARTICLES:
        raise ValueError("invalid card particles")
    color = card.get("color")
    if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("invalid card color")
    bg = card.get("bg", "")
    if not isinstance(bg, str) or len(bg) > 400:
        raise ValueError("invalid card background URL")
    bg = bg.strip()
    if bg:
        if not bg.startswith("https://"):
            raise ValueError("card background must use HTTPS")
        validate_url(bg)
    settings.update({
        "card_layout": card["layout"],
        "card_particles": card["particles"],
        "card_color": color.lower(),
        "card_bg_url": bg or None,
        "card_design": _clean_card_design(card),
    })

    multipliers = []
    for field, kind in (("roleMult", "role"), ("chanMult", "channel")):
        rows = points.get(field)
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError(f"{field} must be an array with at most 200 entries")
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"invalid {field} entry")
            item_id = (
                _owned_role(guild, row.get("id")) if kind == "role"
                else _owned_channel(guild, row.get("id"))
            )
            if item_id in seen:
                raise ValueError(f"duplicate target in {field}")
            seen.add(item_id)
            multipliers.append({
                "target_type": kind,
                "target_id": item_id,
                "multiplier": _number(row.get("mult"), f"{field} multiplier", 0, 10),
            })

    blacklist = []
    bl = points.get("bl")
    if not isinstance(bl, dict):
        raise ValueError("invalid blacklist")
    for field, kind in (("channels", "channel"), ("roles", "role"), ("users", "user")):
        values = bl.get(field)
        if not isinstance(values, list) or len(values) > 500:
            raise ValueError(f"blacklist {field} must contain at most 500 entries")
        seen = set()
        for raw_id in values:
            item_id = (
                _owned_role(guild, raw_id) if kind == "role"
                else _owned_channel(guild, raw_id) if kind == "channel"
                else _snowflake(raw_id)
            )
            if item_id in seen:
                raise ValueError(f"duplicate blacklist {field} entry")
            seen.add(item_id)
            blacklist.append({"target_type": kind, "target_id": item_id})

    rewards = rewards_section.get("list")
    if not isinstance(rewards, list) or len(rewards) > 200:
        raise ValueError("reward list must contain at most 200 entries")
    clean_rewards, seen_rewards = [], set()
    for reward in rewards:
        if not isinstance(reward, dict):
            raise ValueError("invalid reward")
        level = _integer(reward.get("level"), "reward level", 1, 1000)
        reward_type = reward.get("type")
        if reward_type not in {"text", "voice"}:
            raise ValueError("invalid reward type")
        role_id = _owned_role(guild, reward.get("role"), assignable=True)
        key = (reward_type, level)
        if key in seen_rewards:
            raise ValueError("duplicate reward level and type")
        seen_rewards.add(key)
        clean_rewards.append({
            "reward_type": reward_type, "level_required": level, "role_id": role_id,
        })

    now = datetime.now(timezone.utc)
    settings["timed_xp_boosts"] = _parse_boosts(
        current_settings, points.get("boosts"), now
    )
    prime_draft = controls_with_defaults(draft.get("prime"), current_settings)
    streak_section = draft.get("streak")
    if streak_section is None:
        streak_section = {
            "channel": str(current_settings.get("streak_channel_id") or ""),
            "dailyXp": int(current_settings.get("streak_daily_xp", 50) or 0),
            "maxCap": int(current_settings.get("streak_max_cap", 500) or 0),
            "timezone": "Asia/Riyadh",
            "resetTime": "00:00",
            **prime_draft["streak"],
        }
    if not isinstance(streak_section, dict):
        raise ValueError("invalid streak settings")
    if streak_section.get("timezone") != "Asia/Riyadh":
        raise ValueError("streak timezone is fixed to Asia/Riyadh")
    if streak_section.get("resetTime") != "00:00":
        raise ValueError("streak reset time is fixed to 00:00")
    channel = streak_section.get("channel")
    settings["streak_channel_id"] = (
        _owned_channel(guild, channel, messageable=True) if channel else None
    )
    settings["streak_daily_xp"] = _integer(
        streak_section.get("dailyXp"), "streak.dailyXp", 0, 100000,
    )
    settings["streak_max_cap"] = _integer(
        streak_section.get("maxCap"), "streak.maxCap", 0, 10000000,
    )
    streak_control_fields = (
        "progressCardEnabled", "successReaction", "messages", "stages", "milestones",
    )
    for field in streak_control_fields:
        if field in streak_section:
            prime_draft["streak"][field] = streak_section[field]
    message_fields = (
        ("levelup", "levelup_enabled", "levelup_channel_id", "levelup_template"),
        ("milestone", "milestone_alert_enabled", "milestone_channel_id", "milestone_template"),
        ("overtake", "overtake_alert_enabled", "overtake_channel_id", "overtake_template"),
        ("role_promotion", None, None, None),
    )
    for key, enabled_field, channel_field, template_field in message_fields:
        item = messages.get(key)
        if not isinstance(item, dict):
            raise ValueError(f"invalid {key} message settings")
        enabled = _bool(item.get("on"), f"{key}.on")
        channel = item.get("channel")
        channel_id = (
            _owned_channel(guild, channel, messageable=True) if channel else None
        )
        template = _format_template(item.get("tpl"), key)
        if enabled_field:
            settings[enabled_field] = int(enabled)
            settings[channel_field] = channel_id
            settings[template_field] = template
        if key == "levelup":
            prime_draft["levelup"]["sendNotification"] = enabled
            prime_draft["levelup"]["channel"] = str(channel_id or "")
            prime_draft["levelup"]["message"] = template
        else:
            prime_item = prime_draft["notifications"][key]
            prime_item["enabled"] = enabled
            prime_item["channel"] = str(channel_id or "")
            prime_item["message"] = template
    settings["prime_controls"] = validate_controls(
        prime_draft,
        current_settings,
        validate_channel=lambda raw, messageable=False: _owned_channel(
            guild, raw, messageable=messageable,
        ),
        validate_role=lambda raw: _owned_role(guild, raw),
        validate_assignable_role=lambda raw: _owned_role(
            guild, raw, assignable=True,
        ),
        validate_image=database._validated_streak_image,
    )
    settings["command_rank_channels"] = settings["prime_controls"]["rank"]["channels"]
    return settings, clean_rewards, multipliers, blacklist


def register_leveling_routes(
    routes, *, authorize, level_admin_authorize, json_error, read_json_body, logger,
):
    async def get_snapshot(req):
        _, guild = await authorize(req)
        try:
            return web.json_response(await _dashboard_snapshot(guild.id))
        except Exception:
            logger.exception("Leveling settings read failed guild=%s", guild.id)
            return json_error(500, "leveling_unavailable")

    @routes.get("/api/guild/{guild_id}/leveling/settings")
    async def api_leveling_settings_get(req):
        return await get_snapshot(req)

    @routes.post("/api/guild/{guild_id}/leveling/settings")
    @routes.patch("/api/guild/{guild_id}/leveling/settings")
    async def api_leveling_settings_patch(req):
        session, guild = await authorize(req, write=True)
        try:
            body = await read_json_body(req)
        except (ValueError, UnicodeDecodeError):
            return json_error(400, "invalid_json")
        if not isinstance(body, dict):
            return json_error(400, "validation", fields={"_": "request body must be an object"})
        revision = body.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return json_error(400, "validation", fields={"revision": "revision is required"})
        current = await database.get_level_settings(guild.id) or {}
        try:
            clean = _validate_draft(guild, body.get("draft"), current)
        except (TypeError, ValueError) as error:
            return json_error(400, "validation", fields={"_": str(error)[:180]})
        try:
            await database.replace_level_dashboard_config(
                guild.id, revision, *clean
            )
            result = await _dashboard_snapshot(guild.id)
        except database.LevelingConflict as conflict:
            return json_error(409, "conflict", currentRevision=conflict.current_revision)
        except database.LevelingSlugConflict:
            return json_error(
                409, "slug_conflict",
                fields={"public.slug": "هذا المعرّف مستخدم في سيرفر آخر."},
            )
        except sqlite3.IntegrityError as error:
            if "level_settings.web_slug" in str(error):
                return json_error(
                    409, "slug_conflict",
                    fields={"public.slug": "هذا المعرّف مستخدم في سيرفر آخر."},
                )
            logger.exception("Leveling dashboard integrity failure guild=%s user=%s",
                             guild.id, session.get("id"))
            return json_error(500, "leveling_unavailable")
        except Exception:
            logger.exception("Leveling settings save failed guild=%s user=%s",
                             guild.id, session.get("id"))
            return json_error(500, "leveling_unavailable")
        logger.info("Leveling dashboard settings saved guild=%s user=%s",
                    guild.id, session.get("id"))
        return web.json_response({"ok": True, **result})

    @routes.post("/api/guild/{guild_id}/leveling/reset-progress")
    async def api_leveling_reset_progress(req):
        session, guild = await authorize(req, write=True)
        # The local preview identity is deliberately never allowed to perform
        # a destructive reset, even when it can edit development settings.
        if session.get("_local_dev"):
            return json_error(403, "production_session_required")
        if not await level_admin_authorize(session, guild):
            return json_error(403, "level_admin_required")
        try:
            body = await read_json_body(req)
        except (ValueError, UnicodeDecodeError):
            return json_error(400, "invalid_json")
        if not isinstance(body, dict) or body.get("confirmation") != "RESET_LEVEL_PROGRESS":
            return json_error(
                400, "confirmation_required",
                fields={"confirmation": "explicit confirmation is required"},
            )
        try:
            result = await database.reset_level_progress(guild.id)
        except Exception:
            logger.exception(
                "Leveling progress reset failed guild=%s user=%s",
                guild.id, session.get("id"),
            )
            return json_error(500, "leveling_unavailable")
        logger.warning(
            "Leveling progress reset guild=%s user=%s members=%s daily_rows=%s xp_events=%s",
            guild.id, session.get("id"), result["members_reset"],
            result["daily_rows_removed"], result["xp_events_removed"],
        )
        return web.json_response({"ok": True, **result})

    @routes.get("/api/guild/{guild_id}/leveling/analytics")
    async def api_leveling_analytics(req):
        _, guild = await authorize(req)
        try:
            return web.json_response(await database.get_level_dashboard_analytics(guild.id))
        except Exception:
            logger.exception("Leveling analytics read failed guild=%s", guild.id)
            return json_error(500, "leveling_unavailable")

    @routes.get("/api/guild/{guild_id}/leveling/leaderboard")
    async def api_leveling_leaderboard(req):
        _, guild = await authorize(req)
        mode = req.query.get("mode", "text")
        if mode not in {"text", "voice"}:
            return json_error(400, "validation", fields={"mode": "choose text or voice"})
        try:
            limit = int(req.query.get("limit", "20"))
            offset = int(req.query.get("offset", "0"))
            page = await database.get_level_leaderboard_page(
                guild.id, mode, limit, offset
            )
            rows = []
            for row in page["rows"]:
                member = guild.get_member(int(row["user_id"]))
                rows.append({
                    **row,
                    "user_id": str(row["user_id"]),
                    "name": (
                        getattr(member, "display_name", None)
                        or getattr(member, "name", None)
                    ),
                })
            next_offset = page["offset"] + len(rows)
            return web.json_response({
                **page, "rows": rows,
                "nextOffset": next_offset if next_offset < page["total"] else None,
            })
        except (TypeError, ValueError):
            return json_error(400, "validation", fields={"page": "invalid page"})
        except Exception:
            logger.exception("Leveling leaderboard read failed guild=%s", guild.id)
            return json_error(500, "leveling_unavailable")

    @routes.get("/api/guild/{guild_id}/leveling/card-preview")
    async def api_leveling_card_preview(req):
        session, guild = await authorize(req)
        output_format = req.query.get("format", "png").lower()
        body = {
            "layout": req.query.get("layout", "vertical"),
            "particles": req.query.get("particles", "none"),
            "color": req.query.get("color", "#1E293B"),
            "bg": req.query.get("bg", ""),
        }
        try:
            if output_format not in {"png", "gif", "auto"}:
                raise ValueError("unsupported card preview format")
            layout = body.get("layout", "vertical")
            particles = body.get("particles", "none")
            color = body.get("color", "#1E293B")
            bg = body.get("bg", "")
            if layout not in LAYOUTS or particles not in PARTICLES:
                raise ValueError("invalid card layout or effect")
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValueError("invalid card color")
            if not isinstance(bg, str) or len(bg) > 400:
                raise ValueError("invalid card background")
            bg = bg.strip()
            if bg:
                if not bg.startswith("https://"):
                    raise ValueError("card background must use HTTPS")
                validate_url(bg)
            design = dict(CARD_DESIGN_DEFAULTS)
            for key, minimum, maximum in (
                ("glowStrength", 0, 100), ("particleDensity", 0, 100),
                ("bgOverlay", 0, 85), ("bgBlur", 0, 18),
                ("animationIntensity", 0, 100),
            ):
                if key in req.query:
                    try:
                        raw = int(req.query[key])
                    except (TypeError, ValueError):
                        raise ValueError(f"{key} must be an integer") from None
                    design[key] = _integer(raw, key, minimum, maximum)
            for key in ("particleColor", "barStyle", "frame", "animationStyle"):
                if key in req.query:
                    design[key] = req.query[key]

            def query_bool(key, default):
                raw = req.query.get(key)
                if raw is None:
                    return default
                value = raw.lower()
                if value not in {"true", "false"}:
                    raise ValueError(f"{key} must be boolean")
                return value == "true"

            design["animationEnabled"] = query_bool("animationEnabled", True)
            design["stats"] = {
                "messages": query_bool("showMessages", True),
                "voice": query_bool("showVoice", True),
                "streak": query_bool("showStreak", True),
                "serverRank": query_bool("showServerRank", True),
            }
            design = _clean_card_design({**design, "presets": []})
            body["animated"] = query_bool("animated", True)
            body["showStats"] = query_bool("showStats", True)
        except (TypeError, ValueError) as error:
            return json_error(400, "validation", fields={"_": str(error)[:180]})

        try:
            user_id = _snowflake(session.get("id"))
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    member = None
            if member is None:
                return json_error(403, "guild_member_required")
            row = await database.get_user_level(guild.id, user_id)
            text_xp = int((row or {}).get("text_xp", 0) or 0)
            text_level = int((row or {}).get("text_level", 0) or 0)
            progress = text_progress(text_xp)
            rank_data = await database.get_text_rank(guild.id, user_id)
            rank = (rank_data or {}).get("rank")
            total = (rank_data or {}).get("total_eligible_members", 0)
            stored = await database.get_level_settings(guild.id) or {}
            settings = {
                **stored,
                "card_layout": layout,
                "card_particles": particles,
                "card_color": color.lower(),
                "card_bg_url": bg or None,
                "card_animated_bar": body.get("animated", stored.get("card_animated_bar", True)),
                "card_show_stats": body.get("showStats", stored.get("card_show_stats", True)),
                "card_design": design,
                "total_messages": (row or {}).get("total_messages"),
                "total_voice_seconds": (row or {}).get("total_voice_seconds"),
                "current_streak": (row or {}).get("current_streak"),
            }
            if output_format == "auto":
                output_format = (
                    "gif" if await has_animated_background(settings) else "png"
                )
            renderer = generate_level_up_gif if output_format == "gif" else generate_rank_card
            image = await renderer(
                member, text_level, text_xp, xp_required(text_level), rank, total, settings,
            )
            content_type = "image/gif" if output_format == "gif" else "image/png"
            return web.Response(
                body=image.getvalue(), content_type=content_type,
                headers={
                    "Cache-Control": "no-store, max-age=0",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except Exception:
            logger.exception("Leveling card preview failed guild=%s user=%s",
                             guild.id, session.get("id"))
            return json_error(500, "card_preview_unavailable")
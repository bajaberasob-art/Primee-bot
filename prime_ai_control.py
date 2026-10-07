"""Persistent control plane for PRIME AI settings, skills, memory, and audits."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

import database


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or now_utc()).isoformat(timespec="seconds")


def _json_object(value: Any) -> dict:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _ids(value: Any, field: str, maximum: int = 500) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"invalid_{field}")
    result = []
    for item in value:
        item = str(item)
        if not item.isascii() or not item.isdigit() or not 15 <= len(item) <= 22:
            raise ValueError(f"invalid_{field}")
        if item not in result:
            result.append(item)
    return result


def _merge_known(defaults: dict, incoming: dict, prefix: str = "") -> dict:
    if not isinstance(incoming, dict):
        raise ValueError(f"invalid_{prefix or 'settings'}")
    unknown = set(incoming) - set(defaults)
    if unknown:
        raise ValueError(f"unknown_{prefix or 'settings'}_field")
    result = {}
    for key, default in defaults.items():
        path = f"{prefix}.{key}" if prefix else key
        value = incoming.get(key, default)
        if isinstance(default, dict):
            result[key] = _merge_known(default, value, path)
        else:
            result[key] = value
    return result


def _default_rate(limit: int, window: int) -> dict:
    return {"limit": limit, "window_seconds": window}


DANGEROUS_CONFIRMATION_TOOLS = {
    "delete_message",
    "delete_channel",
    "delete_role",
    "kick_member",
    "ban_member",
}

# This is the authoritative inventory for Phase 4 actions. Runtime handlers,
# policy checks, and Dashboard policy editors all key off these registered IDs.
ACTION_REGISTRY: dict[str, dict[str, Any]] = {
    "send_message": {
        "action_id": "SEND_MESSAGE", "name": "إرسال رسالة", "description": "إرسال رسالة في قناة محددة.",
        "category": "MESSAGE", "risk": "LOW", "discord_permission": "send_messages",
        "prime_permission": "access.minimum_permission", "confirmation_required": False,
        "handler": "send_message",
    },
    "reply_message": {
        "action_id": "REPLY_MESSAGE", "name": "الرد على رسالة", "description": "الرد على رسالة موجودة في قناة محددة.",
        "category": "MESSAGE", "risk": "LOW", "discord_permission": "send_messages",
        "prime_permission": "access.minimum_permission", "confirmation_required": False,
        "handler": "reply_message",
    },
    "add_reaction": {
        "action_id": "ADD_REACTION", "name": "إضافة تفاعل", "description": "إضافة تفاعل إلى رسالة محددة.",
        "category": "MESSAGE", "risk": "LOW", "discord_permission": "add_reactions",
        "prime_permission": "access.minimum_permission", "confirmation_required": False,
        "handler": "add_reaction",
    },
    "edit_message": {
        "action_id": "EDIT_BOT_MESSAGE", "name": "تعديل رسالة PRIME", "description": "تعديل رسالة أرسلها البوت فقط.",
        "category": "MESSAGE", "risk": "MEDIUM", "discord_permission": "manage_messages",
        "prime_permission": "access.minimum_permission", "confirmation_required": False,
        "handler": "edit_message",
    },
    "delete_message": {
        "action_id": "DELETE_BOT_MESSAGE", "name": "حذف رسالة PRIME", "description": "حذف رسالة أرسلها البوت فقط.",
        "category": "MESSAGE", "risk": "HIGH", "discord_permission": "manage_messages",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "delete_message",
    },
    "create_channel": {
        "action_id": "CREATE_CHANNEL", "name": "إنشاء قناة", "description": "إنشاء قناة نصية جديدة.",
        "category": "CHANNEL", "risk": "MEDIUM", "discord_permission": "manage_channels",
        "prime_permission": "access.minimum_permission", "confirmation_required": False,
        "handler": "create_channel",
    },
    "rename_channel": {
        "action_id": "RENAME_CHANNEL", "name": "إعادة تسمية قناة", "description": "تغيير اسم قناة موجودة.",
        "category": "CHANNEL", "risk": "MEDIUM", "discord_permission": "manage_channels",
        "prime_permission": "access.minimum_permission", "confirmation_required": False,
        "handler": "rename_channel",
    },
    "delete_channel": {
        "action_id": "DELETE_CHANNEL", "name": "حذف قناة", "description": "حذف قناة نهائياً.",
        "category": "CHANNEL", "risk": "CRITICAL", "discord_permission": "manage_channels",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "delete_channel",
    },
    "set_channel_mode": {
        "action_id": "SET_CHANNEL_MODE", "name": "تغيير وضع الكتابة في قناة",
        "description": "فتح القناة للكتابة أو جعلها للقراءة فقط عبر صلاحيات everyone.",
        "category": "CHANNEL", "risk": "HIGH", "discord_permission": "manage_channels",
        "additional_discord_permissions": ["manage_roles"],
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "set_channel_mode",
    },
    "create_role": {
        "action_id": "CREATE_ROLE", "name": "إنشاء رتبة", "description": "إنشاء رتبة جديدة دون صلاحيات إدارية.",
        "category": "ROLE", "risk": "MEDIUM", "discord_permission": "manage_roles",
        "prime_permission": "access.minimum_permission", "confirmation_required": False,
        "handler": "create_role",
    },
    "edit_role": {
        "action_id": "EDIT_ROLE", "name": "تعديل رتبة", "description": "تعديل اسم أو لون رتبة قابلة للإدارة.",
        "category": "ROLE", "risk": "HIGH", "discord_permission": "manage_roles",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "edit_role",
    },
    "delete_role": {
        "action_id": "DELETE_ROLE", "name": "حذف رتبة", "description": "حذف رتبة قابلة للإدارة نهائياً.",
        "category": "ROLE", "risk": "CRITICAL", "discord_permission": "manage_roles",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "delete_role",
    },
    "assign_role": {
        "action_id": "ASSIGN_ROLE", "name": "إعطاء رتبة", "description": "إعطاء عضو رتبة قابلة للإدارة.",
        "category": "ROLE", "risk": "HIGH", "discord_permission": "manage_roles",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "assign_role",
    },
    "remove_role": {
        "action_id": "REMOVE_ROLE", "name": "إزالة رتبة", "description": "إزالة رتبة قابلة للإدارة من عضو.",
        "category": "ROLE", "risk": "HIGH", "discord_permission": "manage_roles",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "remove_role",
    },
    "timeout_member": {
        "action_id": "TIMEOUT_MEMBER", "name": "إسكات عضو مؤقتاً", "description": "تطبيق مهلة مؤقتة على عضو يمكن إدارته.",
        "category": "MODERATION", "risk": "HIGH", "discord_permission": "moderate_members",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "timeout_member",
    },
    "kick_member": {
        "action_id": "KICK_MEMBER", "name": "طرد عضو", "description": "طرد عضو يمكن إدارته من الخادم.",
        "category": "MODERATION", "risk": "CRITICAL", "discord_permission": "kick_members",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "kick_member",
    },
    "ban_member": {
        "action_id": "BAN_MEMBER", "name": "حظر عضو", "description": "حظر عضو يمكن إدارته من الخادم.",
        "category": "MODERATION", "risk": "CRITICAL", "discord_permission": "ban_members",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "ban_member",
    },
    "unban_member": {
        "action_id": "UNBAN_MEMBER", "name": "إلغاء حظر عضو", "description": "إلغاء حظر عضو محدد.",
        "category": "MODERATION", "risk": "HIGH", "discord_permission": "ban_members",
        "prime_permission": "access.minimum_permission", "confirmation_required": True,
        "handler": "unban_member",
    },
}

# Keep the static registry complete and expose runtime policy separately in
# DEFAULT_CONTROL_SETTINGS. Actions are available by default; HIGH/CRITICAL
# actions retain their mandatory confirmation policy.
for _action_key, _action in ACTION_REGISTRY.items():
    _action.update({
        "enabled": True,
        "enabled_by_default": True,
        "confirmation_required": _action_key in DANGEROUS_CONFIRMATION_TOOLS,
        "prime_permission": "access.minimum_permission",
        "audit_required": True,
        "dashboard_config": f"actions.{_action_key}",
    })

ACTION_POLICY_DEFAULTS = {
    key: {
        # PRIME actions are available to natural-language requests by default;
        # HIGH/CRITICAL actions still require real Discord permissions plus
        # their risk-based confirmation gate.
        "enabled": True,
        "confirmation_required": key in DANGEROUS_CONFIRMATION_TOOLS,
        "allowed_channels": [],
        "allowed_roles": [],
        "minimum_role_id": "",
    }
    for key, item in ACTION_REGISTRY.items()
}
MODERATION_CATEGORIES = {
    "spam", "harassment", "suspicious_behavior", "prohibited_content",
    "repeated_violations",
}


DEFAULT_CONTROL_SETTINGS: dict[str, Any] = {
    "policy_version": 4,
    "mode": "CHAT",
    "modes": {
        "chat": True,
        "assistant": False,
        "action": True,
        "autonomous": False,
    },
    "activation": {
        "mention": True,
        "reply": True,
        "wake_word": True,
        "command": True,
        "automatic": False,
    },
    "access": {
        "allowed_channels": [],
        "blocked_channels": [],
        "allowed_roles": [],
        "blocked_roles": [],
        "minimum_permission": "everyone",
        "legacy_allowlist_conflict": False,
    },
    "natural_commands": {
        "enabled": True,
        "clarification_behavior": "ask",
        "unknown_command_behavior": "respond",
    },
    "actions": ACTION_POLICY_DEFAULTS,
    "safety": {
        "enabled": True,
        "dry_run": False,
        "confirmation_enabled": False,
        "prompt_injection_protection": True,
        "mass_action_protection": True,
        "max_action_count": 3,
    },
    "context": {
        "max_messages": 12,
        "include_reply_context": True,
    },
    "personality": {
        "preset": "Practical",
        "tone": "clear",
        "language": "auto",
        "arabic_dialect": "",
        "formality": 50,
        "response_length": "medium",
        "humor": 20,
        "emoji_usage": 20,
        "toughness": 20,
        "directness": 50,
        "friendliness": 65,
        "seriousness": 50,
        "emotional_style": "balanced",
        "greeting_style": "brief",
        "reply_style": "helpful",
        "custom_instructions": "",
    },
    "channel_personas": {},
    "role_overrides": {},
    "provider": {
        "name": "Google Gemini",
        "model": "gemini-3.8-flash",
        "temperature": 0.7,
        "thinking_level": "medium",
        "max_tokens": 1200,
        "timeout_seconds": 30,
        "retry_count": 2,
    },
    "response": {
        "maximum_length": 3500,
        "streaming": False,
        "typing_indicator": True,
        "mention_behavior": "none",
        "reply_behavior": True,
        "embed_behavior": True,
        "markdown": True,
        "emoji": True,
        "auto_delete_seconds": 0,
    },
    "memory": {
        "enabled": True,
        "auto_store": False,
        "user_memory_enabled": True,
        "server_memory_enabled": True,
        "creation_enabled": True,
        "retrieval_enabled": True,
        "context_limit": 8,
        "maximum_count": 500,
        "maximum_content_length": 1000,
        "default_expiration_days": 90,
        "sensitive_storage": False,
    },
    "sandbox": {"enabled": False},
    "moderation": {
        "mode": "OFF",
        "confidence_threshold": 0.9,
        "channel_ids": [],
        "alert_channel_id": "",
        "categories": sorted(MODERATION_CATEGORIES),
        "auto_action_policy": "NONE",
        "log_findings": True,
        "timeout_minutes": 10,
        # Retained and forced safe for settings written by earlier phases.
        "warn_enabled": False,
        "timeout_enabled": False,
    },
    "rate_limits": {
        "user": _default_rate(5, 60),
        "role": _default_rate(40, 60),
        "channel": _default_rate(80, 60),
        "guild": _default_rate(250, 60),
        "action": _default_rate(3, 60),
        "dangerous_action": _default_rate(1, 300),
        "moderation": _default_rate(30, 60),
    },
    "retention": {
        "memory_days": 90,
        "audit_days": 90,
        "moderation_days": 30,
    },
}

PERMISSION_LEVELS = {
    "everyone",
    "manage_messages",
    "moderate_members",
    "manage_roles",
    "manage_channels",
    "manage_guild",
    "administrator",
    "owner",
}
AI_MODES = {"CHAT", "ASSISTANT", "ACTION", "AUTONOMOUS"}
MODERATION_MODES = {
    "OFF", "LOG_ONLY", "ALERT", "RECOMMEND", "AUTO_WITH_CONFIRMATION",
}
PERSONALITIES = {
    "Practical", "Formal", "Friendly", "Romantic", "Sarcastic", "Firm",
    "Mysterious", "Funny", "Smart", "Gaming", "Aggressive", "Custom",
}
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


SKILL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "key": "conversation", "skill_id": "GENERAL", "name": "General",
        "description": "Chat, answer general questions, and explain PRIME.",
        "category": "General", "permission": "everyone", "actions": [],
        "action_permissions": {}, "required_context": ["guild", "member", "channel"],
        "supported_intents": ["CHAT", "QUESTION"],
        "handler": "prime_ai_service.generate_response",
        "dashboard_config": "skills.conversation", "safety_level": "read_only",
        "available": True, "enabled_by_default": True,
    },
    {
        "key": "help", "skill_id": "HELP", "name": "Help & Skill Discovery",
        "description": "Explain PRIME and list the currently enabled read-only skills.",
        "category": "General", "permission": "everyone", "actions": [],
        "action_permissions": {}, "required_context": ["guild", "member"],
        "supported_intents": ["HELP"], "handler": "prime_ai_control.get_skills",
        "dashboard_config": "skills.help", "safety_level": "read_only",
        "available": True, "enabled_by_default": True,
    },
    {
        "key": "summary", "skill_id": "SUMMARY", "name": "Conversation Summary",
        "description": "Summarize recent context using PRIME's existing memory and conversation pipeline.",
        "category": "General", "permission": "everyone", "actions": [],
        "action_permissions": {}, "required_context": ["guild", "member", "channel", "conversation"],
        "supported_intents": ["SUMMARY"], "handler": "prime_ai_service.generate_response",
        "dashboard_config": "skills.summary", "safety_level": "read_only",
        "available": True, "enabled_by_default": True,
    },
    {
        "key": "leveling", "skill_id": "LEVELING_STATUS", "name": "Leveling",
        "description": "Read text XP, level, rank, and the existing leaderboard.",
        "category": "Leveling", "permission": "everyone", "actions": ["query_leveling"],
        "action_permissions": {"query_leveling": "view_channel"},
        "required_context": ["guild", "member"], "supported_intents": [
            "CHECK_LEVEL", "CHECK_XP", "CHECK_RANK", "LEADERBOARD",
        ],
        "handler": "database.get_user_level/database.get_text_rank",
        "dashboard_config": "skills.leveling", "safety_level": "read_only",
        "available": True, "enabled_by_default": True,
    },
    {
        "key": "streak", "skill_id": "STREAK_STATUS", "name": "Streak",
        "description": "Read current and best streaks using existing streak data.",
        "category": "Streak", "permission": "everyone", "actions": ["query_streak"],
        "action_permissions": {"query_streak": "view_channel"},
        "required_context": ["guild", "member"], "supported_intents": [
            "CHECK_STREAK", "CHECK_BEST_STREAK", "STREAK_STATUS",
        ],
        "handler": "database.get_user_level/database.get_streak_leaderboard",
        "dashboard_config": "skills.streak", "safety_level": "read_only",
        "available": True, "enabled_by_default": True,
    },
    {
        "key": "subscription", "skill_id": "SUBSCRIPTION_STATUS", "name": "Subscription",
        "description": "Read subscription status and expiry for the requester or an authorized target.",
        "category": "Subscription", "permission": "everyone", "actions": ["query_subscription"],
        "action_permissions": {"query_subscription": "view_channel"},
        "required_context": ["guild", "member"], "supported_intents": [
            "CHECK_SUBSCRIPTION", "SUBSCRIPTION_STATUS", "SUBSCRIPTION_EXPIRY",
        ],
        "handler": "subscription_service.list_subscriptions",
        "dashboard_config": "skills.subscription", "safety_level": "read_only",
        "available": True, "enabled_by_default": True,
    },
    {
        "key": "server_information", "skill_id": "SERVER_INFO", "name": "Server Information",
        "description": "Read server counts and visible channel and role information.",
        "category": "Server", "permission": "everyone", "actions": ["fetch_server_data"],
        "action_permissions": {"fetch_server_data": "view_channel"},
        "required_context": ["guild", "member"], "supported_intents": ["SERVER_INFO"],
        "handler": "discord.Guild", "dashboard_config": "skills.server_information",
        "safety_level": "read_only", "available": True, "enabled_by_default": True,
    },
    {
        "key": "members", "skill_id": "MEMBER_INFO", "name": "Members",
        "description": "Read basic information about a uniquely resolved server member.",
        "category": "Server", "permission": "manage_guild", "actions": ["fetch_member"],
        "action_permissions": {"fetch_member": "view_channel"},
        "required_context": ["guild", "member"], "supported_intents": ["MEMBER_INFO"],
        "handler": "discord.Guild.get_member/discord.Guild.fetch_member",
        "dashboard_config": "skills.members", "safety_level": "read_only",
        "available": True, "enabled_by_default": True,
    },
    {
        "key": "roles", "skill_id": "ROLE_INFO", "name": "Roles",
        "description": "Read role metadata from the current server.",
        "category": "Server", "permission": "everyone",
        "actions": ["fetch_roles", "fetch_role"],
        "action_permissions": {
            "fetch_roles": "view_channel", "fetch_role": "view_channel",
        },
        "required_context": ["guild"], "supported_intents": ["ROLE_INFO"],
        "handler": "discord.Guild.roles", "dashboard_config": "skills.roles",
        "safety_level": "read_only", "available": True, "enabled_by_default": True,
    },
    {
        "key": "channels", "skill_id": "CHANNEL_INFO", "name": "Channels",
        "description": "Read channel metadata only when the requester can view that channel.",
        "category": "Server", "permission": "everyone",
        "actions": ["fetch_channels", "fetch_channel"],
        "action_permissions": {
            "fetch_channels": "view_channel", "fetch_channel": "view_channel",
        },
        "required_context": ["guild", "member", "channel"],
        "supported_intents": ["CHANNEL_INFO"],
        "handler": "discord.Guild.channels", "dashboard_config": "skills.channels",
        "safety_level": "read_only", "available": True, "enabled_by_default": True,
    },
    {
        "key": "analytics", "skill_id": "SERVER_ANALYTICS", "name": "Analytics",
        "description": "Read supported leveling, streak, subscription, and message analytics.",
        "category": "Analytics", "permission": "manage_guild",
        "actions": ["query_analytics"],
        "action_permissions": {"query_analytics": "manage_guild"},
        "required_context": ["guild"], "supported_intents": [
            "SERVER_ANALYTICS", "LEVELING_ANALYTICS", "STREAK_ANALYTICS",
            "SUBSCRIPTION_ANALYTICS",
        ],
        "handler": (
            "database.get_level_dashboard_analytics/"
            "database.get_streak_dashboard_analytics/"
            "database.get_analytics_summary"
        ),
        "dashboard_config": "skills.analytics", "safety_level": "read_only",
        "available": True, "enabled_by_default": True,
    },
)


def _validate_rate(value: dict, field: str) -> dict:
    limit = value["limit"]
    window = value["window_seconds"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError(f"invalid_{field}_limit")
    if isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= 86400:
        raise ValueError(f"invalid_{field}_window")
    return {"limit": limit, "window_seconds": window}


def normalize_control_settings(
    incoming: dict, *, allow_legacy_values: bool = False
) -> dict:
    if not isinstance(incoming, dict):
        raise ValueError("invalid_settings")
    incoming_provider = incoming.get("provider")
    legacy_provider_context = (
        incoming_provider.get("context_limit")
        if allow_legacy_values and isinstance(incoming_provider, dict)
        else None
    )
    incoming_context_limit = (
        incoming.get("context", {}).get("max_messages")
        if isinstance(incoming.get("context"), dict)
        else None
    )
    dynamic_fields = {"channel_personas", "role_overrides"}
    fixed_defaults = {
        key: value for key, value in DEFAULT_CONTROL_SETTINGS.items()
        if key not in dynamic_fields
    }
    fixed_incoming = {
        key: value for key, value in incoming.items()
        if key not in dynamic_fields
    }
    # These keys were exposed by early dashboard drafts but never had runtime
    # behavior. Ignore them when reading saved configurations so old rows stay
    # loadable; do not return them as active settings.
    for section, obsolete_keys in (
        ("provider", {"context_limit"}),
        ("context", {"expire_seconds"}),
        ("response", {"cooldown_seconds"}),
    ):
        section_value = fixed_incoming.get(section)
        if isinstance(section_value, dict):
            section_value = dict(section_value)
            for obsolete_key in obsolete_keys:
                section_value.pop(obsolete_key, None)
            fixed_incoming[section] = section_value
    if allow_legacy_values:
        # These retention controls were saved by an earlier dashboard version,
        # but no longer have runtime behavior. Ignore only these known legacy
        # keys while reading persisted settings; current writes stay strict.
        legacy_retention = fixed_incoming.get("retention")
        if isinstance(legacy_retention, dict):
            legacy_retention = dict(legacy_retention)
            legacy_retention.pop("context_days", None)
            legacy_retention.pop("conversation_days", None)
            fixed_incoming["retention"] = legacy_retention
    incoming_policy_version = incoming.get("policy_version", 1)
    try:
        incoming_policy_version = int(incoming_policy_version)
    except (TypeError, ValueError):
        incoming_policy_version = 1
    value = _merge_known(fixed_defaults, fixed_incoming)
    if legacy_provider_context is not None:
        if (
            isinstance(legacy_provider_context, bool)
            or not isinstance(legacy_provider_context, int)
            or not 0 <= legacy_provider_context <= 30
        ):
            raise ValueError("invalid_provider_context_limit")
        if incoming_context_limit is None:
            value["context"]["max_messages"] = legacy_provider_context
        elif (
            isinstance(incoming_context_limit, bool)
            or not isinstance(incoming_context_limit, int)
            or not 0 <= incoming_context_limit <= 30
        ):
            raise ValueError("invalid_context_limit")
        else:
            # Earlier versions applied both limits with min(); retain that
            # effective ceiling when upgrading a stored or stale dashboard draft.
            value["context"]["max_messages"] = min(
                incoming_context_limit, legacy_provider_context
            )
    for key in dynamic_fields:
        value[key] = incoming.get(key, DEFAULT_CONTROL_SETTINGS[key])
    # Migrate the previous AI-control behavior to the direct PRIME operator
    # defaults: safe actions are enabled and Dry Run is off. This is additive;
    # HIGH/CRITICAL actions remain gated by live Discord permissions.
    if incoming_policy_version < 4:
        for action_key, metadata in ACTION_REGISTRY.items():
            # Preserve dangerous-action opt-outs from older server policies.
            if metadata.get("risk") not in {"HIGH", "CRITICAL"}:
                value["actions"][action_key]["enabled"] = True
        value["safety"]["dry_run"] = False
    value["policy_version"] = 4
    if value["mode"] not in AI_MODES:
        raise ValueError("invalid_mode")
    if not isinstance(value["modes"], dict) or any(not isinstance(v, bool) for v in value["modes"].values()):
        raise ValueError("invalid_modes")
    # Natural-language execution has no separate mode switch; the per-action
    # policy and live Discord checks are authoritative. Autonomous stays off.
    value["modes"]["action"] = True
    value["modes"]["autonomous"] = False
    if value["mode"] in {"ACTION", "AUTONOMOUS"}:
        value["mode"] = "CHAT"
    if not isinstance(value["activation"], dict) or any(not isinstance(v, bool) for v in value["activation"].values()):
        raise ValueError("invalid_activation")
    value["activation"]["automatic"] = False
    natural = value["natural_commands"]
    if (
        not isinstance(natural, dict)
        or not isinstance(natural.get("enabled"), bool)
        or natural.get("clarification_behavior") not in {"ask", "show_matches"}
        or natural.get("unknown_command_behavior") not in {"respond", "ignore"}
    ):
        raise ValueError("invalid_natural_commands")
    actions = value["actions"]
    if not isinstance(actions, dict) or set(actions) != set(ACTION_REGISTRY):
        raise ValueError("invalid_actions")
    for action_id, metadata in ACTION_REGISTRY.items():
        policy = actions[action_id]
        if not isinstance(policy, dict):
            raise ValueError("invalid_action_policy")
        if not isinstance(policy.get("enabled"), bool) or not isinstance(
            policy.get("confirmation_required"), bool
        ):
            raise ValueError("invalid_action_policy")
        # Confirmation policy is fixed by registered action risk: safe actions
        # do not ask, and high-risk actions cannot be disabled by a stale draft.
        policy["confirmation_required"] = action_id in DANGEROUS_CONFIRMATION_TOOLS
        for key in ("allowed_channels", "allowed_roles"):
            policy[key] = _ids(policy.get(key), f"{action_id}_{key}")
        minimum_role_id = str(policy.get("minimum_role_id", "") or "")
        if minimum_role_id and (
            not minimum_role_id.isascii()
            or not minimum_role_id.isdigit()
            or not 15 <= len(minimum_role_id) <= 22
        ):
            raise ValueError(f"invalid_{action_id}_minimum_role")
        policy["minimum_role_id"] = minimum_role_id

    safety = value["safety"]
    for key in (
        "enabled", "dry_run", "confirmation_enabled",
        "prompt_injection_protection", "mass_action_protection",
    ):
        if not isinstance(safety.get(key), bool):
            raise ValueError("invalid_safety_policy")
    if not safety["prompt_injection_protection"] or not safety["mass_action_protection"]:
        raise ValueError("required_safety_protection")
    # Retained for old clients only; PRIME Autopilot actions never use a second confirmation.
    safety["confirmation_enabled"] = False
    number = safety["max_action_count"]
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 5:
        raise ValueError("invalid_max_action_count")
    for key in ("allowed_channels", "blocked_channels", "allowed_roles", "blocked_roles"):
        value["access"][key] = _ids(value["access"][key], key)
    if not isinstance(value["access"].get("legacy_allowlist_conflict"), bool):
        raise ValueError("invalid_access_policy")
    if value["access"]["minimum_permission"] not in PERMISSION_LEVELS:
        raise ValueError("invalid_minimum_permission")

    if isinstance(value["context"]["max_messages"], bool) or not isinstance(value["context"]["max_messages"], int) or not 0 <= value["context"]["max_messages"] <= 30:
        raise ValueError("invalid_context_limit")
    if not isinstance(value["context"]["include_reply_context"], bool):
        raise ValueError("invalid_reply_context")

    personality = value["personality"]
    if personality["preset"] not in PERSONALITIES:
        raise ValueError("invalid_personality")
    if not isinstance(personality["language"], str) or len(personality["language"]) > 30:
        raise ValueError("invalid_language")
    if not isinstance(personality["tone"], str) or len(personality["tone"]) > 80:
        raise ValueError("invalid_personality_tone")
    if not isinstance(personality["arabic_dialect"], str) or len(personality["arabic_dialect"]) > 40:
        raise ValueError("invalid_arabic_dialect")
    thinking_level = str(value["provider"].get("thinking_level", "medium")).lower()
    if thinking_level not in {"low", "medium", "high"}:
        raise ValueError("invalid_thinking_level")
    value["provider"]["thinking_level"] = thinking_level
    for key in ("greeting_style", "reply_style"):
        if not isinstance(personality[key], str) or len(personality[key]) > 80:
            raise ValueError(f"invalid_personality_{key}")
    if not isinstance(personality["custom_instructions"], str) or len(personality["custom_instructions"]) > 1000:
        raise ValueError("invalid_custom_personality")
    for key in (
        "formality", "humor", "emoji_usage", "toughness", "directness",
        "friendliness", "seriousness",
    ):
        if isinstance(personality[key], bool) or not isinstance(personality[key], int) or not 0 <= personality[key] <= 100:
            raise ValueError(f"invalid_personality_{key}")
    for key, choices in (
        ("response_length", {"short", "medium", "long"}),
        ("emotional_style", {"balanced", "warm", "direct", "neutral"}),
    ):
        if personality[key] not in choices:
            raise ValueError(f"invalid_personality_{key}")

    personas = value["channel_personas"]
    if not isinstance(personas, dict) or len(personas) > 100:
        raise ValueError("invalid_channel_personas")
    clean_personas = {}
    for channel_id, config in personas.items():
        if not str(channel_id).isascii() or not str(channel_id).isdigit() or not isinstance(config, dict):
            raise ValueError("invalid_channel_persona")
        allowed_persona_fields = {
            "enabled", "preset", "tone", "custom_instructions", "response_length",
            "formality", "humor", "emoji_usage", "directness", "friendliness",
            "seriousness",
        }
        if set(config) - allowed_persona_fields:
            raise ValueError("invalid_channel_persona")
        if not isinstance(config.get("enabled", True), bool):
            raise ValueError("invalid_channel_persona")
        if config.get("preset", personality["preset"]) not in PERSONALITIES:
            raise ValueError("invalid_channel_persona")
        tone = config.get("tone", "")
        custom_instructions = config.get("custom_instructions", "")
        if not isinstance(tone, str) or len(tone) > 80:
            raise ValueError("invalid_channel_persona")
        if not isinstance(custom_instructions, str) or len(custom_instructions) > 500:
            raise ValueError("invalid_channel_persona")
        clean = {
            "enabled": config.get("enabled", True),
            "preset": config.get("preset", personality["preset"]),
            "tone": tone,
            "custom_instructions": custom_instructions,
        }
        if "response_length" in config:
            if config["response_length"] not in {"short", "medium", "long"}:
                raise ValueError("invalid_channel_persona")
            clean["response_length"] = config["response_length"]
        for key in (
            "formality", "humor", "emoji_usage", "directness", "friendliness",
            "seriousness",
        ):
            if key in config:
                number = config[key]
                if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 100:
                    raise ValueError("invalid_channel_persona")
                clean[key] = number
        clean_personas[str(channel_id)] = clean
    value["channel_personas"] = clean_personas

    roles = value["role_overrides"]
    if not isinstance(roles, dict) or len(roles) > 100:
        raise ValueError("invalid_role_overrides")
    clean_roles = {}
    for role_id, entry in roles.items():
        if (
            not str(role_id).isascii()
            or not str(role_id).isdigit()
            or not isinstance(entry, dict)
            or set(entry) - {"preset", "tone"}
        ):
            raise ValueError("invalid_role_override")
        clean_entry = {}
        if "preset" in entry:
            if entry["preset"] not in PERSONALITIES:
                raise ValueError("invalid_role_override")
            clean_entry["preset"] = entry["preset"]
        if "tone" in entry:
            if not isinstance(entry["tone"], str) or len(entry["tone"]) > 80:
                raise ValueError("invalid_role_override")
            clean_entry["tone"] = entry["tone"]
        clean_roles[str(role_id)] = clean_entry
    value["role_overrides"] = clean_roles
    if value["provider"]["name"] == "Pollinations":
        # Existing guild rows use Pollinations-specific model IDs. Convert
        # those settings in memory so the provider switch does not break them.
        value["provider"]["name"] = "Google Gemini"
        value["provider"]["model"] = "gemini-3.8-flash"
    if value["provider"]["name"] != "Google Gemini":
        raise ValueError("unsupported_provider")
    if not isinstance(value["provider"]["model"], str) or not _SAFE_MODEL_RE.fullmatch(value["provider"]["model"]):
        raise ValueError("invalid_model")
    provider = value["provider"]
    if isinstance(provider["temperature"], bool) or not isinstance(provider["temperature"], (int, float)) or not 0 <= provider["temperature"] <= 2:
        raise ValueError("invalid_temperature")
    for key, low, high in (("max_tokens", 64, 8192), ("timeout_seconds", 3, 120), ("retry_count", 0, 3)):
        number = provider[key]
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
            raise ValueError(f"invalid_provider_{key}")

    response = value["response"]
    if (
        allow_legacy_values
        and isinstance(response["maximum_length"], int)
        and not isinstance(response["maximum_length"], bool)
        and response["maximum_length"] > 3500
    ):
        response["maximum_length"] = 3500
    for key, low, high in (("maximum_length", 100, 3500), ("auto_delete_seconds", 0, 86400)):
        number = response[key]
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
            raise ValueError(f"invalid_response_{key}")
    for key in ("streaming", "typing_indicator", "reply_behavior", "embed_behavior", "markdown", "emoji"):
        if not isinstance(response[key], bool):
            raise ValueError(f"invalid_response_{key}")
    if response["mention_behavior"] not in {"none", "user", "roles"}:
        raise ValueError("invalid_mention_behavior")

    memory = value["memory"]
    if any(
        not isinstance(memory[key], bool)
        for key in (
            "enabled", "auto_store", "sensitive_storage", "user_memory_enabled",
            "server_memory_enabled", "creation_enabled", "retrieval_enabled",
        )
    ):
        raise ValueError("invalid_memory_flags")
    # Automatic capture and sensitive storage are intentionally unsupported.
    memory["auto_store"] = False
    memory["sensitive_storage"] = False
    for key, low, high in (
        ("context_limit", 0, 30),
        ("default_expiration_days", 0, 3650),
        ("maximum_count", 1, 500),
        ("maximum_content_length", 100, 1000),
    ):
        number = memory[key]
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
            raise ValueError(f"invalid_memory_{key}")
    if not isinstance(value["sandbox"]["enabled"], bool):
        raise ValueError("invalid_sandbox")
    value["sandbox"]["enabled"] = False

    moderation = value["moderation"]
    if moderation["mode"] not in MODERATION_MODES:
        raise ValueError("invalid_moderation_mode")
    if (
        isinstance(moderation["confidence_threshold"], bool)
        or not isinstance(moderation["confidence_threshold"], (int, float))
        or not 0.5 <= moderation["confidence_threshold"] <= 1
    ):
        raise ValueError("invalid_moderation_threshold")
    moderation["channel_ids"] = _ids(moderation["channel_ids"], "moderation_channels")
    alert_channel = moderation.get("alert_channel_id", "")
    if alert_channel and (
        not str(alert_channel).isascii()
        or not str(alert_channel).isdigit()
        or not 15 <= len(str(alert_channel)) <= 22
    ):
        raise ValueError("invalid_moderation_alert_channel")
    moderation["alert_channel_id"] = str(alert_channel)
    categories = moderation.get("categories")
    if (
        not isinstance(categories, list)
        or len(categories) > len(MODERATION_CATEGORIES)
        or any(item not in MODERATION_CATEGORIES for item in categories)
    ):
        raise ValueError("invalid_moderation_categories")
    moderation["categories"] = list(dict.fromkeys(categories))
    if moderation.get("auto_action_policy") not in {"NONE", "TIMEOUT_MEMBER"}:
        raise ValueError("invalid_moderation_auto_action")
    if not isinstance(moderation.get("log_findings"), bool):
        raise ValueError("invalid_moderation_logging")
    if any(
        not isinstance(moderation.get(key), bool)
        for key in ("warn_enabled", "timeout_enabled")
    ):
        raise ValueError("invalid_moderation_actions")
    if isinstance(moderation["timeout_minutes"], bool) or not isinstance(moderation["timeout_minutes"], int) or not 1 <= moderation["timeout_minutes"] <= 10080:
        raise ValueError("invalid_timeout_minutes")
    # Older moderation enforcement switches remain readable but cannot activate
    # legacy direct-punishment behavior. Phase 4 findings use the Action Engine.
    moderation["warn_enabled"] = False
    moderation["timeout_enabled"] = False
    if moderation["mode"] in {"ALERT", "RECOMMEND", "AUTO_WITH_CONFIRMATION"}:
        if not moderation["channel_ids"] or not moderation["alert_channel_id"]:
            raise ValueError("moderation_alert_channel_required")
    if (
        moderation["mode"] == "AUTO_WITH_CONFIRMATION"
        and moderation["auto_action_policy"] == "TIMEOUT_MEMBER"
        and not moderation["log_findings"]
    ):
        raise ValueError("moderation_review_requires_logging")

    expected_rates = set(DEFAULT_CONTROL_SETTINGS["rate_limits"])
    if set(value["rate_limits"]) != expected_rates:
        raise ValueError("invalid_rate_limits")
    for key, config in value["rate_limits"].items():
        value["rate_limits"][key] = _validate_rate(config, key)
    for key, number in value["retention"].items():
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 3650:
            raise ValueError(f"invalid_retention_{key}")
    return value


class ControlSettingsConflict(RuntimeError):
    def __init__(self, current: dict):
        super().__init__("PRIME AI control settings changed")
        self.current = current


def _legacy_channel_ids(raw: Any) -> list[str]:
    try:
        decoded = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []
    return list(dict.fromkeys(
        str(item) for item in decoded
        if str(item).isascii() and str(item).isdigit() and 15 <= len(str(item)) <= 22
    ))


async def sync_legacy_channel_allowlist(
    db,
    guild_id: int,
    actor_id: int,
    allowed_channels: list[str],
    updated_at: str,
) -> None:
    """Keep the Phase 1 settings row as a compatibility mirror of the one UI policy."""
    channels = _ids(allowed_channels, "allowed_channels")
    async with db.execute(
        "SELECT allowed_channel_ids, allowed_channels_migrated, revision "
        "FROM prime_ai_settings WHERE guild_id = ?",
        (int(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        if not channels:
            return
        await db.execute(
            "INSERT INTO prime_ai_settings "
            "(guild_id, enabled, system_prompt, allowed_channel_ids, "
            "allowed_channels_migrated, revision, updated_by, updated_at) "
            "VALUES (?, 1, '', ?, 1, 0, ?, ?)",
            (
                int(guild_id),
                json.dumps(channels, ensure_ascii=False),
                int(actor_id),
                updated_at,
            ),
        )
        return
    current_channels = _legacy_channel_ids(row["allowed_channel_ids"])
    if current_channels == channels and int(row["allowed_channels_migrated"] or 0) == 1:
        return
    await db.execute(
        "UPDATE prime_ai_settings SET allowed_channel_ids = ?, "
        "allowed_channels_migrated = 1, revision = revision + 1, "
        "updated_by = ?, updated_at = ? WHERE guild_id = ?",
        (
            json.dumps(channels, ensure_ascii=False),
            int(actor_id),
            updated_at,
            int(guild_id),
        ),
    )


async def get_control_settings(guild_id: int) -> dict:
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT settings_json, revision, updated_by, updated_at "
            "FROM prime_ai_control_settings WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
        async with db.execute(
            "SELECT allowed_channel_ids, allowed_channels_migrated "
            "FROM prime_ai_settings WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            legacy_row = await cur.fetchone()

        revision = int(row["revision"]) if row else 0
        updated_by = row["updated_by"] if row else None
        updated_at = row["updated_at"] if row else None
        stored = _json_object(row["settings_json"]) if row else {}
        config = normalize_control_settings(stored, allow_legacy_values=True)
        migrated = bool(
            legacy_row and int(legacy_row["allowed_channels_migrated"] or 0)
        )
        if legacy_row and not migrated:
            legacy_channels = _legacy_channel_ids(legacy_row["allowed_channel_ids"])
            control_channels = config["access"]["allowed_channels"]
            if legacy_channels and control_channels:
                legacy_set = set(legacy_channels)
                intersection = [
                    channel for channel in control_channels
                    if channel in legacy_set
                ]
                conflict = set(legacy_channels) != set(control_channels)
            else:
                intersection = control_channels or legacy_channels
                conflict = False
            config["access"]["allowed_channels"] = intersection
            config["access"]["legacy_allowlist_conflict"] = conflict
            serialized = json.dumps(config, ensure_ascii=False)
            migration_time = timestamp()
            if row:
                revision += 1
                await db.execute(
                    "UPDATE prime_ai_control_settings SET settings_json = ?, "
                    "revision = ?, updated_at = ? WHERE guild_id = ?",
                    (serialized, revision, migration_time, int(guild_id)),
                )
                updated_at = migration_time
            else:
                await db.execute(
                    "INSERT INTO prime_ai_control_settings "
                    "(guild_id, settings_json, revision, updated_at) "
                    "VALUES (?, ?, 0, ?)",
                    (int(guild_id), serialized, migration_time),
                )
                updated_at = migration_time
            await db.execute(
                "UPDATE prime_ai_settings SET allowed_channels_migrated = 1 "
                "WHERE guild_id = ?",
                (int(guild_id),),
            )
        await db.commit()

    return {
        "config": config,
        "revision": revision,
        "updated_by": str(updated_by) if updated_by is not None else None,
        "updated_at": updated_at,
    }


async def save_control_settings(
    guild_id: int,
    actor_id: int,
    config: dict,
    expected_revision: int,
) -> dict:
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise ValueError("invalid_revision")
    if not isinstance(config, dict):
        raise ValueError("invalid_settings")
    config = dict(config)
    normalized = normalize_control_settings(config)
    gid, actor = int(guild_id), int(actor_id)
    updated_at = timestamp()
    async with database.connect(aiosqlite.Row) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT settings_json, revision, updated_by, updated_at "
                "FROM prime_ai_control_settings WHERE guild_id = ?",
                (gid,),
            ) as cur:
                row = await cur.fetchone()
            current = (
                {
                    "config": normalize_control_settings(
                        _json_object(row["settings_json"]),
                        allow_legacy_values=True,
                    ),
                    "revision": int(row["revision"]),
                }
                if row else {"config": json.loads(json.dumps(DEFAULT_CONTROL_SETTINGS)), "revision": 0}
            )
            if current["revision"] != expected_revision:
                raise ControlSettingsConflict(current)
            current_access = current["config"]["access"]
            next_access = normalized["access"]
            if current_access["legacy_allowlist_conflict"]:
                # Keep a disjoint legacy/control mismatch fail-closed until a
                # non-empty allowlist is saved. For a non-empty intersection,
                # syncing it to the legacy row can only preserve or narrow access.
                next_access["legacy_allowlist_conflict"] = not bool(
                    next_access["allowed_channels"]
                )
            revision = current["revision"] + 1
            await db.execute(
                "INSERT INTO prime_ai_control_settings "
                "(guild_id, settings_json, revision, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
                "settings_json=excluded.settings_json, revision=excluded.revision, "
                "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (gid, json.dumps(normalized, ensure_ascii=False), revision, actor, updated_at),
            )
            await sync_legacy_channel_allowlist(
                db,
                gid,
                actor,
                normalized["access"]["allowed_channels"],
                updated_at,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return {
        "config": normalized,
        "revision": revision,
        "updated_by": str(actor),
        "updated_at": updated_at,
    }


async def get_skills(guild_id: int) -> list[dict]:
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT skill_key, enabled, settings_json, revision, updated_by, updated_at "
            "FROM prime_ai_skills WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            rows = await cur.fetchall()
    stored = {str(row["skill_key"]): row for row in rows}
    result = []
    for item in SKILL_CATALOG:
        row = stored.get(item["key"])
        overrides = _json_object(row["settings_json"]) if row else {}
        result.append({
            **item,
            "enabled": bool(row["enabled"]) if row else item["enabled_by_default"],
            "required_permission": overrides.get("required_permission", item["permission"]),
            "allowed_roles": overrides.get("allowed_roles", []),
            "allowed_channels": overrides.get("allowed_channels", []),
            # Action lists are part of the server-side registry only. Stored
            # Phase 4 overrides are ignored so stale DB values cannot reopen them.
            "allowed_actions": list(item["actions"]),
            "confirmation_required": False,
            "dangerous_actions": [],
            "rate_limit": overrides.get("rate_limit", _default_rate(5, 60)),
            "revision": int(row["revision"]) if row else 0,
            "updated_by": str(row["updated_by"]) if row and row["updated_by"] is not None else None,
            "updated_at": row["updated_at"] if row else None,
        })
    return result


def public_skill(skill: dict) -> dict:
    """Dashboard-safe registry projection; omits handlers and implementation fields."""
    public_fields = {
        "key", "skill_id", "name", "description", "category", "enabled",
        "required_permission", "required_context", "supported_intents",
        "dashboard_config", "safety_level", "available", "allowed_roles",
        "allowed_channels", "rate_limit", "revision", "updated_by", "updated_at",
    }
    return {key: value for key, value in skill.items() if key in public_fields}


async def get_public_skills(guild_id: int) -> list[dict]:
    """Dashboard-safe registry projection; omits handlers and implementation fields."""
    return [public_skill(skill) for skill in await get_skills(guild_id)]


async def save_skill(
    guild_id: int,
    actor_id: int,
    skill_key: str,
    payload: dict,
    expected_revision: int,
) -> dict:
    catalog = next((item for item in SKILL_CATALOG if item["key"] == skill_key), None)
    if catalog is None:
        raise ValueError("unknown_skill")
    allowed_fields = {
        "enabled", "required_permission", "allowed_roles", "allowed_channels",
        "rate_limit",
    }
    if not isinstance(payload, dict) or set(payload) - allowed_fields:
        raise ValueError("invalid_skill_settings")
    current = next(item for item in await get_skills(guild_id) if item["key"] == skill_key)
    if current["revision"] != expected_revision:
        raise ControlSettingsConflict({"skill": current, "revision": current["revision"]})
    config = {
        "enabled": payload.get("enabled", current["enabled"]),
        "required_permission": payload.get("required_permission", current["required_permission"]),
        "allowed_roles": _ids(payload.get("allowed_roles", current["allowed_roles"]), "skill_roles"),
        "allowed_channels": _ids(payload.get("allowed_channels", current["allowed_channels"]), "skill_channels"),
        "rate_limit": _validate_rate(payload.get("rate_limit", current["rate_limit"]), "skill"),
    }
    if not isinstance(config["enabled"], bool):
        raise ValueError("invalid_skill_flags")
    if config["required_permission"] not in PERMISSION_LEVELS:
        raise ValueError("invalid_skill_permission")
    gid, actor = int(guild_id), int(actor_id)
    updated_at = timestamp()
    stored = {key: value for key, value in config.items() if key != "enabled"}
    async with database.connect(aiosqlite.Row) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT enabled, settings_json, revision, updated_by, updated_at "
                "FROM prime_ai_skills WHERE guild_id=? AND skill_key=?",
                (gid, skill_key),
            ) as cur:
                row = await cur.fetchone()
            current_revision = int(row["revision"]) if row else 0
            if current_revision != expected_revision:
                await db.rollback()
                fresh = next(item for item in await get_skills(gid) if item["key"] == skill_key)
                raise ControlSettingsConflict({"skill": fresh, "revision": fresh["revision"]})
            revision = current_revision + 1
            await db.execute(
                "INSERT INTO prime_ai_skills "
                "(guild_id, skill_key, enabled, settings_json, revision, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id, skill_key) DO UPDATE SET "
                "enabled=excluded.enabled, settings_json=excluded.settings_json, "
                "revision=excluded.revision, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (gid, skill_key, int(config["enabled"]), json.dumps(stored), revision, actor, updated_at),
            )
            await db.commit()
        except ControlSettingsConflict:
            raise
        except Exception:
            await db.rollback()
            raise
    return {**catalog, **config, "revision": revision, "updated_by": str(actor), "updated_at": updated_at}


async def save_memory(
    guild_id: int,
    actor_id: int,
    content: str,
    *,
    scope: str = "SERVER",
    scope_id: str = "",
    expires_in_days: int | None = None,
    memory_id: int | None = None,
    enabled: bool = True,
    source_guild_id: int | None = None,
    maximum_count: int = 500,
    maximum_content_length: int = 1000,
) -> dict:
    value = str(content).strip()
    scope = str(scope).upper()
    if scope == "USER":
        raise ValueError("user_memory_requires_owner_confirmation")
    if scope not in {"GLOBAL", "SERVER", "CHANNEL", "ROLE"}:
        raise ValueError("invalid_memory_scope")
    validate_memory_content(value, maximum_content_length)
    if not isinstance(enabled, bool):
        raise ValueError("invalid_memory_enabled")
    if (
        isinstance(maximum_count, bool)
        or not isinstance(maximum_count, int)
        or not 1 <= maximum_count <= 500
    ):
        raise ValueError("invalid_memory_maximum_count")
    if expires_in_days is not None and (
        isinstance(expires_in_days, bool)
        or not isinstance(expires_in_days, int)
        or not 0 <= expires_in_days <= 3650
    ):
        raise ValueError("invalid_memory_expiry")
    if scope in {"CHANNEL", "ROLE"}:
        if not str(scope_id).isascii() or not str(scope_id).isdigit() or not 15 <= len(str(scope_id)) <= 22:
            raise ValueError("invalid_memory_scope_id")
    else:
        scope_id = ""
    gid, actor = int(guild_id), int(actor_id)
    created = timestamp()
    expires = timestamp(now_utc() + timedelta(days=expires_in_days)) if expires_in_days else None
    async with database.connect(aiosqlite.Row) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            if memory_id is None:
                async with db.execute(
                    "SELECT COUNT(*) AS total FROM prime_ai_memories "
                    "WHERE guild_id = ? AND status IN ('ACTIVE', 'PENDING')",
                    (gid,),
                ) as cur:
                    count = int((await cur.fetchone())["total"])
                if count >= maximum_count:
                    raise ValueError("memory_limit_reached")
                cur = await db.execute(
                    "INSERT INTO prime_ai_memories "
                    "(guild_id, content, created_by, created_at, scope, scope_id, enabled, expires_at, "
                    "updated_at, source, confidence, status, owner_user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ADMIN', 1.0, 'ACTIVE', NULL)",
                    (
                        gid, value, actor, created, scope, str(scope_id), int(enabled),
                        expires, created,
                    ),
                )
                mid = int(cur.lastrowid)
            else:
                cur = await db.execute(
                    "UPDATE prime_ai_memories SET guild_id=?, content=?, scope=?, scope_id=?, enabled=?, "
                    "expires_at=?, updated_at=?, source='ADMIN', confidence=1.0, "
                    "status='ACTIVE', owner_user_id=NULL "
                    "WHERE guild_id=? AND memory_id=? AND scope != 'USER' "
                    "AND source != 'AI_CANDIDATE'",
                    (
                        gid, value, scope, str(scope_id), int(enabled), expires, created,
                        int(source_guild_id if source_guild_id is not None else gid),
                        int(memory_id),
                    ),
                )
                if cur.rowcount == 0:
                    raise ValueError("memory_not_found")
                mid = int(memory_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return {
        "id": mid,
        "content": value,
        "scope": scope,
        "scope_id": str(scope_id),
        "enabled": enabled,
        "expires_at": expires,
        "created_by": str(actor),
        "created_at": created,
        "updated_at": created,
        "source": "ADMIN",
        "confidence": 1.0,
        "status": "ACTIVE",
        "owner_user_id": None,
    }


_MEMORY_SECRET_RE = re.compile(
    r"(?i)(?:api[\s_-]*key|password|passwd|secret|private[\s_-]*key|"
    r"access[\s_-]*token|auth(?:entication)?[\s_-]*token|كلمة\s*المرور|"
    r"كلمة\s*السر|مفتاح\s*(?:سري|خاص)|رمز\s*الدخول)\s*(?:[:=]|\bis\b)\s*\S+"
)
_MEMORY_PAYMENT_RE = re.compile(
    r"(?i)(?:credit\s*card|debit\s*card|card\s*(?:number|no\.?)|cvv|cvc|"
    r"iban|bank\s*account|payment\s*(?:card|details)|بطاقة\s*(?:الائتمان|البنك|الصراف)|"
    r"رقم\s*(?:بطاقتي|البطاقة|الحساب)|حساب\s*بنكي|رمز\s*التحقق).{0,60}\d"
)
_MEMORY_PRIVATE_PERSONAL_RE = re.compile(
    r"(?i)(?:my\s+(?:home\s+)?address|my\s+phone(?:\s+number)?|"
    r"عنوان(?:ي)?\s*(?:هو|:)|رقم\s*(?:جوالي|هاتفي)|هاتف(?:ي)?\s*:).{0,60}\d"
)


def validate_memory_content(content: str, maximum_length: int = 1000) -> str:
    value = str(content or "").strip()
    if (
        isinstance(maximum_length, bool)
        or not isinstance(maximum_length, int)
        or not 100 <= maximum_length <= 1000
        or not value
        or len(value) > maximum_length
    ):
        raise ValueError("invalid_memory_content")
    if (
        _MEMORY_SECRET_RE.search(value)
        or _MEMORY_PAYMENT_RE.search(value)
        or _MEMORY_PRIVATE_PERSONAL_RE.search(value)
    ):
        raise ValueError("sensitive_memory_rejected")
    return value


async def create_memory_candidate(
    guild_id: int,
    actor_id: int,
    content: str,
    *,
    confidence: float,
    expires_in_days: int,
    maximum_count: int = 500,
    maximum_content_length: int = 1000,
    candidate_ttl_hours: int = 24,
) -> dict:
    """Persist a user-owned, pending candidate; it is not retrievable until approved."""
    value = validate_memory_content(content, maximum_content_length)
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.85 <= confidence <= 1
    ):
        raise ValueError("invalid_memory_confidence")
    if (
        isinstance(expires_in_days, bool)
        or not isinstance(expires_in_days, int)
        or not 0 <= expires_in_days <= 3650
    ):
        raise ValueError("invalid_memory_expiry")
    if (
        isinstance(candidate_ttl_hours, bool)
        or not isinstance(candidate_ttl_hours, int)
        or not 1 <= candidate_ttl_hours <= 72
    ):
        raise ValueError("invalid_candidate_expiry")
    gid, actor = int(guild_id), int(actor_id)
    created = timestamp()
    expires = timestamp(now_utc() + timedelta(days=expires_in_days)) if expires_in_days else None
    candidate_expires = timestamp(now_utc() + timedelta(hours=candidate_ttl_hours))
    async with database.connect(aiosqlite.Row) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT COUNT(*) AS total FROM prime_ai_memories "
                "WHERE guild_id = ? AND status IN ('ACTIVE', 'PENDING')",
                (gid,),
            ) as cur:
                count = int((await cur.fetchone())["total"])
            if count >= maximum_count:
                raise ValueError("memory_limit_reached")
            cur = await db.execute(
                "INSERT INTO prime_ai_memories "
                "(guild_id, content, created_by, created_at, scope, scope_id, enabled, expires_at, "
                "updated_at, source, confidence, status, owner_user_id, candidate_expires_at) "
                "VALUES (?, ?, ?, ?, 'USER', ?, 0, ?, ?, 'AI_CANDIDATE', ?, 'PENDING', ?, ?)",
                (
                    gid, value, actor, created, str(actor), expires, created,
                    float(confidence), actor, candidate_expires,
                ),
            )
            memory_id = int(cur.lastrowid)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return {
        "id": memory_id,
        "content": value,
        "scope": "USER",
        "scope_id": str(actor),
        "enabled": False,
        "expires_at": expires,
        "created_by": str(actor),
        "created_at": created,
        "updated_at": created,
        "source": "AI_CANDIDATE",
        "confidence": float(confidence),
        "status": "PENDING",
        "owner_user_id": str(actor),
        "candidate_expires_at": candidate_expires,
        "confirmation_message_id": None,
    }


async def set_memory_candidate_message(guild_id: int, memory_id: int, message_id: int) -> bool:
    async with database.connect() as db:
        cur = await db.execute(
            "UPDATE prime_ai_memories SET confirmation_message_id = ? "
            "WHERE guild_id = ? AND memory_id = ? AND status = 'PENDING' "
            "AND candidate_expires_at > ?",
            (int(message_id), int(guild_id), int(memory_id), timestamp()),
        )
        await db.commit()
    return cur.rowcount > 0


async def get_memory_candidate(guild_id: int, memory_id: int) -> dict | None:
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT memory_id, guild_id, content, created_by, owner_user_id, confidence, "
            "candidate_expires_at, confirmation_message_id, status "
            "FROM prime_ai_memories WHERE guild_id = ? AND memory_id = ? "
            "AND source = 'AI_CANDIDATE' AND status = 'PENDING' "
            "AND candidate_expires_at > ?",
            (int(guild_id), int(memory_id), timestamp()),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": int(row["memory_id"]),
        "guild_id": int(row["guild_id"]),
        "content": row["content"],
        "created_by": str(row["created_by"]),
        "owner_user_id": str(row["owner_user_id"] or ""),
        "confidence": float(row["confidence"]),
        "candidate_expires_at": row["candidate_expires_at"],
        "confirmation_message_id": row["confirmation_message_id"],
        "status": row["status"],
    }


async def list_pending_memory_candidates(guild_id: int, limit: int = 100) -> list[dict]:
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT memory_id, confirmation_message_id FROM prime_ai_memories "
            "WHERE guild_id = ? AND source = 'AI_CANDIDATE' AND status = 'PENDING' "
            "AND confirmation_message_id IS NOT NULL AND candidate_expires_at > ? "
            "ORDER BY memory_id DESC LIMIT ?",
            (int(guild_id), timestamp(), max(1, min(int(limit), 100))),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"id": int(row["memory_id"]), "message_id": int(row["confirmation_message_id"])}
        for row in rows
    ]


async def resolve_memory_candidate(
    guild_id: int,
    memory_id: int,
    owner_user_id: int,
    *,
    approve: bool,
) -> bool:
    if not isinstance(approve, bool):
        raise ValueError("invalid_memory_decision")
    gid, mid, owner = int(guild_id), int(memory_id), int(owner_user_id)
    async with database.connect() as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            if approve:
                cur = await db.execute(
                    "UPDATE prime_ai_memories SET status='ACTIVE', enabled=1, "
                    "updated_at=?, candidate_expires_at=NULL "
                    "WHERE guild_id=? AND memory_id=? AND source='AI_CANDIDATE' "
                    "AND status='PENDING' AND owner_user_id=? AND candidate_expires_at>?",
                    (timestamp(), gid, mid, owner, timestamp()),
                )
            else:
                cur = await db.execute(
                    "DELETE FROM prime_ai_memories WHERE guild_id=? AND memory_id=? "
                    "AND source='AI_CANDIDATE' AND status='PENDING' "
                    "AND owner_user_id=? AND candidate_expires_at>?",
                    (gid, mid, owner, timestamp()),
                )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return cur.rowcount > 0


async def cancel_memory_candidate(
    guild_id: int,
    memory_id: int,
    owner_user_id: int,
) -> bool:
    async with database.connect() as db:
        cur = await db.execute(
            "DELETE FROM prime_ai_memories WHERE guild_id=? AND memory_id=? "
            "AND source='AI_CANDIDATE' AND status='PENDING' AND owner_user_id=?",
            (int(guild_id), int(memory_id), int(owner_user_id)),
        )
        await db.commit()
    return cur.rowcount > 0


async def save_pending_action_context(
    guild_id: int,
    channel_id: int,
    user_id: int,
    request_text: str,
    *,
    expires_in: int = 600,
) -> None:
    expires = timestamp(now_utc() + timedelta(seconds=max(30, min(int(expires_in), 1800))))
    async with database.connect() as db:
        await db.execute(
            "INSERT INTO prime_ai_pending_actions "
            "(guild_id,channel_id,user_id,request_text,expires_at,updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(guild_id,channel_id,user_id) DO UPDATE SET "
            "request_text=excluded.request_text,expires_at=excluded.expires_at,"
            "updated_at=excluded.updated_at",
            (
                int(guild_id), int(channel_id), int(user_id),
                str(request_text)[:1500], expires, timestamp(),
            ),
        )
        await db.commit()


async def get_pending_action_context(
    guild_id: int,
    channel_id: int,
    user_id: int,
) -> str | None:
    now = timestamp()
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT request_text,expires_at FROM prime_ai_pending_actions "
            "WHERE guild_id=? AND channel_id=? AND user_id=?",
            (int(guild_id), int(channel_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        if str(row["expires_at"]) <= now:
            await db.execute(
                "DELETE FROM prime_ai_pending_actions "
                "WHERE guild_id=? AND channel_id=? AND user_id=?",
                (int(guild_id), int(channel_id), int(user_id)),
            )
            await db.commit()
            return None
        return str(row["request_text"])[:1500]


async def clear_pending_action_context(
    guild_id: int,
    channel_id: int,
    user_id: int,
) -> None:
    async with database.connect() as db:
        await db.execute(
            "DELETE FROM prime_ai_pending_actions "
            "WHERE guild_id=? AND channel_id=? AND user_id=?",
            (int(guild_id), int(channel_id), int(user_id)),
        )
        await db.commit()


async def create_operation(
    *,
    guild_id: int,
    user_id: int,
    channel_id: int | None,
    request: str,
    intent: str,
    skill: str,
    steps: list[dict],
    permissions: dict,
    expires_in: int = 600,
    confirmation: str = "required",
) -> dict:
    if confirmation not in {"required", "not_required"}:
        raise ValueError("invalid_operation_confirmation")
    operation_id = secrets.token_urlsafe(18)
    created = timestamp()
    expires = timestamp(now_utc() + timedelta(seconds=max(30, min(int(expires_in), 3600))))
    target = [
        {
            str(key): str(value)
            for key, value in step.get("target", {}).items()
            if str(key).endswith("_id")
        }
        for step in steps
    ]
    tools = [str(step.get("tool", "")) for step in steps]
    async with database.connect() as db:
        await db.execute(
            "INSERT INTO prime_ai_operations "
            "(operation_id,guild_id,user_id,channel_id,request,detected_intent,skill,tools_json,"
            "target_json,permissions_json,confirmation,status,steps_json,expires_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?,?,?)",
            (
                operation_id, int(guild_id), int(user_id),
                int(channel_id) if channel_id is not None else None,
                str(request)[:1500], str(intent)[:120], str(skill)[:80],
                json.dumps(tools, ensure_ascii=False),
                json.dumps(target, ensure_ascii=False),
                json.dumps(permissions, ensure_ascii=False),
                confirmation,
                json.dumps(steps, ensure_ascii=False),
                expires, created, created,
            ),
        )
        await db.commit()
    return {
        "operation_id": operation_id,
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        "channel_id": str(channel_id) if channel_id is not None else None,
        "request": str(request)[:1500],
        "detected_intent": str(intent)[:120],
        "skill": str(skill)[:80],
        "tools": tools,
        "target": target,
        "permissions": permissions,
        "confirmation": confirmation,
        "status": "PENDING",
        "steps": steps,
        "expires_at": expires,
        "created_at": created,
    }


async def get_operation(operation_id: str) -> dict | None:
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM prime_ai_operations WHERE operation_id = ?",
            (str(operation_id),),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    item = dict(row)
    for key, source in (("tools", "tools_json"), ("target", "target_json"), ("permissions", "permissions_json"), ("steps", "steps_json")):
        try:
            item[key] = json.loads(item.pop(source) or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            item[key] = [] if key in {"tools", "target", "steps"} else {}
    return item


async def attach_operation_message(operation_id: str, message_id: int) -> None:
    async with database.connect() as db:
        await db.execute(
            "UPDATE prime_ai_operations SET message_id=?, updated_at=? WHERE operation_id=? AND status='PENDING'",
            (int(message_id), timestamp(), str(operation_id)),
        )
        await db.commit()


async def set_operation_status(
    operation_id: str,
    status: str,
    *,
    result: str = "",
    error: str = "",
    allowed_from: tuple[str, ...] = ("PENDING",),
) -> bool:
    if status not in {"RUNNING", "SUCCESS", "FAILED", "CANCELLED", "EXPIRED"}:
        raise ValueError("invalid_operation_status")
    placeholders = ",".join("?" for _ in allowed_from)
    async with database.connect() as db:
        values = (
            status,
            str(result)[:1000],
            str(error)[:500],
            timestamp(),
            str(operation_id),
            *allowed_from,
        )
        if status in {"SUCCESS", "FAILED", "CANCELLED", "EXPIRED"}:
            async with db.execute(
                "SELECT steps_json,target_json FROM prime_ai_operations WHERE operation_id=?",
                (str(operation_id),),
            ) as cursor:
                row = await cursor.fetchone()
            if row:
                try:
                    steps = json.loads(row[0] or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    steps = []
                safe_steps = []
                for step in steps if isinstance(steps, list) else []:
                    if not isinstance(step, dict):
                        continue
                    arguments = step.get("arguments", {})
                    safe_steps.append({
                        "tool": str(step.get("tool", ""))[:80],
                        "arguments": {
                            str(key): value
                            for key, value in arguments.items()
                            if isinstance(arguments, dict)
                            and (str(key).endswith("_id") or str(key) == "minutes")
                        } if isinstance(arguments, dict) else {},
                        "target": {
                            str(key): value
                            for key, value in step.get("target", {}).items()
                            if isinstance(step.get("target"), dict)
                            and str(key).endswith("_id")
                        } if isinstance(step.get("target"), dict) else {},
                        "status": str(step.get("status", ""))[:20],
                        "result": str(step.get("result", ""))[:500],
                        "error": str(step.get("error", ""))[:120],
                    })
                try:
                    targets = json.loads(row[1] or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    targets = []
                safe_targets = [
                    {
                        str(key): value
                        for key, value in target.items()
                        if isinstance(target, dict) and str(key).endswith("_id")
                    }
                    for target in targets
                    if isinstance(target, dict)
                ] if isinstance(targets, list) else []
                cur = await db.execute(
                    f"UPDATE prime_ai_operations SET status=?,execution_result=?,error=?,updated_at=?,"
                    f"steps_json=?,target_json=? WHERE operation_id=? AND status IN ({placeholders})",
                    (
                        *values[:4],
                        json.dumps(safe_steps, ensure_ascii=False),
                        json.dumps(safe_targets, ensure_ascii=False),
                        *values[4:],
                    ),
                )
            else:
                cur = await db.execute(
                    f"UPDATE prime_ai_operations SET status=?, execution_result=?, error=?, updated_at=? "
                    f"WHERE operation_id=? AND status IN ({placeholders})",
                    values,
                )
        else:
            cur = await db.execute(
                f"UPDATE prime_ai_operations SET status=?, execution_result=?, error=?, updated_at=? "
                f"WHERE operation_id=? AND status IN ({placeholders})",
                values,
            )
        await db.commit()
    return cur.rowcount == 1


async def claim_operation(operation_id: str, user_id: int) -> bool:
    """Atomically reserve a pending request for its requester before Discord calls."""
    async with database.connect() as db:
        cur = await db.execute(
            "UPDATE prime_ai_operations SET status='RUNNING', updated_at=? "
            "WHERE operation_id=? AND user_id=? AND status='PENDING' AND expires_at>?",
            (timestamp(), str(operation_id), int(user_id), timestamp()),
        )
        await db.commit()
    return cur.rowcount == 1


async def update_operation_steps(operation_id: str, steps: list[dict]) -> None:
    async with database.connect() as db:
        await db.execute(
            "UPDATE prime_ai_operations SET steps_json=?, updated_at=? "
            "WHERE operation_id=? AND status='RUNNING'",
            (json.dumps(steps, ensure_ascii=False), timestamp(), str(operation_id)),
        )
        await db.commit()


async def list_operations(guild_id: int, limit: int = 50) -> list[dict]:
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT operation_id FROM prime_ai_operations WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
            (int(guild_id), max(1, min(int(limit), 100))),
        ) as cur:
            ids = [row["operation_id"] for row in await cur.fetchall()]
    items = []
    for operation_id in ids:
        item = await get_operation(operation_id)
        if item:
            items.append(item)
    return items


async def record_request(
    guild_id: int,
    user_id: int | None,
    channel_id: int | None,
    *,
    skill: str,
    mode: str,
    result: str,
    latency_ms: int,
    tokens_used: int | None = None,
) -> None:
    async with database.connect() as db:
        await db.execute(
            "INSERT INTO prime_ai_request_events "
            "(guild_id,user_id,channel_id,skill,mode,result,latency_ms,tokens_used,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                int(guild_id), int(user_id) if user_id is not None else None,
                int(channel_id) if channel_id is not None else None,
                str(skill)[:80], str(mode)[:20], str(result)[:30],
                max(0, min(int(latency_ms), 3_600_000)),
                max(0, int(tokens_used)) if tokens_used is not None else None,
                timestamp(),
            ),
        )
        await db.commit()


async def record_moderation(
    guild_id: int,
    user_id: int,
    channel_id: int,
    message_id: int,
    message_content: str,
    detection_type: str,
    confidence: float,
    rule_matched: str,
    action: str,
    retention_days: int,
) -> int:
    if not 0 <= float(confidence) <= 1:
        raise ValueError("invalid_confidence")
    created = timestamp()
    expires = timestamp(now_utc() + timedelta(days=retention_days)) if retention_days else None
    async with database.connect() as db:
        cur = await db.execute(
            "INSERT INTO prime_ai_moderation_events "
            "(guild_id,user_id,channel_id,message_id,message_content,detection_type,confidence,"
            "rule_matched,action,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(guild_id), int(user_id), int(channel_id), int(message_id),
                str(message_content)[:1200], str(detection_type)[:80],
                float(confidence), str(rule_matched)[:200], str(action)[:40],
                created, expires,
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_moderation(guild_id: int, limit: int = 50) -> list[dict]:
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT event_id,user_id,channel_id,message_id,message_content,detection_type,"
            "confidence,rule_matched,action,created_at,expires_at "
            "FROM prime_ai_moderation_events WHERE guild_id=? ORDER BY event_id DESC LIMIT ?",
            (int(guild_id), max(1, min(int(limit), 100))),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_moderation_event(guild_id: int, event_id: int) -> dict | None:
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT event_id,guild_id,user_id,channel_id,message_id,message_content,"
            "detection_type,confidence,rule_matched,action,created_at,expires_at "
            "FROM prime_ai_moderation_events WHERE guild_id=? AND event_id=?",
            (int(guild_id), int(event_id)),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def update_moderation_action(
    guild_id: int,
    event_id: int,
    action: str,
    *,
    expected_action: str | None = None,
) -> bool:
    allowed = {
        "LOG_ONLY", "ALERT", "RECOMMEND", "REVIEW_PENDING", "REVIEWING",
        "ACTION_PENDING", "ACTION_SUCCEEDED", "ACTION_FAILED",
        "REVIEW_UNAVAILABLE",
    }
    if action not in allowed:
        raise ValueError("invalid_moderation_action")
    async with database.connect() as db:
        if expected_action is None:
            cursor = await db.execute(
                "UPDATE prime_ai_moderation_events SET action=? "
                "WHERE guild_id=? AND event_id=?",
                (action, int(guild_id), int(event_id)),
            )
        else:
            cursor = await db.execute(
                "UPDATE prime_ai_moderation_events SET action=? "
                "WHERE guild_id=? AND event_id=? AND action=?",
                (action, int(guild_id), int(event_id), str(expected_action)),
            )
        await db.commit()
    return cursor.rowcount == 1


async def get_analytics(guild_id: int) -> dict:
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT COUNT(*) AS requests, "
            "SUM(CASE WHEN result='success' THEN 1 ELSE 0 END) AS successful, "
            "SUM(CASE WHEN result!='success' THEN 1 ELSE 0 END) AS failed, "
            "AVG(latency_ms) AS avg_latency_ms, SUM(COALESCE(tokens_used,0)) AS tokens "
            "FROM prime_ai_request_events WHERE guild_id=? AND created_at >= datetime('now','-30 days')",
            (int(guild_id),),
        ) as cur:
            totals = dict(await cur.fetchone())
        async with db.execute(
            "SELECT skill, COUNT(*) AS requests FROM prime_ai_request_events "
            "WHERE guild_id=? AND created_at >= datetime('now','-30 days') "
            "GROUP BY skill ORDER BY requests DESC LIMIT 10",
            (int(guild_id),),
        ) as cur:
            skills = [dict(row) for row in await cur.fetchall()]
        async with db.execute(
            "SELECT channel_id, COUNT(*) AS requests FROM prime_ai_request_events "
            "WHERE guild_id=? AND channel_id IS NOT NULL AND created_at >= datetime('now','-30 days') "
            "GROUP BY channel_id ORDER BY requests DESC LIMIT 10",
            (int(guild_id),),
        ) as cur:
            channels = [dict(row) for row in await cur.fetchall()]
        async with db.execute(
            "SELECT user_id, COUNT(*) AS requests FROM prime_ai_request_events "
            "WHERE guild_id=? AND user_id IS NOT NULL AND created_at >= datetime('now','-30 days') "
            "GROUP BY user_id ORDER BY requests DESC LIMIT 10",
            (int(guild_id),),
        ) as cur:
            users = [dict(row) for row in await cur.fetchall()]
        async with db.execute(
            "SELECT COUNT(*) FROM prime_ai_operations WHERE guild_id=? AND created_at >= datetime('now','-30 days')",
            (int(guild_id),),
        ) as cur:
            actions = int((await cur.fetchone())[0])
        async with db.execute(
            "SELECT COUNT(*) FROM prime_ai_moderation_events WHERE guild_id=? AND created_at >= datetime('now','-30 days')",
            (int(guild_id),),
        ) as cur:
            moderation = int((await cur.fetchone())[0])
    for key in ("requests", "successful", "failed", "tokens"):
        totals[key] = int(totals.get(key) or 0)
    totals["avg_latency_ms"] = round(float(totals.get("avg_latency_ms") or 0), 1)
    return {"totals": totals, "actions": actions, "moderation_events": moderation, "skills": skills, "channels": channels, "users": users}


async def prune_expired_data(guild_id: int, settings: dict) -> dict:
    retention = settings.get("retention", {})
    now = timestamp()
    deleted = {}
    async with database.connect() as db:
        cur = await db.execute(
            "DELETE FROM prime_ai_memories WHERE guild_id=? "
            "AND source='AI_CANDIDATE' AND status='PENDING' "
            "AND candidate_expires_at IS NOT NULL AND candidate_expires_at <= ?",
            (int(guild_id), now),
        )
        deleted["expired_memory_candidates"] = max(0, int(cur.rowcount))
        for key, table in (
            ("memory_days", "prime_ai_memories"),
            ("audit_days", "prime_ai_audit"),
            ("moderation_days", "prime_ai_moderation_events"),
        ):
            days = int(retention.get(key, 0) or 0)
            if days <= 0:
                deleted[key] = 0
                continue
            cur = await db.execute(
                f"DELETE FROM {table} WHERE guild_id=? AND created_at < datetime('now', ?)",
                (int(guild_id), f"-{days} days"),
            )
            deleted[key] = max(0, int(cur.rowcount))
        cur = await db.execute(
            "DELETE FROM prime_ai_memories WHERE guild_id=? AND expires_at IS NOT NULL AND expires_at <= ?",
            (int(guild_id), now),
        )
        deleted["expired_memories"] = max(0, int(cur.rowcount))
        cur = await db.execute(
            "UPDATE prime_ai_operations SET status='EXPIRED', updated_at=? "
            "WHERE guild_id=? AND status='PENDING' AND expires_at <= ?",
            (now, int(guild_id), now),
        )
        deleted["expired_operations"] = max(0, int(cur.rowcount))
        audit_days = int(retention.get("audit_days", 0) or 0)
        if audit_days > 0:
            cur = await db.execute(
                "DELETE FROM prime_ai_request_events WHERE guild_id=? AND created_at < datetime('now', ?)",
                (int(guild_id), f"-{audit_days} days"),
            )
            deleted["request_events"] = max(0, int(cur.rowcount))
            cur = await db.execute(
                "DELETE FROM prime_ai_operations WHERE guild_id=? "
                "AND status IN ('SUCCESS','FAILED','CANCELLED','EXPIRED') "
                "AND updated_at < datetime('now', ?)",
                (int(guild_id), f"-{audit_days} days"),
            )
            deleted["operations"] = max(0, int(cur.rowcount))
        await db.commit()
    return deleted

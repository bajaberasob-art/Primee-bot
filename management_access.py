"""Shared per-guild PRIME management-role checks.

These checks only restrict access. Discord's own permission, bot-permission,
and role-hierarchy checks remain authoritative for each underlying action.
"""

from __future__ import annotations

from typing import Any


MANAGEMENT_TIERS = ("admin", "moderator", "staff")
PERMISSION_TIER = {
    "manage_guild": "admin",
    "manage_roles": "admin",
    "manage_channels": "admin",
    "manage_webhooks": "admin",
    "manage_expressions": "admin",
    "ban_members": "moderator",
    "kick_members": "moderator",
    "moderate_members": "moderator",
    "view_audit_log": "moderator",
    "manage_messages": "staff",
    "manage_nicknames": "staff",
    "manage_threads": "staff",
}
TIER_RANK = {
    "member": 0,
    "staff": 1,
    "moderator": 2,
    "admin": 3,
    "administrator": 4,
    "owner": 5,
}


def management_role_ids(settings: dict[str, Any] | None) -> dict[str, str]:
    configured = (settings or {}).get("management_role_ids")
    if not isinstance(configured, dict):
        return {tier: "" for tier in MANAGEMENT_TIERS}
    return {
        tier: str(configured.get(tier, "") or "")
        for tier in MANAGEMENT_TIERS
    }


def management_roles_configured(settings: dict[str, Any] | None) -> bool:
    return any(management_role_ids(settings).values())


def member_management_tier(
    member: Any,
    guild: Any,
    settings: dict[str, Any] | None,
) -> str:
    """Return the highest configured PRIME tier held by a member."""
    try:
        if int(getattr(member, "id", 0)) == int(
            getattr(guild, "owner_id", 0) or 0
        ):
            return "owner"
    except (TypeError, ValueError):
        pass

    permissions = getattr(member, "guild_permissions", None)
    if bool(getattr(permissions, "administrator", False)):
        return "administrator"

    role_ids = {
        str(getattr(role, "id", ""))
        for role in (getattr(member, "roles", ()) or ())
    }
    configured = management_role_ids(settings)
    for tier in MANAGEMENT_TIERS:
        role_id = configured[tier]
        if role_id and role_id in role_ids:
            return tier
    return "member"


def member_has_management_tier(
    member: Any,
    guild: Any,
    settings: dict[str, Any] | None,
    required_tier: str,
) -> bool:
    """Check a mapped role tier, while leaving native Discord checks separate.

    An empty map preserves current command behavior. Once any tier is mapped,
    unconfigured tiers are not implicitly granted to ordinary members.
    """
    required_tier = str(required_tier).strip().lower()
    if required_tier not in TIER_RANK:
        return False
    if not management_roles_configured(settings):
        return True
    actual_tier = member_management_tier(member, guild, settings)
    return TIER_RANK.get(actual_tier, 0) >= TIER_RANK[required_tier]


def required_tier_for_permission(permission: str | None) -> str | None:
    return PERMISSION_TIER.get(str(permission or ""))

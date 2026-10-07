"""Shared authorization policy for manual leveling changes."""

import os
import re


LEVEL_ADMIN_ROLE_NAMES = frozenset(
    {"admin", "owner", "prime", "management", "مشرف"}
)


def configured_level_admin_role_ids() -> set[int]:
    """Read explicitly trusted role IDs without exposing them to clients."""
    raw = (os.getenv("ADMIN_ROLE_IDS") or "").strip()
    return {
        int(value)
        for value in re.split(r"[,\s]+", raw)
        if value.isdigit()
    }


def is_level_admin(member, guild) -> bool:
    """Allow the guild owner, Discord Administrators, and trusted admin roles."""
    try:
        if int(getattr(member, "id", 0)) == int(getattr(guild, "owner_id", 0) or 0):
            return True
    except (TypeError, ValueError):
        pass

    permissions = getattr(member, "guild_permissions", None)
    if permissions is not None and bool(getattr(permissions, "administrator", False)):
        return True

    role_ids = configured_level_admin_role_ids()
    for role in getattr(member, "roles", ()) or ():
        try:
            role_id = int(getattr(role, "id", 0))
        except (TypeError, ValueError):
            role_id = 0
        role_name = str(getattr(role, "name", "") or "").strip().casefold()
        if role_id in role_ids or role_name in LEVEL_ADMIN_ROLE_NAMES:
            return True
    return False
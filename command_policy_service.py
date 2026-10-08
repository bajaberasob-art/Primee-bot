"""Shared command-policy checks for Slash commands and PRIME AI actions."""

from database import get_command_policies


class CommandPolicyDenied(PermissionError):
    """A command is disabled or outside its configured role/channel scope."""


async def ensure_command_policy(
    guild_id: int,
    command_name: str,
    actor,
    channel_id: int | None,
) -> None:
    policy = (await get_command_policies(int(guild_id))).get(
        str(command_name or "").lower()
    )
    if not policy:
        return
    if not policy.get("enabled", True):
        raise CommandPolicyDenied("command_disabled")
    role_ids = {
        int(getattr(role, "id", 0))
        for role in getattr(actor, "roles", ())
        if getattr(role, "id", None) is not None
    }
    allowed_roles = {
        int(role_id) for role_id in policy.get("allowed_roles", ())
    }
    if allowed_roles and not role_ids.intersection(allowed_roles):
        raise CommandPolicyDenied("command_role_restricted")
    allowed_channels = {
        int(item) for item in policy.get("allowed_channels", ())
    }
    if allowed_channels and int(channel_id or 0) not in allowed_channels:
        raise CommandPolicyDenied("command_channel_restricted")

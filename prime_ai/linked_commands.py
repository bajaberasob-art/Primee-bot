"""Bridge AI tools to the existing Slash-command implementations and policies."""

from typing import Any

import discord

from .errors import AccessDenied

_LINKED_COMMANDS = {
    "timeout_member": ("timeout", "Moderation"),
    "kick_member": ("kick", "SanctionsVoiceCog"),
    "ban_member": ("ban", "SanctionsVoiceCog"),
    "unban_member": ("unban", "SanctionsVoiceCog"),
}


async def execute_linked_command(
    bot: Any, guild: Any, actor: Any, channel: Any, step: dict, checked: dict,
) -> str | None:
    """Called only after execution-time target, hierarchy and AI policy checks."""
    tool = step["tool"]
    args = step["arguments"]
    linked = _LINKED_COMMANDS.get(tool)
    if tool == "set_channel_mode":
        linked = (
            "lock" if args.get("mode") == "read_only" else "unlock", "ChatJailCog",
        )
    if linked is None:
        return None
    from command_policy_service import CommandPolicyDenied, ensure_command_policy

    command_name, cog_name = linked
    try:
        await ensure_command_policy(
            guild.id, command_name, actor, getattr(channel, "id", None),
        )
    except CommandPolicyDenied as error:
        raise AccessDenied(str(error)) from error
    get_cog = getattr(bot, "get_cog", None)
    cog = get_cog(cog_name) if callable(get_cog) else None
    if cog is None:
        raise AccessDenied("linked_command_handler_unavailable")
    reason = str(args.get("reason") or "PRIME AI action requested")[:512]
    if tool == "timeout_member":
        target = checked["targets"]["member"]
        expires_at = await cog.execute_timeout_command(
            guild, target, int(args["minutes"]), reason,
        )
        return f"timeout_user={target.id}_until={expires_at.isoformat()}"
    if tool == "kick_member":
        target = checked["targets"]["member"]
        await cog.execute_kick_command(guild, actor, target, reason)
        return f"kicked_user={target.id}"
    if tool == "ban_member":
        target = checked["targets"]["member"]
        await cog.execute_ban_command(
            guild, actor, target, int(args.get("delete_days", 0)), reason,
        )
        return f"banned_user={target.id}"
    if tool == "unban_member":
        target = str(int(args["user_id"]))
        user = await cog._unban_target(guild, target, reason)
        if user is None:
            raise ValueError("ban_not_found")
        try:
            await guild.fetch_ban(discord.Object(id=int(args["user_id"])))
        except discord.NotFound:
            return f"unbanned_user={args['user_id']}"
        raise RuntimeError("discord_unban_not_confirmed")
    if tool == "set_channel_mode":
        target = checked["targets"]["channel"]
        await cog.execute_channel_mode_command(
            target, actor, open_channel=args["mode"] == "open",
        )
        return f"set_channel_mode={args['mode']}_channel_id={target.id}"
    return None

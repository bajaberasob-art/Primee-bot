"""Additive chat management, channel blacklist, and jail execution engine."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections import defaultdict
from typing import Any, Iterable

import discord
from discord import app_commands
from discord.ext import commands

from cogs.sanctions_voice import sanctions_policy_check
from database import (
    add_channel_restriction,
    get_channel_restrictions,
    get_guild_settings,
    get_command_policies,
    get_jailed_user,
    get_logging_channels,
    jail_user,
    remove_channel_restriction,
    unjail_user,
)


logger = logging.getLogger("ChatJailCog")
MESSAGE_ID_RE = re.compile(r"(?:/channels/\d+/\d+/)?(\d{15,22})")
PROTECTED_NAME_PARTS = (
    "log",
    "audit",
    "staff",
    "admin",
    "management",
    "سجل",
    "إدارة",
)


async def chat_policy_check(interaction: discord.Interaction) -> bool:
    """Check command_policies locally in addition to the global interceptor."""
    if interaction.guild is None:
        raise app_commands.NoPrivateMessage()
    command = getattr(interaction, "command", None)
    name = str(getattr(command, "name", "") or "").lower()
    policy = (await get_command_policies(interaction.guild.id)).get(name)
    if not policy:
        return True
    if not policy.get("enabled", True):
        raise app_commands.CheckFailure("command_disabled")
    role_ids = {int(role.id) for role in getattr(interaction.user, "roles", [])}
    allowed_roles = {int(role_id) for role_id in policy.get("allowed_roles", [])}
    if allowed_roles and not role_ids.intersection(allowed_roles):
        raise app_commands.CheckFailure("command_role_restricted")
    allowed_channels = {int(channel_id) for channel_id in policy.get("allowed_channels", [])}
    if allowed_channels and int(interaction.channel_id or 0) not in allowed_channels:
        raise app_commands.CheckFailure("command_channel_restricted")
    return True


class _SafeValues(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class ChatJailCog(commands.Cog):
    """Execution layer for the 27 Step 3 commands not already registered."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._delete_tasks: set[Any] = set()

    def cog_unload(self) -> None:
        for task in tuple(self._delete_tasks):
            task.cancel()
        self._delete_tasks.clear()

    async def _policy(self, guild_id: int, command_name: str) -> dict[str, Any]:
        return (await get_command_policies(int(guild_id))).get(command_name.lower(), {})

    @staticmethod
    def _render(template: str, values: dict[str, Any], fallback: str) -> str:
        if not template:
            return fallback
        try:
            return template.format_map(
                _SafeValues({key: str(value) for key, value in values.items()})
            )[:2000]
        except (KeyError, ValueError, IndexError):
            return fallback

    async def _delete_later(self, message: Any, delay: int) -> None:
        if not message or delay <= 0 or not hasattr(message, "delete"):
            return
        try:
            await message.delete(delay=delay)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to auto-delete chat management response", exc_info=True)

    async def _respond(
        self,
        interaction: discord.Interaction,
        command_name: str,
        title: str,
        description: str,
        *,
        categories: Iterable[str],
        color: int = 0x5865F2,
        fields: Iterable[tuple[str, str, bool]] = (),
        values: dict[str, Any] | None = None,
        ephemeral: bool = False,
    ) -> Any:
        policy = await self._policy(interaction.guild.id, command_name)
        style = str(policy.get("response_style") or "default").lower()
        rendered = self._render(
            str(policy.get("response_template") or ""),
            values or {},
            description,
        )
        field_values = list(fields)
        embed = discord.Embed(
            title=title,
            description=rendered,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        for name, value, inline in field_values:
            embed.add_field(name=str(name)[:256], value=str(value)[:1024], inline=inline)
        if style == "compact":
            payload: dict[str, Any] = {"content": f"**{title}**\n{rendered}"}
        else:
            payload = {"embed": embed}
        if style == "silent":
            ephemeral = True
        payload["ephemeral"] = ephemeral
        message = None
        try:
            if interaction.response.is_done():
                message = await interaction.followup.send(wait=True, **payload)
            else:
                await interaction.response.send_message(**payload)
                with contextlib.suppress(discord.NotFound, discord.HTTPException):
                    message = await interaction.original_response()
        except (discord.NotFound, discord.HTTPException, discord.InteractionResponded):
            logger.debug("Unable to deliver chat management response", exc_info=True)
        delay = int(policy.get("auto_delete_seconds") or 0)
        if message is not None and delay > 0 and not ephemeral:
            task = discord.utils.MISSING
            try:
                import asyncio
                task = asyncio.create_task(self._delete_later(message, delay))
                self._delete_tasks.add(task)
                task.add_done_callback(self._delete_tasks.discard)
            except RuntimeError:
                logger.debug("No event loop available for auto-delete", exc_info=True)
        analytics = self.bot.get_cog("Analytics")
        if analytics is not None:
            for category in dict.fromkeys(categories):
                try:
                    await analytics._log(
                        interaction.guild,
                        category,
                        title,
                        rendered,
                        author=interaction.user,
                        fields=field_values,
                        color=color,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logger.debug("Unable to write %s audit", category, exc_info=True)
        return message

    async def _error(self, interaction: discord.Interaction, command_name: str, text: str):
        return await self._respond(
            interaction,
            command_name,
            "تعذر تنفيذ الإجراء",
            text,
            categories=("log_sanctions",),
            color=0xEF4444,
            ephemeral=True,
        )

    @staticmethod
    def _text_channel(channel: Any) -> bool:
        return isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel))

    @staticmethod
    def _overwrite_channel(channel: Any, role_or_member: Any, **values: Any):
        overwrite = channel.overwrites_for(role_or_member)
        for key, value in values.items():
            setattr(overwrite, key, value)
        return overwrite

    async def _set_everyone(self, channel: Any, reason: str, **values: Any) -> None:
        overwrite = self._overwrite_channel(channel, channel.guild.default_role, **values)
        await channel.set_permissions(
            channel.guild.default_role,
            overwrite=overwrite,
            reason=reason,
        )

    async def _managed_text_channels(self, guild: discord.Guild) -> list[discord.TextChannel]:
        routes = await get_logging_channels(guild.id)
        protected_ids = {int(value) for value in routes.values() if value}
        channels = []
        for channel in guild.text_channels:
            category_name = str(getattr(getattr(channel, "category", None), "name", "")).casefold()
            full_name = f"{channel.name} {category_name}".casefold()
            if channel.id in protected_ids or any(part in full_name for part in PROTECTED_NAME_PARTS):
                continue
            channels.append(channel)
        return channels

    async def _bulk_channel_permissions(
        self,
        guild: discord.Guild,
        *,
        send_messages: bool | None = None,
        view_channel: bool | None = None,
        reason: str,
    ) -> tuple[int, int]:
        success = failed = 0
        for channel in await self._managed_text_channels(guild):
            try:
                values = {}
                if send_messages is not None:
                    values["send_messages"] = send_messages
                    values["send_messages_in_threads"] = send_messages
                if view_channel is not None:
                    values["view_channel"] = view_channel
                await self._set_everyone(channel, reason, **values)
                success += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
        return success, failed

    @staticmethod
    def _message_id(value: str) -> int | None:
        matches = MESSAGE_ID_RE.findall(str(value or ""))
        return int(matches[-1]) if matches else None

    async def _purge_relative(self, channel: Any, message_id: int, *, before: bool) -> int:
        anchor = await channel.fetch_message(message_id)
        total = 0
        for _ in range(100):
            kwargs = {"before": anchor} if before else {"after": anchor}
            deleted = await channel.purge(limit=100, **kwargs)
            total += len(deleted)
            if len(deleted) < 100:
                break
            anchor = deleted[-1] if before else deleted[0]
        return total

    async def _jail_role(self, guild: discord.Guild) -> discord.Role | None:
        settings = await get_guild_settings(guild.id)
        role_id = settings.get("settings", {}).get("quarantine_role_id")
        role = guild.get_role(int(role_id)) if role_id else None
        return role or discord.utils.find(
            lambda item: item.name.casefold() in {"jailed", "quarantine", "سجن"},
            guild.roles,
        )

    @staticmethod
    def _saved_roles(member: discord.Member) -> list[int]:
        return [role.id for role in member.roles if not role.is_default()]

    async def _apply_jail_roles(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str,
        jail_type: str,
        private_channel_id: int = 0,
    ) -> list[int] | None:
        bot_member = interaction.guild.me
        if bot_member is None or member.id == interaction.guild.owner_id:
            return None
        if member.top_role >= bot_member.top_role:
            return None
        saved = self._saved_roles(member)
        manageable = [
            role
            for role in member.roles
            if not role.is_default() and role < bot_member.top_role
        ]
        jail_role = await self._jail_role(interaction.guild)
        if jail_role is None:
            jail_role = await interaction.guild.create_role(name="Jailed", reason=reason)
        if jail_role >= bot_member.top_role:
            return None
        if manageable:
            await member.remove_roles(*manageable, reason=reason)
        await member.add_roles(jail_role, reason=reason)
        await jail_user(
            interaction.guild.id,
            member.id,
            interaction.user.id,
            json.dumps(saved),
            jail_type,
            private_channel_id,
        )
        return saved

    async def _channel_response(
        self,
        interaction: discord.Interaction,
        command_name: str,
        title: str,
        description: str,
        *,
        color: int = 0x10B981,
        fields: Iterable[tuple[str, str, bool]] = (),
    ):
        return await self._respond(
            interaction,
            command_name,
            title,
            description,
            categories=("log_channel",),
            color=color,
            fields=fields,
        )

    @app_commands.command(name="clear_user", description="حذف آخر رسائل عضو محدد")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear_user(self, interaction: discord.Interaction, member: discord.Member, limit: app_commands.Range[int, 1, 100] = 50):
        deleted = await interaction.channel.purge(limit=int(limit), check=lambda message: message.author.id == member.id)
        return await self._respond(interaction, "clear_user", "🧹 مسح رسائل عضو", f"تم حذف **{len(deleted)}** رسالة من {member.mention}.", categories=("log_message",), color=0xEF4444, fields=[("الحد المبدئي", str(limit), True)])

    @app_commands.command(name="cleanup", description="تنظيف القناة حتى 100 رسالة")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def cleanup(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 100] = 100):
        deleted = await interaction.channel.purge(limit=int(limit))
        return await self._respond(interaction, "cleanup", "🧹 تنظيف القناة", f"تم تطهير **{len(deleted)}** رسالة.", categories=("log_message",), color=0xEF4444)

    @app_commands.command(name="nuke", description="استنساخ القناة مع الحفاظ على إعداداتها")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def nuke(self, interaction: discord.Interaction, reason: str = "تطهير القناة"):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await self._error(interaction, "nuke", "هذا الأمر متاح في القنوات النصية فقط.")
        try:
            position = channel.position
            new_channel = await channel.clone(reason=reason)
            await new_channel.edit(position=position, reason=reason)
            await channel.delete(reason=reason)
            await new_channel.send(embed=discord.Embed(title="💥 تم تطهير القناة", description=f"نفذ الإجراء: {interaction.user.mention}", color=0xEF4444))
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "nuke", "تعذر استنساخ القناة أو حذفها.")
        return await self._channel_response(interaction, "nuke", "💥 نيوك القناة", f"تم استنساخ {new_channel.mention} وحذف النسخة القديمة.", fields=[("السبب", reason, False)])

    @app_commands.command(name="lock", description="قفل القناة الحالية")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction):
        try:
            await self._set_everyone(interaction.channel, f"lock by {interaction.user}", send_messages=False, send_messages_in_threads=False)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "lock", "تعذر قفل القناة.")
        return await self._channel_response(interaction, "lock", "🔒 قفل القناة", f"تم قفل {interaction.channel.mention}.", color=0xEF4444)

    @app_commands.command(name="unlock", description="فتح القناة الحالية")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction):
        try:
            await self._set_everyone(interaction.channel, f"unlock by {interaction.user}", send_messages=True, send_messages_in_threads=True)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "unlock", "تعذر فتح القناة.")
        return await self._channel_response(interaction, "unlock", "🔓 فتح القناة", f"تم فتح {interaction.channel.mention}.", color=0x22C55E)

    @app_commands.command(name="lockall", description="قفل القنوات النصية غير الإدارية")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lockall(self, interaction: discord.Interaction):
        success, failed = await self._bulk_channel_permissions(interaction.guild, send_messages=False, reason=f"lockall by {interaction.user}")
        return await self._channel_response(interaction, "lockall", "🔒 قفل الكل", f"تم قفل **{success}** قناة وفشل **{failed}**.", color=0xEF4444)

    @app_commands.command(name="unlockall", description="فتح القنوات النصية المقفلة")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlockall(self, interaction: discord.Interaction):
        success, failed = await self._bulk_channel_permissions(interaction.guild, send_messages=True, reason=f"unlockall by {interaction.user}")
        return await self._channel_response(interaction, "unlockall", "🔓 فتح الكل", f"تم فتح **{success}** قناة وفشل **{failed}**.", color=0x22C55E)

    @app_commands.command(name="hide", description="إخفاء القناة عن الأعضاء")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def hide(self, interaction: discord.Interaction):
        try:
            await self._set_everyone(interaction.channel, f"hide by {interaction.user}", view_channel=False)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "hide", "تعذر إخفاء القناة.")
        return await self._channel_response(interaction, "hide", "🙈 إخفاء القناة", f"تم إخفاء {interaction.channel.mention}.", color=0xF59E0B)

    @app_commands.command(name="show", description="إظهار القناة للأعضاء")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def show(self, interaction: discord.Interaction):
        try:
            await self._set_everyone(interaction.channel, f"show by {interaction.user}", view_channel=True)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "show", "تعذر إظهار القناة.")
        return await self._channel_response(interaction, "show", "👁️ إظهار القناة", f"تم إظهار {interaction.channel.mention}.", color=0x22C55E)

    @app_commands.command(name="hideall", description="إخفاء القنوات النصية عن الأعضاء")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def hideall(self, interaction: discord.Interaction):
        success, failed = await self._bulk_channel_permissions(interaction.guild, view_channel=False, reason=f"hideall by {interaction.user}")
        return await self._channel_response(interaction, "hideall", "🙈 إخفاء الكل", f"تم إخفاء **{success}** قناة وفشل **{failed}**.", color=0xF59E0B)

    @app_commands.command(name="showall", description="إظهار القنوات النصية للأعضاء")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def showall(self, interaction: discord.Interaction):
        success, failed = await self._bulk_channel_permissions(interaction.guild, view_channel=True, reason=f"showall by {interaction.user}")
        return await self._channel_response(interaction, "showall", "👁️ إظهار الكل", f"تم إظهار **{success}** قناة وفشل **{failed}**.", color=0x22C55E)

    @app_commands.command(name="emergency", description="قفل وإخفاء القنوات وتفعيل أعلى تحقق")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def emergency(self, interaction: discord.Interaction, reason: str = "وضع الطوارئ"):
        success, failed = await self._bulk_channel_permissions(
            interaction.guild,
            send_messages=False,
            view_channel=False,
            reason=f"emergency by {interaction.user}",
        )
        try:
            await interaction.guild.edit(verification_level=discord.VerificationLevel.highest, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
        return await self._channel_response(interaction, "emergency", "🚨 وضع الطوارئ", f"تم تأمين **{success}** قناة، وفشل **{failed}** إجراء.", color=0xDC2626, fields=[("السبب", reason, False)])

    @app_commands.command(name="thread_lock", description="قفل وأرشفة الثريد الحالي")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_threads=True)
    async def thread_lock(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            return await self._error(interaction, "thread_lock", "هذا الأمر متاح داخل Thread فقط.")
        try:
            await interaction.channel.edit(locked=True, archived=True, reason=f"thread_lock by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "thread_lock", "تعذر قفل الثريد.")
        return await self._channel_response(interaction, "thread_lock", "🔒 قفل Thread", "تم قفل وأرشفة الثريد.", color=0xEF4444)

    @app_commands.command(name="thread_unlock", description="فتح وإلغاء أرشفة الثريد الحالي")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_threads=True)
    async def thread_unlock(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            return await self._error(interaction, "thread_unlock", "هذا الأمر متاح داخل Thread فقط.")
        try:
            await interaction.channel.edit(locked=False, archived=False, reason=f"thread_unlock by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "thread_unlock", "تعذر فتح الثريد.")
        return await self._channel_response(interaction, "thread_unlock", "🔓 فتح Thread", "تم فتح الثريد.", color=0x22C55E)

    @app_commands.command(name="clean_commands", description="حذف رسائل الأوامر والبوتات")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clean_commands(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 100] = 100):
        prefixes = ("!", "/", "-", ".")
        deleted = await interaction.channel.purge(
            limit=int(limit),
            check=lambda message: message.content.lstrip().startswith(prefixes) or message.author.bot,
        )
        return await self._respond(interaction, "clean_commands", "🧹 تنظيف الأوامر", f"تم حذف **{len(deleted)}** رسالة.", categories=("log_message",), color=0xEF4444)

    @app_commands.command(name="clean_bots", description="حذف رسائل حسابات البوتات")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clean_bots(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 100] = 100):
        deleted = await interaction.channel.purge(limit=int(limit), check=lambda message: message.author.bot)
        return await self._respond(interaction, "clean_bots", "🤖 حذف رسائل البوتات", f"تم حذف **{len(deleted)}** رسالة بوت.", categories=("log_message",), color=0xEF4444)

    @app_commands.command(name="delete_after", description="حذف كل الرسائل بعد رسالة محددة")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def delete_after(self, interaction: discord.Interaction, message: str):
        message_id = self._message_id(message)
        if message_id is None:
            return await self._error(interaction, "delete_after", "أرسل رابط الرسالة أو Message ID صالحاً.")
        try:
            count = await self._purge_relative(interaction.channel, message_id, before=False)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "delete_after", "تعذر العثور على الرسالة أو حذف ما بعدها.")
        return await self._respond(interaction, "delete_after", "🗑️ حذف ما بعد الرسالة", f"تم حذف **{count}** رسالة.", categories=("log_message",), color=0xEF4444)

    @app_commands.command(name="delete_before", description="حذف كل الرسائل قبل رسالة محددة")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def delete_before(self, interaction: discord.Interaction, message: str):
        message_id = self._message_id(message)
        if message_id is None:
            return await self._error(interaction, "delete_before", "أرسل رابط الرسالة أو Message ID صالحاً.")
        try:
            count = await self._purge_relative(interaction.channel, message_id, before=True)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "delete_before", "تعذر العثور على الرسالة أو حذف ما قبلها.")
        return await self._respond(interaction, "delete_before", "🗑️ حذف ما قبل الرسالة", f"تم حذف **{count}** رسالة.", categories=("log_message",), color=0xEF4444)

    async def _restriction_response(
        self,
        interaction: discord.Interaction,
        command_name: str,
        title: str,
        description: str,
        *,
        member: discord.Member,
        color: int,
    ):
        return await self._respond(
            interaction,
            command_name,
            title,
            description,
            categories=("log_sanctions", "log_automod"),
            color=color,
            fields=[("👤 العضو", f"{member.mention} (`{member.id}`)", True), ("📍 القناة", interaction.channel.mention, True)],
        )

    @app_commands.command(name="block_write", description="منع عضو من الكتابة في القناة")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def block_write(self, interaction: discord.Interaction, member: discord.Member):
        try:
            overwrite = self._overwrite_channel(interaction.channel, member, send_messages=False, send_messages_in_threads=False)
            await interaction.channel.set_permissions(member, overwrite=overwrite, reason=f"block_write by {interaction.user}")
            await add_channel_restriction(interaction.guild.id, interaction.channel.id, member.id, "write_block")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "block_write", "تعذر منع العضو من الكتابة.")
        return await self._restriction_response(interaction, "block_write", "✋ حظر كتابة", f"تم منع {member.mention} من الكتابة.", member=member, color=0xF97316)

    @app_commands.command(name="unblock_write", description="رفع حظر الكتابة عن عضو")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unblock_write(self, interaction: discord.Interaction, member: discord.Member):
        try:
            await interaction.channel.set_permissions(member, overwrite=None, reason=f"unblock_write by {interaction.user}")
            await remove_channel_restriction(interaction.guild.id, interaction.channel.id, member.id, "write_block")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "unblock_write", "تعذر رفع حظر الكتابة.")
        return await self._restriction_response(interaction, "unblock_write", "✅ رفع حظر الكتابة", f"عاد {member.mention} للوضع الطبيعي.", member=member, color=0x22C55E)

    @app_commands.command(name="hide_member", description="إخفاء القناة عن عضو محدد")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def hide_member(self, interaction: discord.Interaction, member: discord.Member):
        try:
            overwrite = self._overwrite_channel(interaction.channel, member, view_channel=False)
            await interaction.channel.set_permissions(member, overwrite=overwrite, reason=f"hide_member by {interaction.user}")
            await add_channel_restriction(interaction.guild.id, interaction.channel.id, member.id, "hide_member")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "hide_member", "تعذر إخفاء القناة عن العضو.")
        return await self._restriction_response(interaction, "hide_member", "🙈 إخفاء عضو", f"تم إخفاء القناة عن {member.mention}.", member=member, color=0xF97316)

    @app_commands.command(name="show_member", description="إظهار القناة لعضو محدد")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def show_member(self, interaction: discord.Interaction, member: discord.Member):
        try:
            await interaction.channel.set_permissions(member, overwrite=None, reason=f"show_member by {interaction.user}")
            await remove_channel_restriction(interaction.guild.id, interaction.channel.id, member.id, "hide_member")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "show_member", "تعذر إظهار القناة للعضو.")
        return await self._restriction_response(interaction, "show_member", "👁️ إظهار عضو", f"تم إظهار القناة لـ {member.mention}.", member=member, color=0x22C55E)

    @app_commands.command(name="open_chat_member", description="منح عضو صلاحية الرؤية والكتابة")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def open_chat_member(self, interaction: discord.Interaction, member: discord.Member):
        try:
            overwrite = self._overwrite_channel(
                interaction.channel,
                member,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
            await interaction.channel.set_permissions(member, overwrite=overwrite, reason=f"open_chat_member by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "open_chat_member", "تعذر فتح الشات للعضو.")
        return await self._restriction_response(interaction, "open_chat_member", "🔓 فتح شات لعضو", f"تم منح {member.mention} صلاحية الرؤية والكتابة.", member=member, color=0x22C55E)

    @app_commands.command(name="remove_chat_member", description="إزالة صلاحيات العضو الاستثنائية")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def remove_chat_member(self, interaction: discord.Interaction, member: discord.Member):
        try:
            await interaction.channel.set_permissions(member, overwrite=None, reason=f"remove_chat_member by {interaction.user}")
            await remove_channel_restriction(interaction.guild.id, interaction.channel.id, member.id, "write_block")
            await remove_channel_restriction(interaction.guild.id, interaction.channel.id, member.id, "hide_member")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "remove_chat_member", "تعذر إزالة صلاحيات العضو.")
        return await self._restriction_response(interaction, "remove_chat_member", "↩️ إزالة عضو من الشات", f"تمت إعادة مزامنة صلاحيات {member.mention}.", member=member, color=0x22C55E)

    async def _jail_result(
        self,
        interaction: discord.Interaction,
        command_name: str,
        member: discord.Member,
        saved: list[int] | None,
        title: str,
        description: str,
    ):
        if saved is None:
            return await self._error(interaction, command_name, "لا يمكن سجن هذا العضو بسبب ترتيب الرتب أو ملكية السيرفر.")
        return await self._respond(
            interaction,
            command_name,
            title,
            description,
            categories=("log_sanctions", "log_automod"),
            color=0x7C3AED,
            fields=[("👤 العضو", f"{member.mention} (`{member.id}`)", True), ("🎭 الرتب المحفوظة", str(len(saved)), True)],
        )

    @app_commands.command(name="jail", description="سحب رتب العضو ووضعه في السجن العام")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def jail(self, interaction: discord.Interaction, member: discord.Member, reason: str = "سجن عام"):
        existing = await get_jailed_user(interaction.guild.id, member.id)
        if existing:
            return await self._error(interaction, "jail", "العضو مسجون بالفعل.")
        try:
            saved = await self._apply_jail_roles(interaction, member, reason, "general")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "jail", "تعذر تطبيق رتبة السجن أو سحب الرتب.")
        return await self._jail_result(interaction, "jail", member, saved, "🔒 سجن عام", f"تم نقل {member.mention} إلى السجن العام.",)

    @app_commands.command(name="solo_jail", description="إنشاء سجن خاص ومعزول لعضو")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def solo_jail(self, interaction: discord.Interaction, member: discord.Member, reason: str = "سجن فردي"):
        existing = await get_jailed_user(interaction.guild.id, member.id)
        if existing:
            return await self._error(interaction, "solo_jail", "العضو مسجون بالفعل.")
        try:
            saved = self._saved_roles(member)
            private_channel = await interaction.guild.create_text_channel(
                f"🔒-سجن-{member.name}"[:100],
                overwrites={
                    interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                    interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                },
                reason=reason,
            )
            applied = await self._apply_jail_roles(interaction, member, reason, "solo", private_channel.id)
            if applied is None:
                await private_channel.delete(reason="تعذر تطبيق السجن الفردي")
                return await self._error(interaction, "solo_jail", "لا يمكن سجن هذا العضو بسبب ترتيب الرتب.")
            await private_channel.send(embed=discord.Embed(title="🔒 سجن فردي", description=f"{member.mention}\nالسبب: {reason}\nالتزم بتعليمات المشرفين.", color=0x7C3AED))
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "solo_jail", "تعذر إنشاء القناة الخاصة أو تطبيق السجن.")
        return await self._jail_result(interaction, "solo_jail", member, saved, "🔒 سجن فردي", f"تم إنشاء {private_channel.mention} للعضو {member.mention}.")

    @app_commands.command(name="unjail", description="إعادة رتب العضو وحذف سجنه")
    @app_commands.check(chat_policy_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def unjail(self, interaction: discord.Interaction, member: discord.Member):
        record = await get_jailed_user(interaction.guild.id, member.id)
        if record is None:
            return await self._error(interaction, "unjail", "لا يوجد سجل سجن لهذا العضو.")
        try:
            jail_role = await self._jail_role(interaction.guild)
            if jail_role and jail_role in member.roles:
                await member.remove_roles(jail_role, reason=f"unjail by {interaction.user}")
            saved_ids = json.loads(record.get("saved_roles") or "[]")
            roles = [
                interaction.guild.get_role(int(role_id))
                for role_id in saved_ids
            ]
            roles = [role for role in roles if role is not None and role < interaction.guild.me.top_role]
            if roles:
                await member.add_roles(*roles, reason=f"unjail by {interaction.user}")
            private_channel_id = int(record.get("private_channel_id") or 0)
            if private_channel_id:
                private_channel = interaction.guild.get_channel(private_channel_id)
                if private_channel:
                    await private_channel.delete(reason="انتهاء السجن الفردي")
            await unjail_user(interaction.guild.id, member.id)
        except (ValueError, TypeError, discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to restore jailed member %s", member.id)
            return await self._error(interaction, "unjail", "تعذر استرجاع كل الرتب أو القناة؛ بقي سجل السجن محفوظاً للمحاولة مرة أخرى.")
        return await self._respond(interaction, "unjail", "🔓 فك السجن", f"تم فك السجن عن {member.mention} واسترجاع الرتب المحفوظة.", categories=("log_sanctions", "log_automod"), color=0x22C55E)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChatJailCog(bot))
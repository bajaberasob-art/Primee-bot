"""Additive sanctions and voice-control command engine.

This cog owns only the new Step 2 commands and durable timed-penalty state.
Existing moderation commands remain registered in their original cog; in
particular, timeout and untimeout are intentionally not duplicated here.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import re
from collections import defaultdict
from typing import Any, Iterable

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import (
    add_temp_ban,
    add_text_mute,
    add_voice_ban,
    get_command_policies,
    get_expired_temp_bans,
    get_text_mutes,
    is_voice_banned,
    remove_temp_ban,
    remove_text_mute,
    remove_voice_ban,
)


logger = logging.getLogger("SanctionsVoiceCog")
TARGET_RE = re.compile(r"<@!?(\d+)>|(?<!\d)(\d{1,20})(?!\d)")
DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
MAX_TIMEOUT = dt.timedelta(days=28)
RATE_LIMIT_DELAY = 0.65


async def sanctions_policy_check(interaction: discord.Interaction) -> bool:
    """Read the canonical policy before a Step 2 command executes.

    Utilities also applies the global tree policy check. This local check keeps
    the cog safe when invoked directly by tests or another command adapter.
    """
    if interaction.guild is None:
        raise app_commands.NoPrivateMessage()
    command = getattr(interaction, "command", None)
    name = str(getattr(command, "name", "") or "").lower()
    if not name:
        return True
    policy = (await get_command_policies(interaction.guild.id)).get(name)
    if not policy:
        return True
    if not policy.get("enabled", True):
        raise app_commands.CheckFailure("command_disabled")
    roles = {int(role.id) for role in getattr(interaction.user, "roles", [])}
    allowed_roles = {int(role_id) for role_id in policy.get("allowed_roles", [])}
    if allowed_roles and not roles.intersection(allowed_roles):
        raise app_commands.CheckFailure("command_role_restricted")
    allowed_channels = {int(channel_id) for channel_id in policy.get("allowed_channels", [])}
    if allowed_channels and int(interaction.channel_id or 0) not in allowed_channels:
        raise app_commands.CheckFailure("command_channel_restricted")
    return True


def parse_duration(value: str) -> dt.timedelta | None:
    match = DURATION_RE.match(str(value or ""))
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return dt.timedelta(seconds=seconds) if seconds > 0 else None


def target_ids(value: str) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for match in TARGET_RE.finditer(str(value or "")):
        user_id = int(match.group(1) or match.group(2))
        if user_id not in seen:
            seen.add(user_id)
            result.append(user_id)
    return result


class _TemplateValues(defaultdict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class SanctionsVoiceCog(commands.Cog):
    """Execution engine for sanctions and voice operations."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._delete_tasks: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        if not self.temp_ban_watcher.is_running():
            self.temp_ban_watcher.start()

    def cog_unload(self) -> None:
        self.temp_ban_watcher.cancel()
        for task in tuple(self._delete_tasks):
            task.cancel()
        self._delete_tasks.clear()

    async def _policy(self, guild_id: int, command_name: str) -> dict[str, Any]:
        policies = await get_command_policies(int(guild_id))
        return policies.get(str(command_name).lower(), {})

    @staticmethod
    def _format_template(template: str, values: dict[str, Any], fallback: str) -> str:
        if not template:
            return fallback
        safe = _TemplateValues({key: str(value) for key, value in values.items()})
        try:
            return template.format_map(safe)[:2000]
        except (KeyError, ValueError, IndexError):
            return fallback

    async def _schedule_delete(self, message: Any, delay: int) -> None:
        if not message or delay <= 0 or not hasattr(message, "delete"):
            return
        try:
            await message.delete(delay=delay)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to auto-delete sanctions response", exc_info=True)

    async def _send(
        self,
        interaction: discord.Interaction,
        command_name: str,
        title: str,
        description: str,
        *,
        category: str,
        color: int,
        fields: Iterable[tuple[str, str, bool]] = (),
        values: dict[str, Any] | None = None,
        ephemeral: bool = False,
        log_title: str | None = None,
        log_description: str | None = None,
    ) -> Any:
        guild = interaction.guild
        policy = await self._policy(guild.id, command_name)
        style = str(policy.get("response_style") or "default").lower()
        rendered = self._format_template(
            str(policy.get("response_template") or ""),
            values or {},
            description,
        )
        embed = discord.Embed(
            title=title,
            description=rendered,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        field_values = list(fields)
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
                with contextlib.suppress(discord.HTTPException, discord.NotFound):
                    message = await interaction.original_response()
        except (discord.HTTPException, discord.NotFound, discord.InteractionResponded):
            logger.debug("Unable to send sanctions response", exc_info=True)
        delay = int(policy.get("auto_delete_seconds") or 0)
        if message is not None and delay > 0 and not ephemeral:
            task = asyncio.create_task(self._schedule_delete(message, delay))
            self._delete_tasks.add(task)
            task.add_done_callback(self._delete_tasks.discard)
        analytics = self.bot.get_cog("Analytics")
        if analytics is not None and guild is not None:
            try:
                await analytics._log(
                    guild,
                    category,
                    log_title or title,
                    log_description or rendered,
                    author=interaction.user,
                    fields=field_values,
                    color=color,
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.debug("Unable to write %s sanctions audit", category, exc_info=True)
        return message

    async def _error(self, interaction: discord.Interaction, command_name: str, text: str) -> Any:
        return await self._send(
            interaction,
            command_name,
            "تعذر تنفيذ الإجراء",
            text,
            category="log_sanctions",
            color=0xEF4444,
            ephemeral=True,
        )

    @staticmethod
    def _hierarchy_ok(interaction: discord.Interaction, member: discord.Member) -> bool:
        actor = interaction.user
        return (
            member.id != interaction.guild.owner_id
            and member.id != actor.id
            and (
                interaction.guild.owner_id == actor.id
                or member.top_role < actor.top_role
            )
        )

    @staticmethod
    def _current_voice(interaction: discord.Interaction) -> discord.VoiceChannel | None:
        voice = getattr(interaction.user, "voice", None)
        channel = getattr(voice, "channel", None)
        return channel if isinstance(channel, discord.VoiceChannel) else None

    async def _muted_role(self, guild: discord.Guild, reason: str) -> discord.Role:
        role = discord.utils.get(guild.roles, name="Muted")
        if role is None:
            role = await guild.create_role(name="Muted", reason=reason)
        for channel in guild.text_channels:
            try:
                overwrite = channel.overwrites_for(role)
                overwrite.send_messages = False
                overwrite.add_reactions = False
                await channel.set_permissions(role, overwrite=overwrite, reason=reason)
            except (discord.Forbidden, discord.HTTPException):
                logger.debug("Unable to apply Muted overwrite in %s", channel.id, exc_info=True)
        return role

    async def _unban_target(self, guild: discord.Guild, target: str, reason: str) -> discord.User | None:
        ids = target_ids(target)
        if ids:
            user = discord.Object(id=ids[0])
            try:
                await guild.unban(user, reason=reason)
                return user  # type: ignore[return-value]
            except discord.NotFound:
                return None
        query = str(target).strip().casefold()
        async for entry in guild.bans(limit=None):
            user = entry.user
            if query in {
                str(user.id).casefold(),
                str(user).casefold(),
                str(getattr(user, "name", "")).casefold(),
            }:
                await guild.unban(user, reason=reason)
                return user
        return None

    async def _bulk_ids(self, interaction: discord.Interaction, raw: str) -> list[int]:
        ids = target_ids(raw)
        return [user_id for user_id in ids if user_id != interaction.guild.me.id]

    async def _bulk_unban(self, interaction: discord.Interaction, command_name: str) -> tuple[int, int]:
        success = failed = 0
        async for entry in interaction.guild.bans(limit=None):
            try:
                await interaction.guild.unban(entry.user, reason=f"{command_name} by {interaction.user}")
                success += 1
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                failed += 1
            await asyncio.sleep(RATE_LIMIT_DELAY)
        return success, failed

    async def _bulk_voice_move(
        self,
        members: Iterable[discord.Member],
        channel: discord.VoiceChannel | None,
        reason: str,
    ) -> tuple[int, int]:
        success = failed = 0
        for member in list(members):
            try:
                await member.move_to(channel, reason=reason)
                success += 1
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                failed += 1
        return success, failed

    @tasks.loop(minutes=1)
    async def temp_ban_watcher(self) -> None:
        for record in await get_expired_temp_bans():
            guild = self.bot.get_guild(int(record["guild_id"]))
            if guild is None:
                continue
            user = discord.Object(id=int(record["user_id"]))
            try:
                await guild.unban(user, reason="انتهاء الحظر المؤقت")
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("Failed to expire temp ban for %s/%s", guild.id, user.id, exc_info=True)
                continue
            await remove_temp_ban(guild.id, user.id)
            analytics = self.bot.get_cog("Analytics")
            if analytics:
                await analytics._log(
                    guild,
                    "log_sanctions",
                    "⌛ انتهاء حظر مؤقت",
                    "تم فك الحظر تلقائياً بعد انتهاء المدة.",
                    fields=[("👤 العضو", f"<@{user.id}> (`{user.id}`)", True)],
                    color=0x22C55E,
                )

    @temp_ban_watcher.before_loop
    async def before_temp_ban_watcher(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if after.channel is None or before.channel == after.channel or member.bot:
            return
        if not await is_voice_banned(member.guild.id, member.id):
            return
        try:
            await member.move_to(None, reason="حظر صوتي")
            try:
                await member.send("تم منعك من دخول القنوات الصوتية في هذا السيرفر.")
            except (discord.Forbidden, discord.HTTPException):
                pass
            analytics = self.bot.get_cog("Analytics")
            if analytics:
                await analytics._log(
                    member.guild,
                    "log_voice",
                    "🚫 منع دخول صوتي",
                    "تم فصل عضو محظور صوتياً عند محاولته الانضمام.",
                    author=member,
                    fields=[("👤 العضو", f"{member.mention} (`{member.id}`)", True)],
                    color=0xEF4444,
                )
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to enforce voice ban for %s", member.id, exc_info=True)

    @app_commands.command(name="kick", description="طرد عضو من السيرفر")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.describe(member="العضو", reason="سبب الطرد")
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "غير محدد"):
        if not self._hierarchy_ok(interaction, member):
            return await self._error(interaction, "kick", "لا يمكن تنفيذ الإجراء على هذا العضو.")
        try:
            await member.kick(reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "kick", "فشل الطرد؛ تحقق من ترتيب الرتب وصلاحيات البوت.")
        return await self._send(interaction, "kick", "👢 طرد عضو", f"تم طرد {member.mention}.", category="log_sanctions", color=0xEF4444, fields=[("السبب", reason, False)], values={"member": member.mention, "reason": reason})

    @app_commands.command(name="ban", description="حظر عضو مع تحديد أيام حذف الرسائل")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.describe(member="العضو", delete_days="أيام حذف الرسائل من 0 إلى 7", reason="سبب الحظر")
    async def ban(self, interaction: discord.Interaction, member: discord.Member, delete_days: app_commands.Range[int, 0, 7] = 0, reason: str = "غير محدد"):
        if not self._hierarchy_ok(interaction, member):
            return await self._error(interaction, "ban", "لا يمكن حظر هذا العضو بسبب ترتيب الرتب.")
        try:
            await member.ban(delete_message_days=int(delete_days), reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "ban", "فشل الحظر؛ تحقق من الصلاحيات.")
        return await self._send(interaction, "ban", "🔨 حظر عضو", f"تم حظر {member.mention}.", category="log_sanctions", color=0xB91C1C, fields=[("حذف الرسائل", f"{delete_days} يوم", True), ("السبب", reason, False)], values={"member": member.mention, "reason": reason})

    @app_commands.command(name="unban", description="فك حظر عضو بالمعرف أو الاسم")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.describe(target="معرف العضو أو اسمه", reason="سبب فك الحظر")
    async def unban(self, interaction: discord.Interaction, target: str, reason: str = "غير محدد"):
        try:
            user = await self._unban_target(interaction.guild, target, reason)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "unban", "تعذر فك الحظر؛ تحقق من الصلاحيات.")
        if user is None:
            return await self._error(interaction, "unban", "لم أعثر على هذا العضو ضمن قائمة الحظر.")
        return await self._send(interaction, "unban", "🔓 فك الحظر", f"تم فك الحظر عن <@{user.id}>.", category="log_sanctions", color=0x22C55E, fields=[("السبب", reason, False)])

    @app_commands.command(name="tempban", description="حظر عضو لمدة محددة")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.describe(member="العضو", duration="مثال: 10m أو 1h أو 7d", reason="سبب الحظر")
    async def tempban(self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "غير محدد"):
        delta = parse_duration(duration)
        if delta is None or delta > dt.timedelta(days=28):
            return await self._error(interaction, "tempban", "المدة يجب أن تكون بين ثانية و28 يوماً بصيغة 10m أو 1h أو 7d.")
        if not self._hierarchy_ok(interaction, member):
            return await self._error(interaction, "tempban", "لا يمكن حظر هذا العضو بسبب ترتيب الرتب.")
        expires = discord.utils.utcnow() + delta
        try:
            await member.ban(reason=reason)
            await add_temp_ban(interaction.guild.id, member.id, expires)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "tempban", "فشل الحظر المؤقت.")
        return await self._send(interaction, "tempban", "⏳ حظر مؤقت", f"تم حظر {member.mention} حتى <t:{int(expires.timestamp())}:F>.", category="log_sanctions", color=0xF59E0B, fields=[("المدة", duration, True), ("السبب", reason, False)], values={"member": member.mention, "duration": duration, "reason": reason})

    @app_commands.command(name="softban", description="حظر عضو ثم فك الحظر لمسح رسائله")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.describe(member="العضو", reason="سبب السوفت بان")
    async def softban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "تنظيف الرسائل"):
        if not self._hierarchy_ok(interaction, member):
            return await self._error(interaction, "softban", "لا يمكن تنفيذ الإجراء على هذا العضو.")
        try:
            await member.ban(delete_message_days=1, reason=reason)
            await interaction.guild.unban(discord.Object(id=member.id), reason="Softban release")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "softban", "فشل تنفيذ السوفت بان.")
        return await self._send(interaction, "softban", "🧹 سوفت بان", f"تم تنظيف رسائل {member.mention} والسماح له بالعودة.", category="log_sanctions", color=0xF97316, fields=[("السبب", reason, False)])

    @app_commands.command(name="massban", description="حظر عدة أعضاء بالمنشن أو المعرف")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(ban_members=True)
    async def massban(self, interaction: discord.Interaction, members: str, reason: str = "حظر جماعي"):
        ids = await self._bulk_ids(interaction, members)
        if not ids:
            return await self._error(interaction, "massban", "أرسل منشنات أو معرفات أعضاء.")
        success = failed = 0
        for user_id in ids[:25]:
            try:
                await interaction.guild.ban(discord.Object(id=user_id), reason=reason)
                success += 1
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                failed += 1
            await asyncio.sleep(0.35)
        return await self._send(interaction, "massban", "🔨 حظر جماعي", f"اكتمل الحظر الجماعي: **{success}** ناجح، **{failed}** فشل.", category="log_sanctions", color=0xB91C1C, fields=[("السبب", reason, False)])

    @app_commands.command(name="masskick", description="طرد عدة أعضاء دفعة واحدة")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(kick_members=True)
    async def masskick(self, interaction: discord.Interaction, members: str, reason: str = "طرد جماعي"):
        ids = await self._bulk_ids(interaction, members)
        if not ids:
            return await self._error(interaction, "masskick", "أرسل منشنات أو معرفات أعضاء.")
        success = failed = 0
        for user_id in ids[:25]:
            member = interaction.guild.get_member(user_id)
            try:
                if member is None or not self._hierarchy_ok(interaction, member):
                    raise PermissionError("member hierarchy")
                await member.kick(reason=reason)
                success += 1
            except (PermissionError, discord.Forbidden, discord.HTTPException, discord.NotFound):
                failed += 1
            await asyncio.sleep(0.35)
        return await self._send(interaction, "masskick", "👢 طرد جماعي", f"اكتمل الطرد الجماعي: **{success}** ناجح، **{failed}** فشل.", category="log_sanctions", color=0xEF4444, fields=[("السبب", reason, False)])

    @app_commands.command(name="massmute", description="تطبيق تايم أوت على عدة أعضاء")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def massmute(self, interaction: discord.Interaction, members: str, duration: str, reason: str = "إسكات جماعي"):
        delta = parse_duration(duration)
        if delta is None or delta > MAX_TIMEOUT:
            return await self._error(interaction, "massmute", "المدة يجب أن تكون بين ثانية و28 يوماً.")
        ids = await self._bulk_ids(interaction, members)
        if not ids:
            return await self._error(interaction, "massmute", "أرسل منشنات أو معرفات أعضاء.")
        success = failed = 0
        until = discord.utils.utcnow() + delta
        for user_id in ids[:25]:
            member = interaction.guild.get_member(user_id)
            try:
                if member is None or not self._hierarchy_ok(interaction, member):
                    raise PermissionError("member hierarchy")
                await member.timeout(until, reason=reason)
                success += 1
            except (PermissionError, discord.Forbidden, discord.HTTPException, discord.NotFound):
                failed += 1
        return await self._send(interaction, "massmute", "🔇 إسكات جماعي", f"اكتمل الإسكات: **{success}** ناجح، **{failed}** فشل.", category="log_sanctions", color=0xF59E0B, fields=[("المدة", duration, True), ("السبب", reason, False)])

    @app_commands.command(name="unbanall", description="فك حظر جميع الأعضاء مع تأخير آمن")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(ban_members=True)
    async def unbanall(self, interaction: discord.Interaction):
        success, failed = await self._bulk_unban(interaction, "unbanall")
        return await self._send(interaction, "unbanall", "🔓 فك حظر الكل", f"تم فك **{success}** حظراً، وفشل **{failed}**.", category="log_sanctions", color=0x22C55E)

    @app_commands.command(name="clearbans", description="مسح قائمة الحظر بالكامل")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def clearbans(self, interaction: discord.Interaction):
        success, failed = await self._bulk_unban(interaction, "clearbans")
        return await self._send(interaction, "clearbans", "🧹 مسح الحظر", f"تم مسح **{success}** حظراً بواسطة {interaction.user.mention}، وفشل **{failed}**.", category="log_sanctions", color=0xDC2626)

    @app_commands.command(name="mute", description="تطبيق ميوت كتابي عبر رتبة Muted")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member, reason: str = "ميوت كتابي"):
        if not self._hierarchy_ok(interaction, member):
            return await self._error(interaction, "mute", "لا يمكن تطبيق الميوت على هذا العضو.")
        try:
            role = await self._muted_role(interaction.guild, reason)
            await member.add_roles(role, reason=reason)
            await add_text_mute(interaction.guild.id, member.id, interaction.user.id)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "mute", "تعذر تطبيق رتبة Muted.")
        return await self._send(interaction, "mute", "🔇 ميوت كتابي", f"تم منع {member.mention} من الكتابة.", category="log_sanctions", color=0xF59E0B, fields=[("السبب", reason, False)])

    @app_commands.command(name="unmute", description="فك الميوت الكتابي وإزالة رتبة Muted")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        role = discord.utils.get(interaction.guild.roles, name="Muted")
        try:
            if role and role in member.roles:
                await member.remove_roles(role, reason="فك الميوت الكتابي")
            await remove_text_mute(interaction.guild.id, member.id)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "unmute", "تعذر فك الميوت.")
        return await self._send(interaction, "unmute", "🔊 فك الميوت", f"تم فك الميوت عن {member.mention}.", category="log_sanctions", color=0x22C55E)

    @app_commands.command(name="mutedlist", description="عرض أعضاء التايم أوت والميوت الكتابي")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mutedlist(self, interaction: discord.Interaction):
        records = await get_text_mutes(interaction.guild.id)
        ids = {int(record["user_id"]) for record in records}
        now = discord.utils.utcnow()
        for member in interaction.guild.members:
            until = getattr(member, "communication_disabled_until", None)
            if until and until > now:
                ids.add(member.id)
        mentions = [f"<@{user_id}>" for user_id in sorted(ids)]
        description = "\n".join(mentions[:100]) if mentions else "لا يوجد أعضاء خاضعون للتايم أوت أو الميوت."
        return await self._send(interaction, "mutedlist", "📋 قائمة المسكوتين", description, category="log_sanctions", color=0x64748B)

    async def _voice_action(self, interaction: discord.Interaction, command_name: str, member: discord.Member, action: str, *, value: bool | None = None, reason: str = "إجراء صوتي"):
        if not self._hierarchy_ok(interaction, member):
            return await self._error(interaction, command_name, "لا يمكن تنفيذ الإجراء على هذا العضو.")
        try:
            if action == "move":
                await member.move_to(value, reason=reason)
            else:
                await member.edit(**{action: value}, reason=reason)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return await self._error(interaction, command_name, "فشل الإجراء الصوتي؛ تحقق من وجود العضو والصلاحيات.")
        return await self._send(interaction, command_name, "🎙️ إجراء صوتي", f"تم تنفيذ `{command_name}` على {member.mention}.", category="log_voice", color=0x06B6D4, fields=[("السبب", reason, False)])

    @app_commands.command(name="vmute", description="كتم ميكروفون عضو في القناة الصوتية")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(mute_members=True)
    async def vmute(self, interaction: discord.Interaction, member: discord.Member, reason: str = "كتم صوتي"):
        return await self._voice_action(interaction, "vmute", member, "mute", value=True, reason=reason)

    @app_commands.command(name="vunmute", description="فك كتم ميكروفون عضو")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(mute_members=True)
    async def vunmute(self, interaction: discord.Interaction, member: discord.Member):
        return await self._voice_action(interaction, "vunmute", member, "mute", value=False)

    @app_commands.command(name="vkick", description="فصل عضو من القناة الصوتية")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(move_members=True)
    async def vkick(self, interaction: discord.Interaction, member: discord.Member):
        return await self._voice_action(interaction, "vkick", member, "move", value=None)

    @app_commands.command(name="deafen", description="منع عضو من سماع الصوت")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(deafen_members=True)
    async def deafen(self, interaction: discord.Interaction, member: discord.Member):
        return await self._voice_action(interaction, "deafen", member, "deaf", value=True)

    @app_commands.command(name="undeafen", description="فك الصمم عن عضو")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(deafen_members=True)
    async def undeafen(self, interaction: discord.Interaction, member: discord.Member):
        return await self._voice_action(interaction, "undeafen", member, "deaf", value=False)

    @app_commands.command(name="vban", description="حظر عضو من دخول القنوات الصوتية")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(move_members=True)
    async def vban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "حظر صوتي"):
        if not self._hierarchy_ok(interaction, member):
            return await self._error(interaction, "vban", "لا يمكن حظر هذا العضو صوتياً.")
        await add_voice_ban(interaction.guild.id, member.id, interaction.user.id)
        try:
            await member.move_to(None, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Voice ban recorded but member could not be disconnected", exc_info=True)
        return await self._send(interaction, "vban", "🚫 حظر صوتي", f"تم حظر {member.mention} من القنوات الصوتية.", category="log_voice", color=0xDC2626, fields=[("السبب", reason, False)])

    @app_commands.command(name="vunban", description="فك الحظر الصوتي عن عضو")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(move_members=True)
    async def vunban(self, interaction: discord.Interaction, member: discord.Member):
        removed = await remove_voice_ban(interaction.guild.id, member.id)
        return await self._send(interaction, "vunban", "🔊 فك الحظر الصوتي", f"{'تم' if removed else 'لم يكن'} حظر {member.mention} الصوتي موجوداً.", category="log_voice", color=0x22C55E)

    @app_commands.command(name="vmove", description="نقل عضو إلى قناة صوتية أو قناة المشرف")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(move_members=True)
    async def vmove(self, interaction: discord.Interaction, member: discord.Member, channel: discord.VoiceChannel | None = None):
        destination = channel or self._current_voice(interaction)
        if destination is None:
            return await self._error(interaction, "vmove", "يجب أن تكون في قناة صوتية أو تحدد قناة الهدف.")
        return await self._voice_action(interaction, "vmove", member, "move", value=destination)

    @app_commands.command(name="moveall", description="نقل جميع أعضاء قناة صوتية إلى قناة أخرى")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(move_members=True)
    async def moveall(self, interaction: discord.Interaction, source: discord.VoiceChannel, destination: discord.VoiceChannel):
        success, failed = await self._bulk_voice_move(source.members, destination, f"moveall by {interaction.user}")
        return await self._send(interaction, "moveall", "🔄 نقل الكل صوتياً", f"تم نقل **{success}** أعضاء، وفشل **{failed}**.", category="log_voice", color=0x06B6D4)

    @app_commands.command(name="disconnectall", description="فصل جميع أعضاء القناة الصوتية الحالية")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(move_members=True)
    async def disconnectall(self, interaction: discord.Interaction):
        source = self._current_voice(interaction)
        if source is None:
            return await self._error(interaction, "disconnectall", "يجب أن تكون داخل قناة صوتية.")
        success, failed = await self._bulk_voice_move(source.members, None, f"disconnectall by {interaction.user}")
        return await self._send(interaction, "disconnectall", "⛔ قطع الكل", f"تم فصل **{success}** أعضاء، وفشل **{failed}**.", category="log_voice", color=0xEF4444)

    async def _voice_lock(self, interaction: discord.Interaction, command_name: str, connect: bool):
        channel = self._current_voice(interaction)
        if channel is None:
            return await self._error(interaction, command_name, "يجب أن تكون داخل قناة صوتية.")
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = connect
        try:
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"{command_name} by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, command_name, "تعذر تعديل صلاحيات القناة الصوتية.")
        return await self._send(interaction, command_name, "🔒 قفل صوتي" if not connect else "🔓 فتح صوتي", f"تم {'السماح' if connect else 'منع'} بالانضمام إلى {channel.mention}.", category="log_voice", color=0x22C55E if connect else 0xEF4444)

    @app_commands.command(name="vlock", description="قفل القناة الصوتية الحالية")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def vlock(self, interaction: discord.Interaction):
        return await self._voice_lock(interaction, "vlock", False)

    @app_commands.command(name="vunlock", description="فتح القناة الصوتية الحالية")
    @app_commands.check(sanctions_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def vunlock(self, interaction: discord.Interaction):
        return await self._voice_lock(interaction, "vunlock", True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SanctionsVoiceCog(bot))
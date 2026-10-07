"""Additive execution engine for Step 4 administration operations."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import re
from typing import Any, Iterable

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.sanctions_voice import parse_duration
from database import (
    add_member_warning,
    add_mod_note,
    add_temp_role,
    adjust_event_points,
    clear_member_warnings,
    delete_member_warning,
    delete_mod_note,
    get_command_policies,
    get_event_leaderboard,
    get_expired_temp_roles,
    get_member_warnings,
    get_mod_notes,
    get_warnings,
    remove_temp_role_entry,
)


logger = logging.getLogger("AdminAdvancedCog")
HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


async def admin_policy_check(interaction: discord.Interaction) -> bool:
    """Apply command_policies locally as well as through Utilities."""
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


class _TemplateValues(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class AdminAdvancedCog(commands.Cog):
    """Step 4 commands that do not replace existing moderation commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._delete_tasks: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        if not self.temp_role_watcher.is_running():
            self.temp_role_watcher.start()

    def cog_unload(self) -> None:
        self.temp_role_watcher.cancel()
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
                _TemplateValues({key: str(value) for key, value in values.items()})
            )[:2000]
        except (KeyError, ValueError, IndexError):
            return fallback

    async def _delete_later(self, message: Any, delay: int) -> None:
        if not message or delay <= 0 or not hasattr(message, "delete"):
            return
        try:
            await message.delete(delay=delay)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to auto-delete admin response", exc_info=True)

    async def _respond(
        self,
        interaction: discord.Interaction,
        command_name: str,
        title: str,
        description: str,
        *,
        category: str,
        color: int = 0x5865F2,
        fields: Iterable[tuple[str, str, bool]] = (),
        values: dict[str, Any] | None = None,
        ephemeral: bool = False,
        log_description: str | None = None,
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
            logger.debug("Unable to send admin response", exc_info=True)
        delay = int(policy.get("auto_delete_seconds") or 0)
        if message is not None and delay > 0 and not ephemeral:
            task = asyncio.create_task(self._delete_later(message, delay))
            self._delete_tasks.add(task)
            task.add_done_callback(self._delete_tasks.discard)
        analytics = self.bot.get_cog("Analytics")
        if analytics is not None:
            try:
                await analytics._log(
                    interaction.guild,
                    category,
                    title,
                    log_description or rendered,
                    author=interaction.user,
                    fields=field_values,
                    color=color,
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.debug("Unable to write %s audit", category, exc_info=True)
        return message

    async def _error(
        self,
        interaction: discord.Interaction,
        command_name: str,
        text: str,
        *,
        category: str = "log_violations",
    ) -> Any:
        return await self._respond(
            interaction,
            command_name,
            "تعذر تنفيذ الإجراء",
            text,
            category=category,
            color=0xEF4444,
            ephemeral=True,
        )

    @staticmethod
    def _bot_member(guild: discord.Guild) -> discord.Member | None:
        return guild.me

    @classmethod
    def _member_manageable(
        cls,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> bool:
        bot_member = cls._bot_member(interaction.guild)
        if bot_member is None or member.id == interaction.guild.owner_id:
            return False
        return member.id != bot_member.id and member.top_role < bot_member.top_role

    @classmethod
    def _role_manageable(
        cls,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> bool:
        bot_member = cls._bot_member(interaction.guild)
        return (
            bot_member is not None
            and not role.is_default()
            and not role.managed
            and role < bot_member.top_role
        )

    @staticmethod
    def _member_fields(member: discord.Member) -> list[tuple[str, str, bool]]:
        return [("👤 العضو", f"{member.mention} (`{member.id}`)", True)]

    async def _role_result(
        self,
        interaction: discord.Interaction,
        command_name: str,
        title: str,
        description: str,
        role: discord.Role,
        *,
        color: int = 0x8B5CF6,
        fields: Iterable[tuple[str, str, bool]] = (),
    ) -> Any:
        return await self._respond(
            interaction,
            command_name,
            title,
            description,
            category="log_roles",
            color=color,
            fields=[("🎭 الرتبة", f"{role.mention} (`{role.id}`)", True), *list(fields)],
        )

    @app_commands.command(name="setnick", description="تغيير لقب عضو")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def setnick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        new_nick: str | None = None,
    ):
        if not self._member_manageable(interaction, member):
            return await self._error(interaction, "setnick", "لا يمكن تغيير لقب هذا العضو بسبب ترتيب الرتب.")
        old_nick = member.nick or member.name
        try:
            await member.edit(nick=(new_nick.strip() or None) if new_nick is not None else None)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "setnick", "تعذر تغيير اللقب؛ تحقق من صلاحيات البوت.")
        current = member.nick or member.name
        return await self._respond(
            interaction,
            "setnick",
            "✏️ تغيير لقب عضو",
            f"تم تحديث لقب {member.mention}.",
            category="log_member",
            color=0x22C55E,
            fields=[
                *self._member_fields(member),
                ("الاسم السابق", old_nick, True),
                ("الاسم الجديد", current, True),
            ],
        )

    @app_commands.command(name="summon", description="استدعاء عضو برسالة خاصة")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def summon(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        message: str,
    ):
        guild = interaction.guild
        embed = discord.Embed(
            title=f"📩 تم استدعاؤك من قبل إدارة سيرفر **{guild.name}**",
            description="يرجى مراجعة الإدارة في القناة المحددة عند تمكنك.",
            color=0x8B5CF6,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="👤 المشرف المستدعي", value=interaction.user.mention, inline=False)
        embed.add_field(name="📝 سبب الاستدعاء", value=message[:1024], inline=False)
        jump_url = getattr(interaction.channel, "jump_url", "")
        if jump_url:
            embed.add_field(name="📍 الانتقال للروم", value=f"[اضغط هنا للانتقال للقناة فوراً]({jump_url})", inline=False)
        icon = getattr(getattr(guild, "icon", None), "url", None)
        banner = getattr(getattr(guild, "banner", None), "url", None)
        if icon:
            embed.set_thumbnail(url=str(icon))
        if banner:
            embed.set_image(url=str(banner))
        try:
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(
                interaction,
                "summon",
                "تعذر إرسال الاستدعاء؛ الرسائل الخاصة مغلقة لدى العضو.",
                category="log_member",
            )
        return await self._respond(
            interaction,
            "summon",
            "📩 تم إرسال الاستدعاء",
            f"تم إرسال رسالة استدعاء خاصة إلى {member.mention}.",
            category="log_member",
            color=0x22C55E,
            fields=[*self._member_fields(member), ("📝 السبب", message, False)],
        )

    @app_commands.command(name="delwarn", description="حذف تحذير من سجل العضو")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(kick_members=True)
    async def delwarn(self, interaction: discord.Interaction, warning_id: int):
        if not await delete_member_warning(warning_id, interaction.guild.id):
            return await self._error(interaction, "delwarn", "لم أجد هذا التحذير في هذا السيرفر.")
        return await self._respond(
            interaction,
            "delwarn",
            "🗑️ حذف تحذير",
            f"تم حذف التحذير رقم `{warning_id}`.",
            category="log_violations",
            color=0x22C55E,
        )

    @app_commands.command(name="clearwarns", description="مسح تحذيرات عضو")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(kick_members=True)
    async def clearwarns(self, interaction: discord.Interaction, member: discord.Member):
        if not self._member_manageable(interaction, member):
            return await self._error(interaction, "clearwarns", "لا يمكن إدارة سجل هذا العضو بسبب ترتيب الرتب.")
        count = await clear_member_warnings(interaction.guild.id, member.id)
        return await self._respond(
            interaction,
            "clearwarns",
            "🧹 مسح التحذيرات",
            f"تم مسح **{count}** تحذيراً من سجل {member.mention}.",
            category="log_violations",
            color=0x22C55E,
            fields=self._member_fields(member),
        )

    @app_commands.command(name="give_role", description="إعطاء رتبة لعضو")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def give_role(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if not self._role_manageable(interaction, role) or not self._member_manageable(interaction, member):
            return await self._error(interaction, "give_role", "لا يمكن تطبيق هذه الرتبة بسبب ترتيب الرتب.", category="log_roles")
        try:
            await member.add_roles(role, reason=f"give_role by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "give_role", "تعذر إعطاء الرتبة.", category="log_roles")
        return await self._role_result(interaction, "give_role", "🎭 إعطاء رتبة", f"تم منح {role.mention} إلى {member.mention}.", role, fields=self._member_fields(member))

    @app_commands.command(name="take_role", description="سحب رتبة من عضو")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def take_role(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if not self._role_manageable(interaction, role) or not self._member_manageable(interaction, member):
            return await self._error(interaction, "take_role", "لا يمكن إدارة هذه الرتبة بسبب ترتيب الرتب.", category="log_roles")
        try:
            await member.remove_roles(role, reason=f"take_role by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "take_role", "تعذر سحب الرتبة.", category="log_roles")
        return await self._role_result(interaction, "take_role", "↩️ سحب رتبة", f"تم سحب {role.mention} من {member.mention}.", role, color=0xF59E0B, fields=self._member_fields(member))

    @app_commands.command(name="strip_roles", description="سلب رتب عضو القابلة للإزالة")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def strip_roles(self, interaction: discord.Interaction, member: discord.Member):
        if not self._member_manageable(interaction, member):
            return await self._error(interaction, "strip_roles", "لا يمكن سلب رتب هذا العضو.", category="log_roles")
        bot_member = self._bot_member(interaction.guild)
        roles = [role for role in member.roles if not role.is_default() and role < bot_member.top_role and not role.managed]
        try:
            if roles:
                await member.remove_roles(*roles, reason=f"strip_roles by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "strip_roles", "تعذر سلب الرتب.", category="log_roles")
        return await self._respond(
            interaction,
            "strip_roles",
            "🧹 سلب الرتب",
            f"تم سلب **{len(roles)}** رتبة قابلة للإزالة من {member.mention}.",
            category="log_roles",
            color=0xF59E0B,
            fields=self._member_fields(member),
        )

    @app_commands.command(name="role_color", description="تغيير لون رتبة")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_color(self, interaction: discord.Interaction, role: discord.Role, hex_code: str):
        if not HEX_RE.fullmatch(hex_code.strip()) or not self._role_manageable(interaction, role):
            return await self._error(interaction, "role_color", "أرسل لوناً بصيغة #RRGGBB ورتبة قابلة للإدارة.", category="log_roles")
        value = hex_code.strip().lstrip("#")
        try:
            await role.edit(colour=discord.Colour(int(value, 16)), reason=f"role_color by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "role_color", "تعذر تغيير لون الرتبة.", category="log_roles")
        return await self._role_result(interaction, "role_color", "🎨 لون الرتبة", f"تم تغيير لون {role.mention} إلى `#{value.upper()}`.", role)

    @app_commands.command(name="dossier", description="عرض ملف العضو الإداري")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(kick_members=True)
    async def dossier(self, interaction: discord.Interaction, member: discord.Member):
        step_warnings = await get_member_warnings(interaction.guild.id, member.id)
        legacy_warnings = await get_warnings(member.id, interaction.guild.id)
        notes = await get_mod_notes(interaction.guild.id, member.id)
        embed = discord.Embed(
            title=f"📁 الملف الإداري — {member.display_name}",
            description=f"{member.mention} (`{member.id}`)",
            color=0x3B82F6,
            timestamp=discord.utils.utcnow(),
        )
        warning_lines = []
        warning_keys: set[tuple[str, str]] = set()
        for row in step_warnings:
            key = (str(row["reason"]), str(row["created_at"])[:16])
            warning_keys.add(key)
            warning_lines.append(
                f"`#{row['id']}` {row['reason']} — {str(row['created_at'])[:16]}"
            )
        for row in legacy_warnings:
            key = (str(row[1]), str(row[2])[:16])
            if key in warning_keys:
                continue
            warning_lines.append(f"`#{row[0]}` {row[1]} — {str(row[2])[:16]}")
        warning_lines = warning_lines[:8]
        note_lines = [
            f"`#{row['id']}` {row['note_text']} — {str(row['created_at'])[:16]}"
            for row in notes[:8]
        ]
        embed.add_field(name="⚠️ التحذيرات", value="\n".join(warning_lines)[:1024] or "لا توجد تحذيرات.", inline=False)
        embed.add_field(name="🔒 الملاحظات السرية", value="\n".join(note_lines)[:1024] or "لا توجد ملاحظات.", inline=False)
        embed.add_field(name="🎭 الرتب", value=", ".join(role.mention for role in member.roles if not role.is_default())[:1024] or "لا توجد", inline=False)
        return await self._respond(
            interaction,
            "dossier",
            "📁 ملف العضو",
            "تم تجميع ملف العضو الإداري.",
            category="log_violations",
            color=0x3B82F6,
            fields=[("👤 العضو", member.mention, True), ("التحذيرات", str(len(warning_lines)), True), ("الملاحظات", str(len(notes)), True)],
        )

    @app_commands.command(name="note", description="إضافة ملاحظة إدارية سرية")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def note(self, interaction: discord.Interaction, member: discord.Member, text: str):
        note_id = await add_mod_note(interaction.guild.id, member.id, interaction.user.id, text)
        return await self._respond(
            interaction,
            "note",
            "📝 حفظ ملاحظة سرية",
            f"تم حفظ الملاحظة رقم `{note_id}` في ملف {member.mention}.",
            category="log_violations",
            color=0x3B82F6,
            fields=self._member_fields(member),
        )

    @app_commands.command(name="notes", description="عرض الملاحظات السرية لعضو")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def notes(self, interaction: discord.Interaction, member: discord.Member):
        records = await get_mod_notes(interaction.guild.id, member.id)
        description = "\n".join(
            f"`#{row['id']}` {row['note_text']} — {str(row['created_at'])[:16]}"
            for row in records
        )[:4000] or "لا توجد ملاحظات سرية."
        return await self._respond(
            interaction,
            "notes",
            f"📝 ملاحظات {member.display_name}",
            description,
            category="log_violations",
            color=0x3B82F6,
            ephemeral=True,
        )

    @app_commands.command(name="delnote", description="حذف ملاحظة إدارية")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def delnote(self, interaction: discord.Interaction, note_id: int):
        if not await delete_mod_note(note_id, interaction.guild.id):
            return await self._error(interaction, "delnote", "لم أجد هذه الملاحظة في هذا السيرفر.")
        return await self._respond(interaction, "delnote", "🗑️ حذف ملاحظة", f"تم حذف الملاحظة رقم `{note_id}`.", category="log_violations", color=0x22C55E)

    @app_commands.command(name="event_points", description="عرض أو تعديل نقاط الفعاليات")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_events=True)
    async def event_points(self, interaction: discord.Interaction, member: discord.Member | None = None, delta: int = 0):
        target = member or interaction.user
        if delta:
            points = await adjust_event_points(interaction.guild.id, target.id, delta)
            description = f"تم تعديل نقاط {target.mention} بمقدار **{delta:+d}**."
        else:
            leaderboard = await get_event_leaderboard(interaction.guild.id)
            points = next((row["points"] for row in leaderboard if row["user_id"] == target.id), 0)
            description = f"رصيد نقاط {target.mention} الحالي: **{points}**."
        return await self._respond(
            interaction,
            "event_points",
            "🏆 نقاط الفعاليات",
            description,
            category="log_roles",
            color=0xF59E0B,
            fields=[("👤 العضو", target.mention, True), ("النقاط", str(points), True)],
        )

    @app_commands.command(name="reset_points", description="تصفير نقاط الفعاليات")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_points(self, interaction: discord.Interaction):
        count = await self._reset_points(interaction.guild.id)
        return await self._respond(interaction, "reset_points", "♻️ تصفير نقاط الفعاليات", f"تم تصفير نقاط **{count}** عضواً.", category="log_roles", color=0xF59E0B)

    async def _reset_points(self, guild_id: int) -> int:
        from database import reset_event_points
        return await reset_event_points(guild_id)

    async def _bulk_role_change(
        self,
        interaction: discord.Interaction,
        command_name: str,
        role: discord.Role,
        *,
        add: bool,
        members: Iterable[discord.Member],
    ) -> tuple[int, int]:
        if not self._role_manageable(interaction, role):
            return 0, 1
        success = failed = 0
        for index, member in enumerate(list(members)):
            if not self._member_manageable(interaction, member):
                failed += 1
                continue
            try:
                if add:
                    await member.add_roles(role, reason=f"{command_name} by {interaction.user}")
                else:
                    await member.remove_roles(role, reason=f"{command_name} by {interaction.user}")
                success += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
            if index and index % 5 == 0:
                await asyncio.sleep(0.35)
        return success, failed

    @app_commands.command(name="roleall", description="منح رتبة لجميع الأعضاء القابلين للإدارة")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def roleall(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer()
        success, failed = await self._bulk_role_change(interaction, "roleall", role, add=True, members=interaction.guild.members)
        return await self._role_result(interaction, "roleall", "🎭 رتبة للكل", f"تم منح الرتبة لـ **{success}** عضواً وفشل **{failed}**.", role)

    @app_commands.command(name="removeroleall", description="سحب رتبة من جميع الأعضاء")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def removeroleall(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer()
        success, failed = await self._bulk_role_change(interaction, "removeroleall", role, add=False, members=interaction.guild.members)
        return await self._role_result(interaction, "removeroleall", "↩️ سحب رتبة الكل", f"تم سحب الرتبة من **{success}** عضواً وفشل **{failed}**.", role, color=0xF59E0B)

    @app_commands.command(name="massrole", description="تطبيق رتبة على مجموعة أعضاء")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.choices(
        action=[
            app_commands.Choice(name="إضافة", value="add"),
            app_commands.Choice(name="سحب", value="remove"),
        ],
        scope=[
            app_commands.Choice(name="الجميع", value="all"),
            app_commands.Choice(name="البوتات فقط", value="bots"),
            app_commands.Choice(name="حاملو رتبة المصدر", value="role"),
        ],
    )
    async def massrole(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        action: app_commands.Choice[str],
        scope: app_commands.Choice[str],
        source_role: discord.Role | None = None,
    ):
        if scope.value == "role" and source_role is None:
            return await self._error(interaction, "massrole", "حدد رتبة المصدر.", category="log_roles")
        members = interaction.guild.members
        if scope.value == "bots":
            members = [member for member in members if member.bot]
        elif scope.value == "role":
            members = [member for member in members if source_role in member.roles]
        await interaction.response.defer()
        success, failed = await self._bulk_role_change(
            interaction,
            "massrole",
            role,
            add=action.value == "add",
            members=members,
        )
        return await self._role_result(interaction, "massrole", "🎭 رتبة جماعية", f"تم تنفيذ الإجراء على **{success}** عضواً وفشل **{failed}**.", role, color=0x22C55E if action.value == "add" else 0xF59E0B)

    @app_commands.command(name="temprole", description="منح رتبة مؤقتة")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def temprole(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role, duration: str):
        if not self._role_manageable(interaction, role) or not self._member_manageable(interaction, member):
            return await self._error(interaction, "temprole", "لا يمكن تطبيق هذه الرتبة بسبب ترتيب الرتب.", category="log_roles")
        parsed = parse_duration(duration)
        if parsed is None:
            return await self._error(interaction, "temprole", "المدة يجب أن تكون مثل `30m` أو `2h` أو `7d`.", category="log_roles")
        expires_at = discord.utils.utcnow() + parsed
        try:
            await member.add_roles(role, reason=f"temprole by {interaction.user}")
            entry_id = await add_temp_role(interaction.guild.id, member.id, role.id, expires_at)
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "temprole", "تعذر منح الرتبة المؤقتة.", category="log_roles")
        return await self._role_result(interaction, "temprole", "⏳ رتبة مؤقتة", f"تم منح {role.mention} لـ {member.mention} حتى <t:{int(expires_at.timestamp())}:F>.", role, fields=[*self._member_fields(member), ("معرف السجل", str(entry_id), True)])

    @app_commands.command(name="role_icon", description="تغيير أيقونة رتبة")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_icon(self, interaction: discord.Interaction, role: discord.Role, emoji: str = "", image: discord.Attachment | None = None):
        if not self._role_manageable(interaction, role):
            return await self._error(interaction, "role_icon", "لا يمكن إدارة أيقونة هذه الرتبة.", category="log_roles")
        icon: bytes | str | None = None
        if image is not None:
            if image.size > 256 * 1024:
                return await self._error(interaction, "role_icon", "حجم صورة الأيقونة يجب ألا يتجاوز 256KB.", category="log_roles")
            try:
                icon = await image.read()
            except (discord.HTTPException, discord.NotFound):
                return await self._error(interaction, "role_icon", "تعذر قراءة الصورة المرفقة.", category="log_roles")
        elif emoji.strip():
            icon = emoji.strip()[:100]
        else:
            return await self._error(interaction, "role_icon", "أرسل إيموجي أو صورة للأيقونة.", category="log_roles")
        try:
            await role.edit(display_icon=icon, reason=f"role_icon by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "role_icon", "تعذر تغيير أيقونة الرتبة؛ قد لا تكون الميزة متاحة لهذا السيرفر.", category="log_roles")
        return await self._role_result(interaction, "role_icon", "🖼️ أيقونة الرتبة", f"تم تحديث أيقونة {role.mention}.", role)

    @app_commands.command(name="sync_perms", description="مزامنة صلاحيات القناة مع الفئة")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def sync_perms(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not hasattr(channel, "sync_permissions") or getattr(channel, "category", None) is None:
            return await self._error(interaction, "sync_perms", "القناة الحالية لا تتبع فئة قابلة للمزامنة.", category="log_roles")
        try:
            await channel.sync_permissions()
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "sync_perms", "تعذر مزامنة صلاحيات القناة.", category="log_roles")
        return await self._respond(interaction, "sync_perms", "🔄 مزامنة الصلاحيات", f"تمت مزامنة صلاحيات {channel.mention} مع الفئة.", category="log_roles", color=0x22C55E)

    @app_commands.command(name="role_members", description="عرض أعضاء رتبة")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_members(self, interaction: discord.Interaction, role: discord.Role):
        members = [member for member in interaction.guild.members if role in member.roles]
        names = ", ".join(member.display_name for member in members)[:1800] or "لا يوجد أعضاء."
        return await self._respond(interaction, "role_members", f"👥 أعضاء {role.name}", names, category="log_roles", color=0x8B5CF6, fields=[("العدد", str(len(members)), True)])

    @app_commands.command(name="no_role", description="عرض أعضاء بلا رتب")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(kick_members=True)
    async def no_role(self, interaction: discord.Interaction):
        members = [member for member in interaction.guild.members if not any(not role.is_default() for role in member.roles)]
        names = ", ".join(member.display_name for member in members)[:1800] or "لا يوجد أعضاء بلا رتب."
        return await self._respond(interaction, "no_role", "👤 أعضاء بلا رتب", names, category="log_member", color=0x3B82F6, fields=[("العدد", str(len(members)), True)])

    @app_commands.command(name="bot_list", description="عرض بوتات السيرفر")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(kick_members=True)
    async def list_bots(self, interaction: discord.Interaction):
        bots = [member for member in interaction.guild.members if member.bot]
        lines = [f"{member.mention} — `{member.id}`" for member in bots]
        return await self._respond(interaction, "bot_list", "🤖 بوتات السيرفر", "\n".join(lines)[:1900] or "لا توجد بوتات أخرى.", category="log_member", color=0x3B82F6, fields=[("العدد", str(len(bots)), True)])

    @app_commands.command(name="member_stats", description="إحصائيات أعضاء السيرفر")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(kick_members=True)
    async def member_stats(self, interaction: discord.Interaction):
        members = interaction.guild.members
        bots = [member for member in members if member.bot]
        online = [member for member in members if member.status != discord.Status.offline]
        role_holders = [member for member in members if any(not role.is_default() for role in member.roles)]
        description = (
            f"الأعضاء: **{len(members)}**\n"
            f"البشر: **{len(members) - len(bots)}**\n"
            f"البوتات: **{len(bots)}**\n"
            f"المتصلون: **{len(online)}**\n"
            f"حاملو الرتب: **{len(role_holders)}**"
        )
        return await self._respond(interaction, "member_stats", "📊 إحصائيات الأعضاء", description, category="log_member", color=0x3B82F6)

    @app_commands.command(name="reset_nicks", description="إعادة الأسماء الأصلية للأعضاء")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def reset_nicks(self, interaction: discord.Interaction):
        await interaction.response.defer()
        success = failed = 0
        for index, member in enumerate(interaction.guild.members):
            if not member.nick or not self._member_manageable(interaction, member):
                continue
            try:
                await member.edit(nick=None, reason=f"reset_nicks by {interaction.user}")
                success += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
            if index and index % 5 == 0:
                await asyncio.sleep(0.35)
        return await self._respond(interaction, "reset_nicks", "♻️ إعادة ضبط الأسماء", f"تمت إعادة **{success}** أسماء وفشل **{failed}**.", category="log_member", color=0x22C55E)

    @app_commands.command(name="announce", description="إرسال إعلان رسمي")
    @app_commands.check(admin_policy_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def announce(
        self,
        interaction: discord.Interaction,
        title: str,
        content: str,
        channel: discord.TextChannel | None = None,
        mention: bool = False,
    ):
        target = channel or interaction.channel
        if not hasattr(target, "send"):
            return await self._error(interaction, "announce", "القناة المحددة لا تدعم الإعلانات.", category="log_channel")
        icon = getattr(getattr(interaction.guild, "icon", None), "url", None)
        embed = discord.Embed(title=f"📣 {title}", description=content, color=0x3B82F6, timestamp=discord.utils.utcnow())
        if icon:
            embed.set_author(name=interaction.guild.name, icon_url=str(icon))
        embed.set_footer(text=f"إعلان رسمي • {interaction.guild.name}")
        try:
            await target.send(
                content=interaction.guild.default_role.mention if mention else None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=mention, roles=mention),
            )
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "announce", "تعذر إرسال الإعلان إلى القناة المحددة.", category="log_channel")
        return await self._respond(interaction, "announce", "📣 تم نشر الإعلان", f"تم نشر الإعلان في {target.mention}.", category="log_channel", color=0x22C55E, fields=[("العنوان", title, False)])

    @tasks.loop(minutes=1)
    async def temp_role_watcher(self) -> None:
        for record in await get_expired_temp_roles():
            guild = self.bot.get_guild(int(record["guild_id"]))
            if guild is None:
                continue
            member = guild.get_member(int(record["user_id"]))
            role = guild.get_role(int(record["role_id"]))
            if member is None or role is None:
                if role is None or member is None:
                    await remove_temp_role_entry(int(record["id"]))
                continue
            try:
                if role in member.roles and self._role_manageable_for_guild(guild, role):
                    await member.remove_roles(role, reason="انتهاء الرتبة المؤقتة")
                elif role in member.roles:
                    continue
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("Failed to expire temporary role %s/%s", guild.id, record["id"], exc_info=True)
                continue
            await remove_temp_role_entry(int(record["id"]))
            analytics = self.bot.get_cog("Analytics")
            if analytics:
                try:
                    await analytics._log(
                        guild,
                        "log_roles",
                        "⌛ انتهاء رتبة مؤقتة",
                        "تم سحب رتبة مؤقتة تلقائياً بعد انتهاء مدتها.",
                        fields=[("👤 العضو", f"<@{member.id}> (`{member.id}`)", True), ("🎭 الرتبة", f"<@&{role.id}>", True)],
                        color=0x8B5CF6,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logger.debug("Unable to log expired temporary role", exc_info=True)

    @staticmethod
    def _role_manageable_for_guild(guild: discord.Guild, role: discord.Role) -> bool:
        return guild.me is not None and not role.managed and not role.is_default() and role < guild.me.top_role

    @temp_role_watcher.before_loop
    async def before_temp_role_watcher(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminAdvancedCog(bot))
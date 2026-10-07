"""Additive, multi-channel Discord audit logging.

This cog deliberately owns only the new logging surface. Existing moderation,
security, ticket, economy, gaming, and engagement listeners remain untouched.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import discord
from discord.ext import commands

from database import (
    LOG_ROUTING_CACHE,
    get_logging_channels,
    get_cached_logging_channels,
)

logger = logging.getLogger("AnalyticsCog")

COLORS = {
    "log_sanctions": (0xDC2626, "⚔️ سجل العقوبات"),
    "log_violations": (0xEAB308, "⚠️ سجل المخالفات"),
    "log_automod": (0xF97316, "🛡️ سجل Auto-Mod"),
    "log_ticket": (0x14B8A6, "🎫 سجل التذاكر"),
    "log_channel": (0x10B981, "📁 سجل القنوات"),
    "log_server": (0x3B82F6, "🏰 سجل السيرفر"),
    "log_member": (0x22C55E, "👤 سجل الأعضاء"),
    "log_message": (0xEF4444, "💬 سجل الرسائل"),
    "log_react": (0xEC4899, "👍 سجل التفاعلات"),
    "log_roles": (0x8B5CF6, "🎭 سجل الرتب والصلاحيات"),
    "log_voice": (0x06B6D4, "🎙️ سجل النشاط الصوتي"),
    # Legacy names remain valid for existing listeners and integrations.
    "log_messages": (0xEF4444, "🗑️ حذف رسالة"),
    "log_channels": (0x10B981, "📁 نشاط القنوات"),
    "log_moderation": (0xDC2626, "⚔️ إجراء إداري"),
    "log_warnings": (0xEAB308, "⚠️ إنذار إداري"),
}

CATEGORY_ALIASES = {
    "log_messages": "log_message",
    "log_channels": "log_channel",
    "log_moderation": "log_sanctions",
    "log_warnings": "log_violations",
}


def _avatar(user: Any) -> str | None:
    value = getattr(getattr(user, "display_avatar", None), "url", None)
    return str(value) if value else None


def _safe(value: Any, limit: int = 1024) -> str:
    text = str(value if value is not None else "—")
    return text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")[:limit]


def _code(value: Any) -> str:
    cleaned = _safe(value, 850).replace("```", "'''")
    return f"```{cleaned}```"


def create_elite_log_embed(
    title: str,
    description: str,
    color_hex: str,
    author_user=None,
    fields=None,
    thumbnail_url=None,
) -> discord.Embed:
    """Build the shared high-contrast audit card used by every category."""
    try:
        color = int(str(color_hex).replace("#", ""), 16)
    except (TypeError, ValueError):
        color = 0x5865F2
    embed = discord.Embed(
        title=_safe(title, 256),
        description=_safe(description, 4096),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    if author_user:
        name = getattr(author_user, "display_name", None) or getattr(author_user, "name", "System")
        icon = _avatar(author_user)
        if icon:
            embed.set_author(name=_safe(name, 256), icon_url=icon)
        else:
            embed.set_author(name=_safe(name, 256))
        thumbnail_url = thumbnail_url or icon
    if thumbnail_url:
        embed.set_thumbnail(url=str(thumbnail_url))
    for item in fields or []:
        if isinstance(item, dict):
            name, value, inline = item.get("name"), item.get("value"), item.get("inline", True)
        else:
            name, value, *rest = item
            inline = rest[0] if rest else True
        embed.add_field(name=_safe(name, 256), value=_safe(value), inline=bool(inline))
    return embed


class Analytics(commands.Cog):
    """Enterprise audit dispatcher with eleven independent channel routes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _routing(self, guild: discord.Guild) -> dict[str, int]:
        if guild.id not in LOG_ROUTING_CACHE:
            return await get_logging_channels(guild.id)
        return get_cached_logging_channels(guild.id)

    async def _send(
        self,
        guild: discord.Guild,
        category: str,
        embed: discord.Embed,
        *,
        strict: bool = False,
    ) -> bool:
        canonical = CATEGORY_ALIASES.get(category, category)
        routing = await self._routing(guild)
        route = routing.get(canonical, routing.get(category, 0))
        channel = guild.get_channel(int(route)) if route else None
        if channel is None or not hasattr(channel, "send"):
            return False
        try:
            me = guild.me
            if me:
                permissions = channel.permissions_for(me)
                if not permissions.send_messages or not permissions.embed_links:
                    return False
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            return True
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to dispatch %s audit for guild %s", category, guild.id, exc_info=True)
            if strict:
                raise
            return False

    async def _audit(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int | None = None,
    ):
        try:
            async for entry in guild.audit_logs(limit=8, action=action):
                target = getattr(entry, "target", None)
                if target_id is None or getattr(target, "id", None) == int(target_id):
                    return entry
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            return None
        return None

    def _embed(self, guild, category, title, description, *, author=None, fields=None, thumbnail=None, color=None):
        embed = create_elite_log_embed(
            title,
            description,
            f"#{color or COLORS[category][0]:06X}",
            author_user=author,
            fields=fields,
            thumbnail_url=thumbnail,
        )
        icon = getattr(getattr(guild, "icon", None), "url", None)
        if icon:
            embed.set_footer(
                text=f"Enterprise Security Audit • Guild ID: {guild.id}",
                icon_url=str(icon),
            )
        else:
            embed.set_footer(text=f"Enterprise Security Audit • Guild ID: {guild.id}")
        return embed

    async def _log(
        self,
        guild,
        category,
        title,
        description,
        *,
        author=None,
        fields=None,
        thumbnail=None,
        color=None,
        strict: bool = False,
    ):
        return await self._send(
            guild,
            category,
            self._embed(
                guild,
                category,
                title,
                description,
                author=author,
                fields=fields,
                thumbnail=thumbnail,
                color=color,
            ),
            strict=strict,
        )

    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                await get_logging_channels(guild.id)
            except Exception:
                logger.debug("Unable to warm logging route for guild %s", guild.id, exc_info=True)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or getattr(message.author, "bot", False):
            return
        attachments = "\n".join(getattr(item, "url", "") for item in message.attachments) or "لا توجد"
        await self._log(
            message.guild,
            "log_messages",
            "🗑️ حذف رسالة",
            "تم حذف رسالة من سجل السيرفر.",
            author=message.author,
            color=0xEF4444,
            fields=[
                ("👤 العضو", f"{message.author.mention} (`{message.author.id}`)", True),
                ("💬 القناة", f"{message.channel.mention} (`#{message.channel.name}`)", True),
                ("🗑️ المحتوى المحذوف", _code(message.content or "بدون نص"), False),
                ("📎 المرفقات", _safe(attachments, 900), False),
            ],
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or getattr(before.author, "bot", False) or before.content == after.content:
            return
        await self._log(
            before.guild,
            "log_messages",
            "✏️ تعديل رسالة",
            "تم تعديل محتوى رسالة موجودة.",
            author=before.author,
            color=0xF59E0B,
            fields=[
                ("👤 العضو", before.author.mention, True),
                ("💬 القناة", f"{before.channel.mention} [انتقال للرسالة]({after.jump_url})", True),
                ("📝 المحتوى السابق", _code(before.content), False),
                ("✏️ المحتوى المعدل", _code(after.content), False),
            ],
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        added = [role for role in after.roles if role not in before.roles and not role.is_default()]
        removed = [role for role in before.roles if role not in after.roles and not role.is_default()]
        if not added and not removed:
            return
        entry = await self._audit(after.guild, discord.AuditLogAction.member_role_update, after.id)
        moderator = getattr(entry, "user", None)
        for role in added:
            await self._log(
                after.guild, "log_roles", "➕ إضافة رتبة",
                f"تمت إضافة {role.mention} إلى {after.mention}.",
                author=moderator or after, color=0x8B5CF6,
                fields=[
                    ("👤 العضو", f"{after.mention} (`{after.id}`)", True),
                    ("🎭 الرتبة", f"{role.mention} • `{role.id}`", True),
                    ("🎨 اللون", f"`{role.color}`", True),
                    ("🧾 المنفذ", getattr(moderator, "mention", "غير معروف"), True),
                ],
            )
        for role in removed:
            await self._log(
                after.guild, "log_roles", "➖ إزالة رتبة",
                f"تمت إزالة {role.mention} من {after.mention}.",
                author=moderator or after, color=0x8B5CF6,
                fields=[
                    ("👤 العضو", f"{after.mention} (`{after.id}`)", True),
                    ("🎭 الرتبة", f"{role.mention} • `{role.id}`", True),
                    ("🎨 اللون", f"`{role.color}`", True),
                    ("🧾 المنفذ", getattr(moderator, "mention", "غير معروف"), True),
                ],
            )

    async def on_guild_role_create(self, role: discord.Role):
        entry = await self._audit(role.guild, discord.AuditLogAction.role_create, role.id)
        await self._log(
            role.guild, "log_roles", "🟣 إنشاء رتبة", f"تم إنشاء {role.mention}.",
            author=getattr(entry, "user", None) or role.guild.me,
            fields=[
                ("🎭 الاسم", f"`{role.name}`", True),
                ("🎨 اللون", f"`{role.color}`", True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    async def on_guild_role_delete(self, role: discord.Role):
        entry = await self._audit(role.guild, discord.AuditLogAction.role_delete, role.id)
        await self._log(
            role.guild, "log_roles", "🗑️ حذف رتبة", f"تم حذف الرتبة `{role.name}`.",
            author=getattr(entry, "user", None) or role.guild.me,
            fields=[
                ("🎨 اللون", f"`{role.color}`", True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = []
        if before.name != after.name:
            changes.append(f"الاسم: `{before.name}` → `{after.name}`")
        if before.color != after.color:
            changes.append(f"اللون: `{before.color}` → `{after.color}`")
        if before.permissions != after.permissions:
            changes.append("الصلاحيات: تم تحديث مجموعة الصلاحيات")
        if not changes:
            return
        entry = await self._audit(after.guild, discord.AuditLogAction.role_update, after.id)
        await self._log(
            after.guild, "log_roles", "🛠️ تعديل رتبة", "تم تحديث إعدادات رتبة.",
            author=getattr(entry, "user", None) or after.guild.me,
            fields=[
                ("🎭 الرتبة", f"{after.mention} (`{after.id}`)", True),
                ("📝 التغييرات", "\n".join(changes), False),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    def _channel_type(self, channel):
        return "صوتية" if isinstance(channel, discord.VoiceChannel) else "تصنيف" if isinstance(channel, discord.CategoryChannel) else "نصية"

    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        entry = await self._audit(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        await self._log(
            channel.guild, "log_channels", "🟢 إنشاء قناة", "تم إنشاء قناة جديدة.",
            author=getattr(entry, "user", None) or channel.guild.me,
            fields=[
                ("📁 الاسم", f"`#{channel.name}`", True),
                ("🔖 النوع", self._channel_type(channel), True),
                ("📂 الفئة الأب", getattr(getattr(channel, "category", None), "name", "بدون"), True),
                ("🧾 المسؤول", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        entry = await self._audit(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        await self._log(
            channel.guild, "log_channels", "🔴 حذف قناة", "تم حذف قناة من السيرفر.",
            author=getattr(entry, "user", None) or channel.guild.me,
            color=0xEF4444,
            fields=[
                ("📁 الاسم", f"`#{channel.name}`", True),
                ("🔖 النوع", self._channel_type(channel), True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    async def on_guild_channel_update(self, before, after):
        changes = []
        for label, old, new in (
            ("الاسم", before.name, after.name),
            ("الموضوع", getattr(before, "topic", None), getattr(after, "topic", None)),
            ("Slowmode", getattr(before, "slowmode_delay", None), getattr(after, "slowmode_delay", None)),
            ("Bitrate", getattr(before, "bitrate", None), getattr(after, "bitrate", None)),
        ):
            if old != new:
                changes.append(f"{label}: `{old}` → `{new}`")
        if not changes:
            return
        entry = await self._audit(after.guild, discord.AuditLogAction.channel_update, after.id)
        await self._log(
            after.guild, "log_channels", "🛠️ تعديل قناة", "تم تعديل خصائص قناة.",
            author=getattr(entry, "user", None) or after.guild.me,
            fields=[
                ("📁 القناة", f"`#{after.name}`", True),
                ("📝 التغييرات", "\n".join(changes), False),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        entry = await self._audit(guild, discord.AuditLogAction.ban, user.id)
        await self._log(
            guild, "log_moderation", "🔨 حظر عضو (Ban)", "تم حظر عضو من السيرفر.",
            author=getattr(entry, "user", None) or guild.me, thumbnail=_avatar(user),
            fields=[
                ("🎯 العضو", f"{user.mention} (`{user.id}`)", True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
                ("📌 السبب", getattr(entry, "reason", None) or "غير محدد", False),
            ],
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        entry = await self._audit(guild, discord.AuditLogAction.unban, user.id)
        await self._log(
            guild, "log_moderation", "🔓 فك حظر عضو (Unban)", "تم فك حظر عضو.",
            author=getattr(entry, "user", None) or guild.me, thumbnail=_avatar(user),
            color=0x10B981,
            fields=[
                ("🎯 العضو", f"{user.mention} (`{user.id}`)", True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        entry = await self._audit(member.guild, discord.AuditLogAction.kick, member.id)
        if entry is None:
            return
        await self._log(
            member.guild, "log_moderation", "👢 طرد عضو (Kick)", "تم طرد عضو من السيرفر.",
            author=getattr(entry, "user", None) or member.guild.me, thumbnail=_avatar(member),
            fields=[
                ("🎯 العضو", f"{member.mention} (`{member.id}`)", True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
                ("📌 السبب", getattr(entry, "reason", None) or "غير محدد", False),
            ],
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel and before.mute == after.mute and before.deaf == after.deaf:
            return
        if before.channel != after.channel:
            if before.channel and after.channel:
                title, description = "🔄 انتقال بين الرومات", "انتقل العضو بين قناتين صوتيتين."
                fields = [
                    ("👤 العضو", f"{member.mention} (`{member.id}`)", True),
                    ("من", f"`{before.channel.name}`", True),
                    ("إلى", f"`{after.channel.name}`", True),
                ]
            elif after.channel:
                title, description = "🟢 انضمام لروم صوتي", "انضم عضو إلى قناة صوتية."
                fields = [("👤 العضو", member.mention, True), ("📍 القناة", f"`{after.channel.name}`", True)]
            else:
                title, description = "🔴 مغادرة روم صوتي", "غادر عضو قناة صوتية."
                fields = [("👤 العضو", member.mention, True), ("📍 القناة", f"`{before.channel.name}`", True)]
        else:
            title, description = "🎚️ تغيير حالة صوتية", "تم تغيير حالة الكتم أو الصمم."
            fields = [("👤 العضو", member.mention, True)]
        if before.mute != after.mute:
            fields.append(("🔇 كتم السيرفر", f"`{before.mute}` → `{after.mute}`", True))
        if before.deaf != after.deaf:
            fields.append(("🙉 صمم السيرفر", f"`{before.deaf}` → `{after.deaf}`", True))
        await self._log(member.guild, "log_voice", title, description, author=member, fields=fields, color=0x06B6D4)

    async def log_timeout(self, guild, member, moderator, minutes, reason, expires_at=None):
        await self._log(
            guild, "log_moderation", "🤐 كتم عضو (Timeout)", "تم تطبيق كتم مؤقت على عضو.",
            author=moderator, thumbnail=_avatar(member),
            fields=[
                ("🎯 العضو", f"{member.mention} (`{member.id}`)", True),
                ("⏱️ المدة", f"{minutes} دقيقة", True),
                ("🧾 المنفذ", getattr(moderator, "mention", "غير معروف"), True),
                ("📌 السبب", reason, False),
                ("⌛ الانتهاء", expires_at or "غير محدد", False),
            ],
        )

    async def log_warning(self, guild, member, moderator, reason, total):
        await self._log(
            guild, "log_warnings", "⚠️ إصدار إنذار لعضو", "تم تسجيل مخالفة جديدة.",
            author=moderator, thumbnail=_avatar(member), color=0xEAB308,
            fields=[
                ("👤 العضو", f"{member.mention} (`{member.id}`)", True),
                ("📊 الإجمالي الحالي", str(total), True),
                ("🧾 المنفذ", getattr(moderator, "mention", "غير معروف"), True),
                ("📌 السبب", reason, False),
            ],
        )

    async def log_automod(self, guild, title, description, *, actor=None, fields=None, color=None):
        """Additive bridge for the existing Auto-Mod logger."""
        await self._log(
            guild,
            "log_automod",
            title,
            description,
            author=actor or guild.me,
            fields=fields,
            color=color or COLORS["log_automod"][0],
        )

    async def log_ticket_event(self, guild, title, description, *, actor=None, fields=None, color=None):
        """Additive bridge used by ticket operations without replacing ticket storage."""
        await self._log(
            guild,
            "log_ticket",
            title,
            description,
            author=actor or guild.me,
            fields=fields,
            color=color or COLORS["log_ticket"][0],
        )

    @commands.Cog.listener("on_guild_update")
    async def on_guild_update_audit(self, before: discord.Guild, after: discord.Guild):
        changes = []
        for label, old, new in (
            ("الاسم", before.name, after.name),
            ("الوصف", getattr(before, "description", None), getattr(after, "description", None)),
            ("مستوى التحقق", getattr(before, "verification_level", None), getattr(after, "verification_level", None)),
        ):
            if old != new:
                changes.append(f"{label}: `{old}` → `{new}`")
        if changes:
            entry = await self._audit(after, discord.AuditLogAction.guild_update)
            await self._log(
                after,
                "log_server",
                "🏰 تعديل إعدادات السيرفر",
                "تم تحديث إعدادات السيرفر.",
                author=getattr(entry, "user", None) or after.me,
                fields=[
                    ("📝 التغييرات", "\n".join(changes), False),
                    ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
                ],
            )

    @commands.Cog.listener("on_member_join")
    async def on_member_join_audit(self, member: discord.Member):
        await self._log(
            member.guild,
            "log_member",
            "🟢 انضمام عضو",
            "انضم عضو جديد إلى السيرفر.",
            author=member,
            thumbnail=_avatar(member),
            fields=[("👤 العضو", f"{member.mention} (`{member.id}`)", True)],
        )

    @commands.Cog.listener("on_member_remove")
    async def on_member_remove_audit(self, member: discord.Member):
        await self._log(
            member.guild,
            "log_member",
            "🔴 مغادرة عضو",
            "غادر عضو السيرفر أو تمت إزالته.",
            author=member,
            thumbnail=_avatar(member),
            fields=[("👤 العضو", f"{member.mention} (`{member.id}`)", True)],
        )

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add_audit(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id or payload.user_id == getattr(self.bot.user, "id", None):
            return
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id) if guild else None
        if guild is None:
            return
        await self._log(
            guild,
            "log_react",
            "👍 إضافة تفاعل",
            "أضاف عضو تفاعلاً إلى رسالة.",
            author=member or guild.me,
            fields=[
                ("👤 العضو", f"<@{payload.user_id}> (`{payload.user_id}`)", True),
                ("💬 الرسالة", f"`{payload.message_id}`", True),
                ("😀 التفاعل", str(payload.emoji), True),
            ],
        )

    async def send_test(self, guild, category: str, actor=None):
        if category not in COLORS:
            raise ValueError("unsupported logging category")
        _, label = COLORS[category]
        sent = await self._log(
            guild, category, f"🧪 اختبار {label}", "هذه رسالة اختبار من موزع السجلات الاحترافي.",
            author=actor or guild.me,
            fields=[
                ("✅ الحالة", "القناة مرتبطة وتستقبل السجلات", True),
                ("🧭 التصنيف", category, True),
            ],
            strict=True,
        )
        if not sent:
            raise PermissionError("log channel is not writable")


async def setup(bot: commands.Bot):
    await bot.add_cog(Analytics(bot))
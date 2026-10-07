"""Member tools, safe utilities, channel management, and durable reminders.

This cog intentionally keeps the command surface self-contained.  It uses the
same policy and audit contracts as the other additive cogs, while avoiding
network work in command callbacks except where Discord explicitly requires it.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import datetime as dt
import io
import logging
import re
from typing import Any
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import (
    add_reminder,
    connect,
    delete_reminder,
    get_command_policies,
    get_due_user_reminders,
    get_guild_settings,
)

logger = logging.getLogger("ToolsChannelsCog")
LAST_DELETED_MESSAGE: dict[int, dict[str, Any]] = {}
LAST_EDITED_MESSAGE: dict[int, dict[str, Any]] = {}
_DURATION = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.I)
_HEX = re.compile(r"^#?([0-9a-f]{6})$", re.I)
_EMOJI_URL = re.compile(r"<a?:([A-Za-z0-9_]+):(\d+)>")


async def tools_policy_check(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        raise app_commands.NoPrivateMessage()
    name = str(getattr(getattr(interaction, "command", None), "name", "")).lower()
    policy = (await get_command_policies(interaction.guild.id)).get(name)
    if not policy:
        return True
    if not policy.get("enabled", True):
        raise app_commands.CheckFailure("command_disabled")
    roles = {int(r.id) for r in getattr(interaction.user, "roles", [])}
    allowed = {int(r) for r in policy.get("allowed_roles", [])}
    if allowed and not roles.intersection(allowed):
        raise app_commands.CheckFailure("command_role_restricted")
    channels = {int(c) for c in policy.get("allowed_channels", [])}
    if channels and int(interaction.channel_id or 0) not in channels:
        raise app_commands.CheckFailure("command_channel_restricted")
    return True


def _duration(value: str) -> dt.timedelta | None:
    match = _DURATION.fullmatch(str(value or ""))
    if not match:
        return None
    seconds = int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2).lower()]
    return dt.timedelta(seconds=seconds) if seconds else None


def _safe(text: Any, limit: int = 1024) -> str:
    return str(text if text is not None else "—").replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")[:limit]


class ToolsChannelsCog(commands.Cog):
    """Exactly the 39 Step 5 leaf commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._delete_tasks: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        if not self.reminder_watcher.is_running():
            self.reminder_watcher.start()

    def cog_unload(self) -> None:
        self.reminder_watcher.cancel()
        for task in tuple(self._delete_tasks):
            task.cancel()

    async def _policy(self, guild_id: int, name: str) -> dict[str, Any]:
        return (await get_command_policies(int(guild_id))).get(name, {})

    async def _reply(
        self, interaction: discord.Interaction, name: str, title: str, text: str,
        *, category: str = "log_member", color: int = 0x5865F2,
        fields: list[tuple[str, str, bool]] | None = None, ephemeral: bool = False,
        view: discord.ui.View | None = None, image_url: str | None = None,
    ):
        policy = await self._policy(interaction.guild.id, name)
        values = {"user": getattr(interaction.user, "mention", ""), "guild": interaction.guild.name}
        try:
            text = str(policy.get("response_template") or "").format_map({**values, "text": text}) if policy.get("response_template") else text
        except (KeyError, ValueError, IndexError):
            pass
        embed = discord.Embed(title=title, description=_safe(text, 4000), color=color, timestamp=discord.utils.utcnow())
        if image_url:
            embed.set_image(url=str(image_url))
        for field_name, value, inline in fields or []:
            embed.add_field(name=_safe(field_name, 256), value=_safe(value, 1024), inline=inline)
        payload: dict[str, Any] = {"content": f"**{title}**\n{text}" if policy.get("response_style") == "compact" else None}
        if payload["content"] is None:
            payload.pop("content")
            payload["embed"] = embed
        payload["ephemeral"] = ephemeral or policy.get("response_style") == "silent"
        message = None
        try:
            if interaction.response.is_done():
                message = await interaction.followup.send(wait=True, allowed_mentions=discord.AllowedMentions.none(), view=view, **payload)
            else:
                await interaction.response.send_message(allowed_mentions=discord.AllowedMentions.none(), view=view, **payload)
                with contextlib.suppress(discord.HTTPException, discord.NotFound):
                    message = await interaction.original_response()
        except (discord.Forbidden, discord.HTTPException, discord.NotFound, discord.InteractionResponded):
            logger.warning("Could not respond to /%s", name, exc_info=True)
        delay = int(policy.get("auto_delete_seconds") or 0)
        if message is not None and delay > 0 and not payload.get("ephemeral"):
            async def remove() -> None:
                await asyncio.sleep(delay)
                with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    await message.delete()
            task = asyncio.create_task(remove())
            self._delete_tasks.add(task)
            task.add_done_callback(self._delete_tasks.discard)
        analytics = self.bot.get_cog("Analytics")
        if analytics and interaction.guild:
            try:
                await analytics._log(interaction.guild, category, title, text, author=interaction.user, fields=fields or [], color=color)
            except (discord.Forbidden, discord.HTTPException):
                logger.debug("Audit route unavailable for %s", name, exc_info=True)
        return message

    async def _error(self, interaction: discord.Interaction, name: str, text: str):
        return await self._reply(interaction, name, "تعذر تنفيذ الأمر", text, category="log_violations", color=0xEF4444, ephemeral=True)

    @tasks.loop(seconds=30)
    async def reminder_watcher(self):
        for row in await get_due_user_reminders():
            guild = self.bot.get_guild(int(row["guild_id"]))
            channel = guild.get_channel(int(row["channel_id"])) if guild else None
            content = f"<@{int(row['user_id'])}> ⏰ {_safe(row['reminder_text'], 1800)}"
            delivered = False
            try:
                if channel and hasattr(channel, "send"):
                    await channel.send(content, allowed_mentions=discord.AllowedMentions(users=True))
                    delivered = True
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                logger.info("Reminder %s channel delivery failed; trying DM", row["id"])
            if not delivered:
                try:
                    user = self.bot.get_user(int(row["user_id"])) or await self.bot.fetch_user(int(row["user_id"]))
                    await user.send(content, allowed_mentions=discord.AllowedMentions(users=True))
                    delivered = True
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    logger.info("Reminder %s could not be delivered yet", row["id"])
            if delivered:
                await delete_reminder(int(row["id"]))

    @reminder_watcher.before_loop
    async def before_reminder_watcher(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild and not getattr(message.author, "bot", False):
            LAST_DELETED_MESSAGE[message.channel.id] = {
                "author": message.author,
                "content": message.content,
                "timestamp": getattr(message, "created_at", discord.utils.utcnow()),
                "url": getattr(message, "jump_url", ""),
                "attachments": [a.url for a in message.attachments],
            }

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.guild and not getattr(before.author, "bot", False) and before.content != after.content:
            LAST_EDITED_MESSAGE[before.channel.id] = {
                "author": before.author,
                "before": before.content,
                "after": after.content,
                "timestamp": getattr(after, "edited_at", None) or discord.utils.utcnow(),
                "url": getattr(after, "jump_url", ""),
            }

    @app_commands.command(name="avatar", description="عرض صور العضو")
    @app_commands.check(tools_policy_check)
    async def avatar(self, interaction: discord.Interaction, member: discord.Member | None = None):
        user = member or interaction.user
        url = str(user.display_avatar.url)
        embed = discord.Embed(title=f"🖼️ صورة {user.display_name}", color=0x5865F2)
        embed.set_image(url=url)
        view = discord.ui.View(timeout=120)
        view.add_item(discord.ui.Button(label="PNG", style=discord.ButtonStyle.link, url=str(user.display_avatar.with_format("png").url)))
        view.add_item(discord.ui.Button(label="GIF", style=discord.ButtonStyle.link, url=str(user.display_avatar.with_format("gif").url)))
        return await self._reply(interaction, "avatar", embed.title, "اختر صيغة التحميل من الأزرار.", category="log_member", view=view, image_url=url)

    @app_commands.command(name="banner", description="عرض بانر العضو")
    @app_commands.check(tools_policy_check)
    async def banner(self, interaction: discord.Interaction, member: discord.Member | None = None):
        user = member or interaction.user
        try:
            user = await self.bot.fetch_user(user.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "banner", "تعذر جلب بيانات البانر.")
        if not user.banner:
            return await self._error(interaction, "banner", "هذا العضو لا يملك بانراً.")
        return await self._reply(interaction, "banner", "🖼️ بانر العضو", str(user.banner.url), fields=[("الرابط", str(user.banner.url), False)], image_url=str(user.banner.url))

    @app_commands.command(name="userinfo", description="عرض معلومات العضو")
    @app_commands.check(tools_policy_check)
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member | None = None):
        user = member or interaction.user
        roles = ", ".join(r.mention for r in user.roles if not r.is_default()) or "لا توجد"
        return await self._reply(interaction, "userinfo", f"👤 معلومات {user.display_name}", user.mention, fields=[("المعرف", str(user.id), True), ("الحساب", f"<t:{int(user.created_at.timestamp())}:F>", True), ("انضم", f"<t:{int(user.joined_at.timestamp())}:F>" if user.joined_at else "غير معروف", True), ("الرتب", roles, False)])

    @app_commands.command(name="serverinfo", description="عرض معلومات السيرفر")
    @app_commands.check(tools_policy_check)
    async def serverinfo(self, interaction: discord.Interaction):
        guild = interaction.guild
        return await self._reply(interaction, "serverinfo", f"🏰 {guild.name}", guild.description or "بدون وصف", category="log_server", fields=[("المعرف", str(guild.id), True), ("الأعضاء", str(guild.member_count or len(guild.members)), True), ("القنوات", str(len(guild.channels)), True), ("المالك", f"<@{guild.owner_id}>", True), ("الإنشاء", f"<t:{int(guild.created_at.timestamp())}:F>", False)])

    @app_commands.command(name="roleinfo", description="عرض معلومات رتبة")
    @app_commands.check(tools_policy_check)
    async def roleinfo(self, interaction: discord.Interaction, role: discord.Role):
        return await self._reply(interaction, "roleinfo", f"🎭 {role.name}", role.mention, category="log_roles", fields=[("المعرف", str(role.id), True), ("الأعضاء", str(len(role.members)), True), ("اللون", str(role.color), True), ("الموضع", str(role.position), True)])

    @app_commands.command(name="ping", description="قياس زمن استجابة البوت")
    @app_commands.check(tools_policy_check)
    async def ping(self, interaction: discord.Interaction):
        return await self._reply(interaction, "ping", "🏓 Pong", f"`{round(self.bot.latency * 1000)}ms`", category="log_server")

    @app_commands.command(name="serverheader", description="عرض صور السيرفر")
    @app_commands.check(tools_policy_check)
    async def serverheader(self, interaction: discord.Interaction):
        guild = interaction.guild
        urls = [str(x.url) for x in (guild.banner, guild.splash) if x]
        return await self._reply(interaction, "serverheader", "🖼️ صور السيرفر", "\n".join(urls) or "لا توجد صورة بانر أو Splash.", category="log_server")

    @app_commands.command(name="roles", description="قائمة رتب السيرفر")
    @app_commands.check(tools_policy_check)
    async def roles(self, interaction: discord.Interaction):
        text = "\n".join(f"`{r.position}` {r.mention} • {len(r.members)}" for r in reversed(interaction.guild.roles) if not r.is_default())[:3800] or "لا توجد رتب."
        return await self._reply(interaction, "roles", "🎭 رتب السيرفر", text, category="log_roles")

    @app_commands.command(name="emojis", description="قائمة إيموجيات السيرفر")
    @app_commands.check(tools_policy_check)
    async def emojis(self, interaction: discord.Interaction):
        text = " ".join(str(e) for e in interaction.guild.emojis)[:3900] or "لا توجد إيموجيات."
        return await self._reply(interaction, "emojis", "😀 إيموجيات السيرفر", text, category="log_server")

    @app_commands.command(name="joinposition", description="ترتيب انضمام عضو")
    @app_commands.check(tools_policy_check)
    async def joinposition(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        ordered = sorted((m for m in interaction.guild.members if m.joined_at), key=lambda m: m.joined_at)
        pos = next((i for i, m in enumerate(ordered, 1) if m.id == target.id), 0)
        return await self._reply(interaction, "joinposition", "📅 ترتيب الانضمام", f"{target.mention}: **#{pos}** من **{len(ordered)}**")

    @app_commands.command(name="mutual", description="السيرفرات المشتركة مع عضو")
    @app_commands.check(tools_policy_check)
    async def mutual(self, interaction: discord.Interaction, member: discord.User):
        names = [g.name for g in self.bot.guilds if g.get_member(member.id)]
        return await self._reply(interaction, "mutual", "🤝 السيرفرات المشتركة", "\n".join(names)[:3900] or "لا توجد سيرفرات مشتركة.")

    @app_commands.command(name="whois", description="تحليل أمان عضو")
    @app_commands.check(tools_policy_check)
    async def whois(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        flags = ["Bot" if target.bot else "User", "Timeout" if getattr(target, "communication_disabled_until", None) else "Active"]
        return await self._reply(interaction, "whois", f"🔎 Whois {target}", " • ".join(flags), fields=[("الحساب", f"<t:{int(target.created_at.timestamp())}:R>", True), ("أعلى رتبة", target.top_role.mention, True), ("صلاحيات إدارية", "نعم" if target.guild_permissions.administrator else "لا", True)])

    @app_commands.command(name="channelinfo", description="عرض معلومات قناة")
    @app_commands.check(tools_policy_check)
    async def channelinfo(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel | None = None):
        ch = channel or interaction.channel
        return await self._reply(interaction, "channelinfo", f"📁 #{ch.name}", str(ch.mention), category="log_channel", fields=[("المعرف", str(ch.id), True), ("النوع", str(ch.type), True), ("الموضع", str(getattr(ch, "position", "—")), True), ("الفئة", getattr(getattr(ch, "category", None), "name", "بدون"), True)])

    @app_commands.command(name="rolemembers", description="عرض أعضاء رتبة")
    @app_commands.check(tools_policy_check)
    async def rolemembers(self, interaction: discord.Interaction, role: discord.Role):
        return await self._reply(interaction, "rolemembers", f"👥 أعضاء {role.name}", ", ".join(m.mention for m in role.members)[:3900] or "لا يوجد أعضاء.", category="log_roles")

    @app_commands.command(name="snipe", description="عرض آخر رسالة محذوفة")
    @app_commands.check(tools_policy_check)
    async def snipe(self, interaction: discord.Interaction):
        item = LAST_DELETED_MESSAGE.get(interaction.channel.id)
        if not item:
            return await self._reply(interaction, "snipe", "🕵️ آخر رسالة محذوفة", "لا توجد رسالة محفوظة.", category="log_message")
        attachments = "\n".join(item.get("attachments") or [])
        text = f"{item['author'].mention}: {_safe(item['content'], 1600)}\n<t:{int(item['timestamp'].timestamp())}:R>"
        if attachments:
            text += f"\nالمرفقات:\n{_safe(attachments, 900)}"
        return await self._reply(interaction, "snipe", "🕵️ آخر رسالة محذوفة", text, category="log_message")

    @app_commands.command(name="editsnipe", description="عرض آخر تعديل")
    @app_commands.check(tools_policy_check)
    async def editsnipe(self, interaction: discord.Interaction):
        item = LAST_EDITED_MESSAGE.get(interaction.channel.id)
        text = f"{item['author'].mention}\nقبل: {_safe(item['before'], 900)}\nبعد: {_safe(item['after'], 900)}" if item else "لا يوجد تعديل محفوظ."
        return await self._reply(interaction, "editsnipe", "✏️ آخر تعديل", text, category="log_message")

    @app_commands.command(name="firstmsg", description="عرض أول رسالة في القناة")
    @app_commands.check(tools_policy_check)
    async def firstmsg(self, interaction: discord.Interaction):
        try:
            msg = await interaction.channel.history(limit=1, oldest_first=True).__anext__()
        except (StopAsyncIteration, discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "firstmsg", "تعذر العثور على أول رسالة.")
        return await self._reply(interaction, "firstmsg", "📜 أول رسالة", f"[فتح الرسالة]({msg.jump_url})\n{_safe(msg.content, 1800)}", category="log_message")

    async def _download(self, url: str) -> bytes:
        session = getattr(self.bot, "session", None)
        if session is None:
            raise RuntimeError("bot.session is not configured")
        async with session.get(url) as response:
            if response.status != 200:
                raise discord.HTTPException(response, f"download failed: {response.status}")
            return await response.read()

    @app_commands.command(name="steal_emoji", description="نسخ إيموجي إلى السيرفر")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_emojis=True)
    async def steal_emoji(self, interaction: discord.Interaction, emoji: str, name: str | None = None):
        match = _EMOJI_URL.search(emoji)
        if not match:
            return await self._error(interaction, "steal_emoji", "أرسل منشن إيموجي صالحاً.")
        url = f"https://cdn.discordapp.com/emojis/{match.group(2)}.{'gif' if emoji.startswith('<a:') else 'png'}"
        try:
            created = await interaction.guild.create_custom_emoji(name=(name or match.group(1))[:32], image=await self._download(url), reason=f"steal_emoji by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException, RuntimeError):
            return await self._error(interaction, "steal_emoji", "تعذر تحميل أو إنشاء الإيموجي.")
        return await self._reply(interaction, "steal_emoji", "✅ تم نسخ الإيموجي", str(created), category="log_roles")

    @app_commands.command(name="enlarge_emoji", description="تكبير إيموجي")
    @app_commands.check(tools_policy_check)
    async def enlarge_emoji(self, interaction: discord.Interaction, emoji: str):
        match = _EMOJI_URL.search(emoji)
        if not match:
            return await self._error(interaction, "enlarge_emoji", "أرسل منشن إيموجي صالحاً.")
        ext = "gif" if emoji.startswith("<a:") else "png"
        return await self._reply(interaction, "enlarge_emoji", "🔍 الإيموجي", f"https://cdn.discordapp.com/emojis/{match.group(2)}.{ext}?size=2048")

    @app_commands.command(name="remind", description="إنشاء تذكير دائم")
    @app_commands.check(tools_policy_check)
    async def remind(self, interaction: discord.Interaction, delay: str, text: str):
        delta = _duration(delay)
        if delta is None:
            return await self._error(interaction, "remind", "استخدم مدة مثل 10m أو 2h أو 1d.")
        due = discord.utils.utcnow() + delta
        reminder_id = await add_reminder(
            interaction.guild.id,
            interaction.user.id,
            interaction.channel.id,
            text,
            due.strftime("%Y-%m-%d %H:%M:%S"),
        )
        return await self._reply(interaction, "remind", "⏰ تم حفظ التذكير", f"سيظهر <t:{int(due.timestamp())}:R> (رقم `{reminder_id}`).")

    @app_commands.command(name="countdown", description="عرض عد تنازلي")
    @app_commands.check(tools_policy_check)
    async def countdown(self, interaction: discord.Interaction, delay: str):
        delta = _duration(delay)
        if delta is None:
            return await self._error(interaction, "countdown", "استخدم مدة مثل 30s أو 5m.")
        at = discord.utils.utcnow() + delta
        return await self._reply(interaction, "countdown", "⏳ عد تنازلي", f"ينتهي <t:{int(at.timestamp())}:R> — <t:{int(at.timestamp())}:T>")

    @app_commands.command(name="color", description="تحليل لون")
    @app_commands.check(tools_policy_check)
    async def color(self, interaction: discord.Interaction, value: str):
        match = _HEX.fullmatch(value.strip())
        if not match:
            return await self._error(interaction, "color", "أرسل اللون بصيغة #RRGGBB.")
        number = int(match.group(1), 16)
        return await self._reply(interaction, "color", "🎨 اللون", f"#{match.group(1).upper()}", color=number, fields=[("Decimal", str(number), True)])

    @app_commands.command(name="encode", description="ترميز Base64")
    @app_commands.check(tools_policy_check)
    async def encode(self, interaction: discord.Interaction, text: str):
        return await self._reply(interaction, "encode", "🔐 Base64", base64.b64encode(text.encode()).decode(), category="log_server")

    @app_commands.command(name="decode", description="فك ترميز Base64")
    @app_commands.check(tools_policy_check)
    async def decode(self, interaction: discord.Interaction, text: str):
        try:
            decoded = base64.b64decode(text.encode(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return await self._error(interaction, "decode", "النص ليس Base64 صالحاً.")
        return await self._reply(interaction, "decode", "🔓 Base64", decoded, category="log_server")

    @app_commands.command(name="quote", description="اقتباس رسالة من رابط")
    @app_commands.check(tools_policy_check)
    async def quote(self, interaction: discord.Interaction, url: str):
        match = re.search(r"/channels/(\d+)/(\d+)/(\d+)", url)
        if not match or int(match.group(1)) != interaction.guild.id:
            return await self._error(interaction, "quote", "أرسل رابط رسالة من هذا السيرفر.")
        channel = interaction.guild.get_channel(int(match.group(2)))
        try:
            msg = await channel.fetch_message(int(match.group(3)))
        except (AttributeError, discord.Forbidden, discord.NotFound, discord.HTTPException):
            return await self._error(interaction, "quote", "تعذر جلب الرسالة.")
        return await self._reply(interaction, "quote", "💬 اقتباس", f"{msg.author.mention}: {_safe(msg.content, 1800)}\n[الرسالة]({msg.jump_url})", category="log_message")

    @app_commands.command(name="timestamp", description="تحويل وقت إلى Discord Timestamp")
    @app_commands.check(tools_policy_check)
    async def timestamp(self, interaction: discord.Interaction, value: str):
        try:
            instant = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            try:
                instant = dt.datetime.fromtimestamp(int(value), tz=dt.timezone.utc)
            except (ValueError, OverflowError):
                return await self._error(interaction, "timestamp", "استخدم ISO-8601 أو Unix timestamp.")
        return await self._reply(interaction, "timestamp", "🕒 Discord Timestamp", f"`<t:{int(instant.timestamp())}:F>`\n<t:{int(instant.timestamp())}:R>")

    @app_commands.command(name="steal_sticker", description="نسخ ملصق من رابط")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_emojis=True)
    async def steal_sticker(self, interaction: discord.Interaction, url: str, name: str):
        if urlparse(url).scheme not in {"http", "https"}:
            return await self._error(interaction, "steal_sticker", "أرسل رابط HTTPS صالحاً.")
        try:
            data = await self._download(url)
            sticker = await interaction.guild.create_sticker(name=name[:30], description="نسخة ملصق", emoji="🙂", file=discord.File(io.BytesIO(data), filename="sticker.png"))
        except (discord.Forbidden, discord.HTTPException, RuntimeError):
            return await self._error(interaction, "steal_sticker", "تعذر تحميل أو إنشاء الملصق.")
        return await self._reply(interaction, "steal_sticker", "✅ تم نسخ الملصق", str(sticker), category="log_roles")

    async def _channel_action(self, interaction: discord.Interaction, name: str, action: str, channel: discord.abc.GuildChannel | None = None, value: str | None = None):
        target = channel or interaction.channel
        try:
            if action == "delete":
                await target.delete(reason=f"{name} by {interaction.user}")
                return await self._reply(interaction, name, "🗑️ حذف قناة", f"تم حذف #{target.name}.", category="log_channel")
            if action == "clone":
                result = await target.clone(reason=f"{name} by {interaction.user}")
            elif action == "move":
                await target.edit(position=max(0, int(value or 0)))
                result = target
            elif action == "topic":
                await target.edit(topic=(value or "")[:1024])
                result = target
            else:
                await target.edit(name=(value or target.name)[:100])
                result = target
        except (ValueError, discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, name, "تعذر تعديل القناة؛ تحقق من الصلاحيات.")
        return await self._reply(interaction, name, "📁 تم تحديث القناة", getattr(result, "mention", result.name), category="log_channel")

    @app_commands.command(name="create_channel", description="إنشاء قناة نصية")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def create_channel(self, interaction: discord.Interaction, name: str, category: discord.CategoryChannel | None = None):
        try:
            channel = await interaction.guild.create_text_channel(name[:100], category=category, reason=f"create_channel by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "create_channel", "تعذر إنشاء القناة.")
        return await self._reply(interaction, "create_channel", "✅ إنشاء قناة", channel.mention, category="log_channel")

    @app_commands.command(name="delete_channel", description="حذف قناة")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def delete_channel(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel | None = None, confirm: bool = False):
        if not confirm:
            return await self._reply(interaction, "delete_channel", "⚠️ تأكيد حذف القناة", "هذا الإجراء نهائي. أعد الأمر مع `confirm: True` للمتابعة.", category="log_channel", ephemeral=True)
        return await self._channel_action(interaction, "delete_channel", "delete", channel)

    @app_commands.command(name="rename_channel", description="إعادة تسمية قناة")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def rename_channel(self, interaction: discord.Interaction, name: str, channel: discord.abc.GuildChannel | None = None):
        return await self._channel_action(interaction, "rename_channel", "rename", channel, name)

    @app_commands.command(name="move_channel", description="تغيير موضع قناة")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def move_channel(self, interaction: discord.Interaction, position: app_commands.Range[int, 0, 500], channel: discord.abc.GuildChannel | None = None):
        return await self._channel_action(interaction, "move_channel", "move", channel, str(position))

    @app_commands.command(name="set_topic", description="تعيين موضوع قناة")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def set_topic(self, interaction: discord.Interaction, topic: str, channel: discord.TextChannel | None = None):
        return await self._channel_action(interaction, "set_topic", "topic", channel, topic)

    @app_commands.command(name="clone_channel", description="استنساخ قناة")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def clone_channel(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel | None = None):
        return await self._channel_action(interaction, "clone_channel", "clone", channel)

    @app_commands.command(name="create_voice", description="إنشاء قناة صوتية")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def create_voice(self, interaction: discord.Interaction, name: str, category: discord.CategoryChannel | None = None):
        try:
            channel = await interaction.guild.create_voice_channel(name[:100], category=category, reason=f"create_voice by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "create_voice", "تعذر إنشاء القناة الصوتية.")
        return await self._reply(interaction, "create_voice", "✅ إنشاء قناة صوتية", channel.mention, category="log_channel")

    @app_commands.command(name="delete_voice", description="حذف قناة صوتية")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def delete_voice(self, interaction: discord.Interaction, channel: discord.VoiceChannel | None = None, confirm: bool = False):
        if not confirm:
            return await self._reply(interaction, "delete_voice", "⚠️ تأكيد حذف القناة الصوتية", "هذا الإجراء نهائي. أعد الأمر مع `confirm: True` للمتابعة.", category="log_channel", ephemeral=True)
        return await self._channel_action(interaction, "delete_voice", "delete", channel)

    @app_commands.command(name="rename_voice", description="إعادة تسمية قناة صوتية")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def rename_voice(self, interaction: discord.Interaction, name: str, channel: discord.VoiceChannel | None = None):
        return await self._channel_action(interaction, "rename_voice", "rename", channel, name)

    @app_commands.command(name="move_voice", description="تغيير موضع قناة صوتية")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def move_voice(self, interaction: discord.Interaction, position: app_commands.Range[int, 0, 500], channel: discord.VoiceChannel | None = None):
        return await self._channel_action(interaction, "move_voice", "move", channel, str(position))

    @app_commands.command(name="mod_stats", description="إحصاءات التحذيرات للمشرف")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(view_audit_log=True)
    async def mod_stats(self, interaction: discord.Interaction, moderator: discord.User | None = None):
        target = moderator or interaction.user
        async with connect() as db:
            async with db.execute("SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND moderator_id = ?", (interaction.guild.id, target.id)) as cur:
                count = (await cur.fetchone())[0]
        return await self._reply(interaction, "mod_stats", "📊 إحصاءات المشرف", f"{target.mention}: **{count}** تحذيراً", category="log_sanctions")

    @app_commands.command(name="undo_action", description="عكس آخر تحذير أو تايم أوت")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def undo_action(self, interaction: discord.Interaction, member: discord.Member):
        try:
            await member.timeout(None, reason=f"undo_action by {interaction.user}")
            async with connect() as db:
                await db.execute("DELETE FROM warnings WHERE id = (SELECT id FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1)", (interaction.guild.id, member.id))
                await db.commit()
        except (discord.Forbidden, discord.HTTPException):
            return await self._error(interaction, "undo_action", "تعذر عكس آخر إجراء.")
        return await self._reply(interaction, "undo_action", "↩️ تم العكس", f"تم رفع التايم أوت وحذف آخر تحذير لـ {member.mention}.", category="log_sanctions")

    @app_commands.command(name="security_report", description="تقرير أمان السيرفر")
    @app_commands.check(tools_policy_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def security_report(self, interaction: discord.Interaction):
        admins = [r.mention for r in interaction.guild.roles if r.permissions.administrator]
        settings = await get_guild_settings(interaction.guild.id)
        configured = settings.get("settings", {}) if isinstance(settings, dict) else {}
        return await self._reply(interaction, "security_report", "🛡️ تقرير الأمان", f"Anti-nuke: `{bool(configured.get('anti_nuke', True))}`", category="log_server", fields=[("رتب Administrator", ", ".join(admins)[:1000] or "لا توجد", False), ("التحقق", str(interaction.guild.verification_level), True), ("2FA", "مفعل" if interaction.guild.mfa_level else "غير مفعل", True)])


async def setup(bot: commands.Bot):
    await bot.add_cog(ToolsChannelsCog(bot))
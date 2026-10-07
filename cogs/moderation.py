import asyncio
import datetime
import logging
import re
import time
from collections import defaultdict
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    SETTINGS_DEFAULTS,
    add_member_warning,
    add_warning,
    delete_warning,
    get_guild_settings,
    get_recent_warnings,
    get_warning,
    get_warnings,
)


logger = logging.getLogger("ModerationCog")

# One compiled detector keeps message handling cheap. The named groups let
# anti_invites and anti_links be toggled independently.
LINK_RE = re.compile(
    r"(?ix)"
    r"(?P<invite>(?:https?://)?(?:www\.)?"
    r"(?:discord\.gg|discord(?:app)?\.com/invite)/[a-z0-9-]+)"
    r"|(?P<suspicious>(?:https?://)?(?:www\.)?"
    r"(?:bit\.ly|tinyurl\.com|t\.co|is\.gd|ow\.ly|rb\.gy|shorturl\.at|"
    r"rebrand\.ly|cutt\.ly|tiny\.cc|buff\.ly|grabify\.link|iplogger\.(?:org|com)|"
    r"2no\.co|yip\.sx|urlz\.fr)/[^\s<>()]+)",
)

SPAM_WINDOW = 4.0
SPAM_LIMIT = 5
SPAM_TIMEOUT_MINUTES = 10
MENTION_LIMIT = 3
MENTION_TARGET_LIMIT = 3
MENTION_TARGET_WINDOW = 10.0
MENTION_TIMEOUT_MINUTES = 5
INFRACTION_LIMIT = 100


class Moderation(commands.Cog):
    """Dynamic Auto-Mod engine and the bot's manual moderation commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.spam: defaultdict[tuple[int, int], list[float]] = defaultdict(list)
        self.target_mentions: defaultdict[tuple[int, int, int], list[float]] = defaultdict(list)
        self._word_patterns: dict[tuple[str, ...], Optional[re.Pattern]] = {}
        self._last_rate_prune = time.monotonic()

    async def moderation_settings(self, guild_id: int) -> dict[str, Any]:
        """Read Auto-Mod values through database.py's in-memory settings cache."""
        try:
            snapshot = await get_guild_settings(int(guild_id))
            values = snapshot["settings"]
            words = values.get("banned_words_list", [])
            return {
                "anti_invites": bool(values.get("anti_invites", True)),
                "anti_links": bool(values.get("anti_links", True)),
                "anti_spam": bool(values.get("anti_spam", True)),
                "anti_mass_mention": bool(values.get("anti_mass_mention", True)),
                "anti_spam_max_messages": int(values.get("anti_spam_max_messages", SPAM_LIMIT)),
                "anti_spam_time_window_seconds": int(values.get("anti_spam_time_window_seconds", SPAM_WINDOW)),
                "anti_spam_action": values.get("anti_spam_action", "timeout"),
                "anti_spam_timeout_duration_minutes": int(values.get("anti_spam_timeout_duration_minutes", SPAM_TIMEOUT_MINUTES)),
                "anti_spam_ignored_role_ids": values.get("anti_spam_ignored_role_ids", []),
                "anti_spam_ignored_channel_ids": values.get("anti_spam_ignored_channel_ids", []),
                "anti_mention_max_per_message": int(values.get("anti_mention_max_per_message", MENTION_LIMIT)),
                "anti_mention_target_enabled": bool(values.get("anti_mention_target_enabled", True)),
                "anti_mention_target_max_repeats": int(values.get("anti_mention_target_max_repeats", MENTION_TARGET_LIMIT)),
                "anti_mention_target_time_window_seconds": int(values.get("anti_mention_target_time_window_seconds", MENTION_TARGET_WINDOW)),
                "anti_mention_action": values.get("anti_mention_action", "timeout"),
                "anti_mention_timeout_duration_minutes": int(values.get("anti_mention_timeout_duration_minutes", MENTION_TIMEOUT_MINUTES)),
                "anti_mention_ignored_role_ids": values.get("anti_mention_ignored_role_ids", []),
                "anti_mention_ignored_channel_ids": values.get("anti_mention_ignored_channel_ids", []),
                "banned_words_list": words if isinstance(words, list) else [],
                "log_channel_id": values.get("log_channel_id"),
            }
        except Exception:
            logger.exception("[AUTOMOD_CONFIG] تعذر قراءة إعدادات السيرفر %s", guild_id)
            return {
                "anti_invites": True,
                "anti_links": True,
                "anti_spam": True,
                "anti_mass_mention": True,
                "anti_spam_max_messages": SPAM_LIMIT,
                "anti_spam_time_window_seconds": SPAM_WINDOW,
                "anti_spam_action": "timeout",
                "anti_spam_timeout_duration_minutes": SPAM_TIMEOUT_MINUTES,
                "anti_spam_ignored_role_ids": [],
                "anti_spam_ignored_channel_ids": [],
                "anti_mention_max_per_message": MENTION_LIMIT,
                "anti_mention_target_enabled": True,
                "anti_mention_target_max_repeats": MENTION_TARGET_LIMIT,
                "anti_mention_target_time_window_seconds": MENTION_TARGET_WINDOW,
                "anti_mention_action": "timeout",
                "anti_mention_timeout_duration_minutes": MENTION_TIMEOUT_MINUTES,
                "anti_mention_ignored_role_ids": [],
                "anti_mention_ignored_channel_ids": [],
                "banned_words_list": [],
                "log_channel_id": None,
            }

    async def send_log(self, guild: discord.Guild, embed: discord.Embed):
        config = await self.moderation_settings(guild.id)
        channel = (
            guild.get_channel(int(config["log_channel_id"]))
            if config.get("log_channel_id")
            else None
        )
        if channel is None:
            channel = (
                discord.utils.get(guild.text_channels, name="mod-logs")
                or discord.utils.get(guild.text_channels, name="سجل-الإدارة")
            )
        if channel is None or guild.me is None:
            return
        try:
            if channel.permissions_for(guild.me).send_messages:
                await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[AUTOMOD_LOG] تعذر إرسال سجل في السيرفر %s", guild.id)

    def _banned_word_pattern(self, words: list[str]) -> Optional[re.Pattern]:
        normalized = tuple(
            sorted(
                {
                    word.strip().casefold()
                    for word in words
                    if isinstance(word, str) and word.strip()
                }
            )
        )
        if not normalized:
            return None
        if normalized not in self._word_patterns:
            if len(self._word_patterns) >= 64:
                self._word_patterns.pop(next(iter(self._word_patterns)))
            self._word_patterns[normalized] = re.compile(
                "|".join(re.escape(word) for word in normalized),
                re.IGNORECASE,
            )
        return self._word_patterns[normalized]

    def _rate_triggered(
        self,
        bucket: defaultdict,
        key: tuple,
        limit: int,
        window_seconds: float,
    ) -> bool:
        now = time.monotonic()
        timestamps = [
            stamp for stamp in bucket[key] if now - stamp < max(1.0, float(window_seconds))
        ]
        timestamps.append(now)
        bucket[key] = timestamps
        if now - self._last_rate_prune > 30:
            self._last_rate_prune = now
            for stale_key, values in list(self.spam.items()):
                if not values or now - values[-1] >= SPAM_WINDOW:
                    self.spam.pop(stale_key, None)
            for stale_key, values in list(self.target_mentions.items()):
                if not values or now - values[-1] >= MENTION_TARGET_WINDOW:
                    self.target_mentions.pop(stale_key, None)
        if len(timestamps) > max(1, int(limit)):
            bucket.pop(key, None)
            return True
        return False

    def _spam_triggered(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int = SPAM_LIMIT,
        window_seconds: float = SPAM_WINDOW,
    ) -> bool:
        return self._rate_triggered(
            self.spam,
            (int(guild_id), int(user_id)),
            limit,
            window_seconds,
        )

    def _target_mention_triggered(
        self,
        guild_id: int,
        user_id: int,
        target_id: int,
        *,
        limit: int = MENTION_TARGET_LIMIT,
        window_seconds: float = MENTION_TARGET_WINDOW,
    ) -> bool:
        return self._rate_triggered(
            self.target_mentions,
            (int(guild_id), int(user_id), int(target_id)),
            limit,
            window_seconds,
        )

    @staticmethod
    def _is_exempt(msg: discord.Message, config: dict[str, Any], prefix: str) -> bool:
        ignored_channels = {str(item) for item in config.get(f"{prefix}_ignored_channel_ids", [])}
        if str(getattr(msg.channel, "id", "")) in ignored_channels:
            return True
        ignored_roles = {str(item) for item in config.get(f"{prefix}_ignored_role_ids", [])}
        return any(
            str(getattr(role, "id", "")) in ignored_roles
            for role in getattr(msg.author, "roles", ())
        )

    async def _resolve_member(
        self,
        guild: discord.Guild,
        user_id: int,
    ) -> Optional[discord.Member]:
        member = guild.get_member(int(user_id))
        if member is not None:
            return member
        try:
            return await guild.fetch_member(int(user_id))
        except discord.NotFound:
            return None
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            logger.warning("[AUTOMOD_MEMBER] تعذر جلب العضو %s", user_id)
            return None

    async def _apply_violation(
        self,
        msg: discord.Message,
        reason: str,
        *,
        timeout_minutes: int = 0,
        action: str = "timeout",
    ) -> None:
        guild, member = msg.guild, msg.author
        try:
            await msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.debug("[AUTOMOD] تعذر حذف الرسالة %s", msg.id, exc_info=True)

        moderator_id = getattr(getattr(self.bot, "user", None), "id", 0)
        warning_count = None
        try:
            warning_count = await add_warning(
                member.id,
                guild.id,
                moderator_id,
                reason,
            )
        except Exception:
            logger.exception("[AUTOMOD] فشل تسجيل الإنذار للسيرفر %s", guild.id)

        applied_action = "warn_delete"
        timed_out = False
        if action == "timeout" and timeout_minutes:
            try:
                await member.timeout(
                    discord.utils.utcnow()
                    + datetime.timedelta(minutes=timeout_minutes),
                    reason=reason,
                )
                timed_out = True
                applied_action = "timeout"
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "[AUTOMOD] فشل تطبيق الكتم على %s في %s",
                    member.id,
                    guild.id,
                    exc_info=True,
                )
        elif action == "kick":
            try:
                await member.kick(reason=reason)
                applied_action = "kick"
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("[AUTOMOD] فشل طرد %s في %s", member.id, guild.id, exc_info=True)
        elif action == "ban":
            try:
                await guild.ban(member, reason=reason, delete_message_seconds=0)
                applied_action = "ban"
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("[AUTOMOD] فشل حظر %s في %s", member.id, guild.id, exc_info=True)

        # No public @everyone alert: it mentions only the offender and deletes
        # itself, keeping the moderation channel quiet.
        try:
            await msg.channel.send(
                f"⚠️ {member.mention} تم حذف رسالتك لمخالفة قواعد السيرفر.",
                delete_after=6,
                silent=True,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("[AUTOMOD] تعذر إرسال التنبيه الصامت", exc_info=True)

        embed = discord.Embed(
            title="🛡️ Auto-Mod / مخالفة محجوبة",
            description=f"{member.mention} في {msg.channel.mention}",
            color=0xE74C3C if timed_out else 0xF1C40F,
        )
        embed.add_field(name="السبب", value=reason, inline=False)
        embed.add_field(
            name="الإجراء",
            value=(
                f"حذف + كتم {timeout_minutes} دقائق"
                if timed_out
                else {
                    "kick": "حذف + طرد",
                    "ban": "حذف + حظر",
                }.get(applied_action, "حذف + تسجيل إنذار")
            ),
            inline=True,
        )
        if warning_count is not None:
            embed.add_field(name="إجمالي الإنذارات", value=str(warning_count), inline=True)
        await self.send_log(guild, embed)
        analytics = self.bot.get_cog("Analytics")
        if analytics:
            await analytics.log_automod(
                guild,
                embed.title or "🛡️ إجراء Auto-Mod",
                embed.description or "تم تسجيل إجراء من نظام Auto-Mod.",
                actor=member,
                fields=[
                    ("📌 السبب", reason, False),
                    ("👤 العضو", f"{member.mention} (`{member.id}`)", True),
                    ("📍 القناة", f"{msg.channel.mention} (`{msg.channel.id}`)", True),
                ],
                color=embed.color.value if embed.color else None,
            )
        logger.info(
            "[AUTOMOD] guild=%s user=%s reason=%s timeout=%s",
            guild.id,
            member.id,
            reason,
            timed_out,
        )

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if (
            msg.author.bot
            or not msg.guild
            or msg.author.guild_permissions.manage_messages
        ):
            return

        config = await self.moderation_settings(msg.guild.id)
        link_match = LINK_RE.search(msg.content or "")
        if link_match:
            if link_match.group("invite") and config["anti_invites"]:
                await self._apply_violation(msg, "نشر رابط دعوة Discord ممنوع")
                return
            if link_match.group("suspicious") and config["anti_links"]:
                await self._apply_violation(msg, "رابط تصيد أو اختصار مشبوه")
                return

        word_pattern = self._banned_word_pattern(config["banned_words_list"])
        if word_pattern and word_pattern.search(msg.content or ""):
            await self._apply_violation(msg, "استخدام كلمة محظورة")
            return

        if config["anti_mass_mention"] and not self._is_exempt(msg, config, "anti_mention"):
            mass_limit = config.get("anti_mention_max_per_message", MENTION_LIMIT)
            mention_action = config.get("anti_mention_action", "timeout")
            mention_timeout = config.get(
                "anti_mention_timeout_duration_minutes",
                MENTION_TIMEOUT_MINUTES,
            )
            if len(msg.mentions) > mass_limit:
                await self._apply_violation(
                    msg,
                    f"منشن جماعي يتجاوز {mass_limit} أعضاء",
                    timeout_minutes=mention_timeout,
                    action=mention_action,
                )
                return
            if config.get("anti_mention_target_enabled", True):
                target_limit = config.get(
                    "anti_mention_target_max_repeats",
                    MENTION_TARGET_LIMIT,
                )
                target_window = config.get(
                    "anti_mention_target_time_window_seconds",
                    MENTION_TARGET_WINDOW,
                )
                for target in msg.mentions:
                    target_id = getattr(target, "id", None)
                    if target_id is None:
                        continue
                    if self._target_mention_triggered(
                        msg.guild.id,
                        msg.author.id,
                        target_id,
                        limit=target_limit,
                        window_seconds=target_window,
                    ):
                        await self._apply_violation(
                            msg,
                            f"تكرار منشن العضو أكثر من {target_limit} مرات خلال {int(target_window)} ثوان",
                            timeout_minutes=mention_timeout,
                            action=mention_action,
                        )
                        return

        if config["anti_spam"] and not self._is_exempt(msg, config, "anti_spam"):
            spam_limit = config.get("anti_spam_max_messages", SPAM_LIMIT)
            spam_window = config.get("anti_spam_time_window_seconds", SPAM_WINDOW)
            spam_action = config.get("anti_spam_action", "timeout")
            spam_timeout = config.get(
                "anti_spam_timeout_duration_minutes",
                SPAM_TIMEOUT_MINUTES,
            )
            if self._spam_triggered(
                msg.guild.id,
                msg.author.id,
                limit=spam_limit,
                window_seconds=spam_window,
            ):
                await self._apply_violation(
                    msg,
                    f"إرسال أكثر من {spam_limit} رسائل خلال {int(spam_window)} ثوان",
                    timeout_minutes=spam_timeout,
                    action=spam_action,
                )
                return

    async def get_recent_infractions(self, guild_id: int) -> list[dict[str, Any]]:
        return await get_recent_warnings(guild_id, INFRACTION_LIMIT)

    async def revoke_warning(self, warning_id: int) -> Optional[dict[str, Any]]:
        warning = await get_warning(warning_id)
        if warning is None:
            return None
        if not await delete_warning(warning["guild_id"], warning_id):
            return None
        return warning

    async def quick_unmute(self, guild_id: int, user_id: int) -> dict[str, Any]:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return {"ok": False, "error": "guild_not_found"}
        member = await self._resolve_member(guild, user_id)
        if member is None:
            return {"ok": False, "error": "member_not_found"}
        try:
            await member.timeout(None, reason="Dashboard quick unmute")
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "[AUTOMOD_UNMUTE] فشل فك الكتم عن %s في %s",
                user_id,
                guild_id,
                exc_info=True,
            )
            return {"ok": False, "error": "permission_denied"}
        embed = discord.Embed(
            title="🔊 فك كتم سريع",
            description=f"تم فك الكتم عن {member.mention} من لوحة التحكم.",
            color=0x2ECC71,
        )
        await self.send_log(guild, embed)
        return {"ok": True, "guild_id": int(guild_id), "user_id": int(user_id)}

    @app_commands.command(name="timeout", description="كتم عضو بالدقائق")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(
        self,
        itx: discord.Interaction,
        member: discord.Member,
        minutes: int,
        reason: str = "غير محدد",
    ):
        if not 1 <= minutes <= 40320:
            return await itx.response.send_message(
                "❌ مدة الكتم يجب أن تكون بين دقيقة و28 يوماً.",
                ephemeral=True,
            )
        if member.top_role >= itx.user.top_role and itx.user.id != itx.guild.owner_id:
            return await itx.response.send_message(
                "❌ لا تملك صلاحية على هذا العضو.",
                ephemeral=True,
            )
        try:
            expires_at = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
            await member.timeout(
                expires_at,
                reason=reason,
            )
            emb = discord.Embed(
                title="🔇 كتم عضو",
                description=f"{member.mention} لمدة {minutes}د | السبب: {reason}",
                color=0xE67E22,
            )
            await itx.response.send_message(embed=emb)
            await self.send_log(itx.guild, emb)
            analytics = self.bot.get_cog("Analytics")
            if analytics:
                await analytics.log_timeout(
                    itx.guild, member, itx.user, minutes, reason,
                    expires_at.strftime("%Y-%m-%d %H:%M UTC"),
                )
        except (discord.Forbidden, discord.HTTPException):
            await itx.response.send_message(
                "❌ فشل الكتم؛ تأكد من صلاحية ورتبة البوت.",
                ephemeral=True,
            )

    @app_commands.command(name="untimeout", description="فك الكتم عن عضو")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, itx: discord.Interaction, member: discord.Member):
        try:
            await member.timeout(None)
            await itx.response.send_message(f"🔊 تم فك الكتم عن {member.mention}.")
        except (discord.Forbidden, discord.HTTPException):
            await itx.response.send_message(
                "❌ فشل الإجراء؛ تحقق من الصلاحيات.",
                ephemeral=True,
            )

    @app_commands.command(name="warn", description="تحذير عضو")
    @app_commands.checks.has_permissions(kick_members=True)
    async def warn(
        self,
        itx: discord.Interaction,
        member: discord.Member,
        reason: str,
    ):
        if (
            member.top_role >= itx.user.top_role
            and itx.user.id != itx.guild.owner_id
        ) or member.bot:
            return await itx.response.send_message(
                "❌ لا يمكن تحذير هذا الحساب.",
                ephemeral=True,
            )
        cnt = await add_warning(member.id, itx.guild.id, itx.user.id, reason)
        # Preserve the legacy warning count/API while mirroring the same
        # action into Step 4's additive administrative warning table.
        try:
            await add_member_warning(itx.guild.id, member.id, itx.user.id, reason)
        except Exception:
            logger.exception("[MODERATION] تعذر مزامنة تحذير Step 4")
        emb = discord.Embed(
            title="⚠️ تحذير",
            description=(
                f"المخالف: {member.mention}\n"
                f"السبب: {reason}\n"
                f"الإجمالي: **{cnt}**"
            ),
            color=0xF1C40F,
        )
        await itx.response.send_message(embed=emb)
        await self.send_log(itx.guild, emb)
        analytics = self.bot.get_cog("Analytics")
        if analytics:
            await analytics.log_warning(itx.guild, member, itx.user, reason, cnt)
        if cnt >= 3:
            try:
                await member.timeout(
                    discord.utils.utcnow() + datetime.timedelta(hours=1),
                    reason="3 تحذيرات",
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("[MODERATION] تعذر تطبيق عقوبة 3 إنذارات")

    @app_commands.command(name="warnings", description="عرض أرشيف تحذيرات عضو")
    @app_commands.checks.has_permissions(kick_members=True)
    async def show_warnings(
        self,
        itx: discord.Interaction,
        member: discord.Member,
    ):
        recs = await get_warnings(member.id, itx.guild.id)
        if not recs:
            return await itx.response.send_message(
                f"✅ سجل {member.mention} نظيف.",
                ephemeral=True,
            )
        emb = discord.Embed(
            title=f"📋 تحذيرات {member.display_name}",
            color=0x992D22,
        )
        for wid, reason, timestamp in recs:
            emb.add_field(
                name=f"#{wid} | {timestamp[:10]}",
                value=reason,
                inline=False,
            )
        await itx.response.send_message(embed=emb, ephemeral=True)

    @app_commands.command(name="unwarn", description="إلغاء تحذير برقم السجل")
    @app_commands.checks.has_permissions(kick_members=True)
    async def unwarn(self, itx: discord.Interaction, warning_id: int):
        warning = await get_warning(warning_id)
        if warning is None or int(warning["guild_id"]) != itx.guild.id:
            return await itx.response.send_message(
                "❌ لم أجد هذا التحذير داخل هذا السيرفر.",
                ephemeral=True,
            )
        if not await delete_warning(itx.guild.id, warning_id):
            return await itx.response.send_message(
                "❌ تعذر إلغاء التحذير.",
                ephemeral=True,
            )
        await itx.response.send_message(
            f"✅ تم إلغاء التحذير `#{warning_id}` عن <@{warning['user_id']}>.",
        )

    @app_commands.command(name="slowmode", description="تعيين وضع التمهل للقناة")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self, itx: discord.Interaction, seconds: int):
        if not 0 <= seconds <= 21600:
            return await itx.response.send_message(
                "❌ قيمة التمهل يجب أن تكون بين 0 و21600 ثانية.",
                ephemeral=True,
            )
        try:
            await itx.channel.edit(
                slowmode_delay=seconds,
                reason=f"Slowmode by {itx.user} ({itx.user.id})",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await itx.response.send_message(
                "❌ تعذر تعديل وضع التمهل. تحقق من صلاحيات البوت.",
                ephemeral=True,
            )
        await itx.response.send_message(
            f"✅ تم ضبط التمهل إلى `{seconds}` ثانية.",
        )

    @app_commands.command(name="clear", description="مسح رسائل بفلترة ذكية")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear(
        self,
        itx: discord.Interaction,
        amount: int,
        member: discord.Member = None,
        bots_only: bool = False,
    ):
        if not 1 <= amount <= 100:
            return await itx.response.send_message(
                "❌ العدد بين 1 و 100.",
                ephemeral=True,
            )
        await itx.response.defer(ephemeral=True)
        message_filter = lambda m: (
            (not member or m.author.id == member.id)
            and (not bots_only or m.author.bot)
        )
        try:
            deleted = await itx.channel.purge(limit=amount, check=message_filter)
            await itx.followup.send(
                f"🧹 تم مسح **{len(deleted)}** رسالة.",
                ephemeral=True,
            )
        except (discord.Forbidden, discord.HTTPException):
            await itx.followup.send(
                "❌ تعذر الحذف، تحقق من الصلاحيات.",
                ephemeral=True,
            )

    @app_commands.command(name="lockdown", description="قفل أو فتح الشات")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lockdown(self, itx: discord.Interaction, lock: bool):
        overwrite = itx.channel.overwrites_for(itx.guild.default_role)
        overwrite.send_messages = False if lock else None
        overwrite.send_messages_in_threads = False if lock else None
        await itx.channel.set_permissions(itx.guild.default_role, overwrite=overwrite)
        await itx.response.send_message(
            embed=discord.Embed(
                title="🔒 أُغلق الروم" if lock else "🔓 فُتح الروم",
                color=0xE74C3C if lock else 0x2ECC71,
            )
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
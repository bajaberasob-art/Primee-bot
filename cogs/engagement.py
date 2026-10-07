import asyncio
import datetime
import logging
import re
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    get_guild_settings,
    get_role_panels,
    get_rules_panels,
    record_invite_use,
    record_rules_agreement,
    save_role_panel,
    save_rules_panel,
    get_self_role_panels,
    save_self_role_panel,
)


logger = logging.getLogger("EngagementCog")

class TicketControl(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="استلام التذكرة (Claim)",
        style=discord.ButtonStyle.secondary,
        emoji="📌",
        custom_id="btn_claim_t",
    )
    async def claim(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        if not itx.user.guild_permissions.manage_channels:
            return await itx.response.send_message(
                "❌ هذا الإجراء متاح للمشرفين فقط.",
                ephemeral=True,
            )
        btn.disabled = True
        btn.label = f"مستلمة بواسطة {itx.user.display_name}"
        await itx.message.edit(view=self)
        await itx.response.send_message(
            f"📌 تم استلام التذكرة من قبل المشرف: {itx.user.mention}"
        )

    @discord.ui.button(
        label="إغلاق التذكرة",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="btn_close_t",
    )
    async def close(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        await itx.response.send_message(
            "⚠️ سيتم إغلاق التذكرة وحذف القناة خلال 5 ثوانٍ..."
        )
        await asyncio.sleep(5)
        try:
            await itx.channel.delete()
        except Exception:
            logger.exception("Failed to delete closed ticket channel")


class TicketLauncher(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="فتح تذكرة دعم",
        style=discord.ButtonStyle.success,
        emoji="📩",
        custom_id="btn_open_t",
    )
    async def open(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        guild = itx.guild
        category = discord.utils.get(guild.categories, name="Tickets")
        if category is None:
            category = await guild.create_category("Tickets")

        # منع العضو من إنشاء أكثر من تذكرة مفتوحة
        ticket_name = f"ticket-{itx.user.name.lower()}"
        if discord.utils.get(category.text_channels, name=ticket_name):
            return await itx.response.send_message(
                "❌ لديك تذكرة مفتوحة بالفعل داخل السيرفر!",
                ephemeral=True,
            )

        permissions = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            itx.user: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                attach_files=True,
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_channels=True,
            ),
        }
        channel = await guild.create_text_channel(
            name=ticket_name,
            category=category,
            overwrites=permissions,
        )
        embed = discord.Embed(
            title=f"🎫 تذكرة الدعم | {itx.user.display_name}",
            description=(
                "أهلاً بك، تفضل بطرح مشكلتك بالتفصيل وسيقوم أحد المشرفين "
                "بمساعدتك قريباً."
            ),
            color=0x2ECC71,
        )
        embed.set_footer(
            text="يمكن للمشرفين استلام التذكرة أو إغلاقها عبر الأزرار أدناه"
        )
        await channel.send(
            f"{itx.user.mention} تم إنشاء تذكرتك بنجاح.",
            embed=embed,
            view=TicketControl(),
        )
        await itx.response.send_message(
            f"✅ تم فتح تذكرتك: {channel.mention}",
            ephemeral=True,
        )


class RoleSelector(discord.ui.Select):
    def __init__(self, roles, role_specs=None):
        specs = {
            int(spec["id"]): spec
            for spec in (role_specs or [])
            if isinstance(spec, dict) and str(spec.get("id", "")).isdigit()
        }
        options = [
            discord.SelectOption(
                label=str(specs.get(role.id, {}).get("label") or role.name)[:100],
                value=str(role.id),
                emoji=specs.get(role.id, {}).get("emoji") or "🏷️",
            )
            for role in roles[:25]
        ]
        super().__init__(
            placeholder="اختر رتبك واهتماماتك...",
            min_values=1,
            max_values=min(len(options), 5),
            options=options,
            custom_id="slct_multi_roles",
        )

    async def callback(self, itx: discord.Interaction):
        added, removed = [], []
        for value in self.values:
            role = itx.guild.get_role(int(value))
            if not role or role >= itx.guild.me.top_role:
                continue
            if role in itx.user.roles:
                await itx.user.remove_roles(role)
                removed.append(role.name)
            else:
                await itx.user.add_roles(role)
                added.append(role.name)

        result = []
        if added:
            result.append(f"➕ مُنحت: **{', '.join(added)}**")
        if removed:
            result.append(f"➖ أُزيلت: **{', '.join(removed)}**")
        await itx.response.send_message(
            "\n".join(result) if result else "لم يتم تغيير أي رتبة.",
            ephemeral=True,
        )


class RulesAgreementView(discord.ui.View):
    """Persistent one-click rules gate registered again after every restart."""

    def __init__(self, engagement=None):
        super().__init__(timeout=None)
        self.engagement = engagement

    @discord.ui.button(
        label="أوافق على القوانين",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="btn_rules_agree_v1",
    )
    async def agree(self, itx: discord.Interaction, button: discord.ui.Button):
        engagement = self.engagement or itx.client.get_cog("Engagement")
        if engagement is None:
            return await itx.response.send_message(
                "⚠️ نظام التحقق غير متاح مؤقتاً.",
                ephemeral=True,
            )
        await itx.response.send_modal(RulesAgreementModal(engagement))


class RulesAgreementModal(discord.ui.Modal, title="تأكيد الموافقة على القوانين"):
    def __init__(self, engagement):
        super().__init__()
        self.engagement = engagement
        self.confirmation = discord.ui.TextInput(
            label="اكتب أوافق للتأكيد",
            placeholder="أوافق",
            min_length=3,
            max_length=20,
        )
        self.add_item(self.confirmation)

    async def on_submit(self, itx: discord.Interaction):
        if self.confirmation.value.strip().casefold() not in {
            "أوافق",
            "اوافق",
            "موافق",
            "agree",
        }:
            return await itx.response.send_message(
                "❌ اكتب «أوافق» لتأكيد قراءة القوانين.",
                ephemeral=True,
            )
        result = await self.engagement.agree_to_rules(itx)
        if result["ok"]:
            return await itx.response.send_message(
                f"✅ تم توثيق موافقتك في {result['agreed_at']} ومنحك رتبة التحقق.",
                ephemeral=True,
            )
        await itx.response.send_message(result["message"], ephemeral=True)


class Engagement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.invite_cache: dict[int, dict[str, dict[str, Any]]] = {}
        self._invite_lock = asyncio.Lock()
        self._restored_role_panels: set[int] = set()
        self._restored_rules_panels: set[int] = set()
        self._restored_self_role_panels: set[int] = set()

    async def engagement_settings(self, guild_id: int) -> dict[str, Any]:
        try:
            snapshot = await get_guild_settings(int(guild_id))
            values = snapshot["settings"]
            return {
                "welcome_channel_id": values.get("welcome_channel_id"),
                "leave_channel_id": values.get("leave_channel_id"),
                "welcome_message": values.get("welcome_message", ""),
                "leave_message": values.get("leave_message", ""),
                "welcome_embed_enabled": bool(values.get("welcome_embed_enabled", False)),
                "welcome_embed_color": values.get("welcome_embed_color", "#7c3aed"),
                "welcome_embed_title": values.get("welcome_embed_title", "أهلاً بك في {server} ✨"),
                "welcome_embed_description": values.get("welcome_embed_description", ""),
                "welcome_embed_image_url": values.get("welcome_embed_image_url", ""),
                "welcome_embed_sticker_id": values.get("welcome_embed_sticker_id"),
                "welcome_embed_footer": values.get("welcome_embed_footer", "PRIME | TEAM • تطوير abood2026"),
                "welcome_embed_show_avatar": bool(values.get("welcome_embed_show_avatar", True)),
                "welcome_dm_enabled": bool(values.get("welcome_dm_enabled", False)),
                "auto_role_id": values.get("auto_role_id"),
                "member_auto_role_id": values.get("member_auto_role_id"),
                "bot_auto_role_id": values.get("bot_auto_role_id"),
                "verified_role_id": values.get("verified_role_id"),
                "unverified_role_id": values.get("unverified_role_id"),
                "rules_channel_id": values.get("rules_channel_id"),
            }
        except Exception:
            logger.exception("[ENGAGEMENT_CONFIG] تعذر قراءة إعدادات السيرفر %s", guild_id)
            return {
                "welcome_channel_id": None,
                "leave_channel_id": None,
                "welcome_message": "",
                "leave_message": "",
                "welcome_embed_enabled": False,
                "welcome_embed_color": "#7c3aed",
                "welcome_embed_title": "أهلاً بك في {server} ✨",
                "welcome_embed_description": "",
                "welcome_embed_image_url": "",
                "welcome_embed_sticker_id": None,
                "welcome_embed_footer": "PRIME | TEAM • تطوير abood2026",
                "welcome_embed_show_avatar": True,
                "welcome_dm_enabled": False,
                "auto_role_id": None,
                "member_auto_role_id": None,
                "bot_auto_role_id": None,
                "verified_role_id": None,
                "unverified_role_id": None,
                "rules_channel_id": None,
            }

    async def resolve_text_channel(self, guild: discord.Guild, channel_id: int):
        channel = guild.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel) or callable(getattr(channel, "send", None)):
            return channel
        fetch_channels = getattr(guild, "fetch_channels", None)
        if fetch_channels is None:
            return None
        try:
            for fetched in await fetch_channels():
                if fetched.id == int(channel_id) and (
                    isinstance(fetched, discord.TextChannel)
                    or callable(getattr(fetched, "send", None))
                ):
                    return fetched
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("[ENGAGEMENT_CONFIG] تعذر تحديث قنوات السيرفر %s", guild.id, exc_info=True)
        return None

    async def resolve_sticker(self, guild: discord.Guild, sticker_id: int):
        for sticker in getattr(guild, "stickers", ()) or ():
            if sticker.id == int(sticker_id):
                return sticker
        fetch_stickers = getattr(guild, "fetch_stickers", None)
        if fetch_stickers is not None:
            try:
                for sticker in await fetch_stickers():
                    if sticker.id == int(sticker_id):
                        return sticker
            except (discord.Forbidden, discord.HTTPException):
                logger.debug("[ENGAGEMENT_CONFIG] تعذر تحميل ملصق السيرفر %s", sticker_id, exc_info=True)
        return None

    async def get_onboarding_snapshot(self, guild_id: int) -> dict[str, Any]:
        snapshot = await get_guild_settings(int(guild_id))
        fields = await self.engagement_settings(guild_id)
        return {
            "revision": snapshot["revision"],
            "updated_at": snapshot["updated_at"],
            "settings": fields,
            "self_roles": await get_self_role_panels(guild_id),
        }

    @staticmethod
    def format_ordinal(count: int) -> str:
        count = max(0, int(count))
        suffix = "th"
        if 10 <= count % 100 <= 20:
            suffix = "th"
        elif count % 10 == 1:
            suffix = "st"
        elif count % 10 == 2:
            suffix = "nd"
        elif count % 10 == 3:
            suffix = "rd"
        return f"{count:,}{suffix}"

    def render_template(
        self,
        template: str,
        *,
        member: Optional[discord.Member] = None,
        guild: Optional[discord.Guild] = None,
        inviter: str = "غير معروف",
        count: Optional[int] = None,
        template_data: Optional[dict[str, Any]] = None,
    ) -> str:
        member_count = count if count is not None else getattr(guild, "member_count", 0)
        data = {
            "user": getattr(member, "mention", "@user"),
            "username": getattr(member, "display_name", "عضو جديد"),
            "server": getattr(guild, "name", "السيرفر"),
            "count": self.format_ordinal(member_count),
            "inviter": inviter,
        }
        if template_data:
            data.update(
                {
                    str(key): str(value)
                    for key, value in template_data.items()
                    if key in data and value is not None
                }
            )
        rendered = str(template or "")
        for key, value in data.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered

    async def build_welcome_embed(
        self,
        guild: discord.Guild,
        member: Optional[discord.Member] = None,
        *,
        config: Optional[dict[str, Any]] = None,
        inviter: str = "دعوة تجريبية",
        count: Optional[int] = None,
        template_data: Optional[dict[str, Any]] = None,
    ) -> discord.Embed:
        config = config or await self.engagement_settings(guild.id)
        member_count = count if count is not None else int(guild.member_count or 0)
        title = self.render_template(
            config.get("welcome_embed_title") or "أهلاً بك في {server} ✨",
            member=member,
            guild=guild,
            inviter=inviter,
            count=member_count,
            template_data=template_data,
        )
        description = self.render_template(
            config.get("welcome_embed_description")
            or config.get("welcome_message")
            or "يا هلا {user} في {server}! أنت العضو رقم {count}.",
            member=member,
            guild=guild,
            inviter=inviter,
            count=member_count,
            template_data=template_data,
        )
        raw_color = str(config.get("welcome_embed_color") or "#7c3aed").lstrip("#")
        try:
            color = discord.Colour(int(raw_color, 16))
        except (TypeError, ValueError):
            color = discord.Colour(0x7C3AED)
        embed = discord.Embed(
            title=title[:256],
            description=description[:4096],
            color=color,
        )
        avatar = str(getattr(getattr(member, "display_avatar", None), "url", "") or "")
        display_name = getattr(member, "display_name", None) or "عضو جديد"
        if avatar:
            embed.set_author(name=display_name, icon_url=avatar)
            if config.get("welcome_embed_show_avatar", True):
                embed.set_thumbnail(url=avatar)
        embed.add_field(name="العضو رقم", value=f"#{member_count:,}", inline=True)
        embed.add_field(name="عدد الأعضاء", value=f"{member_count:,}", inline=True)
        image_url = str(config.get("welcome_embed_image_url") or "").strip()
        sticker_id = config.get("welcome_embed_sticker_id")
        if not image_url and sticker_id:
            sticker = await self.resolve_sticker(guild, int(sticker_id))
            if sticker is not None:
                image_url = str(sticker.url)
        if image_url:
            embed.set_image(url=image_url)
        footer = str(config.get("welcome_embed_footer") or "").strip()
        if footer:
            embed.set_footer(text=footer[:2048])
        return embed

    async def _cache_guild_invites(self, guild: discord.Guild) -> None:
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            logger.warning("[INVITES] تعذر قراءة دعوات السيرفر %s", guild.id)
            return
        self.invite_cache[guild.id] = {
            invite.code: {
                "uses": int(invite.uses or 0),
                "inviter_id": getattr(getattr(invite, "inviter", None), "id", None),
                "inviter_name": getattr(
                    getattr(invite, "inviter", None),
                    "display_name",
                    None,
                )
                or getattr(getattr(invite, "inviter", None), "name", None),
            }
            for invite in invites
            if getattr(invite, "code", None)
        }

    async def _refresh_all_invites(self) -> None:
        for guild in list(self.bot.guilds):
            await self._cache_guild_invites(guild)

    async def _identify_inviter(self, guild: discord.Guild) -> tuple[str, str]:
        """Compare invite usage while serializing joins to avoid race conditions."""
        async with self._invite_lock:
            previous = self.invite_cache.get(guild.id, {})
            try:
                current_invites = await guild.invites()
            except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
                return "غير معروف", "غير معروف"

            current = {
                invite.code: {
                    "uses": int(invite.uses or 0),
                    "inviter_id": getattr(getattr(invite, "inviter", None), "id", None),
                    "inviter_name": getattr(
                        getattr(invite, "inviter", None),
                        "display_name",
                        None,
                    )
                    or getattr(getattr(invite, "inviter", None), "name", None),
                }
                for invite in current_invites
                if getattr(invite, "code", None)
            }
            best = None
            best_delta = 0
            for code, invite in current.items():
                delta = invite["uses"] - int(previous.get(code, {}).get("uses", 0))
                if delta > best_delta:
                    best_delta, best = delta, invite
            self.invite_cache[guild.id] = current
            if not best or not best.get("inviter_id"):
                return "غير معروف", "غير معروف"
            inviter_id = int(best["inviter_id"])
            inviter = guild.get_member(inviter_id)
            inviter_name = best.get("inviter_name") or str(inviter_id)
            return f"<@{inviter_id}>", inviter_name

    async def _restore_persistent_views(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                for panel in await get_role_panels(guild.id):
                    if panel["message_id"] in self._restored_role_panels:
                        continue
                    channel = guild.get_channel(panel["channel_id"])
                    roles = [
                        guild.get_role(role_id)
                        for role_id in panel["role_ids"]
                    ]
                    roles = [
                        role
                        for role in roles
                        if role and not role.managed and guild.me and role < guild.me.top_role
                    ]
                    if channel and roles:
                        view = discord.ui.View(timeout=None)
                        view.add_item(RoleSelector(roles))
                        self.bot.add_view(view, message_id=panel["message_id"])
                        self._restored_role_panels.add(panel["message_id"])
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("[ROLE_PANEL] تعذر استعادة لوحة في %s", guild.id)
            try:
                for panel in await get_rules_panels(guild.id):
                    if panel["message_id"] in self._restored_rules_panels:
                        continue
                    if guild.get_channel(panel["channel_id"]):
                        self.bot.add_view(
                            RulesAgreementView(self),
                            message_id=panel["message_id"],
                        )
                        self._restored_rules_panels.add(panel["message_id"])
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("[RULES_PANEL] تعذر استعادة لوحة في %s", guild.id)
            try:
                for panel in await get_self_role_panels(guild.id):
                    if panel["message_id"] in self._restored_self_role_panels:
                        continue
                    channel = guild.get_channel(panel["channel_id"])
                    specs = panel.get("role_specs") or []
                    roles = [
                        guild.get_role(int(spec["id"]))
                        for spec in specs
                        if isinstance(spec, dict) and str(spec.get("id", "")).isdigit()
                    ]
                    roles = [
                        role
                        for role in roles
                        if role and not role.managed and guild.me and role < guild.me.top_role
                    ]
                    if channel and roles:
                        view = discord.ui.View(timeout=None)
                        view.add_item(RoleSelector(roles, specs))
                        self.bot.add_view(view, message_id=panel["message_id"])
                        self._restored_self_role_panels.add(panel["message_id"])
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("[SELF_ROLE_PANEL] تعذر استعادة لوحة في %s", guild.id)
    async def agree_to_rules(self, itx: discord.Interaction) -> dict[str, Any]:
        guild, member = itx.guild, itx.user
        if guild is None or not isinstance(member, discord.Member):
            return {"ok": False, "message": "🔒 هذا التحقق متاح داخل السيرفر فقط."}
        config = await self.engagement_settings(guild.id)
        verified = guild.get_role(int(config["verified_role_id"])) if config["verified_role_id"] else None
        unverified = guild.get_role(int(config["unverified_role_id"])) if config["unverified_role_id"] else None
        if verified is None or guild.me is None or verified >= guild.me.top_role:
            return {"ok": False, "message": "⚠️ رتبة التحقق غير مضبوطة أو أعلى من رتبة البوت."}
        roles = [role for role in member.roles if not unverified or role.id != unverified.id]
        if verified not in roles:
            roles.append(verified)
        try:
            await member.edit(roles=roles, reason="Rules agreement gate")
            agreed_at = await record_rules_agreement(guild.id, member.id, verified.id)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[RULES] فشل تحديث رتب %s في %s", member.id, guild.id, exc_info=True)
            return {"ok": False, "message": "❌ تعذر تحديث رتبتك؛ تحقق من صلاحيات البوت."}
        return {"ok": True, "agreed_at": agreed_at}

    async def _assign_join_role(self, mem: discord.Member, config: dict[str, Any]) -> None:
        raw_id = (
            config["bot_auto_role_id"]
            if mem.bot
            else config["member_auto_role_id"] or config["auto_role_id"]
        )
        if not raw_id:
            return
        role = mem.guild.get_role(int(raw_id))
        if role is None or role.managed or not mem.guild.me or role >= mem.guild.me.top_role:
            return
        try:
            await mem.add_roles(role, reason="Automatic onboarding role")
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[ONBOARDING_ROLE] فشل إسناد الرتبة %s", raw_id, exc_info=True)

    def _welcome_channel(self, guild: discord.Guild, config: dict[str, Any]):
        if config["welcome_channel_id"]:
            channel = guild.get_channel(int(config["welcome_channel_id"]))
            if channel:
                return channel
        return guild.system_channel

    @commands.Cog.listener()
    async def on_ready(self):
        await self._refresh_all_invites()
        await self._restore_persistent_views()

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        inviter = getattr(invite, "inviter", None)
        self.invite_cache.setdefault(invite.guild.id, {})[invite.code] = {
            "uses": int(invite.uses or 0),
            "inviter_id": getattr(inviter, "id", None),
            "inviter_name": getattr(inviter, "display_name", None) or getattr(inviter, "name", None),
        }

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        self.invite_cache.get(invite.guild.id, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, mem: discord.Member):
        config = await self.engagement_settings(mem.guild.id)
        inviter, inviter_name = await self._identify_inviter(mem.guild)
        if inviter != "غير معروف":
            await record_invite_use(mem.guild.id, int(inviter.strip("<@>")))
        await self._assign_join_role(mem, config)
        template = config["welcome_message"] or (
            "مرحباً {user} في {server}! أنت العضو {count}. "
            "تمت دعوتك بواسطة {inviter}."
        )
        content = self.render_template(
            template,
            member=mem,
            guild=mem.guild,
            inviter=inviter,
        )
        channel = self._welcome_channel(mem.guild, config)
        if channel:
            allowed_mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
            if config.get("welcome_embed_enabled"):
                await channel.send(
                    embed=await self.build_welcome_embed(
                        mem.guild,
                        mem,
                        config=config,
                        inviter=inviter,
                    ),
                    allowed_mentions=allowed_mentions,
                )
            else:
                await channel.send(content, allowed_mentions=allowed_mentions)
        if config["welcome_dm_enabled"]:
            try:
                await mem.send(
                    self.render_template(
                        template,
                        member=mem,
                        guild=mem.guild,
                        inviter=inviter_name if inviter != "غير معروف" else inviter,
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.info("[WELCOME_DM] تعذر إرسال رسالة خاصة إلى %s", mem.id)

    @commands.Cog.listener()
    async def on_member_remove(self, mem: discord.Member):
        config = await self.engagement_settings(mem.guild.id)
        channel_id = config.get("leave_channel_id") or config.get("welcome_channel_id")
        channel = (
            await self.resolve_text_channel(mem.guild, int(channel_id))
            if channel_id
            else mem.guild.system_channel
        )
        if channel is None:
            return
        template = config["leave_message"] or "{username} غادر {server}. كان عدد الأعضاء {count}."
        await channel.send(
            self.render_template(template, member=mem, guild=mem.guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def send_test_welcome(
        self,
        guild_id: int,
        target_channel_id: int,
        template_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return {"ok": False, "error": "guild_not_found"}
        channel = await self.resolve_text_channel(guild, int(target_channel_id))
        if channel is None or not callable(getattr(channel, "send", None)):
            return {"ok": False, "error": "channel_not_found"}
        config = await self.engagement_settings(guild.id)
        content = self.render_template(
            config["welcome_message"]
            or "مرحباً {user} في {server}! أنت العضو {count}. تمت دعوتك بواسطة {inviter}.",
            guild=guild,
            template_data=template_data,
            inviter="دعوة تجريبية",
            count=guild.member_count or 0,
        )
        try:
            allowed_mentions = discord.AllowedMentions.none()
            if config.get("welcome_embed_enabled"):
                message = await channel.send(
                    embed=await self.build_welcome_embed(
                        guild,
                        getattr(guild, "me", None),
                        config=config,
                        template_data=template_data,
                    ),
                    allowed_mentions=allowed_mentions,
                )
            else:
                message = await channel.send(content, allowed_mentions=allowed_mentions)
        except (discord.Forbidden, discord.HTTPException):
            return {"ok": False, "error": "send_failed"}
        return {"ok": True, "guild_id": guild.id, "channel_id": channel.id, "message_id": message.id}

    async def deploy_self_role_panel(
        self,
        guild_id: int,
        target_channel_id: int,
        title: str,
        description: str,
        color: str,
        emoji: str,
        role_specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return {"ok": False, "error": "guild_not_found"}
        channel = await self.resolve_text_channel(guild, int(target_channel_id))
        if channel is None or not callable(getattr(channel, "send", None)):
            return {"ok": False, "error": "channel_not_found"}
        if not isinstance(role_specs, list) or not 1 <= len(role_specs) <= 25:
            return {"ok": False, "error": "roles_invalid"}
        clean_specs = []
        roles = []
        for spec in role_specs:
            if not isinstance(spec, dict) or not str(spec.get("id", "")).isdigit():
                return {"ok": False, "error": "roles_invalid"}
            role = guild.get_role(int(spec["id"]))
            if role is None or role.managed or not guild.me or role >= guild.me.top_role:
                return {"ok": False, "error": "role_not_assignable"}
            clean_specs.append({
                "id": role.id,
                "label": str(spec.get("label") or role.name)[:100],
                "emoji": str(spec.get("emoji") or "🏷️")[:32],
            })
            roles.append(role)
        normalized_color = str(color or "#5865f2").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", normalized_color):
            normalized_color = "#5865f2"
        try:
            embed = discord.Embed(
                title=f"{str(emoji or '🏷️')[:8]} {str(title or 'الرتب الذاتية')[:256]}",
                description=str(description or "اختر الرتب المناسبة لك:")[:4000],
                color=int(normalized_color[1:], 16),
            )
            view = discord.ui.View(timeout=None)
            view.add_item(RoleSelector(roles, clean_specs))
            message = await channel.send(embed=embed, view=view)
            panel = await save_self_role_panel(
                guild.id,
                channel.id,
                message.id,
                str(title or "الرتب الذاتية")[:256],
                str(description or "")[:4000],
                normalized_color,
                str(emoji or "🏷️")[:8],
                clean_specs,
            )
            self._restored_self_role_panels.add(message.id)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[SELF_ROLE_PANEL] فشل نشر لوحة في %s", guild.id, exc_info=True)
            return {"ok": False, "error": "send_failed"}
        return {"ok": True, "panel": panel}

    @app_commands.command(
        name="setup_tickets",
        description="تثبيت لوحة تذاكر الدعم الفني",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_tickets(self, itx: discord.Interaction):
        community = itx.client.get_cog("Community")
        if community is None:
            return await itx.response.send_message(
                "⚠️ نظام التذاكر غير متاح حالياً.",
                ephemeral=True,
            )
        try:
            panel = await community.deploy_ticket_panel(itx.channel.id)
        except (ValueError, discord.Forbidden, discord.HTTPException):
            logger.exception("[TICKETS_SETUP] فشل نشر لوحة التذاكر")
            return await itx.response.send_message(
                "❌ تعذر نشر لوحة التذاكر. تحقق من صلاحيات البوت.",
                ephemeral=True,
            )
        await itx.response.send_message(
            f"✅ تم تثبيت لوحة التذاكر وحفظها برقم الرسالة `{panel['message_id']}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name="setup_roles",
        description="لوحة اختيار الرتب التفاعلية",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_roles(self, itx: discord.Interaction):
        roles = [
            role
            for role in itx.guild.roles
            if (
                not role.is_default()
                and not role.managed
                and role < itx.guild.me.top_role
            )
        ]
        if not roles:
            return await itx.response.send_message(
                "❌ لا توجد رتب متاحة للإسناد تحت رتبة البوت.",
                ephemeral=True,
            )
        view = discord.ui.View(timeout=None)
        view.add_item(RoleSelector(roles))
        embed = discord.Embed(
            title="🎭 اختيار الرتب الذاتية",
            description=(
                "حدد الرتب المناسبة لك من القائمة لتفعيلها أو إزالتها تلقائياً:"
            ),
            color=0x9B59B6,
        )
        message = await itx.channel.send(
            embed=embed,
            view=view,
        )
        await save_role_panel(
            itx.guild.id,
            itx.channel.id,
            message.id,
            [role.id for role in roles],
        )
        await itx.response.send_message(
            "✅ تم إرسال لوحة الرتب.",
            ephemeral=True,
        )

    @app_commands.command(
        name="setup_rules",
        description="تثبيت بوابة الموافقة على القوانين",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_rules(self, itx: discord.Interaction):
        config = await self.engagement_settings(itx.guild.id)
        if not config["verified_role_id"]:
            return await itx.response.send_message(
                "⚠️ اضبط رتبة التحقق أولاً من لوحة التحكم.",
                ephemeral=True,
            )
        channel = (
            itx.guild.get_channel(int(config["rules_channel_id"]))
            if config["rules_channel_id"]
            else itx.channel
        )
        if channel is None:
            return await itx.response.send_message(
                "❌ لم يتم العثور على قناة القوانين.",
                ephemeral=True,
            )
        embed = discord.Embed(
            title="📜 بوابة القوانين والتحقق",
            description=(
                "اقرأ قوانين السيرفر بعناية، ثم اضغط على الزر أدناه "
                "لتأكيد موافقتك وتفعيل رتبة الدخول."
            ),
            color=0x2ECC71,
        )
        message = await channel.send(embed=embed, view=RulesAgreementView(self))
        await save_rules_panel(itx.guild.id, channel.id, message.id)
        await itx.response.send_message(
            "✅ تم تثبيت بوابة القوانين وحفظها للاستعادة بعد إعادة التشغيل.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    bot.add_view(TicketLauncher())
    bot.add_view(TicketControl())
    await bot.add_cog(Engagement(bot))

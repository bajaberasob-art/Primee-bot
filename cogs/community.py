import asyncio
import datetime
import html
import io
import json
import logging
import re
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import (
    claim_ticket,
    unclaim_ticket,
    close_ticket,
    create_ticket,
    escalate_ticket,
    add_ticket_note,
    get_active_tickets,
    get_ticket_archive,
    get_ticket,
    get_staff_kpis,
    get_ticket_by_channel,
    get_ticket_panels,
    get_ticket_configs,
    get_ticket_config,
    get_ticket_permissions,
    get_ticket_options,
    replace_ticket_options,
    get_ticket_dropdown_config,
    get_ticket_dropdown_categories,
    save_ticket_config,
    get_active_ticket_for_user_category,
    is_ticket_user_blacklisted,
    get_ticket_transcripts,
    get_ticket_transcript,
    get_canned_responses,
    save_canned_response,
    delete_canned_response,
    cancel_reminder,
    record_ticket_response,
    record_ticket_user_message,
    set_ticket_status,
    set_ticket_priority,
    reopen_ticket,
    get_ticket_notes,
    save_ticket_panel,
    save_ticket_rating,
    save_ticket_log,
    save_ticket_transcript,
    complete_reminder,
    create_reminder,
    get_due_reminders,
    claim_due_streak_reminders,
    get_user_reminders,
    set_streak_reminder,
)
from interaction_runtime import mark_modal_callback
from prime_level_controls import render_template
from streak_experience import build_streak_context


logger = logging.getLogger(__name__)
TICKET_PRIORITIES = ("normal", "high", "management")
DEFAULT_TICKET_CATEGORIES = [
    {
        "key": "general",
        "label": "الدعم العام",
        "description": "للاستفسارات العامة، الاقتراحات، أو المشاكل التقنية",
        "welcome_msg": "أهلاً بك في الدعم العام. سيقوم فريقنا بمتابعة طلبك قريباً.",
        "emoji": "🔧",
        "support_role_ids": [],
        "senior_role_ids": [],
    },
    {
        "key": "girls-verification",
        "label": "توثيق البنات",
        "description": "يتم توثيقك وتمييزك عن باقي الأعضاء",
        "welcome_msg": "أهلاً بك. سيتابع فريق التوثيق طلبك بسرية واحترام.",
        "emoji": "🌸",
        "support_role_ids": [],
        "senior_role_ids": [],
    },
    {
        "key": "rewards",
        "label": "المكافآت والجوائز",
        "description": "لاستلام جوائز المسابقات الخاصة بPR1ME",
        "welcome_msg": "أهلاً بك في قسم المكافآت والجوائز.",
        "emoji": "🎁",
        "support_role_ids": [],
        "senior_role_ids": [],
    },
    {
        "key": "content-creators",
        "label": "برنامج صناع المحتوى",
        "description": "للحصول على رتبة صانع محتوى ومزايا خاصة",
        "welcome_msg": "أهلاً بك في برنامج صناع المحتوى.",
        "emoji": "📹",
        "support_role_ids": [],
        "senior_role_ids": [],
    },
    {
        "key": "clan-application",
        "label": "التقديم للكلان",
        "description": "طلبات الانضمام إلى الكلان",
        "welcome_msg": "أهلاً بك. سيقوم فريق الكلان بمراجعة طلبك.",
        "emoji": "🕹️",
        "support_role_ids": [],
        "senior_role_ids": [],
    },
]


def normalize_ticket_categories(categories_config):
    source = categories_config or DEFAULT_TICKET_CATEGORIES
    normalized = []
    used_keys = set()
    for index, raw in enumerate(source[:25]):
        if isinstance(raw, str):
            raw = {"key": raw, "label": raw}
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or raw.get("name") or f"تصنيف {index + 1}").strip()
        key = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(raw.get("key") or label).strip().lower()).strip("-")
        key = key[:60] or f"category-{index + 1}"
        base_key = key
        suffix = 2
        while key in used_keys:
            key = f"{base_key[:54]}-{suffix}"
            suffix += 1
        used_keys.add(key)
        if not key or not label:
            continue
        support_role_ids = [
            str(item)
            for item in raw.get(
                "support_role_ids",
                [raw.get("role_id")] if raw.get("role_id") not in (None, "") else [],
            )
            if str(item).isdigit()
        ]
        ping_role_ids = [
            str(item)
            for item in raw.get("ping_role_ids", support_role_ids)
            if str(item).isdigit()
        ]
        staff_role_ids = [
            str(item)
            for item in raw.get("staff_role_ids", support_role_ids)
            if str(item).isdigit()
        ]
        raw_emoji = str(raw.get("emoji") or "🎫").strip()
        # Keep custom guild emoji tokens intact. Discord uses the full
        # <:name:id> / <a:name:id> token in SelectOption; truncating it to
        # two characters turns a valid picker choice into an unusable label.
        emoji = raw_emoji[:100] or "🎫"
        normalized.append({
            "key": key,
            "label": label[:80],
            "description": str(raw.get("description") or "")[:100],
            "emoji": emoji,
            "category_id": str(raw["category_id"]) if raw.get("category_id") else None,
            "role_id": support_role_ids[0] if support_role_ids else None,
            "support_role_ids": support_role_ids,
            "ping_role_ids": ping_role_ids,
            "staff_role_ids": staff_role_ids,
            "senior_role_ids": [str(item) for item in raw.get("senior_role_ids", []) if str(item).isdigit()],
            "welcome_msg": str(raw.get("welcome_msg") or "")[:2000],
            "intake_fields": [
                {
                    "key": re.sub(
                        r"[^a-zA-Z0-9_-]+",
                        "_",
                        str(field.get("key") or f"field_{field_index + 1}").strip().lower(),
                    ).strip("_")[:40] or f"field_{field_index + 1}",
                    "label": str(field.get("label") or f"معلومة إضافية {field_index + 1}").strip()[:45],
                    "placeholder": str(field.get("placeholder") or "").strip()[:100],
                    "required": bool(field.get("required", False)),
                }
                for field_index, field in enumerate(raw.get("intake_fields", [])[:3])
                if isinstance(field, dict)
                and str(field.get("label") or "").strip()
            ],
        })
    return normalized or normalize_ticket_categories(DEFAULT_TICKET_CATEGORIES)


class TicketCategoryModal(discord.ui.Modal):
    def __init__(self, category: dict):
        super().__init__(title=f"فتح تذكرة · {category['label']}"[:45])
        self.category = category
        self.subject = discord.ui.TextInput(
            label="عنوان المشكلة",
            placeholder="اكتب عنواناً مختصراً وواضحاً",
            max_length=200,
            required=True,
        )
        self.details = discord.ui.TextInput(
            label="التفاصيل والطلب",
            placeholder="اشرح المشكلة أو ما تحتاجه بالتفصيل",
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=True,
        )
        self.extra_inputs = []
        for index, raw in enumerate(category.get("intake_fields", [])[:3]):
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label") or f"معلومة إضافية {index + 1}")[:45]
            field = discord.ui.TextInput(
                label=label,
                placeholder=str(raw.get("placeholder") or "")[:100],
                max_length=500,
                required=bool(raw.get("required", False)),
            )
            self.extra_inputs.append((str(raw.get("key") or f"field_{index + 1}")[:40], field))
        self.add_item(self.subject)
        self.add_item(self.details)
        for _, field in self.extra_inputs:
            self.add_item(field)

    async def on_submit(self, itx: discord.Interaction):
        cog = itx.client.get_cog("Community")
        if cog is None:
            return await itx.response.send_message(
                "⚠️ نظام التذاكر غير متاح حالياً.", ephemeral=True
            )
        await cog.open_ticket(
            itx,
            self.category,
            str(self.subject),
            str(self.details),
            {key: str(field) for key, field in self.extra_inputs if str(field).strip()},
        )


class CloseTicketModal(discord.ui.Modal, title="إغلاق وأرشفة التذكرة"):
    reason = discord.ui.TextInput(
        label="سبب الإغلاق",
        placeholder="اكتب ملخص الحل أو سبب الإغلاق",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    async def on_submit(self, itx: discord.Interaction):
        cog = itx.client.get_cog("Community")
        if cog is None:
            return await itx.response.send_message(
                "⚠️ نظام التذاكر غير متاح حالياً.", ephemeral=True
            )
        await cog.close_ticket_from_interaction(itx, str(self.reason))


class InternalNoteModal(discord.ui.Modal, title="إضافة ملاحظة داخلية"):
    note = discord.ui.TextInput(
        label="الملاحظة",
        placeholder="لن تظهر هذه الملاحظة لصاحب التذكرة",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )

    async def on_submit(self, itx: discord.Interaction):
        cog = itx.client.get_cog("Community")
        if cog is None:
            return await itx.response.send_message("نظام التذاكر غير متاح حالياً.", ephemeral=True)
        await cog.add_internal_note_from_interaction(itx, str(self.note))


def _member_id_from_text(value: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{15,25})(?!\d)", str(value or ""))
    return int(match.group(1)) if match else None


class TicketMemberActionModal(discord.ui.Modal):
    def __init__(self, action: str):
        titles = {
            "add": "إضافة عضو إلى التذكرة",
            "remove": "طرد عضو من التذكرة",
            "transfer": "تحويل التذكرة",
        }
        super().__init__(title=titles[action])
        self.action = action
        self.member_id = discord.ui.TextInput(
            label="معرّف Discord أو منشن العضو",
            placeholder="مثال: 123456789012345678",
            max_length=40,
            required=True,
        )
        self.add_item(self.member_id)

    async def on_submit(self, itx: discord.Interaction):
        cog = itx.client.get_cog("Community")
        if cog is None:
            return await itx.response.send_message("نظام التذاكر غير متاح حالياً.", ephemeral=True)
        await cog.handle_ticket_member_action(itx, self.action, str(self.member_id))


async def send_ticket_action_embed(
    channel,
    title,
    description,
    color,
    staff,
    target_user=None,
    extra_field=None,
):
    """Send one consistent, auditable CRM action message in a ticket channel."""
    embed = discord.Embed(
        title=str(title)[:256],
        description=str(description)[:4096],
        color=int(color),
        timestamp=discord.utils.utcnow(),
    )
    guild = getattr(channel, "guild", None)
    guild_me = getattr(guild, "me", None)
    display_name = getattr(guild_me, "display_name", None) or getattr(guild, "name", "PR1ME")
    avatar = getattr(getattr(guild_me, "display_avatar", None), "url", None)
    if avatar:
        embed.set_author(name=display_name, icon_url=str(avatar))
    else:
        embed.set_author(name=display_name)
    staff_mention = getattr(staff, "mention", f"<@{getattr(staff, 'id', 0)}>")
    embed.add_field(name="المشرف المسؤول", value=staff_mention, inline=True)
    if target_user:
        embed.add_field(
            name="العضو المعني",
            value=getattr(target_user, "mention", f"<@{getattr(target_user, 'id', 0)}>"),
            inline=True,
        )
    if extra_field:
        embed.add_field(
            name=str(extra_field[0])[:256],
            value=str(extra_field[1])[:1024],
            inline=True,
        )
    embed.set_footer(text=f"PR1ME Ticket Engine • #{getattr(channel, 'name', 'ticket')}")
    await channel.send(embed=embed)


class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=60)
        self.owner_id = int(owner_id)

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if int(itx.user.id) != self.owner_id:
            await itx.response.send_message("هذا التأكيد مخصص لمن بدأ عملية الإغلاق.", ephemeral=True)
            return False
        return True

    @discord.ui.button(
        label="تأكيد الإغلاق",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="tkt_close_confirm",
    )
    async def confirm(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = itx.client.get_cog("Community")
        if cog is None:
            return await itx.response.send_message("نظام التذاكر غير متاح حالياً.", ephemeral=True)
        await cog.close_ticket_from_interaction(
            itx,
            "أُغلقت بواسطة فريق الدعم",
            delay_seconds=5,
        )

    @discord.ui.button(
        label="إلغاء الإغلاق",
        style=discord.ButtonStyle.secondary,
        emoji="↩️",
        custom_id="tkt_close_cancel",
    )
    async def cancel(self, itx: discord.Interaction, btn: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await itx.response.edit_message(content="تم إلغاء الإغلاق.", view=self)


class TicketMemberSelectView(discord.ui.View):
    def __init__(self, action: str):
        super().__init__(timeout=60)
        self.action = action
        selector = discord.ui.UserSelect(
            placeholder="اختر العضو من السيرفر",
            min_values=1,
            max_values=1,
            custom_id=f"tkt_user_select:{action}",
        )

        async def callback(itx: discord.Interaction):
            selected = selector.values[0] if selector.values else None
            if selected is None:
                return await itx.response.send_message("اختر عضواً أولاً.", ephemeral=True)
            cog = itx.client.get_cog("Community")
            if cog is None:
                return await itx.response.send_message("نظام التذاكر غير متاح حالياً.", ephemeral=True)
            await cog.handle_ticket_member_action(itx, action, str(selected.id))

        selector.callback = callback
        self.add_item(selector)


class TicketPrioritySelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        selector = discord.ui.Select(
            placeholder="اختر الأولوية الجديدة",
            min_values=1,
            max_values=1,
            custom_id="tkt_priority_select",
            options=[
                discord.SelectOption(label="عادية", value="normal", emoji="🟢"),
                discord.SelectOption(label="مرتفعة", value="high", emoji="🟠"),
                discord.SelectOption(label="عاجلة", value="management", emoji="🔴"),
            ],
        )

        async def callback(itx: discord.Interaction):
            cog = itx.client.get_cog("Community")
            if cog is None:
                return await itx.response.send_message("نظام التذاكر غير متاح حالياً.", ephemeral=True)
            await cog.set_ticket_priority_from_interaction(
                itx,
                (selector.values or ["normal"])[0],
            )

        selector.callback = callback
        self.add_item(selector)


class TicketTransferCategoryView(discord.ui.View):
    def __init__(self, categories):
        super().__init__(timeout=60)
        normalized = normalize_ticket_categories(categories)
        selector = discord.ui.Select(
            placeholder="اختر قسم التذكرة الجديد",
            min_values=1,
            max_values=1,
            custom_id="tkt_transfer_category",
            options=[
                discord.SelectOption(
                    label=item["label"][:100],
                    value=item["key"][:100],
                    emoji=item.get("emoji") or "🎫",
                )
                for item in normalized[:25]
            ],
        )

        async def callback(itx: discord.Interaction):
            cog = itx.client.get_cog("Community")
            if cog is None:
                return await itx.response.send_message("نظام التذاكر غير متاح حالياً.", ephemeral=True)
            selected_key = (selector.values or [""])[0]
            category = next((item for item in normalized if item["key"] == selected_key), None)
            if category is None:
                return await itx.response.send_message("القسم المحدد غير موجود.", ephemeral=True)
            await cog.transfer_ticket_category_from_interaction(itx, category)

        selector.callback = callback
        self.add_item(selector)


class TicketOptionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    async def _staff_ticket(self, itx, action=None):
        cog = itx.client.get_cog("Community")
        if cog is None:
            await itx.response.send_message("نظام التذاكر غير متاح حالياً.", ephemeral=True)
            return None, None
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
            return None, None
        if not await cog._ticket_action_allowed(itx.user, ticket, action):
            await cog._ticket_denied(itx)
            return None, None
        return cog, ticket

    @discord.ui.button(label="إضافة عضو", style=discord.ButtonStyle.secondary, emoji="➕", custom_id="tkt_opt_add")
    async def add_member(self, itx, btn):
        cog, _ = await self._staff_ticket(itx, "add_member")
        if cog:
            await itx.response.send_message(
                "اختر العضو الذي سيحصل على صلاحية رؤية التذكرة.",
                view=TicketMemberSelectView("add"),
                ephemeral=True,
            )

    @discord.ui.button(label="طرد عضو", style=discord.ButtonStyle.secondary, emoji="➖", custom_id="tkt_opt_remove")
    async def remove_member(self, itx, btn):
        cog, _ = await self._staff_ticket(itx, "remove_member")
        if cog:
            await itx.response.send_message(
                "اختر العضو الذي ستتم إزالة صلاحيته.",
                view=TicketMemberSelectView("remove"),
                ephemeral=True,
            )

    @discord.ui.button(label="تغيير الأولوية", style=discord.ButtonStyle.primary, emoji="🚨", custom_id="tkt_opt_priority")
    async def priority(self, itx, btn):
        cog, _ = await self._staff_ticket(itx, "priority")
        if cog:
            await itx.response.send_message("اختر الأولوية:", view=TicketPrioritySelectView(), ephemeral=True)

    @discord.ui.button(label="تحويل القسم", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="tkt_opt_transfer")
    async def transfer(self, itx, btn):
        cog, _ = await self._staff_ticket(itx, "transfer")
        if cog:
            await itx.response.send_message(
                "اختر القسم الذي ستُنقل إليه التذكرة:",
                view=TicketTransferCategoryView(await get_ticket_options(itx.guild.id)),
                ephemeral=True,
            )

    @discord.ui.button(label="ملاحظة داخلية", style=discord.ButtonStyle.secondary, emoji="📝", custom_id="tkt_opt_note")
    @mark_modal_callback
    async def note(self, itx, btn):
        cog, _ = await self._staff_ticket(itx, "note")
        if cog:
            await itx.response.send_modal(InternalNoteModal())


class StreamlinedTicketControlsView(discord.ui.View):
    """The three-button controller used for all newly created tickets."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _ticket(self, itx, action=None):
        cog = itx.client.get_cog("Community")
        if cog is None:
            await itx.response.send_message("نظام التذاكر غير متاح حالياً.", ephemeral=True)
            return None
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket:
            await itx.response.send_message("هذه القناة ليست تذكرة مسجلة.", ephemeral=True)
            return None
        if not await cog._ticket_action_allowed(itx.user, ticket, action):
            await cog._ticket_denied(itx)
            return None
        return cog

    @discord.ui.button(label="استلام التذكرة", style=discord.ButtonStyle.success, emoji="✋", custom_id="tkt_ctrl_claim")
    async def claim(self, itx, btn):
        cog = await self._ticket(itx)
        if cog:
            await cog.claim_ticket_from_interaction(itx)

    @discord.ui.button(label="إغلاق التذكرة", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="tkt_ctrl_close")
    async def close(self, itx, btn):
        cog = await self._ticket(itx)
        if cog:
            await itx.response.send_message(
                "⚠️ سيتم إغلاق التذكرة وأرشفتها خلال 5 ثوانٍ...\nيمكنك الإلغاء الآن.",
                view=TicketCloseConfirmView(itx.user.id),
                ephemeral=True,
            )

    @discord.ui.button(label="خيارات الإدارة", style=discord.ButtonStyle.secondary, emoji="⚙️", custom_id="tkt_ctrl_options")
    async def options(self, itx, btn):
        cog = await self._ticket(itx)
        if cog:
            await itx.response.send_message(
                "اختر إجراء الإدارة المطلوب:",
                view=TicketOptionsView(),
                ephemeral=True,
            )


class TicketRatingModal(discord.ui.Modal, title="تقييم مستوى الخدمة"):
    feedback = discord.ui.TextInput(
        label="ملاحظاتك على الخدمة وسبب التقييم",
        placeholder="اكتب ملاحظاتك (اختياري)",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=False,
    )

    def __init__(self, ticket_id, guild_id, user_id, stars, source_message=None):
        super().__init__(title="تقييم مستوى الخدمة")
        self.ticket_id = int(ticket_id)
        self.guild_id = int(guild_id)
        self.user_id = int(user_id)
        self.stars = int(stars)
        self.source_message = source_message

    async def on_submit(self, itx: discord.Interaction):
        await itx.response.defer()
        rating = await save_ticket_rating(
            self.ticket_id,
            self.guild_id,
            self.user_id,
            self.stars,
            str(self.feedback),
        )
        if not rating:
            return await itx.followup.send("تعذر حفظ التقييم؛ قد تكون التذكرة غير مغلقة.", ephemeral=True)
        if self.source_message is not None:
            try:
                await self.source_message.edit(view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        cog = itx.client.get_cog("Community")
        if cog:
            await cog.publish_ticket_evaluation(self.ticket_id, self.guild_id, self.stars, str(self.feedback))
        await itx.followup.send(f"تم تسجيل تقييمك: {'⭐' * self.stars}", ephemeral=True)


class PersistentDMRatingView(discord.ui.View):
    """Restart-safe DM rating view with stable button IDs."""

    def __init__(self, ticket_id=None, guild_id=None, user_id=None):
        super().__init__(timeout=None)
        self.ticket_id = int(ticket_id) if ticket_id is not None else None
        self.guild_id = int(guild_id) if guild_id is not None else None
        self.user_id = int(user_id) if user_id is not None else None
        for stars in range(1, 6):
            button = discord.ui.Button(
                label=f"{stars} نجوم",
                emoji="⭐",
                style=discord.ButtonStyle.success if stars >= 4 else discord.ButtonStyle.secondary,
                custom_id=f"tkt_star:{stars}",
            )

            async def callback(itx, value=stars):
                ticket_id = self.ticket_id
                guild_id = self.guild_id
                if ticket_id is None or guild_id is None:
                    footer = ""
                    if itx.message and itx.message.embeds:
                        footer = str(itx.message.embeds[0].footer.text or "")
                    match = re.search(r"ticket:(\d+):(\d+)", footer)
                    if match:
                        ticket_id, guild_id = int(match.group(1)), int(match.group(2))
                if ticket_id is None or guild_id is None:
                    return await itx.response.send_message("تعذر ربط التقييم بالتذكرة.", ephemeral=True)
                ticket = await get_ticket(guild_id, ticket_id)
                if not ticket or int(ticket["user_id"]) != int(itx.user.id):
                    return await itx.response.send_message("هذا التقييم مخصص لصاحب التذكرة.", ephemeral=True)
                await itx.response.send_modal(
                    TicketRatingModal(ticket_id, guild_id, itx.user.id, value, itx.message)
                )

            button.callback = callback
            self.add_item(button)


class TicketSelectView(discord.ui.View):
    """Persistent dropdown panel whose options are restored from SQLite."""

    def __init__(self, categories_config=None, guild_id: int | None = None):
        super().__init__(timeout=None)
        self.categories = normalize_ticket_categories(categories_config)
        self.guild_id = int(guild_id) if guild_id is not None else None
        custom_id = (
            f"ticket:select:{self.guild_id}"
            if self.guild_id is not None
            else "ticket:select"
        )
        select = discord.ui.Select(
            placeholder="اختر القسم المناسب لطلبك 📋",
            min_values=1,
            max_values=1,
            custom_id=custom_id,
            options=[
                discord.SelectOption(
                    label=category["label"][:100],
                    description=(
                        category.get("description")
                        or "فتح تذكرة مع فريق الدعم"
                    )[:100],
                    emoji=category["emoji"],
                    value=category["key"][:100],
                )
                for category in self.categories[:25]
            ],
        )

        async def callback(itx: discord.Interaction):
            selected_key = (getattr(select, "values", None) or [None])[0]
            category = next(
                (item for item in self.categories if item["key"] == selected_key),
                None,
            )
            if category is None:
                return await itx.response.send_message(
                    "تعذر تحميل هذا القسم. أعد نشر لوحة التذاكر من لوحة التحكم.",
                    ephemeral=True,
                )
            await itx.response.send_modal(TicketCategoryModal(category))

        mark_modal_callback(callback)
        select.callback = callback
        self.add_item(select)


class PersistentDropdownTicketView(discord.ui.View):
    """Restart-safe ticket dropdown used by the dashboard-owned panel."""

    CUSTOM_ID = "ticket_dropdown_select"

    def __init__(self, categories_config=None, guild_id: int | None = None):
        super().__init__(timeout=None)
        self.categories = normalize_ticket_categories(categories_config)
        self.guild_id = int(guild_id) if guild_id is not None else None
        select = discord.ui.Select(
            placeholder="اختر القسم المناسب لفتح تذكرة 📋",
            min_values=1,
            max_values=1,
            custom_id=self.CUSTOM_ID,
            options=[
                discord.SelectOption(
                    label=category["label"][:100],
                    description=(category.get("description") or "فتح تذكرة مع فريق الدعم")[:100],
                    emoji=category.get("emoji") or "🎫",
                    value=category["key"][:100],
                )
                for category in self.categories[:25]
            ],
        )

        async def callback(itx: discord.Interaction):
            selected_key = (getattr(select, "values", None) or [None])[0]
            category = next(
                (item for item in self.categories if item["key"] == selected_key),
                None,
            )
            if category is None:
                return await itx.response.send_message(
                    "تعذر تحميل هذا القسم. أعد نشر لوحة التذاكر من لوحة التحكم.",
                    ephemeral=True,
                )
            cog = itx.client.get_cog("Community")
            if cog is None:
                return await itx.response.send_message(
                    "نظام التذاكر غير متاح حالياً.",
                    ephemeral=True,
                )
            # The normal open_ticket path performs the per-user/category
            # duplicate check and creates the private channel atomically
            # enough for Discord's channel API, while preserving legacy
            # ticket storage and control buttons.
            await cog.open_ticket(
                itx,
                category,
                f"طلب {category['label']}"[:200],
                "تم فتح الطلب من لوحة التذاكر المنسدلة.",
            )

        select.callback = callback
        self.add_item(select)


class TicketPanelView(discord.ui.View):
    def __init__(self, categories_config):
        super().__init__(timeout=None)
        self.categories = normalize_ticket_categories(categories_config)
        for category in self.categories:
            button = discord.ui.Button(
                label=category["label"][:80],
                emoji=category["emoji"],
                style=discord.ButtonStyle.primary,
                custom_id=f"ticket:category:{category['key']}",
            )

            async def callback(itx: discord.Interaction, selected=category):
                await itx.response.send_modal(TicketCategoryModal(selected))

            mark_modal_callback(callback)
            button.callback = callback
            self.add_item(button)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _cog(self, itx):
        return itx.client.get_cog("Community")

    @discord.ui.button(
        label="استلام التذكرة",
        style=discord.ButtonStyle.success,
        emoji="🙋",
        custom_id="ticket:claim",
    )
    async def claim(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.claim_ticket_from_interaction(itx)

    @discord.ui.button(
        label="تصعيد التذكرة",
        style=discord.ButtonStyle.primary,
        emoji="🚨",
        custom_id="ticket:escalate",
    )
    async def escalate(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.escalate_ticket_from_interaction(itx)

    @discord.ui.button(
        label="إغلاق وأرشفة",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="ticket:close",
    )
    @mark_modal_callback
    async def close(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.show_close_modal(itx)

    @discord.ui.button(
        label="حذف التذكرة",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="ticket:delete",
    )
    async def delete(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.delete_ticket_from_interaction(itx)

    @discord.ui.button(
        label="بانتظار العميل",
        style=discord.ButtonStyle.secondary,
        emoji="⏳",
        custom_id="ticket:waiting-user",
    )
    async def waiting_user(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.set_ticket_status_from_interaction(itx, "waiting_user")

    @discord.ui.button(
        label="ملاحظة داخلية",
        style=discord.ButtonStyle.secondary,
        emoji="📝",
        custom_id="ticket:internal-note",
    )
    @mark_modal_callback
    async def internal_note(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.show_internal_note_modal(itx)

    @discord.ui.button(
        label="إضافة عضو",
        style=discord.ButtonStyle.secondary,
        emoji="➕",
        custom_id="ticket:add-member",
    )
    @mark_modal_callback
    async def add_member(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.show_ticket_member_modal(itx, "add")

    @discord.ui.button(
        label="طرد عضو",
        style=discord.ButtonStyle.secondary,
        emoji="➖",
        custom_id="ticket:remove-member",
    )
    @mark_modal_callback
    async def remove_member(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.show_ticket_member_modal(itx, "remove")

    @discord.ui.button(
        label="ترك التذكرة",
        style=discord.ButtonStyle.secondary,
        emoji="🚪",
        custom_id="ticket:unclaim",
    )
    async def unclaim(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.unclaim_ticket_from_interaction(itx)

    @discord.ui.button(
        label="تحويل التذكرة",
        style=discord.ButtonStyle.primary,
        emoji="🔁",
        custom_id="ticket:transfer",
    )
    @mark_modal_callback
    async def transfer(self, itx: discord.Interaction, btn: discord.ui.Button):
        cog = await self._cog(itx)
        if cog:
            await cog.show_ticket_member_modal(itx, "transfer")


class TicketRatingView(discord.ui.View):
    def __init__(self, ticket_id: int, user_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.ticket_id, self.user_id, self.guild_id = ticket_id, user_id, guild_id
        for stars in range(1, 6):
            button = discord.ui.Button(
                label=f"{stars} نجوم",
                style=discord.ButtonStyle.secondary if stars < 4 else discord.ButtonStyle.success,
                custom_id=f"ticket:rating:{ticket_id}:{stars}",
            )

            async def callback(itx: discord.Interaction, value=stars):
                if itx.user.id != self.user_id:
                    return await itx.response.send_message(
                        "هذا التقييم مخصص لصاحب التذكرة.", ephemeral=True
                    )
                await save_ticket_rating(
                    self.ticket_id,
                    self.guild_id,
                    self.user_id,
                    value,
                )
                for child in self.children:
                    child.disabled = True
                await itx.response.edit_message(
                    content=f"✅ شكراً لك، تم تسجيل تقييمك: {value}/5",
                    view=self,
                )

            button.callback = callback
            self.add_item(button)


# --- نظام أزرار الاقتراحات المتقدم ---
class SuggestionActionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def check_admin(self, itx: discord.Interaction) -> bool:
        if not itx.user.guild_permissions.manage_guild:
            await itx.response.send_message(
                "❌ هذا الإجراء متاح لإدارة السيرفر فقط!",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="قبول",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="sug_accept",
    )
    async def accept(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        if not await self.check_admin(itx):
            return
        embed = itx.message.embeds[0].copy()
        embed.color = 0x2ECC71
        embed.add_field(
            name="📌 القرار الإداري",
            value=f"🟢 **تم القبول** بواسطة {itx.user.mention}",
            inline=False,
        )
        await itx.message.edit(embed=embed, view=None)
        await itx.response.send_message(
            "✅ تم قبول الاقتراح واعتماده.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="قيد الدراسة",
        style=discord.ButtonStyle.primary,
        emoji="⏳",
        custom_id="sug_progress",
    )
    async def progress(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        if not await self.check_admin(itx):
            return
        embed = itx.message.embeds[0].copy()
        embed.color = 0xF39C12
        embed.add_field(
            name="📌 الحالة",
            value=(
                f"🟡 **قيد التنفيذ والدراسة** بواسطة "
                f"{itx.user.mention}"
            ),
            inline=False,
        )
        await itx.message.edit(embed=embed, view=None)
        await itx.response.send_message(
            "⏳ تم تحويل الاقتراح إلى قيد التنفيذ.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="رفض",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="sug_reject",
    )
    async def reject(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        if not await self.check_admin(itx):
            return
        embed = itx.message.embeds[0].copy()
        embed.color = 0xE74C3C
        embed.add_field(
            name="📌 القرار الإداري",
            value=f"🔴 **تم الرفض** بواسطة {itx.user.mention}",
            inline=False,
        )
        await itx.message.edit(embed=embed, view=None)
        await itx.response.send_message(
            "❌ تم رفض الاقتراح.",
            ephemeral=True,
        )


# --- نظام التصويت الحي بالأزرار والنسب المئوية ---
class LivePollView(discord.ui.View):
    def __init__(self, question: str, opt_a: str, opt_b: str):
        super().__init__(timeout=None)
        self.question, self.opt_a, self.opt_b = question, opt_a, opt_b
        self.votes_a, self.votes_b = set(), set()

    def make_embed(self) -> discord.Embed:
        total = len(self.votes_a) + len(self.votes_b)
        percent_a = (
            round((len(self.votes_a) / total) * 100)
            if total > 0
            else 0
        )
        percent_b = (
            round((len(self.votes_b) / total) * 100)
            if total > 0
            else 0
        )
        embed = discord.Embed(
            title="📊 تصويت تفاعلي حي",
            description=f"### {self.question}",
            color=0x3498DB,
        )
        embed.add_field(
            name=f"1️⃣ {self.opt_a}",
            value=f"**{len(self.votes_a)}** صوت ({percent_a}%)",
            inline=True,
        )
        embed.add_field(
            name=f"2️⃣ {self.opt_b}",
            value=f"**{len(self.votes_b)}** صوت ({percent_b}%)",
            inline=True,
        )
        embed.set_footer(text=f"إجمالي الأصوات: {total}")
        return embed

    @discord.ui.button(
        label="الخيار الأول",
        style=discord.ButtonStyle.secondary,
        emoji="1️⃣",
        custom_id="btn_poll_a",
    )
    async def vote_a(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        self.votes_b.discard(itx.user.id)
        self.votes_a.add(itx.user.id)
        await itx.response.edit_message(
            embed=self.make_embed(),
            view=self,
        )

    @discord.ui.button(
        label="الخيار الثاني",
        style=discord.ButtonStyle.secondary,
        emoji="2️⃣",
        custom_id="btn_poll_b",
    )
    async def vote_b(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        self.votes_a.discard(itx.user.id)
        self.votes_b.add(itx.user.id)
        await itx.response.edit_message(
            embed=self.make_embed(),
            view=self,
        )


class Community(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.counters: dict[int, dict[str, int]] = {}
        self.update_counters_task.start()
        self.reminder_task.start()
        logger.info("Community counter updater initialized.")

    def cog_unload(self):
        self.update_counters_task.cancel()
        self.reminder_task.cancel()

    async def deploy_ticket_panel(
        self,
        channel_id: int,
        categories_config: list[dict] | None = None,
        embed_config: dict | None = None,
    ) -> dict:
        """Publish or update a persistent dropdown panel."""
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            raise ValueError("ticket panel channel was not found")
        categories = normalize_ticket_categories(categories_config)
        guild_id = int(channel.guild.id)
        await replace_ticket_options(guild_id, categories)
        stored_config = await get_ticket_config(guild_id) or {}
        config = {**stored_config, **(embed_config or {})}
        embed = discord.Embed(
            title=str(config.get("embed_title") or "🎫 مركز الدعم والتذاكر")[:256],
            description=str(
                config.get("embed_description")
                or (
                    "اختر التصنيف الأقرب لطلبك. ستظهر لك نافذة قصيرة لجمع "
                    "التفاصيل قبل فتح قناة خاصة مع فريق الدعم."
                )
            )[:4096],
            color=int(config.get("embed_color") or 0x6366F1),
        )
        embed.set_footer(
            text=str(
                config.get("footer_text")
                or "Help Desk • اختر تصنيفاً لبدء المحادثة"
            )[:2048]
        )
        view = (
            TicketPanelView(categories)
            if str(config.get("panel_mode") or "dropdown").lower() == "buttons"
            else TicketSelectView(categories, guild_id)
        )
        message = None
        previous_message_id = config.get("message_id")
        previous_channel_id = config.get("channel_id")
        if previous_message_id and int(previous_channel_id or channel.id) == channel.id:
            try:
                message = await channel.fetch_message(int(previous_message_id))
                await message.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.info(
                    "Ticket panel message %s is unavailable; sending a replacement.",
                    previous_message_id,
                )
        if message is None:
            message = await channel.send(embed=embed, view=view)
            try:
                await message.pin()
            except (AttributeError, discord.Forbidden, discord.HTTPException):
                logger.debug("Unable to pin ticket panel message", exc_info=True)
        self.bot.add_view(view, message_id=message.id)
        await save_ticket_config(
            guild_id,
            channel.id,
            message.id,
            embed_title=str(config.get("embed_title") or "🎫 مركز الدعم والتذاكر"),
            embed_description=str(config.get("embed_description") or (
                "اختر التصنيف الأقرب لطلبك. ستظهر لك نافذة قصيرة لجمع "
                "التفاصيل قبل فتح قناة خاصة مع فريق الدعم."
            )),
            embed_color=int(config.get("embed_color") or 0x6366F1),
            footer_text=str(config.get("footer_text") or "Help Desk • اختر تصنيفاً لبدء المحادثة"),
        )
        panel = await save_ticket_panel(
            guild_id,
            channel.id,
            message.id,
            categories,
            title=str(config.get("embed_title") or "🎫 مركز الدعم والتذاكر"),
            description=str(config.get("embed_description") or ""),
            color=int(config.get("embed_color") or 0x6366F1),
            mode=str(config.get("panel_mode") or "dropdown"),
        )
        # Keep the public Discord deployment contract snowflake-safe for
        # browser JSON consumers and legacy callers that compare IDs as text.
        for key in ("guild_id", "channel_id", "message_id"):
            if panel.get(key) is not None:
                panel[key] = str(panel[key])
        return panel

    async def deploy_persistent_dropdown_panel(
        self,
        channel_id: int,
        categories_config: list[dict] | None = None,
        embed_config: dict | None = None,
    ) -> dict:
        """Publish the static-custom-id dropdown without replacing legacy panels."""
        channel = self.bot.get_channel(int(channel_id))
        if channel is None or not hasattr(channel, "send"):
            raise ValueError("ticket dropdown channel was not found")
        categories = normalize_ticket_categories(categories_config)
        guild_id = int(channel.guild.id)
        config = dict(embed_config or {})
        try:
            color = int(config.get("embed_color") or 0x5865F2)
        except (TypeError, ValueError):
            color = 0x5865F2
        embed = discord.Embed(
            title=str(config.get("embed_title") or "🎫 مركز الدعم والتذاكر")[:256],
            description=str(config.get("embed_description") or "اختر التصنيف لفتح تذكرة خاصة مع فريق الدعم.")[:4096],
            color=max(0, min(color, 0xFFFFFF)),
        )
        embed.set_footer(text=str(config.get("footer_text") or "Help Desk • PR1ME TEAM")[:2048])
        view = PersistentDropdownTicketView(categories, guild_id)
        message = None
        previous_message_id = config.get("message_id")
        previous_channel_id = config.get("channel_id")
        if previous_message_id and int(previous_channel_id or channel.id) == channel.id:
            try:
                message = await channel.fetch_message(int(previous_message_id))
                await message.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.info("Persistent ticket dropdown message %s is unavailable.", previous_message_id)
        if message is None:
            message = await channel.send(embed=embed, view=view)
        self.bot.add_view(view, message_id=message.id)
        await replace_ticket_options(guild_id, categories)
        await save_ticket_config(
            guild_id,
            channel.id,
            message.id,
            embed_title=str(config.get("embed_title") or "🎫 مركز الدعم والتذاكر"),
            embed_description=str(config.get("embed_description") or "اختر التصنيف لفتح تذكرة خاصة مع فريق الدعم."),
            embed_color=max(0, min(color, 0xFFFFFF)),
            footer_text=str(config.get("footer_text") or "Help Desk • PR1ME TEAM"),
        )
        return {
            "guild_id": guild_id,
            "channel_id": channel.id,
            "message_id": message.id,
            "categories": categories,
        }

    async def get_ticket_config(self, guild_id: int) -> dict:
        return {
            "config": await get_ticket_config(guild_id),
            "options": await get_ticket_options(guild_id),
        }

    async def save_ticket_config(
        self,
        guild_id: int,
        config: dict,
        options: list[dict],
    ) -> dict:
        saved = await save_ticket_config(
            guild_id,
            config.get("channel_id"),
            config.get("message_id"),
            config.get("embed_title") or "الدعم الفني",
            config.get("embed_description") or "",
            int(config.get("embed_color") or 0x5865F2),
            config.get("footer_text") or "PR1ME TEAM Support",
        )
        await replace_ticket_options(guild_id, options)
        return {"config": saved, "options": await get_ticket_options(guild_id)}

    async def get_active_tickets(self, guild_id: int) -> list[dict]:
        return await get_active_tickets(guild_id)

    async def get_ticket_transcripts(
        self,
        guild_id: int,
        query: str = "",
    ) -> list[dict]:
        return await get_ticket_transcripts(guild_id, query)

    async def get_ticket_archive(
        self,
        guild_id: int,
        query: str = "",
    ) -> list[dict]:
        return await get_ticket_archive(guild_id, query)

    async def get_ticket_transcript(
        self,
        guild_id: int,
        ticket_id: int,
    ) -> dict | None:
        return await get_ticket_transcript(guild_id, ticket_id)

    async def get_staff_kpis(self, guild_id: int) -> list[dict]:
        return await get_staff_kpis(guild_id)

    async def get_canned_responses(self, guild_id: int) -> list[dict]:
        return await get_canned_responses(guild_id)

    async def get_ticket_notes(self, guild_id: int, ticket_id: int) -> list[dict]:
        return await get_ticket_notes(guild_id, ticket_id)

    async def get_ticket(self, guild_id: int, ticket_id: int) -> dict | None:
        return await get_ticket(guild_id, ticket_id)

    async def publish_ticket_evaluation(
        self,
        ticket_id: int,
        guild_id: int,
        stars: int,
        feedback: str,
    ):
        ticket = await get_ticket(guild_id, ticket_id)
        config = await get_ticket_config(guild_id) or {}
        channel_id = config.get("evaluation_channel_id")
        channel = self.bot.get_channel(int(channel_id)) if channel_id else None
        if channel is None or ticket is None:
            return
        guild = self.bot.get_guild(int(guild_id))
        user = guild.get_member(int(ticket["user_id"])) if guild else None
        staff = guild.get_member(int(ticket["closed_by"])) if guild and ticket.get("closed_by") else None
        embed = discord.Embed(
            title="⭐ تقييم جديد لخدمة التذاكر",
            description=f"تم استلام تقييم للتذكرة **#{ticket_id}**.",
            color=0xF59E0B,
            timestamp=discord.utils.utcnow(),
        )
        if user:
            avatar = getattr(getattr(user, "display_avatar", None), "url", None)
            if avatar:
                embed.set_author(name=user.display_name, icon_url=str(avatar))
        embed.add_field(
            name="العضو",
            value=getattr(user, "mention", f"<@{ticket['user_id']}>"),
            inline=True,
        )
        embed.add_field(
            name="المشرف",
            value=getattr(staff, "mention", f"<@{ticket.get('closed_by') or 0}>"),
            inline=True,
        )
        embed.add_field(name="التقييم", value=f"{'⭐' * int(stars)} ({int(stars)}/5)", inline=True)
        embed.add_field(name="الملاحظات", value=str(feedback or "بدون ملاحظات")[:1024], inline=False)
        embed.set_footer(text=f"PR1ME Ticket Engine • ticket:{ticket_id}")
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            logger.info("Could not publish ticket evaluation for %s", ticket_id)

    async def save_canned_response(
        self,
        guild_id: int,
        title: str,
        content: str,
        category: str = "عام",
        created_by: int | str | None = None,
        response_id: int | None = None,
        shortcut: str | None = None,
        sticker_id: int | str | None = None,
    ) -> dict:
        return await save_canned_response(
            guild_id,
            title,
            content,
            category,
            created_by,
            response_id,
            shortcut,
            sticker_id,
        )

    async def delete_canned_response(self, guild_id: int, response_id: int) -> bool:
        return await delete_canned_response(guild_id, response_id)

    @staticmethod
    def _render_ticket_reply(template: str, itx: discord.Interaction, ticket: dict) -> str:
        guild = itx.guild
        channel = itx.channel
        values = {
            "user": f"<@{ticket['user_id']}>",
            "staff": getattr(itx.user, "mention", f"<@{itx.user.id}>"),
            "channel": getattr(channel, "mention", f"#{getattr(channel, 'name', 'ticket')}"),
            "server": getattr(guild, "name", "السيرفر"),
            "count": f"{getattr(guild, 'member_count', 0):,}",
            "members": f"{getattr(guild, 'member_count', 0):,}",
            "ticket": str(ticket["id"]),
            "subject": ticket.get("subject", ""),
            "category": ticket.get("category_label", ""),
        }
        rendered = str(template or "")
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))

        def choose(match: re.Match) -> str:
            options = [item.strip() for item in match.group(1).split("|") if item.strip()]
            return options[0] if options else ""

        return re.sub(r"\{random:([^{}|]+(?:\|[^{}|]+)+)\}", choose, rendered)[:2000]

    async def _find_ticket_sticker(self, guild, sticker_id):
        if sticker_id in (None, ""):
            return None
        for sticker in getattr(guild, "stickers", ()) or ():
            if int(getattr(sticker, "id", 0)) == int(sticker_id):
                return sticker
        fetch_stickers = getattr(guild, "fetch_stickers", None)
        if fetch_stickers is not None:
            try:
                for sticker in await fetch_stickers():
                    if int(getattr(sticker, "id", 0)) == int(sticker_id):
                        return sticker
            except (discord.Forbidden, discord.HTTPException):
                logger.debug("Unable to resolve canned response sticker", exc_info=True)
        return None

    @app_commands.command(
        name="ticket_reply",
        description="إرسال رد سريع داخل التذكرة مع دعم المتغيرات والملصقات",
    )
    @app_commands.describe(response="اسم الرد السريع أو اختصاره")
    async def ticket_reply(self, itx: discord.Interaction, response: str):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذا الأمر يعمل داخل تذكرة مفتوحة فقط.", ephemeral=True)
        if not self._is_ticket_staff(itx.user, ticket):
            return await self._ticket_denied(itx)
        query = str(response).strip().casefold()
        choices = await get_canned_responses(itx.guild.id)
        selected = next(
            (
                item for item in choices
                if query in {
                    str(item.get("title", "")).casefold(),
                    str(item.get("shortcut", "")).casefold(),
                    str(item.get("id", "")),
                }
            ),
            None,
        )
        if not selected:
            return await itx.response.send_message(
                "لم أجد رداً سريعاً بهذا الاسم أو الاختصار.", ephemeral=True
            )
        content = self._render_ticket_reply(selected["content"], itx, ticket)
        sticker = await self._find_ticket_sticker(itx.guild, selected.get("sticker_id"))
        try:
            await itx.channel.send(
                content,
                stickers=[sticker] if sticker else [],
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            return await itx.response.send_message("تعذر إرسال الرد السريع في هذه القناة.", ephemeral=True)
        await record_ticket_response(ticket["guild_id"], ticket["id"])
        await itx.response.send_message(
            f"✅ تم إرسال الرد السريع: **{selected['title']}**",
            ephemeral=True,
        )

    async def add_internal_note(
        self, guild_id: int, ticket_id: int, staff_id: int, content: str
    ) -> dict | None:
        return await add_ticket_note(guild_id, ticket_id, staff_id, content)

    async def reassign_ticket(
        self,
        guild_id: int,
        ticket_id: int,
        staff_id: int,
    ) -> dict | None:
        return await claim_ticket(guild_id, ticket_id, staff_id)

    async def update_ticket_priority(
        self, guild_id: int, ticket_id: int, priority: str
    ) -> dict | None:
        return await set_ticket_priority(guild_id, ticket_id, priority)

    async def set_ticket_priority_from_interaction(
        self,
        itx: discord.Interaction,
        priority: str,
    ):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not await self._ticket_action_allowed(itx.user, ticket, "priority"):
            return await self._ticket_denied(itx)
        if priority not in TICKET_PRIORITIES:
            return await itx.response.send_message("الأولوية غير صالحة.", ephemeral=True)
        updated = await set_ticket_priority(itx.guild.id, ticket["id"], priority)
        if not updated:
            return await itx.response.send_message("تعذر تحديث الأولوية.", ephemeral=True)
        await itx.channel.edit(
            topic=f"Ticket • {updated['category_label']} • {priority.upper()}"
        )
        color = {"normal": 0xF59E0B, "high": 0xF59E0B, "management": 0xEF4444}[priority]
        label = {"normal": "عادية", "high": "مرتفعة", "management": "عاجلة"}[priority]
        await save_ticket_log(
            updated["id"], updated["guild_id"], "priority_changed",
            staff_id=itx.user.id, metadata={"priority": priority},
        )
        await send_ticket_action_embed(
            itx.channel,
            "🚨 تغيرت أولوية التذكرة",
            f"تم ضبط أولوية التذكرة **#{updated['id']}** إلى **{label}**.",
            color,
            itx.user,
            extra_field=("الأولوية", label),
        )
        await itx.response.send_message(f"تم تحديث الأولوية إلى **{label}**.", ephemeral=True)

    async def transfer_ticket_category_from_interaction(
        self,
        itx: discord.Interaction,
        category: dict,
    ):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not await self._ticket_action_allowed(itx.user, ticket, "transfer"):
            return await self._ticket_denied(itx)
        parent = None
        if category.get("category_id"):
            parent = itx.guild.get_channel(int(category["category_id"]))
            if not isinstance(parent, discord.CategoryChannel):
                parent = None
        try:
            await itx.channel.edit(
                category=parent,
                topic=f"Ticket • {category['label']} • transferred",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await itx.response.send_message("تعذر نقل قناة التذكرة إلى القسم المحدد.", ephemeral=True)
        await save_ticket_log(
            ticket["id"], ticket["guild_id"], "category_transferred",
            staff_id=itx.user.id, metadata={"category": category.get("key"), "label": category.get("label")},
        )
        await send_ticket_action_embed(
            itx.channel,
            "🔄 تم تحويل قسم التذكرة",
            f"تم نقل التذكرة إلى قسم **{category['label']}**.",
            0x6366F1,
            itx.user,
            extra_field=("القسم", category["label"]),
        )
        await itx.response.send_message("تم نقل التذكرة إلى القسم الجديد.", ephemeral=True)

    async def set_ticket_status(
        self, guild_id: int, ticket_id: int, status: str, staff_id: int | None = None
    ) -> dict | None:
        return await set_ticket_status(guild_id, ticket_id, status, staff_id=staff_id)

    async def reopen_ticket(
        self, guild_id: int, ticket_id: int, staff_id: int
    ) -> dict | None:
        ticket = await reopen_ticket(guild_id, ticket_id, staff_id)
        if not ticket:
            return None
        channel = self.bot.get_channel(ticket["channel_id"])
        if channel is not None:
            try:
                await channel.edit(
                    name=f"ticket-reopened-{ticket['id']}"[:100],
                    topic=f"Ticket • {ticket['category_label']} • reopened by dashboard",
                )
                member = channel.guild.get_member(ticket["user_id"])
                if member:
                    overwrite = channel.overwrites_for(member)
                    overwrite.view_channel = True
                    overwrite.send_messages = True
                    overwrite.read_message_history = True
                    await channel.set_permissions(member, overwrite=overwrite)
                for role_id in ticket.get("support_role_ids", []):
                    role = channel.guild.get_role(int(role_id))
                    if role:
                        overwrite = channel.overwrites_for(role)
                        overwrite.view_channel = True
                        overwrite.send_messages = True
                        overwrite.read_message_history = True
                        await channel.set_permissions(role, overwrite=overwrite)
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("Could not reopen ticket channel %s", ticket["channel_id"])
        return ticket

    async def _delete_ticket_channel_after_delay(self, channel, ticket_id: int):
        await asyncio.sleep(10)
        try:
            await channel.delete(reason=f"Ticket #{ticket_id} closed; delayed cleanup")
        except discord.NotFound:
            return
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Could not delete closed ticket channel %s", getattr(channel, "id", "?"))

    def _schedule_ticket_channel_deletion(self, channel, ticket_id: int):
        asyncio.create_task(self._delete_ticket_channel_after_delay(channel, ticket_id))

    async def force_close_ticket(
        self,
        guild_id: int,
        ticket_id: int,
        staff_id: int,
        reason: str = "أُغلقت من لوحة الإدارة",
    ) -> dict | None:
        active = await get_active_tickets(guild_id)
        ticket = next((item for item in active if item["id"] == int(ticket_id)), None)
        if not ticket:
            return None
        channel = self.bot.get_channel(ticket["channel_id"])
        if channel is not None:
            text, content_html = await self._build_transcript(channel, ticket)
            text += f"\n\nClose reason: {reason}"
            content_html = content_html.replace(
                "</body></html>",
                f"<hr><p><strong>سبب الإغلاق:</strong> {html.escape(reason)}</p></body></html>",
            )
            await save_ticket_transcript(
                ticket["id"], ticket["guild_id"], ticket["channel_id"], text, content_html
            )
            try:
                await channel.edit(
                    name=f"archived-ticket-{ticket['id']}"[:100],
                    topic=f"Archived ticket • closed by dashboard",
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("Could not archive ticket channel %s", ticket["channel_id"])
        closed = await close_ticket(guild_id, ticket_id, staff_id, reason)
        if closed and channel is not None:
            self._schedule_ticket_channel_deletion(channel, closed["id"])
        return closed

    @staticmethod
    def _is_ticket_staff(member, ticket: dict) -> bool:
        permissions = getattr(member, "guild_permissions", None)
        if permissions and (
            getattr(permissions, "administrator", False)
            or getattr(permissions, "manage_channels", False)
            or getattr(permissions, "manage_guild", False)
        ):
            return True
        allowed = {str(role_id) for role_id in ticket.get("support_role_ids", [])}
        return bool(
            allowed.intersection(
                {str(role.id) for role in getattr(member, "roles", [])}
            )
        )

    async def _ticket_action_allowed(self, member, ticket: dict, action: str | None) -> bool:
        """Apply the optional CRM matrix without removing legacy staff access."""
        if not self._is_ticket_staff(member, ticket):
            return False
        if not action:
            return True
        configured = await get_ticket_permissions(ticket["guild_id"])
        role_ids = configured.get(str(action))
        # An omitted/empty action preserves the legacy support-role policy.
        if not role_ids:
            return True
        permissions = getattr(member, "guild_permissions", None)
        if permissions and getattr(permissions, "administrator", False):
            return True
        return bool({str(role.id) for role in getattr(member, "roles", [])}.intersection(role_ids))

    async def _ticket_denied(self, itx: discord.Interaction):
        message = "⛔ هذا الإجراء متاح لفريق الدعم والإدارة فقط."
        if itx.response.is_done():
            await itx.followup.send(message, ephemeral=True)
        else:
            await itx.response.send_message(message, ephemeral=True)

    async def open_ticket(
        self,
        itx: discord.Interaction,
        category: dict,
        subject: str,
        details: str,
        intake_data: dict | None = None,
    ) -> dict | None:
        guild = itx.guild
        if guild is None:
            return await itx.response.send_message(
                "🔒 فتح التذاكر متاح داخل السيرفرات فقط.", ephemeral=True
            )
        if await is_ticket_user_blacklisted(guild.id, itx.user.id):
            return await itx.response.send_message(
                "⛔ لا يمكنك فتح تذكرة حالياً. تواصل مع إدارة السيرفر إذا كنت ترى أن هذا بالخطأ.",
                ephemeral=True,
            )
        existing = await get_active_ticket_for_user_category(
            guild.id,
            itx.user.id,
            category["key"],
        )
        if existing:
            return await itx.response.send_message(
                f"📌 لديك تذكرة مفتوحة في قسم «{category['label']}» بالفعل.",
                ephemeral=True,
            )
        parent = None
        if category.get("category_id"):
            parent = guild.get_channel(int(category["category_id"]))
            if not isinstance(parent, discord.CategoryChannel):
                parent = None
        if parent is None and isinstance(itx.channel, discord.TextChannel):
            parent = itx.channel.category
        safe_name = re.sub(r"[^a-zA-Z0-9-]+", "-", itx.user.name.lower()).strip("-")
        safe_name = (safe_name or f"user-{itx.user.id}")[:80]
        channel_name = f"ticket-{safe_name}"[:100]
        existing_names = {
            str(item.name).casefold()
            for item in getattr(guild, "channels", ())
            if getattr(item, "name", None)
        }
        if channel_name.casefold() in existing_names:
            channel_name = f"ticket-{safe_name[:85]}-{itx.user.id}"[:100]
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            itx.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        for role_id in category.get("support_role_ids", []):
            role = guild.get_role(int(role_id))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            )
        channel = await guild.create_text_channel(
            name=channel_name,
            category=parent,
            overwrites=overwrites,
            topic=f"Ticket • {category['label']} • Normal • {subject[:80]}",
        )
        try:
            ticket = await create_ticket(
                guild.id,
                channel.id,
                itx.user.id,
                category["key"],
                category["label"],
                subject,
                details,
                category.get("support_role_ids"),
                category.get("senior_role_ids"),
                intake_data=intake_data,
            )
        except sqlite3.IntegrityError:
            try:
                await channel.delete(reason="Duplicate active ticket prevented")
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("Could not remove duplicate ticket channel %s", channel.id)
            return await itx.response.send_message(
                f"📌 لديك تذكرة مفتوحة في قسم «{category['label']}» بالفعل.",
                ephemeral=True,
            )
        embed = self._ticket_embed(ticket)
        if category.get("welcome_msg"):
            embed.add_field(
                name="رسالة القسم",
                value=str(category["welcome_msg"])[:1024],
                inline=False,
            )
        ping_role_ids = category.get("ping_role_ids") or category.get("support_role_ids") or []
        ping_mentions = " ".join(
            f"<@&{role_id}>" for role_id in ping_role_ids if str(role_id).isdigit()
        )
        message = await channel.send(
            content=ping_mentions or itx.user.mention,
            embed=embed,
            view=StreamlinedTicketControlsView(),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
        )
        try:
            await message.pin()
        except (AttributeError, discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to pin ticket control message", exc_info=True)
        await itx.response.send_message(
            f"✅ تم فتح تذكرتك: {channel.mention}", ephemeral=True
        )
        analytics = self.bot.get_cog("Analytics")
        if analytics:
            await analytics.log_ticket_event(
                guild,
                "🎫 فتح تذكرة",
                f"تم فتح تذكرة جديدة في {channel.mention}.",
                actor=itx.user,
                fields=[
                    ("🎫 التذكرة", f"#{ticket['id']}", True),
                    ("📂 التصنيف", category["label"], True),
                    ("📝 الموضوع", subject, False),
                ],
            )
        return ticket

    @staticmethod
    def _ticket_embed(ticket: dict) -> discord.Embed:
        priority = {
            "normal": "🟢 عادية",
            "high": "🟠 عالية",
            "management": "🔴 تصعيد إداري",
        }.get(ticket.get("priority"), "🟢 عادية")
        embed = discord.Embed(
            title=f"🎫 {ticket['category_label']} · #{ticket['id']}",
            description=ticket["details"],
            color={
                "normal": 0x00D9A6,
                "high": 0xF59E0B,
                "management": 0xEF4444,
            }.get(ticket.get("priority"), 0x00D9A6),
        )
        embed.add_field(name="الموضوع", value=ticket["subject"], inline=False)
        embed.add_field(name="الأولوية", value=priority, inline=True)
        embed.add_field(
            name="الحالة",
            value={
                "active": "🟢 قيد المعالجة",
                "waiting_user": "⏳ بانتظار العميل",
                "waiting_staff": "📥 بانتظار فريق الدعم",
            }.get(ticket.get("status"), "🟢 قيد المعالجة"),
            inline=True,
        )
        for key, value in list(ticket.get("intake_data", {}).items())[:3]:
            embed.add_field(name=str(key)[:256], value=str(value)[:1024] or "—", inline=True)
        embed.add_field(
            name="التعليمات",
            value=(
                "استلم التذكرة، أضف أو أزل أعضاء عند الحاجة، حوّلها لموظف آخر "
                "أو اتركها للفريق، ثم أغلقها بعد حل الطلب."
            ),
            inline=False,
        )
        return embed

    async def claim_ticket_from_interaction(self, itx: discord.Interaction):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not await self._ticket_action_allowed(itx.user, ticket, "claim"):
            return await self._ticket_denied(itx)
        if ticket.get("claimed_by") and ticket["claimed_by"] != itx.user.id:
            return await itx.response.send_message(
                "👤 التذكرة مستلمة من عضو آخر في فريق الدعم.", ephemeral=True
            )
        ticket = await claim_ticket(
            itx.guild.id,
            ticket["id"],
            itx.user.id,
            expected_claimed_by=ticket.get("claimed_by"),
        )
        if not ticket:
            return await itx.response.send_message(
                "تعذر استلام التذكرة؛ سبقك موظف آخر.",
                ephemeral=True,
            )
        overwrites = itx.channel.overwrites
        for role_id in ticket.get("support_role_ids", []):
            role = itx.guild.get_role(int(role_id))
            if role:
                overwrite = itx.channel.overwrites_for(role)
                overwrite.send_messages = False
                await itx.channel.set_permissions(role, overwrite=overwrite)
        overwrite = itx.channel.overwrites_for(itx.user)
        overwrite.view_channel = True
        overwrite.send_messages = True
        await itx.channel.set_permissions(itx.user, overwrite=overwrite)
        await itx.channel.edit(
            topic=f"Ticket • {ticket['category_label']} • مستلمة بواسطة {itx.user.display_name}"
        )
        await save_ticket_log(
            ticket["id"],
            ticket["guild_id"],
            "claimed",
            staff_id=itx.user.id,
            metadata={"claimed_by": itx.user.id},
        )
        await send_ticket_action_embed(
            itx.channel,
            "✋ تم استلام التذكرة",
            f"تم استلام التذكرة **#{ticket['id']}** حصرياً بواسطة {itx.user.mention}.",
            0x10B981,
            itx.user,
            extra_field=("الحالة", "قيد المعالجة"),
        )
        await itx.response.send_message("✅ تم استلام التذكرة حصرياً لك.", ephemeral=True)

    async def _resolve_ticket_member(self, guild, raw_value: str):
        member_id = _member_id_from_text(raw_value)
        if member_id is None:
            return None
        member = guild.get_member(member_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(member_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def show_ticket_member_modal(self, itx: discord.Interaction, action: str):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        permission_action = {
            "add": "add_member",
            "remove": "remove_member",
            "transfer": "transfer",
        }.get(action)
        if not await self._ticket_action_allowed(itx.user, ticket, permission_action):
            return await self._ticket_denied(itx)
        await itx.response.send_modal(TicketMemberActionModal(action))

    async def handle_ticket_member_action(
        self,
        itx: discord.Interaction,
        action: str,
        raw_member: str,
    ):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        permission_action = {
            "add": "add_member",
            "remove": "remove_member",
            "transfer": "transfer",
        }.get(action)
        if not await self._ticket_action_allowed(itx.user, ticket, permission_action):
            return await self._ticket_denied(itx)
        member = await self._resolve_ticket_member(itx.guild, raw_member)
        if member is None:
            return await itx.response.send_message(
                "لم أجد هذا العضو داخل السيرفر. أرسل Discord ID صحيحاً أو منشن العضو.",
                ephemeral=True,
            )
        if action == "add":
            await itx.channel.set_permissions(
                member,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                ),
            )
            await save_ticket_log(
                ticket["id"], ticket["guild_id"], "member_added",
                staff_id=itx.user.id, target_user_id=member.id,
            )
            await send_ticket_action_embed(
                itx.channel,
                "➕ تمت إضافة عضو",
                f"تم منح {member.mention} صلاحية الوصول إلى التذكرة.",
                0x10B981,
                itx.user,
                target_user=member,
            )
            return await itx.response.send_message(
                f"✅ تمت إضافة {member.mention} إلى التذكرة.",
                ephemeral=True,
            )
        if action == "remove":
            if member.id == ticket["user_id"]:
                return await itx.response.send_message(
                    "لا يمكن طرد صاحب التذكرة. استخدم إغلاق التذكرة عند انتهاء الطلب.",
                    ephemeral=True,
                )
            if itx.guild.me and member.id == itx.guild.me.id:
                return await itx.response.send_message(
                    "لا يمكن إزالة البوت من قناة التذكرة.",
                    ephemeral=True,
                )
            member_role_ids = {str(role.id) for role in getattr(member, "roles", [])}
            support_role_ids = {str(role_id) for role_id in ticket.get("support_role_ids", [])}
            if member_role_ids.intersection(support_role_ids):
                await itx.channel.set_permissions(
                    member,
                    overwrite=discord.PermissionOverwrite(
                        view_channel=False,
                        send_messages=False,
                        read_message_history=False,
                    ),
                )
            else:
                await itx.channel.set_permissions(member, overwrite=None)
            await save_ticket_log(
                ticket["id"], ticket["guild_id"], "member_removed",
                staff_id=itx.user.id, target_user_id=member.id,
            )
            await send_ticket_action_embed(
                itx.channel,
                "➖ تمت إزالة عضو",
                f"تمت إزالة {member.mention} من صلاحيات التذكرة.",
                0xEF4444,
                itx.user,
                target_user=member,
            )
            return await itx.response.send_message(
                f"✅ تمت إزالة {member.mention} من التذكرة.",
                ephemeral=True,
            )
        if action == "transfer":
            if not self._is_ticket_staff(member, ticket):
                return await itx.response.send_message(
                    "لا يمكن تحويل التذكرة إلا إلى عضو من فريق الدعم أو الإدارة.",
                    ephemeral=True,
                )
            if ticket.get("claimed_by") == member.id:
                return await itx.response.send_message(
                    "التذكرة مستلمة بالفعل من هذا العضو.",
                    ephemeral=True,
                )
            old_staff = (
                itx.guild.get_member(ticket["claimed_by"])
                if ticket.get("claimed_by")
                else None
            )
            ticket = await claim_ticket(
                itx.guild.id,
                ticket["id"],
                member.id,
                expected_claimed_by=ticket.get("claimed_by"),
            )
            if not ticket:
                return await itx.response.send_message(
                    "تعذر تحويل التذكرة؛ تغير المستلم قبل حفظ العملية.",
                    ephemeral=True,
                )
            if old_staff and old_staff.id != member.id:
                old_overwrite = itx.channel.overwrites_for(old_staff)
                old_overwrite.send_messages = None
                await itx.channel.set_permissions(old_staff, overwrite=old_overwrite)
            for role_id in ticket.get("support_role_ids", []):
                role = itx.guild.get_role(int(role_id))
                if role:
                    role_overwrite = itx.channel.overwrites_for(role)
                    role_overwrite.send_messages = False
                    await itx.channel.set_permissions(role, overwrite=role_overwrite)
            target_overwrite = itx.channel.overwrites_for(member)
            target_overwrite.view_channel = True
            target_overwrite.send_messages = True
            target_overwrite.read_message_history = True
            await itx.channel.set_permissions(member, overwrite=target_overwrite)
            await itx.channel.edit(
                topic=f"Ticket • {ticket['category_label']} • مستلمة بواسطة {member.display_name}"
            )
            await save_ticket_log(
                ticket["id"], ticket["guild_id"], "transferred",
                staff_id=itx.user.id, target_user_id=member.id,
            )
            await send_ticket_action_embed(
                itx.channel,
                "🔄 تم تحويل التذكرة",
                f"تم تحويل التذكرة إلى {member.mention}.",
                0x6366F1,
                itx.user,
                target_user=member,
            )
            return await itx.response.send_message(
                f"🔁 تم تحويل التذكرة إلى {member.mention}.",
                ephemeral=True,
            )
        await itx.response.send_message("إجراء التذكرة غير معروف.", ephemeral=True)

    async def unclaim_ticket_from_interaction(self, itx: discord.Interaction):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not await self._ticket_action_allowed(itx.user, ticket, "claim"):
            return await self._ticket_denied(itx)
        if ticket.get("claimed_by") != itx.user.id:
            return await itx.response.send_message(
                "لا يمكنك ترك تذكرة لم تستلمها أنت.",
                ephemeral=True,
            )
        ticket = await unclaim_ticket(itx.guild.id, ticket["id"], itx.user.id)
        if not ticket:
            return await itx.response.send_message(
                "تعذر ترك التذكرة؛ ربما استلمها موظف آخر.",
                ephemeral=True,
            )
        for role_id in ticket.get("support_role_ids", []):
            role = itx.guild.get_role(int(role_id))
            if role:
                role_overwrite = itx.channel.overwrites_for(role)
                role_overwrite.view_channel = True
                role_overwrite.send_messages = True
                await itx.channel.set_permissions(role, overwrite=role_overwrite)
        overwrite = itx.channel.overwrites_for(itx.user)
        overwrite.send_messages = None
        await itx.channel.set_permissions(itx.user, overwrite=overwrite)
        await itx.channel.edit(
            topic=f"Ticket • {ticket['category_label']} • بانتظار فريق الدعم"
        )
        await itx.response.send_message(
            "🚪 تركت التذكرة وأصبحت متاحة لفريق الدعم من جديد.",
            ephemeral=True,
        )

    async def escalate_ticket_from_interaction(self, itx: discord.Interaction):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not await self._ticket_action_allowed(itx.user, ticket, "priority"):
            return await self._ticket_denied(itx)
        current = ticket.get("priority", "normal")
        priority = TICKET_PRIORITIES[
            min(TICKET_PRIORITIES.index(current) + 1, len(TICKET_PRIORITIES) - 1)
        ]
        ticket = await escalate_ticket(itx.guild.id, ticket["id"], priority)
        await itx.channel.edit(
            topic=f"Ticket • {ticket['category_label']} • {priority.upper()}"
        )
        mentions = [
            itx.guild.get_role(int(role_id)).mention
            for role_id in ticket.get("senior_role_ids", [])
            if itx.guild.get_role(int(role_id))
        ]
        if mentions:
            await itx.channel.send(
                " ".join(mentions) + " 🚨 تم تصعيد هذه التذكرة.",
                allowed_mentions=discord.AllowedMentions(roles=True, everyone=False),
            )
        await save_ticket_log(
            ticket["id"],
            ticket["guild_id"],
            "priority_changed",
            staff_id=itx.user.id,
            metadata={"priority": priority},
        )
        await send_ticket_action_embed(
            itx.channel,
            "🚨 تغيرت أولوية التذكرة",
            f"تم تحديث أولوية التذكرة **#{ticket['id']}**.",
            0xEF4444 if priority == "management" else 0xF59E0B,
            itx.user,
            extra_field=("الأولوية", priority),
        )
        await itx.response.send_message(
            f"🚨 تم تحديث الأولوية إلى: **{priority}**.", ephemeral=True
        )

    async def set_ticket_status_from_interaction(
        self, itx: discord.Interaction, status: str
    ):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not self._is_ticket_staff(itx.user, ticket):
            return await self._ticket_denied(itx)
        ticket = await set_ticket_status(itx.guild.id, ticket["id"], status, staff_id=itx.user.id)
        labels = {"active": "قيد المعالجة", "waiting_user": "بانتظار العميل", "waiting_staff": "بانتظار فريق الدعم"}
        await itx.channel.edit(topic=f"Ticket • {ticket['category_label']} • {labels[status]}")
        await itx.response.send_message(f"تم تحديث الحالة إلى: **{labels[status]}**.", ephemeral=True)

    async def show_internal_note_modal(self, itx: discord.Interaction):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not await self._ticket_action_allowed(itx.user, ticket, "note"):
            return await self._ticket_denied(itx)
        await itx.response.send_modal(InternalNoteModal())

    async def add_internal_note_from_interaction(self, itx: discord.Interaction, content: str):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not await self._ticket_action_allowed(itx.user, ticket, "note"):
            return await self._ticket_denied(itx)
        note = await self.add_internal_note(itx.guild.id, ticket["id"], itx.user.id, content)
        if not note:
            return await itx.response.send_message("تعذر حفظ الملاحظة.", ephemeral=True)
        await itx.response.send_message("📝 تم حفظ الملاحظة الداخلية.", ephemeral=True)

    async def show_close_modal(self, itx: discord.Interaction):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not await self._ticket_action_allowed(itx.user, ticket, "close"):
            return await self._ticket_denied(itx)
        await itx.response.send_modal(CloseTicketModal())

    async def delete_ticket_from_interaction(self, itx: discord.Interaction):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket:
            return await itx.response.send_message(
                "هذه القناة ليست تذكرة مسجلة.", ephemeral=True
            )
        if not await self._ticket_action_allowed(itx.user, ticket, "close"):
            return await self._ticket_denied(itx)
        await itx.response.defer(ephemeral=True)
        existing_transcript = await get_ticket_transcript(
            ticket["guild_id"],
            ticket["id"],
        )
        if not existing_transcript:
            text, content_html = await self._build_transcript(itx.channel, ticket)
            text += "\n\nClose reason: Deleted by staff"
            content_html = content_html.replace(
                "</body></html>",
                "<hr><p><strong>سبب الإغلاق:</strong> حذف بواسطة فريق الدعم</p></body></html>",
            )
            await save_ticket_transcript(
                ticket["id"],
                ticket["guild_id"],
                ticket["channel_id"],
                text,
                content_html,
            )
        if ticket["status"] != "closed":
            await close_ticket(
                ticket["guild_id"],
                ticket["id"],
                itx.user.id,
                "Deleted by staff after transcript capture",
            )
        try:
            await itx.channel.delete(reason=f"Ticket #{ticket['id']} deleted by staff")
        except (discord.Forbidden, discord.HTTPException):
            return await itx.followup.send(
                "تعذر حذف قناة التذكرة. تحقق من صلاحيات البوت.",
                ephemeral=True,
            )

    async def close_ticket_from_interaction(
        self,
        itx: discord.Interaction,
        reason: str,
        delay_seconds: int = 0,
    ):
        ticket = await get_ticket_by_channel(itx.channel.id)
        if not ticket or ticket["status"] == "closed":
            return await itx.response.send_message("هذه التذكرة مغلقة.", ephemeral=True)
        if not await self._ticket_action_allowed(itx.user, ticket, "close"):
            return await self._ticket_denied(itx)
        await itx.response.defer(ephemeral=True)
        if delay_seconds:
            await asyncio.sleep(max(0, min(int(delay_seconds), 30)))
        text, content_html = await self._build_transcript(itx.channel, ticket)
        text += f"\n\nClose reason: {reason}"
        content_html = content_html.replace(
            "</body></html>",
            f"<hr><p><strong>سبب الإغلاق:</strong> {html.escape(reason)}</p></body></html>",
        )
        await save_ticket_transcript(
            ticket["id"], ticket["guild_id"], ticket["channel_id"], text, content_html
        )
        ticket = await close_ticket(ticket["guild_id"], ticket["id"], itx.user.id, reason)
        await save_ticket_log(
            ticket["id"],
            ticket["guild_id"],
            "closed",
            staff_id=itx.user.id,
            metadata={"reason": reason},
        )
        await send_ticket_action_embed(
            itx.channel,
            "🔒 تم إغلاق التذكرة",
            f"تم إغلاق التذكرة **#{ticket['id']}** وأرشفتها.",
            0xEF4444,
            itx.user,
            extra_field=("السبب", reason),
        )
        self._schedule_ticket_channel_deletion(itx.channel, ticket["id"])
        await itx.channel.edit(
            name=f"archived-ticket-{ticket['id']}"[:100],
            topic=f"Archived ticket • closed by {itx.user.display_name}",
        )
        for target in [itx.guild.get_member(ticket["user_id"]), itx.user]:
            if target:
                overwrite = itx.channel.overwrites_for(target)
                overwrite.send_messages = False
                await itx.channel.set_permissions(target, overwrite=overwrite)
        self.bot.add_view(
            TicketRatingView(ticket["id"], ticket["user_id"], ticket["guild_id"])
        )
        user = itx.guild.get_member(ticket["user_id"])
        if user is None:
            try:
                user = await self.bot.fetch_user(ticket["user_id"])
            except (discord.NotFound, discord.HTTPException):
                user = None
        if user:
            try:
                await user.send(
                    f"📁 تم إغلاق تذكرتك **#{ticket['id']}**.\n"
                    "نقدّر تقييمك لتجربة الدعم:",
                    files=[
                        discord.File(
                            io.BytesIO(content_html.encode("utf-8")),
                            filename=f"ticket-{ticket['id']}.html",
                        ),
                        discord.File(
                            io.BytesIO(text.encode("utf-8")),
                            filename=f"ticket-{ticket['id']}.txt",
                        ),
                    ],
                    embed=(lambda rating_embed: (
                        rating_embed.set_footer(
                            text=f"ticket:{ticket['id']}:{ticket['guild_id']}"
                        ),
                        rating_embed,
                    )[1])(discord.Embed(
                        title="⭐ قيّم مستوى خدمة التذاكر",
                        description="اختر عدد النجوم ثم اكتب ملاحظاتك عن الخدمة.",
                        color=0xF59E0B,
                    )),
                    view=PersistentDMRatingView(
                        ticket["id"], ticket["guild_id"], ticket["user_id"]
                    ),
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.info("Could not DM transcript for ticket %s", ticket["id"])
        await itx.followup.send(
            "✅ أُغلقت التذكرة وحُفظ transcript وأُرسل للمستخدم.", ephemeral=True
        )
        analytics = self.bot.get_cog("Analytics")
        if analytics:
            await analytics.log_ticket_event(
                itx.guild,
                "🔒 إغلاق تذكرة",
                f"تم إغلاق التذكرة رقم `#{ticket['id']}` وأرشفتها.",
                actor=itx.user,
                fields=[
                    ("🎫 التذكرة", f"#{ticket['id']}", True),
                    ("🧾 المنفذ", f"{itx.user.mention} (`{itx.user.id}`)", True),
                    ("📌 السبب", reason, False),
                ],
            )

    async def _build_transcript(self, channel, ticket):
        lines = [
            f"Ticket #{ticket['id']} — {ticket['category_label']}",
            f"Subject: {ticket['subject']}",
            f"Opened by: {ticket['user_id']}",
            "",
        ]
        html_lines = [
            "<!doctype html><html lang='ar' dir='rtl'><meta charset='utf-8'>",
            "<style>body{background:#050505;color:#e5edf8;font:15px system-ui;padding:28px}"
            ".msg{border:1px solid #1e293b;border-radius:10px;padding:10px;margin:8px 0}"
            ".meta{color:#7dd3fc;font-size:12px}</style><body>",
            f"<h1>Ticket #{ticket['id']} · {html.escape(ticket['category_label'])}</h1>",
            f"<p>{html.escape(ticket['subject'])}</p>",
        ]
        try:
            history = channel.history(limit=None, oldest_first=True)
            async for message in history:
                created = getattr(message, "created_at", None)
                stamp = created.isoformat() if created else ""
                author = html.escape(getattr(message.author, "display_name", str(message.author)))
                content = str(getattr(message, "content", "") or "")
                lines.append(f"[{stamp}] {author}: {content}")
                html_lines.append(
                    f"<div class='msg'><div class='meta'>{author} · {html.escape(stamp)}</div>"
                    f"<div>{html.escape(content).replace(chr(10), '<br>')}</div></div>"
                )
        except (AttributeError, discord.HTTPException):
            logger.warning("Could not read transcript history for ticket %s", ticket["id"])
        html_lines.append("</body></html>")
        return "\n".join(lines), "\n".join(html_lines)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        ticket = await get_ticket_by_channel(message.channel.id)
        if not ticket or ticket["status"] == "closed":
            return
        if message.author.id == ticket["user_id"]:
            await record_ticket_user_message(ticket["guild_id"], ticket["id"])
        elif self._is_ticket_staff(message.author, ticket):
            await record_ticket_response(ticket["guild_id"], ticket["id"])

    @tasks.loop(minutes=10)
    async def update_counters_task(self):
        for guild_id, channels in list(self.counters.items()):
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            if channel_id := channels.get("members"):
                if channel := guild.get_channel(channel_id):
                    try:
                        await channel.edit(
                            name=f"👥 الأعضاء: {guild.member_count}",
                        )
                    except discord.HTTPException:
                        logger.warning("Could not update member counter in guild %s", guild.id, exc_info=True)
            if channel_id := channels.get("boosts"):
                if channel := guild.get_channel(channel_id):
                    try:
                        await channel.edit(
                            name=(
                                "🚀 البوست: "
                                f"{guild.premium_subscription_count}"
                            ),
                        )
                    except discord.HTTPException:
                        logger.warning("Could not update boost counter in guild %s", guild.id, exc_info=True)

    @update_counters_task.before_loop
    async def before_counter(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=10)
    async def reminder_task(self):
        """Deliver queued reminders from SQLite so restarts do not lose them."""
        for item in await get_due_reminders():
            delivered = False
            content = (
                f"🔔 <@{item['user_id']}> تذكيرك المستحق:\n"
                f"**{item['reminder']}**"
            )
            channel = self.bot.get_channel(int(item["channel_id"]))
            if channel is not None:
                try:
                    await channel.send(content)
                    delivered = True
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning(
                        "[REMINDER] تعذر الإرسال في القناة %s",
                        item["channel_id"],
                    )
            if not delivered:
                try:
                    user = self.bot.get_user(int(item["user_id"])) or await self.bot.fetch_user(int(item["user_id"]))
                    await user.send(f"🔔 تذكيرك المستحق:\n**{item['reminder']}**")
                    delivered = True
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning(
                        "[REMINDER] تعذر الإرسال الخاص للمستخدم %s",
                        item["user_id"],
                    )
            if delivered:
                await complete_reminder(int(item["id"]))
        streak_experience_cache = {}
        for item in await claim_due_streak_reminders():
            try:
                user = (
                    self.bot.get_user(int(item["user_id"]))
                    or await self.bot.fetch_user(int(item["user_id"]))
                )
                guild_id = int(item["guild_id"])
                if guild_id not in streak_experience_cache:
                    try:
                        streak_experience_cache[guild_id] = (
                            await database.get_level_streak_experience_config(guild_id)
                        )
                    except Exception:
                        logger.exception(
                            "[STREAK REMINDER] تعذر تحميل إعدادات العرض للسيرفر %s",
                            guild_id,
                        )
                        streak_experience_cache[guild_id] = {"stages": []}
                try:
                    ranks = await database.get_streak_ranks(
                        guild_id, int(item["user_id"])
                    )
                except Exception:
                    logger.exception(
                        "[STREAK REMINDER] تعذر تحميل ترتيب العضو %s",
                        item["user_id"],
                    )
                    ranks = {"server_rank": 1, "global_rank": 1}
                context = build_streak_context(
                    user,
                    self.bot.get_guild(guild_id),
                    {
                        "current_streak": int(item["current_streak"]),
                        "best_streak": int(item["best_streak"]),
                    },
                    streak_experience_cache[guild_id]["stages"],
                    ranks,
                )
                await user.send(
                    render_template(
                        item["reminder_message"], context["values"],
                    )[:1900],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                # The daily delivery key is claimed before sending, so a retry
                # or restart cannot send the same reminder twice.
                logger.info(
                    "[STREAK REMINDER] تعذر إرسال الخاص للمستخدم %s",
                    item["user_id"],
                    exc_info=True,
                )

    @commands.command(
        name="streakreminders",
        help="تفعيل أو إيقاف تذكير الستريك اليومي الخاص بك.",
    )
    @commands.guild_only()
    async def streak_reminders(self, ctx, enabled: bool):
        """Let each member opt in without enabling unsolicited DMs by default."""
        reminder = await set_streak_reminder(
            ctx.guild.id, ctx.author.id, enabled
        )
        if reminder["enabled"]:
            if reminder.get("delivery_enabled"):
                response = (
                    "تم تفعيل تذكير الستريك اليومي الساعة "
                    f"{reminder['reminder_time']} بتوقيت الرياض. "
                    "لإيقافه استخدم الأمر مع `false`."
                )
            else:
                response = (
                    "تم حفظ اشتراكك، لكن إرسال تذكيرات الستريك متوقف حالياً "
                    "من إعدادات هذا السيرفر."
                )
        else:
            response = "تم إيقاف تذكير الستريك اليومي."
        await ctx.send(response)

    @reminder_task.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="suggest",
        description="إرسال اقتراح وطرحه للتصويت والإدارة",
    )
    @app_commands.checks.bot_has_permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        add_reactions=True,
        read_message_history=True,
    )
    @app_commands.describe(idea="تفاصيل فكرة الاقتراح")
    async def suggest(
        self,
        itx: discord.Interaction,
        idea: str,
    ):
        embed = discord.Embed(
            title=f"💡 اقتراح جديد من: {itx.user.display_name}",
            description=idea,
            color=0x3498DB,
        )
        embed.set_thumbnail(url=itx.user.display_avatar.url)
        embed.set_footer(
            text="صوّت عبر الرياكشن | القرار الإداري متاح بالأزرار",
        )
        message = await itx.channel.send(
            embed=embed,
            view=SuggestionActionView(),
        )
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        await itx.response.send_message(
            "✅ تم إرسال الاقتراح للمناقشة والتصويت.",
            ephemeral=True,
        )

    @app_commands.command(
        name="poll",
        description="طرح تصويت حي بالأزرار مع نسب مئوية فورية",
    )
    @app_commands.checks.bot_has_permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        read_message_history=True,
    )
    @app_commands.describe(
        question="سؤال الاستطلاع",
        opt_a="الخيار الأول",
        opt_b="الخيار الثاني",
    )
    async def poll(
        self,
        itx: discord.Interaction,
        question: str,
        opt_a: str,
        opt_b: str,
    ):
        poll_view = LivePollView(question, opt_a, opt_b)
        await itx.channel.send(
            embed=poll_view.make_embed(),
            view=poll_view,
        )
        await itx.response.send_message(
            "✅ تم إنشاء التصويت التفاعلي بنجاح.",
            ephemeral=True,
        )

    async def remind(
        self,
        itx: discord.Interaction,
        minutes: int,
        reminder: str,
    ):
        if minutes < 1:
            return await itx.response.send_message(
                "❌ أقل وقت للتذكير هو دقيقة واحدة.",
                ephemeral=True,
            )
        due_at = (
            discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        ).strftime("%Y-%m-%d %H:%M:%S")
        reminder_id = await create_reminder(
            itx.guild.id,
            itx.user.id,
            itx.channel.id,
            reminder,
            due_at,
        )
        await itx.response.send_message(
            "⏰ تم حفظ التذكير بنجاح.\n"
            f"الرقم: `{reminder_id}` | بعد: `{minutes}` دقيقة\n"
            "سيستمر حتى لو أعيد تشغيل البوت."
        )

    @app_commands.command(
        name="reminders",
        description="عرض تذكيراتك المحفوظة في هذا السيرفر",
    )
    async def reminders(self, itx: discord.Interaction):
        items = await get_user_reminders(itx.guild.id, itx.user.id)
        if not items:
            return await itx.response.send_message(
                "لا توجد لديك تذكيرات معلقة.",
                ephemeral=True,
            )
        lines = [
            f"`#{item['id']}` — <t:{int(datetime.datetime.fromisoformat(item['due_at']).replace(tzinfo=datetime.timezone.utc).timestamp())}:R> — {item['reminder']}"
            for item in items
        ]
        await itx.response.send_message(
            "⏰ **تذكيراتك المعلقة**\n" + "\n".join(lines),
            ephemeral=True,
        )

    @app_commands.command(
        name="reminder_cancel",
        description="إلغاء تذكير محفوظ بالرقم",
    )
    async def reminder_cancel(self, itx: discord.Interaction, reminder_id: int):
        if await cancel_reminder(itx.guild.id, itx.user.id, reminder_id):
            return await itx.response.send_message(
                f"✅ تم إلغاء التذكير `#{reminder_id}`.",
                ephemeral=True,
            )
        await itx.response.send_message(
            "❌ لم أجد تذكيراً نشطاً بهذا الرقم يخصك.",
            ephemeral=True,
        )

    @app_commands.command(
        name="setup_counters",
        description="تثبيت قنوات صوتية حية لعرض إحصائيات السيرفر",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(
        view_channel=True,
        send_messages=True,
        manage_channels=True,
    )
    async def setup_counters(self, itx: discord.Interaction):
        guild = itx.guild
        category = discord.utils.get(guild.categories, name="📊 إحصائيات السيرفر")
        if category is None:
            category = await guild.create_category("📊 إحصائيات السيرفر")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False)
        }
        member_channel = discord.utils.get(category.voice_channels, name__startswith="👥 الأعضاء:")
        if member_channel is None:
            member_channel = await guild.create_voice_channel(
                name=f"👥 الأعضاء: {guild.member_count}",
                category=category,
                overwrites=overwrites,
            )
        else:
            await member_channel.edit(name=f"👥 الأعضاء: {guild.member_count}")
        boost_channel = discord.utils.get(category.voice_channels, name__startswith="🚀 البوست:")
        if boost_channel is None:
            boost_channel = await guild.create_voice_channel(
                name=f"🚀 البوست: {guild.premium_subscription_count}",
                category=category,
                overwrites=overwrites,
            )
        else:
            await boost_channel.edit(name=f"🚀 البوست: {guild.premium_subscription_count}")
        self.counters[guild.id] = {
            "members": member_channel.id,
            "boosts": boost_channel.id,
        }
        await itx.response.send_message(
            "✅ تم إنشاء قنوات الإحصائيات الحية بنجاح!",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    bot.add_view(SuggestionActionView())
    bot.add_view(TicketControlView())
    bot.add_view(StreamlinedTicketControlsView())
    legacy_panels = await get_ticket_panels()
    legacy_by_message = {
        (int(panel["guild_id"]), int(panel["message_id"])): panel["categories"]
        for panel in legacy_panels
        if panel.get("message_id") is not None
    }
    for config in await get_ticket_configs():
        channel = bot.get_channel(config["channel_id"]) if config.get("channel_id") else None
        if channel is None or not config.get("message_id"):
            continue
        options = await get_ticket_options(config["guild_id"])
        if options:
            option_categories = [
                {
                    "key": re.sub(
                        r"[^a-zA-Z0-9_-]+",
                        "-",
                        option["label"].strip().lower(),
                    ).strip("-") or f"category-{option['id']}",
                    "label": option["label"],
                    "description": option["description"],
                    "emoji": option["emoji"],
                    "role_id": option["role_id"],
                    "category_id": option["category_id"],
                    "welcome_msg": option["welcome_msg"],
                    "support_role_ids": (
                        [str(option["role_id"])]
                        if option.get("role_id") is not None
                        else []
                    ),
                    "senior_role_ids": [],
                }
                for option in options
            ]
            categories = legacy_by_message.get(
                (int(config["guild_id"]), int(config["message_id"])),
                option_categories,
            )
            bot.add_view(
                TicketSelectView(categories, config["guild_id"]),
                message_id=config["message_id"],
            )
    for panel in legacy_panels:
        if panel.get("message_id") is not None and bot.get_channel(panel["channel_id"]):
            bot.add_view(
                TicketPanelView(panel["categories"]),
                message_id=panel["message_id"],
            )
    # Restore dashboard-owned static-custom-id dropdowns after every restart.
    for guild in getattr(bot, "guilds", ()) or ():
        try:
            config = await get_ticket_dropdown_config(guild.id)
            if not config or not config.get("channel_id") or not config.get("message_id"):
                continue
            channel = bot.get_channel(int(config["channel_id"]))
            if channel is None:
                continue
            categories = await get_ticket_dropdown_categories(guild.id)
            if categories:
                bot.add_view(
                    PersistentDropdownTicketView(categories, guild.id),
                    message_id=int(config["message_id"]),
                )
        except (TypeError, ValueError, discord.HTTPException):
            logger.warning(
                "Unable to restore dashboard ticket dropdown for guild %s",
                getattr(guild, "id", "unknown"),
                exc_info=True,
            )
    community = Community(bot)
    await bot.add_cog(community)
    logger.info(
        "Community cog initialized with /suggest, /poll, /remind, "
        "/setup_counters, and persistent ticket controls."
    )

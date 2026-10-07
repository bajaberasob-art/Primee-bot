"""Persistent scrim lobbies and lightweight esports operations."""

import logging

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from database import (
    cancel_scrim_slot,
    close_scrim,
    create_scrim_config,
    get_active_scrims,
    reserve_scrim_slot,
    set_scrim_message,
    toggle_scrim_checkin,
)

logger = logging.getLogger("GamingCog")


def _admin(member: discord.Member) -> bool:
    permissions = getattr(member, "guild_permissions", None)
    return bool(permissions and (permissions.manage_events or permissions.manage_guild))


def _members(value: str, leader_id: int) -> list[int]:
    result = [int(leader_id)]
    for token in str(value or "").replace(",", " ").split():
        if token.isdigit() and int(token) not in result:
            result.append(int(token))
    return result[:16]


async def _scrim_embed(scrim: dict) -> discord.Embed:
    registrations = scrim.get("registrations", [])
    lines = []
    by_slot = {int(item["slot_number"]): item for item in registrations}
    for slot in range(1, int(scrim["max_slots"]) + 1):
        item = by_slot.get(slot)
        if not item:
            lines.append(f"`#{slot:02d}` — شاغر")
            continue
        check = " ✅" if item.get("checked_in") else ""
        lines.append(
            f"`#{slot:02d}` — **{discord.utils.escape_markdown(str(item['team_name']))}**"
            f" · <@{int(item['leader_id'])}>{check}"
        )
    embed = discord.Embed(
        title=f"🎮 {scrim['title']}",
        description=(
            f"**اللعبة:** {scrim['game_type']} · **حجم الفريق:** {scrim['team_size']}\n"
            f"**المقاعد:** {scrim.get('occupied_slots', 0)}/{scrim['max_slots']}\n\n"
            + "\n".join(lines)
        ),
        color=0x00E5FF,
    )
    embed.set_footer(text=f"SCRIM:{scrim['id']} • الحجز خاص، والقائمة ظاهرة للجميع")
    return embed


class BookingModal(discord.ui.Modal, title="حجز مقعد سكريم"):
    team_name = discord.ui.TextInput(
        label="اسم الفريق",
        placeholder="مثال: PRIME Alpha",
        max_length=100,
    )
    members = discord.ui.TextInput(
        label="Discord IDs للأعضاء الآخرين (اختياري)",
        placeholder="123..., 456...",
        required=False,
        max_length=300,
    )

    def __init__(self, view: "ScrimBoardView"):
        super().__init__()
        self.board = view

    async def on_submit(self, interaction: discord.Interaction):
        registration = await reserve_scrim_slot(
            self.board.scrim_id,
            str(self.team_name),
            interaction.user.id,
            _members(str(self.members), interaction.user.id),
        )
        if registration is None:
            return await interaction.response.send_message(
                "⚠️ لا توجد مقاعد متاحة أو أن التسجيل مغلق.", ephemeral=True
            )
        await interaction.response.send_message(
            f"✅ تم حجز المقعد `#{registration['slot_number']}` لفريق **{self.team_name}**.",
            ephemeral=True,
        )
        await self.board.refresh_message(interaction)


class CancelModal(discord.ui.Modal, title="إلغاء حجز سكريم"):
    slot = discord.ui.TextInput(label="رقم المقعد", placeholder="1", max_length=3)

    def __init__(self, view: "ScrimBoardView"):
        super().__init__()
        self.board = view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            slot_number = int(str(self.slot))
        except ValueError:
            return await interaction.response.send_message("❌ رقم المقعد غير صالح.", ephemeral=True)
        scrims = await get_active_scrims(self.board.guild_id)
        scrim = next((item for item in scrims if int(item["id"]) == self.board.scrim_id), None)
        registration = next(
            (item for item in (scrim or {}).get("registrations", [])
             if int(item["slot_number"]) == slot_number),
            None,
        )
        if registration is None:
            return await interaction.response.send_message("❌ المقعد غير محجوز.", ephemeral=True)
        if int(registration["leader_id"]) != interaction.user.id and not _admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ لا يستطيع إلغاء الحجز إلا قائد الفريق أو المشرف.", ephemeral=True
            )
        removed = await cancel_scrim_slot(
            self.board.scrim_id,
            slot_number=slot_number,
            leader_id=None if _admin(interaction.user) else interaction.user.id,
        )
        if not removed:
            return await interaction.response.send_message("⚠️ تعذر إلغاء الحجز.", ephemeral=True)
        await interaction.response.send_message("✅ تم إلغاء الحجز.", ephemeral=True)
        await self.board.refresh_message(interaction)


class ScrimBoardView(discord.ui.View):
    def __init__(self, scrim_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.scrim_id = int(scrim_id)
        self.guild_id = int(guild_id)
        book = discord.ui.Button(
            label="حجز مقعد 🎮",
            style=discord.ButtonStyle.success,
            custom_id=f"scrim:book:{self.scrim_id}",
        )
        book.callback = self.book
        cancel = discord.ui.Button(
            label="إلغاء حجزي",
            style=discord.ButtonStyle.danger,
            custom_id=f"scrim:cancel:{self.scrim_id}",
        )
        cancel.callback = self.cancel
        checkin = discord.ui.Button(
            label="تأكيد الحضور ✅",
            style=discord.ButtonStyle.primary,
            custom_id=f"scrim:checkin:{self.scrim_id}",
        )
        checkin.callback = self.checkin
        self.add_item(book)
        self.add_item(cancel)
        self.add_item(checkin)

    async def book(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BookingModal(self))

    async def cancel(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CancelModal(self))

    async def checkin(self, interaction: discord.Interaction):
        if not _admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ تأكيد الحضور متاح لمضيف السكريم فقط.", ephemeral=True
            )
        scrims = await get_active_scrims(self.guild_id)
        scrim = next((item for item in scrims if int(item["id"]) == self.scrim_id), None)
        if not scrim:
            return await interaction.response.send_message("❌ السكريم مغلق.", ephemeral=True)
        # Host check-in is intentionally explicit: the button toggles all booked
        # teams into checked-in state after the host confirms the action.
        for registration in scrim.get("registrations", []):
            await toggle_scrim_checkin(
                self.scrim_id,
                slot_number=int(registration["slot_number"]),
                checked_in=not bool(registration.get("checked_in")),
            )
        await interaction.response.send_message("✅ تم تحديث حالة الحضور للفرق.", ephemeral=True)
        await self.refresh_message(interaction)

    async def refresh_message(self, interaction: discord.Interaction):
        scrims = await get_active_scrims(self.guild_id)
        scrim = next((item for item in scrims if int(item["id"]) == self.scrim_id), None)
        if not scrim:
            return
        message = interaction.message
        if message is None and interaction.channel is not None:
            try:
                message = await interaction.channel.fetch_message(int(scrim["message_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
        if message is not None:
            await message.edit(embed=await _scrim_embed(scrim), view=self)


class Gaming(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        for scrim in await get_active_scrims_for_restore():
            if int(scrim.get("message_id") or 0) <= 0:
                continue
            self.bot.add_view(
                ScrimBoardView(scrim["id"], scrim["guild_id"]),
                message_id=int(scrim["message_id"]),
            )

    async def deploy_scrim(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        title: str,
        game_type: str,
        team_size: int,
        max_slots: int,
    ) -> dict:
        scrim = await create_scrim_config(
            guild.id, channel.id, title, game_type, team_size, max_slots
        )
        scrim["registrations"] = []
        scrim["occupied_slots"] = 0
        view = ScrimBoardView(scrim["id"], guild.id)
        try:
            message = await channel.send(embed=await _scrim_embed(scrim), view=view)
            await set_scrim_message(scrim["id"], message.id)
            self.bot.add_view(view, message_id=message.id)
        except (discord.Forbidden, discord.HTTPException):
            await close_scrim(scrim["id"])
            raise
        scrim["message_id"] = message.id
        return scrim

    async def close_scrim_board(self, scrim_id: int, guild: discord.Guild) -> bool:
        changed = await close_scrim(scrim_id)
        if not changed:
            return False
        for channel in guild.text_channels:
            try:
                async for message in channel.history(limit=100):
                    if message.author.id == self.bot.user.id and message.embeds:
                        footer = message.embeds[0].footer.text or ""
                        if footer == f"SCRIM:{int(scrim_id)} • الحجز خاص، والقائمة ظاهرة للجميع":
                            embed = message.embeds[0]
                            embed.title = f"🔒 {embed.title.lstrip('🎮 ').strip()} (مغلق)"
                            embed.color = 0x667085
                            await message.edit(embed=embed, view=None)
                            return True
            except (discord.Forbidden, discord.HTTPException):
                continue
        return True

    @app_commands.command(name="scrim_open", description="فتح لوحة تسجيل سكريم للفرق")
    @app_commands.checks.has_permissions(manage_events=True)
    @app_commands.describe(title="عنوان السكريم", game_type="اسم اللعبة", team_size="حجم الفريق", max_slots="عدد المقاعد")
    async def scrim_open(
        self,
        interaction: discord.Interaction,
        title: str,
        game_type: str,
        team_size: int = 5,
        max_slots: int = 8,
    ):
        if not 1 <= team_size <= 16 or not 1 <= max_slots <= 128:
            return await interaction.response.send_message(
                "❌ حجم الفريق بين 1 و16 وعدد المقاعد بين 1 و128.", ephemeral=True
            )
        try:
            scrim = await self.deploy_scrim(
                interaction.guild, interaction.channel, title, game_type, team_size, max_slots
            )
        except (discord.Forbidden, discord.HTTPException):
            raise
        await interaction.response.send_message(
            f"✅ تم فتح لوحة السكريم رقم `{scrim['id']}`.", ephemeral=True
        )


async def get_active_scrims_for_restore():
    # Restore all guilds without changing the public guild-scoped API helper.
    from database import connect
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM scrim_configs WHERE is_active = 1 AND message_id > 0"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def setup(bot: commands.Bot):
    await bot.add_cog(Gaming(bot))
import datetime
import asyncio
import logging
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger("EconomyCog")

from database import (
    add_economy_audit,
    adjust_user_balance,
    get_economy_leaderboard,
    get_leaderboard_targets,
    get_guild_settings,
    get_or_create_user,
    claim_scaled_daily_reward,
    get_role_multipliers,
    set_leaderboard_embed_target,
    add_giveaway_entry,
    cancel_giveaway,
    complete_giveaway,
    create_giveaway,
    get_due_giveaways,
    get_giveaway_entries,
    get_open_giveaways,
    set_giveaway_message,
    transfer_balance,
    move_balance,
    update_balance,
)


class LiveGiveaway(discord.ui.View):
    def __init__(self, prize: str, giveaway_id: int):
        super().__init__(timeout=None)
        self.prize = prize
        self.giveaway_id = int(giveaway_id)
        button = discord.ui.Button(
            label="دخول السحب 🎉",
            style=discord.ButtonStyle.success,
            custom_id=f"giveaway:enter:{self.giveaway_id}",
        )
        button.callback = self.enter
        self.add_item(button)

    async def enter(self, itx: discord.Interaction):
        if not await add_giveaway_entry(self.giveaway_id, itx.user.id):
            return await itx.response.send_message(
                "❌ أنت مسجل مسبقاً في هذا السحب!",
                ephemeral=True,
            )
        await itx.response.send_message(
            f"✅ تم اشتراكك في السحب على: **{self.prize}**",
            ephemeral=True,
        )


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cooldowns = {}
        self._leaderboard_locks: dict[int, asyncio.Lock] = {}
        self.giveaway_task.start()
        self.leaderboard_task.start()

    async def cog_load(self):
        for giveaway in await get_open_giveaways():
            self.bot.add_view(
                LiveGiveaway(giveaway["prize"], giveaway["id"]),
                message_id=int(giveaway["message_id"]),
            )

    def cog_unload(self):
        self.giveaway_task.cancel()
        self.leaderboard_task.cancel()

    async def _is_economy_support(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator:
            return True
        settings = await get_guild_settings(member.guild.id)
        support_roles = {
            int(role_id)
            for role_id in settings["settings"].get("economy_support_role_ids", [])
            if str(role_id).isdigit()
        }
        return any(role.id in support_roles for role in member.roles)

    async def _leaderboard_embed(self, guild: discord.Guild) -> discord.Embed:
        wealth = await get_economy_leaderboard(guild.id, 10)

        def member_name(user_id: int) -> str:
            member = guild.get_member(int(user_id))
            return member.display_name if member else f"عضو {user_id}"

        wealth_lines = [
            f"**{index}.** {member_name(row['user_id'])} — "
            f"`{int(row['total']):,}` عملة"
            for index, row in enumerate(wealth, 1)
        ]
        embed = discord.Embed(
            title="🏆 لوحة المتصدرين الحية",
            description="تتحدث تلقائياً كل خمس دقائق ومع كل تغيير في الاقتصاد.",
            color=0x00E5FF,
        )
        embed.add_field(
            name="💰 أغنى 10 أعضاء",
            value="\n".join(wealth_lines) or "لا توجد حسابات بعد.",
            inline=True,
        )
        embed.add_field(
            name="📊 طريقة الترتيب",
            value="حسب مجموع المحفظة والبنك.",
            inline=True,
        )
        embed.set_footer(text=f"{guild.name} • LIVE ECONOMY")
        return embed

    async def refresh_leaderboard(self, guild_id: int) -> None:
        settings = await get_guild_settings(guild_id)
        config = settings["settings"]
        channel_id = int(config.get("leaderboard_channel_id") or 0)
        if channel_id <= 0:
            return
        lock = self._leaderboard_locks.setdefault(int(guild_id), asyncio.Lock())
        async with lock:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    return
            message = None
            message_id = int(config.get("leaderboard_message_id") or 0)
            if message_id:
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
            embed = await self._leaderboard_embed(channel.guild)
            try:
                if message is None:
                    message = await channel.send(embed=embed)
                    await set_leaderboard_embed_target(
                        guild_id, channel.id, message.id
                    )
                else:
                    await message.edit(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("Unable to refresh leaderboard for guild %s", guild_id, exc_info=True)

    async def _leaderboard_changed(self, guild_id: int) -> None:
        try:
            await self.refresh_leaderboard(guild_id)
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Leaderboard refresh failed for guild %s", guild_id, exc_info=True)

    async def _admin_check(self, interaction: discord.Interaction) -> bool:
        if await self._is_economy_support(interaction.user):
            return True
        await interaction.response.send_message(
            "⛔ هذا الإجراء متاح للإدارة أو أدوار دعم الاقتصاد فقط.",
            ephemeral=True,
        )
        return False

    @tasks.loop(minutes=5)
    async def leaderboard_task(self):
        for target in await get_leaderboard_targets():
            await self.refresh_leaderboard(int(target["guild_id"]))

    @leaderboard_task.before_loop
    async def before_leaderboard_task(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=10)
    async def giveaway_task(self):
        for giveaway in await get_due_giveaways():
            channel = self.bot.get_channel(int(giveaway["channel_id"]))
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(int(giveaway["channel_id"]))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            entries = await get_giveaway_entries(int(giveaway["id"]))
            try:
                if entries:
                    winner_id = random.choice(entries)
                    winner = channel.guild.get_member(winner_id)
                    mention = winner.mention if winner else f"<@{winner_id}>"
                    await channel.send(
                        f"🎊 مبارك {mention}! فزت بسحب: **{giveaway['prize']}** 🎉"
                    )
                else:
                    await channel.send(
                        f"⚠️ انتهى السحب على **{giveaway['prize']}** دون أي مشتركين."
                    )
                try:
                    message = await channel.fetch_message(int(giveaway["message_id"]))
                    await message.edit(view=None)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logger.debug("Giveaway message cleanup was unavailable", exc_info=True)
            except (discord.Forbidden, discord.HTTPException):
                continue
            await complete_giveaway(int(giveaway["id"]))

    @giveaway_task.before_loop
    async def before_giveaway_task(self):
        await self.bot.wait_until_ready()

    def check_cd(self, key: str, sec: int) -> int:
        now = int(discord.utils.utcnow().timestamp())
        left = sec - (now - self.cooldowns.get(key, 0))
        if left > 0:
            return left
        self.cooldowns[key] = now
        return 0

    @app_commands.command(name="profile", description="عرض الملف المالي والشخصي")
    async def profile(
        self,
        itx: discord.Interaction,
        member: discord.Member = None,
    ):
        target = member or itx.user
        user = await get_or_create_user(target.id, itx.guild.id)
        embed = discord.Embed(
            title=f"💳 بطاقة: {target.display_name}",
            color=0x2ECC71,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="الكاش 💵",
            value=f"`{user['balance']:,}`",
            inline=True,
        )
        embed.add_field(
            name="البنك 🏦",
            value=f"`{user['bank']:,}`",
            inline=True,
        )
        embed.add_field(
            name="الإجمالي 💎",
            value=f"`{user['balance'] + user['bank']:,}`",
            inline=True,
        )
        await itx.response.send_message(embed=embed)

    @app_commands.command(name="daily", description="المكافأة اليومية")
    async def daily(self, itx: discord.Interaction):
        today = discord.utils.utcnow().strftime("%Y-%m-%d")
        settings = await get_guild_settings(itx.guild.id)
        config = settings["settings"]
        base_reward = int(config.get("daily_base_amount", 200))
        role_multipliers = await get_role_multipliers(itx.guild.id)
        role_multiplier = max(
            [
                float(role_multipliers.get(str(role.id), 1.0))
                for role in itx.user.roles
                if str(role.id) in role_multipliers
            ]
            or [1.0]
        )
        result = await claim_scaled_daily_reward(
            itx.user.id,
            itx.guild.id,
            today,
            base_reward,
            role_multiplier,
        )
        if result is None:
            return await itx.response.send_message(
                "❌ استلمت راتبك اليومي مسبقاً! عد غداً.",
                ephemeral=True,
            )
        embed = discord.Embed(
            title="💰 المكافأة اليومية",
            description=f"تم إيداع **{result['reward']:,}** عملة في محفظتك.",
            color=0x2ECC71,
        )
        embed.add_field(name="المبلغ الأساسي", value=f"`{result['base_amount']:,}`", inline=True)
        embed.add_field(name="مضاعف الرتبة", value=f"`×{result['role_multiplier']}`", inline=True)
        embed.add_field(name="الإجمالي المستلم", value=f"`{result['reward']:,}`", inline=True)
        await itx.response.send_message(embed=embed)
        await self._leaderboard_changed(itx.guild.id)

    @commands.command(name="راتب", aliases=["يومي"])
    async def daily_text(self, ctx: commands.Context):
        if not ctx.guild:
            return
        today = discord.utils.utcnow().strftime("%Y-%m-%d")
        config = (await get_guild_settings(ctx.guild.id))["settings"]
        role_multipliers = await get_role_multipliers(ctx.guild.id)
        role_multiplier = max(
            [
                float(role_multipliers.get(str(role.id), 1.0))
                for role in ctx.author.roles
                if str(role.id) in role_multipliers
            ]
            or [1.0]
        )
        result = await claim_scaled_daily_reward(
            ctx.author.id,
            ctx.guild.id,
            today,
            int(config.get("daily_base_amount", 200)),
            role_multiplier,
        )
        if result is None:
            return await ctx.send("❌ استلمت راتبك اليومي مسبقاً! عد غداً.")
        embed = discord.Embed(
            title="💰 المكافأة اليومية",
            description=f"تم إيداع **{result['reward']:,}** عملة في محفظتك.",
            color=0x2ECC71,
        )
        embed.add_field(name="المبلغ الأساسي", value=f"`{result['base_amount']:,}`", inline=True)
        embed.add_field(name="مضاعف الرتبة", value=f"`×{result['role_multiplier']}`", inline=True)
        embed.add_field(name="الإجمالي المستلم", value=f"`{result['reward']:,}`", inline=True)
        await ctx.send(embed=embed)
        await self._leaderboard_changed(ctx.guild.id)

    @app_commands.command(
        name="set_leaderboard_channel",
        description="تثبيت لوحة المتصدرين الحية في قناة",
    )
    async def set_leaderboard_channel(
        self,
        itx: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if not await self._admin_check(itx):
            return
        await set_leaderboard_embed_target(itx.guild.id, channel.id, 0)
        await self.refresh_leaderboard(itx.guild.id)
        await itx.response.send_message(
            f"📌 تم تثبيت لوحة المتصدرين الحية في {channel.mention}.",
            ephemeral=True,
        )

    async def _admin_balance_action(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        target: discord.Member,
        amount: int,
        give: bool,
        send,
    ):
        if amount <= 0:
            return await send("❌ يجب أن يكون المبلغ أكبر من صفر.")
        delta = amount if give else -amount
        updated = await adjust_user_balance(guild.id, target.id, delta, 0)
        if updated is None:
            return await send("❌ لا يمكن خصم مبلغ يؤدي إلى رصيد سالب.")
        await add_economy_audit(
            guild.id,
            target.id,
            actor.id,
            "give_points" if give else "take_points",
            wallet_delta=delta,
            details=f"target={target.id}",
        )
        embed = discord.Embed(
            title="✅ تم تحديث الرصيد",
            description=f"{'إضافة' if give else 'خصم'} **{amount:,}** عملة لـ {target.mention}.",
            color=0x2ECC71 if give else 0xF97316,
        )
        embed.add_field(name="الرصيد الجديد", value=f"`{int(updated['balance']):,}`", inline=True)
        embed.set_footer(text=f"بواسطة {actor.display_name}")
        await send(embed=embed)
        await self._leaderboard_changed(guild.id)

    async def dashboard_adjust(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        target: discord.Member,
        wallet_delta: int = 0,
    ) -> dict:
        """Apply a dashboard economy action using the same guarded mutation path."""
        before = await get_or_create_user(target.id, guild.id)
        updated = before
        if wallet_delta:
            updated = await adjust_user_balance(
                guild.id, target.id, int(wallet_delta), 0
            )
            if updated is None:
                raise ValueError("لا يمكن أن يصبح الرصيد سالباً")
        await add_economy_audit(
            guild.id,
            target.id,
            actor.id,
            "dashboard_adjust",
            wallet_delta=int(wallet_delta),
            details=f"target={target.id}",
        )
        await self._leaderboard_changed(guild.id)
        return {"before": before, "user": updated}

    @app_commands.command(name="give_points", description="إضافة نقاط إلى محفظة عضو")
    async def give_points(
        self,
        itx: discord.Interaction,
        member: discord.Member,
        amount: int,
    ):
        if not await self._admin_check(itx):
            return
        await self._admin_balance_action(
            itx.guild, itx.user, member, amount, True, itx.response.send_message
        )

    @app_commands.command(name="take_points", description="خصم نقاط من محفظة عضو")
    async def take_points(
        self,
        itx: discord.Interaction,
        member: discord.Member,
        amount: int,
    ):
        if not await self._admin_check(itx):
            return
        await self._admin_balance_action(
            itx.guild, itx.user, member, amount, False, itx.response.send_message
        )

    @commands.command(name="اعطاء_نقاط", aliases=["give-points"])
    async def give_points_text(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
    ):
        if not ctx.guild or not await self._is_economy_support(ctx.author):
            return await ctx.send("⛔ هذا الإجراء متاح للإدارة أو أدوار دعم الاقتصاد فقط.")
        await self._admin_balance_action(
            ctx.guild, ctx.author, member, amount, True, ctx.send
        )

    @commands.command(name="سحب_نقاط", aliases=["take-points"])
    async def take_points_text(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
    ):
        if not ctx.guild or not await self._is_economy_support(ctx.author):
            return await ctx.send("⛔ هذا الإجراء متاح للإدارة أو أدوار دعم الاقتصاد فقط.")
        await self._admin_balance_action(
            ctx.guild, ctx.author, member, amount, False, ctx.send
        )

    @app_commands.command(name="work", description="العمل وكسب المال")
    async def work(self, itx: discord.Interaction):
        left = self.check_cd(f"work_{itx.user.id}", 300)
        if left:
            return await itx.response.send_message(
                f"⏳ أنت متعب، يمكنك العمل مجدداً بعد "
                f"`{left // 60}د {left % 60}ث`.",
                ephemeral=True,
            )
        earned = random.randint(80, 250)
        await update_balance(
            itx.user.id,
            itx.guild.id,
            earned,
            "balance",
        )
        await self._leaderboard_changed(itx.guild.id)
        await itx.response.send_message(
            f"💼 أتممت عملاً شاقاً وحصلت على **{earned:,}** عملة نقدية."
        )

    @app_commands.command(name="pay", description="تحويل كاش إلى عضو آخر")
    async def pay(
        self,
        itx: discord.Interaction,
        target: discord.Member,
        amount: int,
    ):
        if target.bot or target.id == itx.user.id or amount <= 0:
            return await itx.response.send_message(
                "❌ اختر عضواً صالحاً وأدخل مبلغاً أكبر من صفر.",
                ephemeral=True,
            )
        if not await transfer_balance(itx.guild.id, itx.user.id, target.id, amount):
            return await itx.response.send_message(
                "❌ لا يملك رصيدك النقدي ما يكفي لإتمام التحويل.",
                ephemeral=True,
            )
        await itx.response.send_message(
            f"💸 تم تحويل **{amount:,}** عملة إلى {target.mention}.",
        )
        await self._leaderboard_changed(itx.guild.id)

    @app_commands.command(name="leaderboard", description="عرض المتصدرين في اقتصاد السيرفر")
    async def leaderboard(self, itx: discord.Interaction):
        rows = await get_economy_leaderboard(itx.guild.id, 10)
        if not rows:
            return await itx.response.send_message(
                "لا توجد حسابات اقتصادية بعد.",
                ephemeral=True,
            )
        lines = []
        for index, row in enumerate(rows, 1):
            member = itx.guild.get_member(int(row["user_id"]))
            name = member.display_name if member else f"عضو {row['user_id']}"
            lines.append(
                f"**{index}.** {name} — `{int(row['total']):,}` عملة"
            )
        embed = discord.Embed(
            title="🏆 لوحة المتصدرين الاقتصادية",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        await itx.response.send_message(embed=embed)

    @app_commands.command(
        name="deposit",
        description="إيداع أموال في حسابك البنكي",
    )
    async def deposit(self, itx: discord.Interaction, amount: int):
        if amount <= 0:
            return await itx.response.send_message(
                "❌ أدخل رقماً صالحاً.",
                ephemeral=True,
            )
        if not await move_balance(
            itx.user.id,
            itx.guild.id,
            amount,
            "balance",
            "bank",
        ):
            return await itx.response.send_message(
                "❌ رصيد الكاش لا يكفي!",
                ephemeral=True,
            )
        await itx.response.send_message(
            f"🏦 تم إيداع **{amount:,}** عملة في البنك بأمان."
        )
        await self._leaderboard_changed(itx.guild.id)

    @app_commands.command(name="withdraw", description="سحب أموال من البنك")
    async def withdraw(self, itx: discord.Interaction, amount: int):
        if amount <= 0:
            return await itx.response.send_message(
                "❌ أدخل رقماً صالحاً.",
                ephemeral=True,
            )
        if not await move_balance(
            itx.user.id,
            itx.guild.id,
            amount,
            "bank",
            "balance",
        ):
            return await itx.response.send_message(
                "❌ رصيد البنك لا يكفي!",
                ephemeral=True,
            )
        await itx.response.send_message(
            f"💵 تم سحب **{amount:,}** عملة كاش إلى محفظتك."
        )
        await self._leaderboard_changed(itx.guild.id)

    @app_commands.command(
        name="rob",
        description="محاولة سرقة كاش عضو آخر (مخاطرة عالية)",
    )
    async def rob(self, itx: discord.Interaction, target: discord.Member):
        if target.id == itx.user.id or target.bot:
            return await itx.response.send_message(
                "❌ لا يمكنك استهداف هذا الحساب.",
                ephemeral=True,
            )
        left = self.check_cd(f"rob_{itx.user.id}", 600)
        if left:
            return await itx.response.send_message(
                f"🚨 الشرطة تلاحقك! انتظر `{left // 60}د {left % 60}ث`.",
                ephemeral=True,
            )

        target_user = await get_or_create_user(target.id, itx.guild.id)
        if target_user["balance"] < 100:
            return await itx.response.send_message(
                f"⚠️ {target.mention} لا يملك ما يكفي من الكاش للسرقة!",
                ephemeral=True,
            )

        if random.random() < 0.45:
            stolen = int(target_user["balance"] * random.uniform(0.2, 0.5))
            if not await transfer_balance(
                itx.guild.id,
                target.id,
                itx.user.id,
                stolen,
            ):
                return await itx.response.send_message(
                    "⚠️ تغيّر رصيد الهدف قبل إتمام العملية؛ حاول مجدداً.",
                    ephemeral=True,
                )
            await itx.response.send_message(
                f"🥷 نجحت بالسطو على {target.mention} وسرقت "
                f"**{stolen:,}** عملة!"
            )
            await self._leaderboard_changed(itx.guild.id)
        else:
            robber = await get_or_create_user(itx.user.id, itx.guild.id)
            penalty = min(200, robber["balance"])
            await update_balance(
                itx.user.id,
                itx.guild.id,
                -penalty,
                "balance",
            )
            await itx.response.send_message(
                f"🚔 كشفتك الشرطة! تم تغريمك **{penalty:,}** عملة كاش وتعويضها."
            )
            await self._leaderboard_changed(itx.guild.id)

    @app_commands.command(
        name="giveaway",
        description="إطلاق سحب مؤقت بالدقائق",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def giveaway(
        self,
        itx: discord.Interaction,
        minutes: int,
        prize: str,
    ):
        if minutes < 1:
            return await itx.response.send_message(
                "❌ المدة دقيقة على الأقل.",
                ephemeral=True,
            )
        ends_at = (
            discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        ).strftime("%Y-%m-%d %H:%M:%S")
        giveaway_id = await create_giveaway(
            itx.guild.id,
            itx.channel.id,
            prize,
            ends_at,
            itx.user.id,
        )
        view = LiveGiveaway(prize, giveaway_id)
        embed = discord.Embed(
            title="🎁 سحب مؤقت!",
            description=(
                f"الجائزة: **{prize}**\n"
                f"المدة: `{minutes}` دقيقة\n"
                "اضغط بالأسفل للاشتراك!"
            ),
            color=0x9B59B6,
        )
        try:
            message = await itx.channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            await cancel_giveaway(giveaway_id)
            raise
        await set_giveaway_message(giveaway_id, message.id)
        self.bot.add_view(view, message_id=message.id)
        await itx.response.send_message("✅ أُطلق السحب.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))

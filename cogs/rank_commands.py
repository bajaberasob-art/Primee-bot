"""Phase 6 PRIME rank commands. No XP writes, dashboard, or public leaderboard."""
import asyncio
import logging
import math
import re
from collections import OrderedDict
from time import monotonic

import discord
from discord import app_commands
from discord.ext import commands

import database
from cogs.card_generator import (
    generate_level_up_gif,
    generate_rank_card,
    should_use_animated_card,
)
from cogs.utilities import ShortcutInteraction
from interaction_runtime import send_interaction_message
from level_progression import text_progress, xp_required
from level_admin import is_level_admin
from prime_level_controls import controls_with_defaults, render_template

logger = logging.getLogger("PrimeRankCommands")
RANK_ALIASES = ("level", "lvl", "لفل", "رانك")


async def level_admin_check(interaction: discord.Interaction) -> bool:
    guild = getattr(interaction, "guild", None)
    if guild is None or not is_level_admin(getattr(interaction, "user", None), guild):
        raise app_commands.CheckFailure("level_admin_required")
    return True


class RankUnavailable(Exception):
    """A safe user-facing denial, not a bot failure."""


class CommandInteraction:
    """Apply the existing /top policy to component clicks without mutating itx."""
    def __init__(self, interaction, command):
        self.original, self.command = interaction, command

    def __getattr__(self, name):
        return getattr(self.original, name)


class LeaderboardView(discord.ui.View):
    def __init__(self, cog, owner_id, guild_id, mode):
        super().__init__(timeout=120)
        self.cog, self.owner_id, self.guild_id = cog, owner_id, guild_id
        self.message = None
        self.busy = False
        self.mode = mode
        self.set_selection()

    def set_selection(self):
        for child in self.children:
            selected = child.label.casefold() == self.mode
            child.style = (
                discord.ButtonStyle.primary if selected
                else discord.ButtonStyle.secondary
            )

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await send_interaction_message(
                interaction, "هذا التحكم لصاحب الأمر فقط؛ استخدم /top لفتح قائمتك.", ephemeral=True)
            return False
        return True

    async def switch(self, interaction, *, mode=None):
        try:
            guild = self.cog.guild_for(interaction)
            if guild.id != self.guild_id:
                raise RankUnavailable("هذه القائمة لم تعد متاحة في هذا السيرفر.")
            if self.busy:
                raise RankUnavailable("يرجى انتظار تحميل القائمة الحالية.")
            retry = self.cog.take_cooldown(guild.id, interaction.user.id, "navigation", 2)
            if retry:
                raise RankUnavailable(f"يرجى الانتظار {retry} ثانية قبل تغيير القائمة.")
            self.busy = True
            try:
                await self.cog.defer(interaction, thinking=False)
                utilities = self.cog.bot.get_cog("Utilities")
                command = self.cog.bot.tree.get_command("top")
                if utilities and command:
                    if not await utilities.app_command_interceptor(CommandInteraction(interaction, command)):
                        return
                settings = await self.cog.settings_for(interaction, "top")
                next_mode = mode or self.mode
                embeds = await self.cog.leaderboard_embeds(
                    guild, next_mode, "all_time", settings
                )
                self.mode = next_mode
                self.set_selection()
                top_config = controls_with_defaults(
                    settings.get("prime_controls"), settings,
                )["top"]
                if top_config["embed"]:
                    await interaction.message.edit(
                        content=None, embeds=embeds, view=self,
                    )
                else:
                    await interaction.message.edit(
                        content=self.cog.leaderboard_content(embeds),
                        embeds=[], view=self,
                    )
            finally:
                self.busy = False
        except RankUnavailable as error:
            await send_interaction_message(interaction, str(error), ephemeral=True)
        except Exception:
            logger.exception("Leaderboard navigation failed guild=%s", self.guild_id)
            await send_interaction_message(
                interaction, "تعذر تحديث المتصدرين الآن؛ حاول مجدداً لاحقاً.", ephemeral=True)

    @discord.ui.button(label="TEXT", style=discord.ButtonStyle.primary, row=0)
    async def text_button(self, interaction, button):
        await self.switch(interaction, mode="text")

    @discord.ui.button(label="VOICE", style=discord.ButtonStyle.secondary, row=0)
    async def voice_button(self, interaction, button):
        await self.switch(interaction, mode="voice")

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class RankCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cooldowns = OrderedDict()
        self._member_locks = [asyncio.Lock() for _ in range(32)]

    def guild_for(self, interaction):
        guild = getattr(interaction, "guild", None)
        if (guild is None or getattr(guild, "unavailable", False)
                or self.bot.get_guild(guild.id) is None):
            raise RankUnavailable("هذا الأمر متاح داخل سيرفر متصل بالبوت فقط.")
        return guild

    def take_cooldown(self, guild_id, user_id, family, seconds):
        key = (guild_id, user_id, family)
        now = monotonic()
        retry = self._cooldowns.get(key, 0) - now
        if retry > 0:
            return math.ceil(retry)
        self._cooldowns[key] = now + seconds
        self._cooldowns.move_to_end(key)
        # Bounded process-local cooldowns; no database writes.
        while len(self._cooldowns) > 20000:
            self._cooldowns.popitem(last=False)
        return 0

    @staticmethod
    async def defer(interaction, thinking=True):
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=thinking)

    async def settings_for(self, interaction, family):
        settings = await database.get_level_settings(interaction.guild.id) or {}
        if not settings.get("is_enabled", True) or not settings.get(f"command_{family}_enabled", True):
            raise RankUnavailable("هذا الأمر معطل في إعدادات المستويات لهذا السيرفر.")
        controls = controls_with_defaults(settings.get("prime_controls"), settings)
        if family == "rank" and not controls["rank"]["enabled"]:
            raise RankUnavailable("أمر الرتبة معطل في إعدادات PRIME.")
        if family == "top" and not controls["top"]["enabled"]:
            raise RankUnavailable("أمر TOP معطل في إعدادات PRIME.")
        allowed = {int(value) for value in settings.get(f"command_{family}_channels", [])}
        channel = interaction.channel
        channels = {channel.id, getattr(channel, "parent_id", None)}
        if allowed and not allowed.intersection(channels):
            raise RankUnavailable("هذا الأمر متاح في قنوات المستويات المحددة فقط.")
        return settings

    async def human_members(self, guild):
        # Exclude bots/deleted members before SQL LIMIT, not after the top ten.
        # A partial gateway cache must not silently produce incorrect rankings.
        if not getattr(guild, "chunked", False):
            async with self._member_locks[guild.id % len(self._member_locks)]:
                if not getattr(guild, "chunked", False):
                    try:
                        await asyncio.wait_for(guild.chunk(cache=True), timeout=15)
                    except (discord.HTTPException, asyncio.TimeoutError):
                        raise RankUnavailable("قائمة أعضاء السيرفر غير جاهزة؛ حاول مجدداً بعد قليل.")
        if not getattr(guild, "chunked", False):
            raise RankUnavailable("قائمة أعضاء السيرفر غير مكتملة؛ حاول مجدداً بعد قليل.")
        return {member.id: member for member in guild.members if not member.bot}

    async def target_member(self, guild, user):
        member = guild.get_member(user.id)
        if member is None:
            try:
                member = await asyncio.wait_for(guild.fetch_member(user.id), timeout=5)
            except (discord.HTTPException, asyncio.TimeoutError):
                raise RankUnavailable("لم أجد هذا العضو في السيرفر؛ ربما غادر أو حُذف حسابه.")
        if member.bot:
            raise RankUnavailable("بطاقات وترتيب المستويات مخصصة للأعضاء وليس للبوتات.")
        return member

    async def show_rank(self, interaction, member=None):
        try:
            guild = self.guild_for(interaction)
            retry = self.take_cooldown(guild.id, interaction.user.id, "rank", 8)
            if retry:
                raise RankUnavailable(f"يرجى الانتظار {retry} ثانية قبل إعادة استخدام أمر الرانك.")
            await self.defer(interaction)
            settings = await self.settings_for(interaction, "rank")
            target = await self.target_member(guild, member or interaction.user)
            humans = await self.human_members(guild)
            if target.id not in humans:
                raise RankUnavailable("هذا العضو لم يعد موجوداً في قائمة أعضاء السيرفر.")
            row = await database.get_command_rank_snapshot(guild.id, target.id, list(humans))
            controls = controls_with_defaults(
                settings.get("prime_controls"), settings,
            )
            rank_config = controls["rank"]
            card_settings = dict(settings)
            for key in ("total_messages", "total_voice_seconds", "current_streak"):
                card_settings[key] = row.get(key, 0)
            # imageOnly is a hard delivery contract. Even a contradictory legacy
            # setting (showCard=false + imageOnly=true) must still produce the card.
            should_render_card = bool(rank_config["showCard"] or rank_config["imageOnly"])
            image = None
            image_filename = "prime-rank.png"
            if should_render_card:
                renderer = (
                    generate_level_up_gif
                    if await should_use_animated_card(card_settings)
                    else generate_rank_card
                )
                if renderer is generate_level_up_gif:
                    image_filename = "prime-rank.gif"
                image = await renderer(
                    target, row["text_level"], row["text_xp"], xp_required(row["text_level"]),
                    row["rank"], row["total_members"], card_settings)
            attachment = discord.File(image, filename=image_filename) if image else None
            values = {
                "user": target.display_name,
                "username": target.name,
                "mention": getattr(target, "mention", f"<@{target.id}>"),
                "level": row["text_level"],
                "old_level": row["text_level"],
                "xp": row["text_xp"],
                "required_xp": xp_required(row["text_level"]),
                "progress": text_progress(row["text_xp"])["percentage"],
                "rank": row.get("rank") or "",
                "total_members": row.get("total_members", 0),
                "messages": row.get("total_messages", 0),
                "voice_time": int(row.get("total_voice_seconds", 0) or 0),
                "streak": row.get("current_streak", 0),
                "server": getattr(guild, "name", "PRIME"),
            }
            content = (
                render_template(rank_config["customMessage"], values)
                if rank_config["showCustomMessage"] else None
            )
            if rank_config["imageOnly"]:
                content = None
            embed = None
            if content and rank_config["sendEmbed"] and not rank_config["imageOnly"]:
                embed = discord.Embed(
                    title="PRIME • بطاقة المستوى",
                    description=content[:4000],
                    color=0x6366F1,
                )
                if attachment:
                    embed.set_image(url=f"attachment://{image_filename}")
            try:
                if not rank_config["imageOnly"] and not attachment and not content:
                    raise RankUnavailable("فعّل بطاقة الرتبة أو الرسالة المخصصة قبل استخدام الأمر.")
                payload = {
                    "allowed_mentions": discord.AllowedMentions.none(),
                }
                if content and not embed:
                    payload["content"] = content
                if attachment:
                    payload["file"] = attachment
                if embed:
                    payload["embed"] = embed
                await send_interaction_message(interaction, **payload)
            finally:
                if attachment:
                    attachment.close()
                if image:
                    image.close()
        except RankUnavailable as error:
            await send_interaction_message(interaction, str(error), ephemeral=True)
        except Exception:
            logger.exception("Rank card command failed guild=%s", getattr(interaction.guild, "id", None))
            await send_interaction_message(
                interaction, "تعذر إنشاء بطاقة PRIME الآن؛ حاول مجدداً لاحقاً.", ephemeral=True)

    async def leaderboard_embeds(self, guild, mode, period="all_time", settings=None):
        if mode not in {"text", "voice"}:
            raise RankUnavailable("اختر TEXT أو VOICE.")
        if period != "all_time":
            raise RankUnavailable("أمر /top يعرض الترتيب الدائم فقط؛ TOP اليومي والأسبوعي والشهري منفصل.")
        settings = settings or {}
        humans = await self.human_members(guild)
        top_config = controls_with_defaults(
            settings.get("prime_controls"), settings,
        )["top"]
        rows = await database.get_command_level_leaderboard(
            guild.id, list(humans), mode, period,
            limit=int(top_config["count"]),
        )
        palette = (0x12D6FF, 0x4263EB, 0x6366F1, 0x8B5CF6)
        period_label = ""
        embeds = []
        eligible_rows = []
        for row in rows:
            member = guild.get_member(row["user_id"])
            if member is not None and not member.bot:
                eligible_rows.append((member, row))
        for position, (member, row) in enumerate(eligible_rows, 1):
            name = discord.utils.escape_mentions(
                discord.utils.escape_markdown(member.display_name[:50])
            ).replace("\n", " ")
            total_xp = max(0, int(row.get("total_xp") or 0))
            progression = text_progress(total_xp)
            current_level = int(row.get("level") or progression["level"])
            required = int(progression["xp_required"])
            percent = min(100, max(0, int(progression["percentage"])))
            period_xp = max(0, int(row.get("xp") or 0))
            if period == "all_time":
                xp_line = f"إجمالي XP: **{total_xp:,}**"
            else:
                xp_line = (
                    f"XP الفترة: **+{period_xp:,}** · "
                    f"الإجمالي: **{total_xp:,}**"
                )
            avatar = getattr(member, "display_avatar", None)
            avatar_url = getattr(avatar, "url", None)
            embed = discord.Embed(
                title=(
                    top_config["embedTitle"]
                    if position == 1 else None
                ),
                description=(
                    f"المستوى **{current_level:,}** · {xp_line}\n"
                    + (
                        f"التقدم **{progression['progress_xp']:,}/{required:,} XP** "
                        f"({percent}%)"
                        if top_config["showProgress"] else ""
                    )
                ),
                color=int(top_config["embedColor"].lstrip("#"), 16),
            )
            embed.set_author(
                name=f"#{position} · {name}",
                icon_url=str(avatar_url) if avatar_url and top_config["showAvatar"] else None,
            )
            if position == 1:
                embed.add_field(
                    name=f"{mode.upper()} · أعلى {int(top_config['count'])}",
                    value=top_config["embedMessage"][:1024],
                    inline=False,
                )
                embed.set_footer(text="ترتيب PRIME الدائم • البوتات والأعضاء المغادرون مستبعدون")
            embeds.append(embed)
        if not embeds:
            embeds.append(discord.Embed(
                title="🏆 PRIME TOP",
                description="لا يوجد أعضاء لديهم XP في هذه القائمة بعد.",
                color=0x12D6FF,
            ))
        return embeds

    @staticmethod
    def leaderboard_content(embeds):
        lines = []
        for embed in embeds:
            name = embed.author.name if embed.author else "PRIME TOP"
            description = embed.description or ""
            lines.append(f"**{name}**\n{description}")
        return "\n\n".join(lines)[:1900]

    async def show_top(self, interaction, mode=None):
        view = None
        try:
            guild = self.guild_for(interaction)
            retry = self.take_cooldown(guild.id, interaction.user.id, "top", 5)
            if retry:
                raise RankUnavailable(f"يرجى الانتظار {retry} ثانية قبل إعادة استخدام /top.")
            await self.defer(interaction)
            settings = await self.settings_for(interaction, "top")
            top_config = controls_with_defaults(
                settings.get("prime_controls"), settings,
            )["top"]
            mode = mode or top_config["defaultMode"]
            embeds = await self.leaderboard_embeds(guild, mode, "all_time", settings)
            view = LeaderboardView(
                self, interaction.user.id, guild.id, mode
            )
            payload = {
                "view": view,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if top_config["embed"]:
                payload["embeds"] = embeds
            else:
                payload["content"] = self.leaderboard_content(embeds)
            view.message = await send_interaction_message(interaction, **payload)
            if view.message is None:
                view.stop()
        except RankUnavailable as error:
            await send_interaction_message(interaction, str(error), ephemeral=True)
        except Exception:
            if view is not None:
                view.stop()
            logger.exception("Leaderboard command failed guild=%s", getattr(interaction.guild, "id", None))
            await send_interaction_message(
                interaction, "تعذر تحميل المتصدرين الآن؛ حاول مجدداً لاحقاً.", ephemeral=True)

    @app_commands.command(name="rank", description="عرض بطاقة PRIME ومستوى العضو")
    @app_commands.guild_only()
    @app_commands.describe(member="العضو المطلوب، أو اتركه فارغاً لعرض بطاقتك")
    async def rank_slash(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await self.show_rank(interaction, member)

    @commands.command(name="rank", aliases=list(RANK_ALIASES), ignore_extra=False)
    @commands.guild_only()
    async def rank_prefix(self, ctx: commands.Context, member: str = None):
        # Resolve raw Discord mentions explicitly so Arabic aliases always show
        # the tagged member instead of accidentally falling back to the caller.
        if member is not None:
            argument = member.strip()
            mention = re.fullmatch(r"<@!?(\d+)>", argument)
            if mention:
                member_id = int(mention.group(1))
                member = ctx.guild.get_member(member_id)
                if member is None:
                    try:
                        member = await asyncio.wait_for(
                            ctx.guild.fetch_member(member_id), timeout=5,
                        )
                    except (discord.HTTPException, asyncio.TimeoutError):
                        raise commands.MemberNotFound(argument) from None
                if member is None:
                    raise commands.MemberNotFound(argument)
            else:
                # Explicit conversion avoids Optional[Member]'s silent
                # fallback-to-self behavior on invalid input.
                member = await commands.MemberConverter().convert(ctx, argument)
        await self.show_rank(ShortcutInteraction(ctx.message, self.rank_slash), member)

    @app_commands.command(name="give_level", description="منح مستويات نصية لعضو")
    @app_commands.guild_only()
    @app_commands.check(level_admin_check)
    @app_commands.describe(
        member="العضو الذي تريد منحه مستويات",
        levels="عدد المستويات المراد منحها (من 1 إلى 100)",
    )
    async def give_level_slash(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        levels: app_commands.Range[int, 1, 100],
    ):
        if member.bot:
            await send_interaction_message(
                interaction, "لا يمكن منح مستويات لحساب بوت.", ephemeral=True,
            )
            return
        try:
            result = await database.grant_text_levels(
                interaction.guild.id, member.id, int(levels),
            )
        except ValueError as error:
            await send_interaction_message(interaction, str(error), ephemeral=True)
            return

        if result["text_level"] > result["old_level"]:
            try:
                settings = await database.get_level_settings(interaction.guild.id)
                level_cog = self.bot.get_cog("Levels")
                handler = getattr(level_cog, "_handle_text_award", None)
                if settings and settings.get("is_enabled", True) and callable(handler):
                    await handler(member, settings, {**result, "overtakes": []}, milestones=False)
            except Exception:
                # The XP transaction is already committed; a reward/notice
                # failure must not make the admin repeat the level grant.
                logger.exception(
                    "Manual level grant post-processing failed guild=%s member=%s",
                    interaction.guild.id, member.id,
                )

        member_mention = getattr(member, "mention", f"<@{member.id}>")
        await send_interaction_message(
            interaction,
            f"تم منح {member_mention} عدد {levels} مستوى. مستواه النصي الآن {result['text_level']}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="take_level", description="خصم مستويات نصية من عضو")
    @app_commands.guild_only()
    @app_commands.check(level_admin_check)
    @app_commands.describe(
        member="العضو الذي تريد خصم مستويات منه",
        levels="عدد المستويات المراد خصمها (من 1 إلى 100)",
    )
    async def take_level_slash(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        levels: app_commands.Range[int, 1, 100],
    ):
        if member.bot:
            await send_interaction_message(
                interaction, "لا يمكن خصم مستويات من حساب بوت.", ephemeral=True,
            )
            return
        try:
            result = await database.take_text_levels(
                interaction.guild.id, member.id, int(levels),
            )
        except ValueError as error:
            await send_interaction_message(interaction, str(error), ephemeral=True)
            return

        member_mention = getattr(member, "mention", f"<@{member.id}>")
        if result["levels_removed"] == 0:
            message = f"مستوى {member_mention} النصي هو 0 بالفعل؛ لم تُخصم مستويات."
        else:
            message = (
                f"تم خصم {result['levels_removed']} مستوى من {member_mention}. "
                f"مستواه النصي الآن {result['text_level']}. لم يتم تغيير رتب المكافآت."
            )
        await send_interaction_message(
            interaction,
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="top", description="أعلى 10 أعضاء في مستويات PRIME النصية أو الصوتية")
    @app_commands.guild_only()
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="TEXT", value="text"),
            app_commands.Choice(name="VOICE", value="voice"),
        ],
    )
    async def top_slash(
        self,
        interaction: discord.Interaction,
        mode: str | None = None,
    ):
        await self.show_top(interaction, mode)

    @commands.command(name="top", aliases=["توب", "متصدرين"], ignore_extra=True)
    @commands.guild_only()
    async def top_prefix(self, ctx: commands.Context):
        await self.show_top(
            ShortcutInteraction(ctx.message, self.top_slash), None
        )

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.MemberNotFound):
            message = "لم أجد هذا العضو؛ استخدم منشن أو ID لعضو موجود في السيرفر."
        elif isinstance(error, commands.NoPrivateMessage):
            message = "هذا الأمر متاح داخل السيرفرات فقط."
        else:
            message = "تعذر تنفيذ أمر الرانك الآن."
        try:
            await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Cannot send prefix rank error")


async def setup(bot):
    await bot.add_cog(RankCommands(bot))


async def publish_rank_commands(bot, guild=None):
    """Upsert only Phase 6 commands, never bulk-replace unrelated registrations.

    Called after all cogs load when broad sync is disabled. Compare signatures
    first so normal restarts do not rewrite unchanged Discord registrations.
    """
    fields = ("name", "description", "type", "options", "default_member_permissions", "dm_permission")
    def signature(payload):
        return {key: payload.get(key) for key in fields}
    try:
        remote = await bot.tree.fetch_commands(guild=guild)
        existing = {command.name: command for command in remote}
        changed = 0
        for name in ("rank", "top", "give_level", "take_level"):
            command = bot.tree.get_command(name)
            if command is None:
                raise RuntimeError(f"local /{name} command is missing")
            payload = command.to_dict(bot.tree)
            previous = existing.get(name)
            if previous and signature(previous.to_dict()) == signature(payload):
                continue
            if guild is None:
                await bot.http.upsert_global_command(bot.application_id, payload)
            else:
                await bot.http.upsert_guild_command(bot.application_id, guild.id, payload)
            changed += 1
        logger.info("PRIME level command registrations ready (%s updated)", changed)
        return True
    except Exception:
        logger.exception("Cannot register PRIME level commands; existing Discord commands are untouched")
        return False
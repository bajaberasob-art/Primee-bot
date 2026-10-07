"""Lona text/voice XP engines and PRIME daily-streak experience."""
import asyncio
import logging
import math
import random
from time import monotonic
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import database
from cogs.card_generator import (
    generate_level_up_gif,
    generate_rank_card,
    generate_streak_card,
    has_animated_background,
)
from cogs.card_images import fetch_image
from level_engagement import EngagementXP
from level_progression import text_progress, xp_required
from prime_level_controls import controls_with_defaults, render_template
from streak_experience import (
    DEFAULT_DUPLICATE_TEMPLATE,
    DEFAULT_REMINDER_TEMPLATE,
    DEFAULT_STREAK_REACTION,
    build_streak_context,
)

logger = logging.getLogger("LonaLevels")
MAX_MULTIPLIER = 100.0
MAX_COOLDOWN = 86400
VOICE_DEFAULTS = {
    "is_enabled": 1, "voice_xp_enabled": 1, "voice_xp_per_minute": 20,
    "voice_mute_no_xp": 1, "voice_deafen_no_xp": 1, "voice_min_two_members": 1,
    "voice_diminishing_enabled": 0, "voice_diminishing_mins": 60,
    "voice_diminishing_rate": 0.5, "voice_separate_levels": 1,
    "xp_multiplier": 1, "boost_multiplier": 1, "boost_expires_at": None,
    "timed_xp_boosts": [], "voice_min_members": 2,
    "rewards_single_highest": 1,
    "overtake_alert_enabled": 1,
}


@dataclass(frozen=True)
class VoiceLevelUp:
    guild: Any
    member: Any
    old_level: int
    new_level: int
    current_xp: int


@dataclass
class VoiceSession:
    channel_id: int
    session_start: float
    last_processed: float
    last_wall: datetime
    role_ids: frozenset
    muted: bool
    deafened: bool
    eligible: bool = False
    eligible_duration: float = 0.0


@dataclass
class VoiceCredit:
    voice_xp: float = 0.0
    text_xp: float = 0.0
    seconds: float = 0.0


def is_blacklisted(
    blacklist: list, role_ids: set, channel_ids: set, user_id: int | None = None,
) -> bool:
    return any(
        row["target_type"] == "role" and row["target_id"] in role_ids
        or row["target_type"] == "channel" and row["target_id"] in channel_ids
        or row["target_type"] == "user" and user_id is not None
        and row["target_id"] == user_id
        for row in blacklist
    )


@dataclass(frozen=True)
class TextLevelUp:
    guild: Any
    member: Any
    old_level: int
    new_level: int
    current_xp: int
    xp_required_for_next_level: int
    next_level_total_xp: int


@dataclass(frozen=True)
class TextMilestone:
    guild: Any
    member: Any
    current_level: int
    current_xp: int
    next_level: int
    next_level_required_xp: int
    percentage: float
    next_level_total_xp: int


@dataclass(frozen=True)
class OvertakeEvent:
    passer: Any
    passed: Any
    new_rank: int
    guild: Any
    xp: int
    previous_rank: int


@dataclass(frozen=True)
class RolePromotionEvent:
    guild: Any
    member: Any
    role: Any
    old_level: int
    new_level: int
    xp: int


def bounded_multiplier(value: Any) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return 1.0
    if not math.isfinite(value) or value < 0:
        return 1.0
    return min(value, MAX_MULTIPLIER)


def boost_is_active(expires_at: Any, now: datetime) -> bool:
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry > now
    except (TypeError, ValueError):
        return False


def resolve_multiplier(settings: dict, multipliers: list, role_ids: set,
                       channel_ids: set, now: datetime) -> float:
    factors = [bounded_multiplier(settings.get("xp_multiplier", 1))]
    # Sorted IDs make multiplication deterministic even for duplicate targets.
    for item in sorted(multipliers, key=lambda row: row["id"]):
        if (
            item["target_type"] == "role" and item["target_id"] in role_ids
            or item["target_type"] == "channel" and item["target_id"] in channel_ids
        ):
            factors.append(bounded_multiplier(item["multiplier"]))
    if boost_is_active(settings.get("boost_expires_at"), now):
        factors.append(bounded_multiplier(settings.get("boost_multiplier", 1)))
    for boost in settings.get("timed_xp_boosts", []):
        if not isinstance(boost, dict):
            continue
        try:
            starts = datetime.fromisoformat(
                str(boost.get("starts_at", "")).replace("Z", "+00:00")
            )
            expires = datetime.fromisoformat(
                str(boost.get("expires_at", "")).replace("Z", "+00:00")
            )
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if starts <= now < expires:
                factors.append(bounded_multiplier(boost.get("multiplier", 1)))
        except (TypeError, ValueError):
            continue
    # Clamp only the final product so fractional factors remain meaningful.
    if any(factor == 0 for factor in factors):
        return 0.0
    return min(math.prod(factors), MAX_MULTIPLIER)


class Levels(EngagementXP, commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Bounded LRU: eviction never bypasses the persisted cooldown guard.
        self._cooldowns: OrderedDict[tuple[int, int], float] = OrderedDict()
        self._cooldown_capacity = 50000
        self._streak_tasks: set[asyncio.Task] = set()
        # Fixed stripes avoid a growing lock object per member.
        self._locks = [asyncio.Lock() for _ in range(256)]
        self.voice_sessions: dict[tuple[int, int], VoiceSession] = {}
        self._voice_pending: dict[tuple[int, int], VoiceCredit] = {}
        self._voice_configs: dict[int, tuple] = {}
        self._voice_lock = asyncio.Lock()
        self._voice_online = False
        self._voice_invalid: dict[tuple[int, str], str] = {}
        self._init_engagement_xp()

    @staticmethod
    def _xp_now():
        return datetime.now(timezone.utc)

    @staticmethod
    def _xp_tick():
        return monotonic()

    @staticmethod
    def cooldown_seconds(settings: dict) -> int:
        try:
            return max(0, min(MAX_COOLDOWN, int(settings.get("message_cooldown_seconds", 60))))
        except (TypeError, ValueError, OverflowError):
            return 60

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        key = (message.guild.id, message.author.id)
        try:
            streak_result = await self.record_message_streak(message)
            if streak_result.get("status") == "duplicate":
                await self._handle_duplicate_streak(message, streak_result)
            elif (
                streak_result.get("status") == "success"
                and streak_result.get("streak_updated")
                and callable(getattr(message, "add_reaction", None))
                and callable(getattr(message.channel, "send", None))
            ):
                self._track_streak_task(
                    self._publish_successful_streak(message, streak_result)
                )
        except Exception:
            # Streak persistence must not interrupt the existing text-XP path.
            logger.exception("Daily streak recording failed for guild=%s member=%s", *key)
        try:
            async with self._locks[hash(key) % len(self._locks)]:
                await self._process_message(message, key)
        except Exception:
            # Only this listener fails; commands, tickets and automod continue.
            logger.exception("Text XP failed for guild=%s member=%s", *key)

    def _track_streak_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._streak_tasks.add(task)
        task.add_done_callback(self._streak_tasks.discard)
        return task

    async def _build_streak_context(self, message, state, *, now=None):
        settings = await database.get_level_settings(message.guild.id) or {}
        experience = await database.get_level_streak_experience_config(
            message.guild.id, settings,
        )
        controls = settings.get("prime_controls") or {}
        streak_controls = controls.get("streak") or {}
        day_reset_time = (
            settings.get("day_reset_time")
            or settings.get("reset_time")
            or settings.get("resetTime")
            or streak_controls.get("day_reset_time")
            or streak_controls.get("dayResetTime")
            or "00:00"
        )
        stages = experience["stages"]
        try:
            ranks = await database.get_streak_ranks(
                message.guild.id, message.author.id
            )
        except Exception:
            logger.exception(
                "Could not load streak ranks guild=%s member=%s",
                message.guild.id, message.author.id,
            )
            ranks = {"server_rank": 1, "global_rank": 1}
        return (
            stages,
            ranks,
            build_streak_context(
                message.author,
                message.guild,
                state,
                stages,
                ranks,
                now=now,
                day_reset_time=day_reset_time,
            ),
            experience["controls"],
        )

    async def _handle_duplicate_streak(self, message, state):
        _, _, context, controls = await self._build_streak_context(message, state)
        values = context["values"]
        duplicate = controls["messages"]["duplicate"]
        response = None
        template = (
            duplicate.get("message")
            if duplicate.get("enabled", True)
            else DEFAULT_DUPLICATE_TEMPLATE
        ) or DEFAULT_DUPLICATE_TEMPLATE
        reply_text = render_template(template, values).strip()
        time_remaining = str(values.get("time_remaining") or "")
        countdown = f"⏳ الستريك القادم بعد {time_remaining}."
        if time_remaining and time_remaining not in reply_text:
            reply_text = f"{reply_text}\n{countdown}".strip()
        try:
            response = await message.reply(
                reply_text[:1900],
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning(
                "Could not send temporary duplicate-streak reply guild=%s member=%s",
                message.guild.id, message.author.id,
                exc_info=True,
            )
        self._track_streak_task(
            self._delete_temporary_duplicate(message, response)
        )

    async def _delete_temporary_duplicate(self, user_message, response):
        await asyncio.sleep(10)
        for item, label in (
            (user_message, "duplicate user message"),
            (response, "duplicate bot reply"),
        ):
            if item is None:
                continue
            delete = getattr(item, "delete", None)
            if not callable(delete):
                continue
            try:
                await delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                logger.warning("Could not delete %s", label, exc_info=True)

    async def _publish_successful_streak(self, message, state):
        try:
            try:
                settings = await database.get_level_settings(message.guild.id) or {}
                presentation = await database.get_level_streak_experience_config(
                    message.guild.id, settings,
                )
                reaction = (
                    presentation["controls"].get("successReaction")
                    or DEFAULT_STREAK_REACTION
                )
            except Exception:
                logger.exception(
                    "Could not load streak reaction guild=%s member=%s",
                    message.guild.id, message.author.id,
                )
                reaction = DEFAULT_STREAK_REACTION
            if reaction:
                try:
                    await message.add_reaction(reaction)
                except discord.HTTPException:
                    # A missing reaction permission must not undo the committed
                    # daily claim or prevent delivery of its PNG card.
                    logger.info(
                        "Could not add streak reaction guild=%s member=%s",
                        message.guild.id, message.author.id,
                        exc_info=True,
                    )

            stages, ranks, context, _ = await self._build_streak_context(message, state)
            card_state = {
                **state,
                "remaining": context["remaining"],
                "progress": context["progress"],
                "time_remaining": context["time_remaining"],
            }
            card_image = await generate_streak_card(
                message.author,
                card_state,
                context["stage"],
                context["next_stage"],
                ranks,
                stages=stages,
            )
            # A confirmed streak is represented only by its standalone PNG.
            # Keep success, stage-up and milestone prose out of this channel flow.
            await message.channel.send(
                file=discord.File(card_image, filename="streak-progress.png"),
            )
        except Exception:
            logger.exception(
                "Streak experience delivery failed guild=%s member=%s",
                message.guild.id, message.author.id,
            )

    async def _send_streak_announcement(
        self, channel, content, color, image_url=None, reaction=None
    ):
        try:
            color_value = int(str(color or "#F5C84C").lstrip("#"), 16)
        except (TypeError, ValueError):
            color_value = 0xF5C84C
        embed = discord.Embed(
            description=str(content or "")[:4000],
            color=discord.Color(color_value),
        )
        image = await fetch_image(image_url)
        file = None
        if image:
            filename = "streak-event.png"
            file = discord.File(image, filename=filename)
            embed.set_image(url=f"attachment://{filename}")
        sent = await channel.send(
            content=str(content or "")[:1900],
            embed=embed,
            file=file,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if reaction and sent is not None:
            try:
                await sent.add_reaction(str(reaction))
            except discord.HTTPException:
                logger.info("Could not add streak announcement reaction", exc_info=True)

    async def _process_message(self, message: discord.Message, key: tuple):
        now_tick = monotonic()
        cached = self._cooldowns.get(key)
        if cached is not None and now_tick < cached:
            return
        self._cooldowns.pop(key, None)
        settings = await database.get_level_settings(key[0])
        if settings is None:
            settings = await database.create_default_level_settings(key[0])
        if not settings["is_enabled"] or not settings.get("text_xp_enabled", True):
            return
        role_ids = {role.id for role in message.author.roles}
        channel_ids = {message.channel.id}
        # Parent-channel configuration applies to its threads too.
        parent_id = getattr(message.channel, "parent_id", None)
        if parent_id:
            channel_ids.add(parent_id)
        blacklist = await database.get_level_blacklist(key[0])
        if is_blacklisted(blacklist, role_ids, channel_ids, message.author.id):
            return
        try:
            allowed_channels = {int(value) for value in settings.get("text_allowed_channels", [])}
        except (TypeError, ValueError):
            allowed_channels = set()
        if allowed_channels and not allowed_channels.intersection(channel_ids):
            return
        now = datetime.now(timezone.utc)
        multipliers = await database.get_level_multipliers(key[0])
        factor = resolve_multiplier(settings, multipliers, role_ids, channel_ids, now)
        try:
            minimum = max(0, min(1000, int(settings.get("text_xp_min", 15))))
            maximum = max(0, min(1000, int(settings.get("text_xp_max", 25))))
        except (TypeError, ValueError, OverflowError):
            minimum, maximum = 15, 25
        if minimum > maximum:
            minimum, maximum = 15, 25
        xp = int(random.randint(minimum, maximum) * factor)
        if xp <= 0:
            return
        cooldown = self.cooldown_seconds(settings)
        award = await database.award_text_xp(
            *key, xp, now, cooldown,
            detect_overtakes=bool(settings.get("overtake_alert_enabled", True)))
        if award is None:
            # Restart/LRU miss: recover the actual remaining cooldown once.
            row = await database.get_user_level(*key)
            last = datetime.fromisoformat(str(row["last_message_at"]).replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            remaining = max(0, cooldown - (now - last).total_seconds())
            self._remember_cooldown(key, monotonic() + remaining)
            return
        self._remember_cooldown(key, monotonic() + cooldown)
        await self._handle_text_award(message.author, settings, award)

    async def _handle_text_award(self, member, settings, award, milestones=True):
        """Common post-commit text rewards/events for every XP source."""
        progress = text_progress(award["text_xp"])
        if award["text_level"] > award["old_level"]:
            granted_roles = await self.apply_text_rewards(
                member, award["text_level"], settings
            )
            for role in granted_roles:
                promotion = RolePromotionEvent(
                    member.guild, member, role, award["old_level"],
                    award["text_level"], award["text_xp"],
                )
                self.bot.dispatch("lona_role_promotion", promotion)
                self.bot.dispatch("prime_role_promotion", promotion)
            self.emit_level_up(TextLevelUp(
                member.guild, member, award["old_level"], award["text_level"],
                award["text_xp"], progress["xp_required"], progress["next_level_total_xp"],
            ))
        elif (milestones and settings.get("milestone_alert_enabled", True)
              and progress["percentage"] >= 90):
            # One notification on crossing 90%, not every following message.
            previous = text_progress(award["old_xp"])
            if previous["level"] == progress["level"] and previous["percentage"] < 90:
                self.emit_milestone(TextMilestone(
                    member.guild, member, award["text_level"], award["text_xp"],
                    award["text_level"] + 1, progress["xp_required"],
                    progress["percentage"], progress["next_level_total_xp"],
                ))
        if settings.get("overtake_alert_enabled", True):
            get_member = getattr(member.guild, "get_member", lambda user_id: None)
            for crossing in award.get("overtakes", []):
                passed = get_member(crossing["passed_id"]) or discord.Object(id=crossing["passed_id"])
                self.bot.dispatch("lona_text_overtake", OvertakeEvent(
                    member, passed, crossing["new_rank"], member.guild,
                    award["text_xp"], crossing["previous_rank"]))

    def _remember_cooldown(self, key: tuple, deadline: float):
        # Lazily purge old entries; the map is hard bounded even when idle.
        self._cooldowns[key] = deadline
        self._cooldowns.move_to_end(key)
        while len(self._cooldowns) > self._cooldown_capacity:
            self._cooldowns.popitem(last=False)

    def emit_level_up(self, event: TextLevelUp):
        """One range event represents every crossed level, including jumps."""
        self.bot.dispatch("lona_text_level_up", event)

    def emit_milestone(self, event: TextMilestone):
        self.bot.dispatch("lona_text_milestone", event)

    async def _send_leveling_notice(self, guild, channel_id, template, values, settings=None, kind=None, users=None):
        prime_config = {}
        if settings is not None and kind:
            prime_config = controls_with_defaults(
                settings.get("prime_controls"), settings
            )["notifications"].get(kind, {}) or {}
            if "enabled" in prime_config and not prime_config["enabled"]:
                return
            channel_id = prime_config.get("channel") or channel_id
            template = prime_config.get("message") or template
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            channel = self.bot.get_channel(int(channel_id))
        if channel is None or not callable(getattr(channel, "send", None)):
            logger.warning("Leveling announcement channel unavailable guild=%s channel=%s",
                           guild.id, channel_id)
            return
        content = render_template(template, values)
        if not content:
            return
        config = prime_config
        role = None
        try:
            if config.get("mentionRole"):
                role = guild.get_role(int(config["mentionRole"]))
        except (TypeError, ValueError):
            role = None
        if role:
            content = f"{role.mention} {content}"
        embed = None
        if config.get("sendAsEmbed"):
            try:
                color = int(str(config.get("embedColor") or "#12D6FF").lstrip("#"), 16)
            except (TypeError, ValueError):
                color = 0x12D6FF
            embed_description = render_template(
                config.get("embedDescription") or "{message}",
                {**values, "message": content},
            ) or content
            embed = discord.Embed(
                title=str(config.get("embedTitle") or "PRIME")[:256],
                description=embed_description[:4000],
                color=discord.Color(color),
            )
            if config.get("embedImage"):
                embed.set_image(url=str(config["embedImage"]))
            if config.get("embedFooter"):
                embed.set_footer(text=str(config["embedFooter"])[:2048])
            if config.get("timestamp"):
                embed.timestamp = datetime.now(timezone.utc)
        try:
            await channel.send(
                content=(content[:1900] if not embed or role else None),
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=(users or []) if config.get("mentionUser", True) else [],
                    roles=[role] if role else [],
                    everyone=False, replied_user=False,
                ),
            )
        except discord.HTTPException:
            logger.warning("Cannot send leveling announcement guild=%s channel=%s",
                           guild.id, channel_id, exc_info=True)

    async def _send_level_up_card(self, guild, member, settings, mode, template, event=None):
        controls = controls_with_defaults(settings.get("prime_controls"), settings)
        config = controls["levelup"]
        if not config["sendNotification"]:
            return
        configured_channel = str(config.get("channel") or "")
        channel_id = configured_channel or (
            settings.get("levelup_channel_id")
            if mode == "text"
            else settings.get("levelup_voice_channel_id")
        )
        if not channel_id or getattr(member, "bot", False):
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            channel = self.bot.get_channel(int(channel_id))
        if channel is None or not callable(getattr(channel, "send", None)):
            logger.warning(
                "Level-up card channel unavailable guild=%s channel=%s",
                guild.id, channel_id,
            )
            return

        if not getattr(guild, "chunked", True):
            try:
                await asyncio.wait_for(guild.chunk(cache=True), timeout=15)
            except (discord.HTTPException, asyncio.TimeoutError):
                logger.warning("Cannot build complete level-up rank guild=%s", guild.id)
                return
        humans = {
            item.id: item
            for item in getattr(guild, "members", ())
            if not getattr(item, "bot", False)
        }
        if member.id not in humans:
            return

        snapshot = await database.get_command_rank_snapshot(
            guild.id, member.id, list(humans), mode=mode,
        )
        event_old_level = int(getattr(event, "old_level", 0)) if event is not None else None
        event_new_level = int(getattr(event, "new_level", 0)) if event is not None else None
        event_xp = int(getattr(event, "current_xp", 0)) if event is not None else None
        current_xp = max(
            0,
            event_xp if event_xp is not None and event_xp > 0
            else int(snapshot.get("xp") or 0),
        )
        current = text_progress(current_xp)
        level = max(
            0,
            event_new_level if event_new_level is not None else int(current["level"]),
        )
        old_level = max(
            0,
            event_old_level if event_old_level is not None else level - 1,
        )
        rank = snapshot.get("rank")
        total_members = int(snapshot.get("total_members") or len(humans))
        card_settings = dict(settings)
        card_settings.update({
            "total_messages": int(snapshot.get("total_messages") or 0),
            "total_voice_seconds": int(snapshot.get("total_voice_seconds") or 0),
            "current_streak": int(snapshot.get("current_streak") or 0),
        })
        image = None
        image_filename = "prime-level-up.png"
        if config["showRankCard"]:
            card_design = card_settings.get("card_design")
            if not isinstance(card_design, dict):
                card_design = {}
            animated = card_design.get("animationEnabled", True) is not False
            animated = animated or await has_animated_background(card_settings)
            renderer = generate_level_up_gif if animated else generate_rank_card
            if animated:
                image_filename = "prime-level-up.gif"
            image = await renderer(
                member, level, current_xp, xp_required(level), rank,
                total_members, card_settings,
            )

        values = {
            "user": getattr(member, "mention", ""),
            "mention": getattr(member, "mention", ""),
            "username": getattr(member, "display_name", getattr(member, "name", "")),
            "level": level,
            "old_level": old_level,
            "xp": current_xp,
            "required_xp": xp_required(level),
            "progress": text_progress(current_xp)["percentage"],
            "rank": rank or "",
            "total_members": total_members,
            "messages": int(snapshot.get("total_messages") or 0),
            "voice_time": int(snapshot.get("total_voice_seconds") or 0),
            "streak": int(snapshot.get("current_streak") or 0),
            "server": getattr(guild, "name", "PRIME"),
            "period": "",
        }
        if not config["mentionUser"]:
            values["user"] = values["username"]
            values["mention"] = values["username"]
        notification_template = config.get("message") or template
        description = render_template(notification_template, values) or (
            f"Congratulations {values['user']} — level {level}."
        )
        title = str(config.get("embedTitle") or settings.get("levelup_title") or "🎉 Level Up!")[:256]
        try:
            color_value = int(str(config.get("embedColor") or "#12D6FF").lstrip("#"), 16)
        except (TypeError, ValueError):
            color_value = 0x12D6FF
        embed = discord.Embed(
            title=title,
            description=description[:4000],
            color=discord.Color(color_value),
        ) if config["sendAsEmbed"] else None
        if embed:
            if image:
                embed.set_image(url=f"attachment://{image_filename}")
            elif config.get("embedImage"):
                embed.set_image(url=config["embedImage"])
            if config.get("embedThumbnail"):
                embed.set_thumbnail(url=config["embedThumbnail"])
            if config.get("embedFooter"):
                embed.set_footer(text=config["embedFooter"][:2048])
            if config["timestamp"]:
                embed.timestamp = datetime.now(timezone.utc)
        attachment = discord.File(image, filename=image_filename) if image else None
        role = None
        if config.get("mentionRole"):
            try:
                role = guild.get_role(int(config["mentionRole"]))
            except (TypeError, ValueError):
                role = None
        try:
            await channel.send(
                content=role.mention if role else (None if embed else description[:1900]),
                embed=embed,
                file=attachment,
                allowed_mentions=discord.AllowedMentions(
                    users=[member] if config["mentionUser"] else [],
                    roles=[role] if role else [],
                    everyone=False, replied_user=False,
                ),
            )
        finally:
            if attachment:
                attachment.close()
            if image:
                image.close()

    @commands.Cog.listener()
    async def on_lona_text_level_up(self, event: TextLevelUp):
        settings = await database.get_level_settings(event.guild.id)
        if not settings or not settings.get("is_enabled", True):
            return
        controls = controls_with_defaults(settings.get("prime_controls"), settings)
        if not controls["levelup"].get("sendNotification", True):
            return
        try:
            await self._send_level_up_card(
                event.guild, event.member, settings, "text",
                settings.get("levelup_template"),
                event=event,
            )
        except Exception:
            logger.exception(
                "Text level-up card failed guild=%s member=%s",
                event.guild.id, event.member.id,
            )

    @commands.Cog.listener()
    async def on_lona_voice_level_up(self, event: VoiceLevelUp):
        settings = await database.get_level_settings(event.guild.id)
        if not settings or not settings.get("is_enabled", True):
            return
        controls = controls_with_defaults(settings.get("prime_controls"), settings)
        if not controls["levelup"].get("sendNotification", True):
            return
        try:
            await self._send_level_up_card(
                event.guild, event.member, settings, "voice",
                settings.get("levelup_voice_template"),
                event=event,
            )
        except Exception:
            logger.exception(
                "Voice level-up card failed guild=%s member=%s",
                event.guild.id, event.member.id,
            )

    @commands.Cog.listener()
    async def on_lona_text_milestone(self, event: TextMilestone):
        settings = await database.get_level_settings(event.guild.id)
        if not settings or not settings.get("is_enabled", True):
            return
        controls = controls_with_defaults(settings.get("prime_controls"), settings)
        config = controls["notifications"].get("milestone", {})
        if not config.get("enabled"):
            return
        await self._send_leveling_notice(
            event.guild, settings.get("milestone_channel_id"), settings.get("milestone_template"),
            {"user": event.member.mention, "mention": event.member.mention,
             "username": event.member.display_name, "level": event.current_level,
             "server": event.guild.name},
            settings, "milestone", [event.member],
        )

    @commands.Cog.listener()
    async def on_lona_text_overtake(self, event: OvertakeEvent):
        settings = await database.get_level_settings(event.guild.id)
        if not settings or not settings.get("is_enabled", True):
            return
        controls = controls_with_defaults(settings.get("prime_controls"), settings)
        config = controls["notifications"].get("overtake", {})
        if not config.get("enabled"):
            return
        await self._send_leveling_notice(
            event.guild, settings.get("overtake_channel_id"), settings.get("overtake_template"),
            {"passer": event.passer.mention, "passed": event.passed.mention,
             "user": event.passer.mention, "mention": event.passer.mention,
             "username": event.passer.display_name, "rank": event.new_rank,
             "server": event.guild.name},
            settings, "overtake", [event.passer, event.passed],
        )

    @commands.Cog.listener()
    async def on_prime_role_promotion(self, event: RolePromotionEvent):
        settings = await database.get_level_settings(event.guild.id) or {}
        if not settings or not settings.get("is_enabled", True):
            return
        controls = controls_with_defaults(settings.get("prime_controls"), settings)
        config = controls["notifications"].get("role_promotion", {})
        if not config.get("enabled"):
            return
        role = event.role
        progress = text_progress(event.xp)
        values = {
            "user": getattr(event.member, "mention", f"<@{event.member.id}>"),
            "mention": getattr(event.member, "mention", f"<@{event.member.id}>"),
            "username": getattr(event.member, "display_name", getattr(event.member, "name", "")),
            "level": event.new_level,
            "old_level": event.old_level,
            "xp": event.xp,
            "required_xp": xp_required(event.new_level),
            "progress": progress["percentage"],
            "rank": "",
            "total_members": "",
            "messages": "",
            "voice_time": "",
            "streak": "",
            "server": getattr(event.guild, "name", "PRIME"),
            "period": "",
            "role": getattr(role, "name", "الرتبة"),
        }
        await self._send_leveling_notice(
            event.guild,
            config.get("channel"),
            config.get("message"),
            values,
            settings,
            "role_promotion",
            [event.member],
        )

    async def apply_text_rewards(self, member: discord.Member, level: int, settings: dict):
        try:
            return await self._apply_text_rewards(member, level, settings)
        except Exception:
            logger.exception("Text reward failure guild=%s member=%s", member.guild.id, member.id)
            return []

    async def _apply_text_rewards(self, member: discord.Member, level: int, settings: dict):
        return await self._apply_level_rewards(member, level, settings, "text")

    async def apply_voice_rewards(self, member: discord.Member, level: int, settings: dict):
        try:
            return await self._apply_level_rewards(member, level, settings, "voice")
        except Exception:
            logger.exception("Voice reward failure guild=%s member=%s", member.guild.id, member.id)
            return []

    async def _apply_level_rewards(self, member, level, settings, reward_type):
        all_rewards = await database.get_level_rewards(member.guild.id)
        rewards = [
            row for row in all_rewards
            if row["reward_type"] == reward_type and row["level_required"] <= level
        ]
        if not rewards:
            return []
        rewards.sort(key=lambda row: (row["level_required"], row["id"]))
        single = bool(settings["rewards_single_highest"])
        selected = rewards[-1:] if single else rewards
        held = {role.id for role in member.roles}
        highest_granted = False
        granted_roles = []
        for reward in selected:
            role = member.guild.get_role(reward["role_id"])
            if role is None:
                logger.warning("Deleted %s reward role %s in guild %s", reward_type, reward["role_id"], member.guild.id)
                continue
            if role.id in held:
                highest_granted = True
                continue
            if not self._manageable(member.guild, role):
                logger.warning("Cannot manage %s reward role %s in guild %s", reward_type, role.id, member.guild.id)
                continue
            try:
                await member.add_roles(role, reason=f"Lona {reward_type} level reward")
                held.add(role.id)
                granted_roles.append(role)
                highest_granted = True
            except discord.HTTPException:
                logger.warning("Cannot grant %s reward role %s", reward_type, role.id, exc_info=True)
        # Never strip old rewards if granting the replacement failed.
        if single and highest_granted:
            selected_id = selected[0]["role_id"]
            lower_ids = {
                row["role_id"] for row in rewards
                if row["level_required"] < selected[0]["level_required"]
                and row["role_id"] != selected_id
            }
            # Preserve any role also configured for the other XP track.
            other_ids = {
                row["role_id"] for row in all_rewards
                if row["reward_type"] != reward_type
            }
            for role_id in sorted(lower_ids - other_ids):
                role = member.guild.get_role(role_id)
                if role and role_id in held and self._manageable(member.guild, role):
                    try:
                        await member.remove_roles(role, reason=f"Lona highest {reward_type} reward")
                    except discord.HTTPException:
                        logger.warning("Cannot remove %s reward role %s", reward_type, role_id, exc_info=True)
        return granted_roles

    async def cog_load(self):
        if not self.voice_xp_worker.is_running():
            self.voice_xp_worker.start()
            logger.info("Voice XP worker started")
        if not self.periodic_top_worker.is_running():
            self.periodic_top_worker.start()
            logger.info("Periodic PRIME TOP scheduler started")

    def cog_unload(self):
        self.voice_xp_worker.cancel()
        self.periodic_top_worker.cancel()
        self._voice_online = False
        self.voice_sessions.clear()
        self._voice_pending.clear()
        self._reaction_seen.clear()
        self._reaction_cooldowns.clear()
        logger.info("Voice XP worker stopped")

    def _voice_number(self, guild_id, settings, field, default, maximum):
        raw = settings.get(field, default)
        try:
            number = float(raw)
            if not math.isfinite(number) or number < 0 or number > maximum:
                raise ValueError("out of range")
            self._voice_invalid.pop((guild_id, field), None)
            return number
        except (ValueError, TypeError, OverflowError):
            if self._voice_invalid.get((guild_id, field)) != repr(raw):
                logger.warning("Invalid voice setting guild=%s %s=%r; using %s",
                               guild_id, field, raw, default)
                self._voice_invalid[(guild_id, field)] = repr(raw)
            return default

    async def _load_voice_config(self, guild_id):
        stored = await database.get_level_settings(guild_id)
        settings = {key: (stored or {}).get(key, value) for key, value in VOICE_DEFAULTS.items()}
        settings["voice_xp_per_minute"] = self._voice_number(
            guild_id, settings, "voice_xp_per_minute", 20, 10000)
        settings["voice_diminishing_mins"] = self._voice_number(
            guild_id, settings, "voice_diminishing_mins", 60, 1000000)
        settings["voice_diminishing_rate"] = self._voice_number(
            guild_id, settings, "voice_diminishing_rate", 0.5, 1)
        return (settings, await database.get_level_multipliers(guild_id),
                await database.get_level_blacklist(guild_id))

    def _refresh_voice_config(self, guild_id, config, tick, wall):
        old = self._voice_configs.get(guild_id)
        if old is not None and old != config:
            # The DB does not timestamp configuration changes. Fail closed for
            # the unobserved interval instead of granting retroactive XP.
            for key, session in self.voice_sessions.items():
                if key[0] == guild_id:
                    session.last_processed = max(session.last_processed, tick)
                    session.last_wall = wall
        self._voice_configs[guild_id] = config

    @staticmethod
    def _voice_flags(state):
        return (bool(state.self_mute or state.mute),
                bool(state.self_deaf or state.deaf))

    def _new_voice_session(self, member, state, tick, wall):
        muted, deafened = self._voice_flags(state)
        return VoiceSession(state.channel.id, tick, tick, wall,
                            frozenset(role.id for role in member.roles), muted, deafened)

    def _voice_eligible(self, key, session, counts):
        settings, _, blacklist = self._voice_configs[key[0]]
        return bool(
            settings["is_enabled"] and settings["voice_xp_enabled"]
            and not (settings["voice_mute_no_xp"] and session.muted)
            and not (settings["voice_deafen_no_xp"] and session.deafened)
            and counts.get(session.channel_id, 0) >= settings["voice_min_members"]
            and not is_blacklisted(blacklist, session.role_ids, {session.channel_id})
        )

    def _recheck_voice_eligibility(self, guild_id, tick, wall, discard_changes=False):
        sessions = [(key, s) for key, s in self.voice_sessions.items() if key[0] == guild_id]
        counts: dict[int, int] = {}
        for _, session in sessions:
            counts[session.channel_id] = counts.get(session.channel_id, 0) + 1
        for key, session in sessions:
            eligible = self._voice_eligible(key, session, counts)
            if discard_changes and eligible != session.eligible:
                session.last_processed, session.last_wall = tick, wall
            session.eligible = eligible

    def _accrue_voice(self, key, session, tick, wall):
        elapsed = max(0.0, tick - session.last_processed)
        settings, multipliers, _ = self._voice_configs[key[0]]
        if session.eligible and elapsed:
            credit = self._voice_pending.setdefault(key, VoiceCredit())
            # Split at every timed multiplier boundary so voice XP uses only
            # the portion of each tick during which that boost was active.
            boundaries = set()
            interval_end = session.last_wall + timedelta(seconds=elapsed)
            for field in ("boost_expires_at",):
                try:
                    boundary = datetime.fromisoformat(
                        str(settings.get(field) or "").replace("Z", "+00:00")
                    )
                    if boundary.tzinfo is None:
                        boundary = boundary.replace(tzinfo=timezone.utc)
                    if session.last_wall < boundary < interval_end:
                        boundaries.add((boundary - session.last_wall).total_seconds())
                except (TypeError, ValueError):
                    pass
            for boost in settings.get("timed_xp_boosts", []):
                if not isinstance(boost, dict):
                    continue
                for field in ("starts_at", "expires_at"):
                    try:
                        boundary = datetime.fromisoformat(
                            str(boost.get(field) or "").replace("Z", "+00:00")
                        )
                        if boundary.tzinfo is None:
                            boundary = boundary.replace(tzinfo=timezone.utc)
                        if session.last_wall < boundary < interval_end:
                            boundaries.add((boundary - session.last_wall).total_seconds())
                    except (TypeError, ValueError):
                        continue
            points = [0.0, *sorted(boundaries), elapsed]
            pieces = [end - start for start, end in zip(points, points[1:]) if end > start]
            offset = 0.0
            for seconds in pieces:
                factor = resolve_multiplier(
                    settings, multipliers, session.role_ids, {session.channel_id},
                    session.last_wall + timedelta(seconds=offset + seconds / 2))
                weighted = seconds
                if settings["voice_diminishing_enabled"]:
                    threshold = settings["voice_diminishing_mins"] * 60
                    full = min(seconds, max(0, threshold - session.eligible_duration))
                    weighted = full + (seconds - full) * settings["voice_diminishing_rate"]
                xp = weighted / 60 * settings["voice_xp_per_minute"] * factor
                if settings["voice_separate_levels"]:
                    credit.voice_xp += xp
                else:
                    credit.text_xp += xp
                session.eligible_duration += seconds
                credit.seconds += seconds
                offset += seconds
        session.last_processed = max(session.last_processed, tick)
        session.last_wall = wall

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or not self._voice_online:
            return
        tick, wall = monotonic(), datetime.now(timezone.utc)
        guild_id, key = member.guild.id, (member.guild.id, member.id)
        async with self._voice_lock:
            if not self._voice_online:
                return
            try:
                config = await self._load_voice_config(guild_id)
                self._refresh_voice_config(guild_id, config, tick, wall)
                affected = {state.channel.id for state in (before, after) if state.channel}
                for other_key, session in list(self.voice_sessions.items()):
                    if other_key[0] == guild_id and session.channel_id in affected:
                        self._accrue_voice(other_key, session, tick, wall)
                session = self.voice_sessions.get(key)
                if after.channel is None:
                    self.voice_sessions.pop(key, None)
                elif session is None:
                    self.voice_sessions[key] = self._new_voice_session(member, after, tick, wall)
                else:
                    session.channel_id = after.channel.id
                    session.role_ids = frozenset(role.id for role in member.roles)
                    session.muted, session.deafened = self._voice_flags(after)
                self._recheck_voice_eligibility(guild_id, tick, wall)
            except Exception:
                # Resume only from a new validated snapshot on the next tick.
                for other_key, session in self.voice_sessions.items():
                    if other_key[0] == guild_id:
                        session.eligible = False
                        session.last_processed, session.last_wall = tick, wall
                logger.exception("Voice state tracking failed guild=%s member=%s", *key)

    async def rebuild_voice_sessions(self):
        async with self._voice_lock:
            self._voice_online = False
            self.voice_sessions.clear()
            self._voice_pending.clear()
            self._voice_configs.clear()
            for guild in self.bot.guilds:
                if guild.unavailable:
                    continue
                try:
                    self._voice_configs[guild.id] = await self._load_voice_config(guild.id)
                    tick, wall = monotonic(), datetime.now(timezone.utc)
                    voice_states = getattr(guild, "voice_states", None)
                    if voice_states is None:
                        # Some Discord clients expose only the cached voice channels and
                        # member.voice state, not a guild.voice_states mapping.
                        voice_states = {
                            member.id: state
                            for channel in getattr(guild, "voice_channels", ())
                            for member in getattr(channel, "members", ())
                            if (state := getattr(member, "voice", None))
                            and getattr(state, "channel", None)
                        }
                    for user_id, state in voice_states.items():
                        member = guild.get_member(user_id)
                        if member and not member.bot and state.channel:
                            self.voice_sessions[(guild.id, user_id)] = self._new_voice_session(member, state, tick, wall)
                    self._recheck_voice_eligibility(guild.id, tick, wall)
                except Exception:
                    logger.exception("Voice recovery failed guild=%s", guild.id)
            self._voice_online = True
            logger.info("Voice tracking resumed: %s active sessions", len(self.voice_sessions))

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._voice_online:
            await self.rebuild_voice_sessions()

    @commands.Cog.listener()
    async def on_resumed(self):
        if not self._voice_online:
            await self.rebuild_voice_sessions()

    @commands.Cog.listener()
    async def on_disconnect(self):
        # Stop eligibility immediately, even if the worker currently holds its lock.
        self._voice_online = False
        async with self._voice_lock:
            self._voice_online = False
            self.voice_sessions.clear()
            self._voice_pending.clear()
        logger.info("Voice tracking paused on gateway disconnect")

    async def _publish_periodic_top(self, guild, period, schedule_date, config):
        zone = ZoneInfo(config["timezone"])
        if period == "daily":
            end_local = datetime.combine(schedule_date, datetime.min.time(), tzinfo=zone)
            start_local = end_local - timedelta(days=1)
            period_key = schedule_date.isoformat()
        elif period == "weekly":
            end_date = schedule_date - timedelta(days=schedule_date.weekday())
            end_local = datetime.combine(end_date, datetime.min.time(), tzinfo=zone)
            start_local = end_local - timedelta(days=7)
            period_key = schedule_date.strftime("%G-W%V")
        else:
            end_date = schedule_date.replace(day=1)
            if end_date.month == 1:
                start_date = end_date.replace(year=end_date.year - 1, month=12)
            else:
                start_date = end_date.replace(month=end_date.month - 1)
            end_local = datetime.combine(end_date, datetime.min.time(), tzinfo=zone)
            start_local = datetime.combine(start_date, datetime.min.time(), tzinfo=zone)
            period_key = schedule_date.strftime("%Y-%m")
        if not getattr(guild, "chunked", True):
            try:
                await asyncio.wait_for(guild.chunk(cache=True), timeout=15)
            except (discord.HTTPException, asyncio.TimeoutError):
                logger.warning("Periodic TOP skipped; member cache incomplete guild=%s", guild.id)
                return
        humans = {
            member.id: member for member in getattr(guild, "members", ())
            if not getattr(member, "bot", False)
        }
        channel = guild.get_channel(int(config["channel"])) if config.get("channel") else None
        if channel is None or not callable(getattr(channel, "send", None)):
            logger.warning("Periodic TOP channel missing guild=%s period=%s", guild.id, period)
            return
        mode = str(config.get("mode") or "both")
        rows = await database.get_level_periodic_top_leaderboard(
            guild.id, list(humans), mode,
            start_local.astimezone(timezone.utc),
            end_local.astimezone(timezone.utc),
            limit=int(config["winners"]),
        )
        if not await database.claim_level_periodic_top_run(
            guild.id, period, period_key,
        ):
            return
        try:
            winners = []
            lines = []
            for position, row in enumerate(rows, 1):
                member = humans.get(int(row["user_id"]))
                if member is None:
                    continue
                winners.append(member)
                name = member.mention if config["mentionWinners"] else discord.utils.escape_markdown(
                    getattr(member, "display_name", getattr(member, "name", "عضو"))
                )
                level = int(row.get("level") or 0)
                total_xp = int(row.get("total_xp") or 0)
                progress = text_progress(total_xp)
                values = {
                    "user": getattr(member, "display_name", getattr(member, "name", "")),
                    "username": getattr(member, "name", ""),
                    "mention": member.mention if config["mentionWinners"] else name,
                    "level": level,
                    "old_level": max(0, level - 1),
                    "xp": int(row.get("xp") or 0),
                    "required_xp": int(progress["xp_required"]),
                    "progress": int(progress["percentage"]),
                    "rank": position,
                    "total_members": len(humans),
                    "messages": int(row.get("total_messages") or 0),
                    "voice_time": int(row.get("total_voice_seconds") or 0),
                    "streak": int(row.get("current_streak") or 0),
                    "server": guild.name,
                    "period": period,
                }
                line = render_template(config["message"], values)
                extras = []
                if config["showRank"]:
                    extras.append(f"#{position}")
                if config["showXp"]:
                    extras.append(f"{values['xp']:,} XP")
                if extras and "{xp}" not in config["message"] and "{rank}" not in config["message"]:
                    line = f"{line} · {' · '.join(extras)}"
                lines.append(line[:500])
            message_text = "\n".join(lines) or "لا يوجد فائزون مسجلون في هذه الفترة."
            description = render_template(
                config["embedDescription"], {"message": message_text, "period": period},
            ) or message_text
            color = int(str(config["embedColor"]).lstrip("#"), 16)
            embed = discord.Embed(
                title=config["embedTitle"][:256],
                description=description[:4000],
                color=discord.Color(color),
            ) if config["embed"] else None
            reward_role = None
            try:
                if config.get("rewardRole"):
                    reward_role = guild.get_role(int(config["rewardRole"]))
            except (TypeError, ValueError):
                reward_role = None
            bot_member = getattr(guild, "me", None)
            can_assign = bool(
                reward_role and bot_member
                and getattr(getattr(bot_member, "guild_permissions", None), "manage_roles", False)
                and not getattr(reward_role, "managed", False)
                and reward_role < getattr(bot_member, "top_role", reward_role)
            )
            if can_assign:
                for member in winners:
                    try:
                        if reward_role not in getattr(member, "roles", ()):
                            await member.add_roles(
                                reward_role,
                                reason=f"PRIME {period} TOP reward",
                            )
                    except (discord.HTTPException, AttributeError):
                        logger.warning(
                            "Cannot assign periodic TOP role guild=%s member=%s",
                            guild.id, member.id, exc_info=True,
                        )
            delivered = False
            try:
                allowed_mentions = discord.AllowedMentions(
                    users=winners if config["mentionWinners"] else [],
                    roles=False, everyone=False, replied_user=False,
                )
                await channel.send(
                    content=None if embed else message_text[:1900],
                    embed=embed,
                    allowed_mentions=allowed_mentions,
                )
                delivered = True
            except discord.HTTPException:
                logger.warning(
                    "Periodic TOP delivery failed guild=%s period=%s; leaving run retryable",
                    guild.id, period, exc_info=True,
                )
        finally:
            if delivered:
                await database.complete_level_periodic_top_run(
                    guild.id, period, period_key,
                )

    @tasks.loop(seconds=60)
    async def periodic_top_worker(self):
        now_utc = datetime.now(timezone.utc)
        for guild in list(getattr(self.bot, "guilds", ())):
            try:
                settings = await database.get_level_settings(guild.id)
                if not settings or not settings.get("is_enabled", True):
                    continue
                controls = controls_with_defaults(settings.get("prime_controls"), settings)
                for period, config in controls["periodic"].items():
                    if not config.get("enabled"):
                        continue
                    try:
                        zone = ZoneInfo(config["timezone"])
                        local = now_utc.astimezone(zone)
                        target_hour, target_minute = map(int, config["time"].split(":"))
                        target = (target_hour, target_minute)
                        current = (local.hour, local.minute)
                        schedule_date = local.date()
                        if period == "daily":
                            if current < target:
                                continue
                        elif period == "weekly":
                            weekday = int(config.get("weekday", 4))
                            schedule_date -= timedelta(days=(local.weekday() - weekday) % 7)
                            if schedule_date == local.date() and current < target:
                                continue
                        else:
                            scheduled = local.date().replace(day=int(config.get("dayOfMonth", 1)))
                            if local.date() < scheduled:
                                continue
                            if local.date() == scheduled and current < target:
                                continue
                            if local.date() > scheduled:
                                pass
                            schedule_date = scheduled
                        await self._publish_periodic_top(
                            guild, period, schedule_date, config,
                        )
                    except Exception:
                        logger.exception(
                            "Periodic TOP failed guild=%s period=%s",
                            guild.id, period,
                        )
            except Exception:
                logger.exception("Periodic TOP settings failed guild=%s", guild.id)

    @periodic_top_worker.before_loop
    async def before_periodic_top_worker(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def voice_xp_worker(self):
        await self.process_voice_tick()

    @voice_xp_worker.before_loop
    async def before_voice_xp_worker(self):
        await self.bot.wait_until_ready()
        if not self._voice_online:
            await self.rebuild_voice_sessions()

    @voice_xp_worker.error
    async def voice_xp_worker_error(self, error):
        logger.error("Voice XP worker stopped unexpectedly", exc_info=(type(error), error, error.__traceback__))

    async def process_voice_tick(self):
        async with self._voice_lock:
            if not self._voice_online:
                return
            guild_ids = {key[0] for key in self.voice_sessions} | {key[0] for key in self._voice_pending}
            for guild_id in guild_ids:
                try:
                    await self._process_voice_guild(guild_id)
                except Exception:
                    # Unknown eligibility must not be backfilled on recovery.
                    tick, wall = monotonic(), datetime.now(timezone.utc)
                    for key, session in self.voice_sessions.items():
                        if key[0] == guild_id:
                            session.last_processed, session.last_wall = tick, wall
                            session.eligible = False
                    logger.exception("Voice XP guild processing failed guild=%s", guild_id)
            live_guilds = {key[0] for key in self.voice_sessions} | {key[0] for key in self._voice_pending}
            for guild_id in set(self._voice_configs) - live_guilds:
                self._voice_configs.pop(guild_id, None)

    async def _process_voice_guild(self, guild_id):
        guild = self.bot.get_guild(guild_id)
        if guild is None or guild.unavailable:
            for key in set(self.voice_sessions) | set(self._voice_pending):
                if key[0] == guild_id:
                    self.voice_sessions.pop(key, None)
                    self._voice_pending.pop(key, None)
            return
        config = await self._load_voice_config(guild_id)
        if not self._voice_online:
            return
        tick, wall = monotonic(), datetime.now(timezone.utc)
        self._refresh_voice_config(guild_id, config, tick, wall)
        voice_states = getattr(guild, "voice_states", None)
        if voice_states is None:
            voice_states = {
                member.id: state
                for channel in getattr(guild, "voice_channels", ())
                for member in getattr(channel, "members", ())
                if (state := getattr(member, "voice", None))
                and getattr(state, "channel", None)
            }
        # Reconcile all current states before counting humans or accruing time.
        for key, session in list(self.voice_sessions.items()):
            if key[0] != guild_id:
                continue
            try:
                member = guild.get_member(key[1])
                state = voice_states.get(key[1])
                if not member or member.bot or not state or not state.channel or not guild.get_channel(state.channel.id):
                    self.voice_sessions.pop(key, None)
                    continue
                flags = self._voice_flags(state)
                roles = frozenset(role.id for role in member.roles)
                if (session.channel_id, session.muted, session.deafened, session.role_ids) != (state.channel.id, *flags, roles):
                    session.channel_id, session.muted, session.deafened, session.role_ids = state.channel.id, *flags, roles
                    session.last_processed, session.last_wall = tick, wall
            except Exception:
                self.voice_sessions.pop(key, None)
                logger.exception("Voice validation failed guild=%s member=%s", *key)
        for user_id, state in voice_states.items():
            key = (guild_id, user_id)
            if key in self.voice_sessions:
                continue
            try:
                member = guild.get_member(user_id)
                if member and not member.bot and state.channel and guild.get_channel(state.channel.id):
                    self.voice_sessions[key] = self._new_voice_session(member, state, tick, wall)
            except Exception:
                logger.exception("Voice recovery validation failed guild=%s member=%s", *key)
        self._recheck_voice_eligibility(guild_id, tick, wall, discard_changes=True)
        for key, session in list(self.voice_sessions.items()):
            if key[0] == guild_id:
                try:
                    self._accrue_voice(key, session, tick, wall)
                except Exception:
                    session.last_processed, session.last_wall = tick, wall
                    logger.exception("Voice accrual failed guild=%s member=%s", *key)
        for key in list(self._voice_pending):
            if not self._voice_online:
                return
            if key[0] == guild_id:
                try:
                    await self._flush_voice_credit(guild, key, config[0])
                except Exception:
                    logger.exception("Voice credit failed guild=%s member=%s", *key)

    async def _flush_voice_credit(self, guild, key, settings):
        credit = self._voice_pending[key]
        voice_xp, text_xp, seconds = (int(value + 1e-9) for value in
                                     (credit.voice_xp, credit.text_xp, credit.seconds))
        if voice_xp or text_xp or seconds:
            award = await database.award_voice_xp(
                *key, voice_xp, text_xp, seconds,
                detect_overtakes=bool(settings.get("overtake_alert_enabled", True)))
            # Subtract only after commit; failures retain a retryable credit.
            credit.voice_xp = max(0, credit.voice_xp - voice_xp)
            credit.text_xp = max(0, credit.text_xp - text_xp)
            credit.seconds = max(0, credit.seconds - seconds)
            member = guild.get_member(key[1])
            if member:
                if award["voice_level"] > award["old_voice_level"]:
                    granted_roles = await self.apply_voice_rewards(
                        member, award["voice_level"], settings
                    )
                    for role in granted_roles:
                        promotion = RolePromotionEvent(
                            guild, member, role, award["old_voice_level"],
                            award["voice_level"], award["voice_xp"],
                        )
                        self.bot.dispatch("lona_role_promotion", promotion)
                        self.bot.dispatch("prime_role_promotion", promotion)
                    self.bot.dispatch("lona_voice_level_up", VoiceLevelUp(
                        guild, member, award["old_voice_level"], award["voice_level"], award["voice_xp"]))
                if text_xp:
                    await self._handle_text_award(member, settings, award, milestones=False)
        if key not in self.voice_sessions:
            self._voice_pending.pop(key, None)

    @staticmethod
    def _manageable(guild: discord.Guild, role: discord.Role) -> bool:
        me = guild.me
        return bool(
            me and me.guild_permissions.manage_roles and not role.managed
            and not role.is_default() and role < me.top_role
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Levels(bot))
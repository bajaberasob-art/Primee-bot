"""Message-streak core and engagement entry points; no announcement UI."""
import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone

import discord
from discord.ext import commands

import database

logger = logging.getLogger("LonaLevels")


class EngagementXP:
    """Small mixin using Levels' existing progression and event handling."""

    def _init_engagement_xp(self):
        self._reaction_seen = OrderedDict()
        self._reaction_cooldowns = OrderedDict()
        self._reaction_locks = [asyncio.Lock() for _ in range(64)]
        self._reaction_cache_capacity = 50000

    def _reaction_cache_put(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._reaction_cache_capacity:
            cache.popitem(last=False)

    async def record_message_streak(self, message):
        """Record an eligible message in the existing leveling/streak service."""
        guild = getattr(message, "guild", None)
        author = getattr(message, "author", None)
        if guild is None or author is None or getattr(author, "bot", True):
            return {"status": "ignored"}
        if getattr(message, "webhook_id", None) is not None:
            return {"status": "ignored"}
        message_type = getattr(message, "type", discord.MessageType.default)
        if message_type not in {
            discord.MessageType.default,
            discord.MessageType.reply,
        }:
            return {"status": "ignored"}

        channel = getattr(message, "channel", None)
        channel_id = getattr(channel, "id", None)
        activity_at = getattr(message, "created_at", None)
        if channel_id is None or not isinstance(activity_at, datetime):
            return {"status": "ignored"}

        settings = await database.get_level_settings(guild.id)
        if (
            settings is None
            or not settings.get("is_enabled")
            or not settings.get("streak_enabled")
        ):
            return {"status": "disabled"}
        streak_channel_id = settings.get("streak_channel_id")
        if streak_channel_id is None:
            return {"status": "channel_not_configured"}
        if int(streak_channel_id) != int(channel_id):
            return {"status": "wrong_channel"}

        # The database rechecks live settings under its write transaction before
        # the unique daily claim and state update.
        result = await database.record_level_streak_activity(
            guild.id, author.id, channel_id, activity_at
        )
        if result.get("status") == "success" and result.get("streak_updated"):
            try:
                experience = await database.get_level_streak_experience_config(
                    guild.id, settings,
                )
                result["experience_events"] = (
                    await database.record_streak_experience_events(
                        guild.id,
                        author.id,
                        result.get("previous_streak", 0),
                        result.get("current_streak", 0),
                        result["activity_date"],
                        stages=experience["stages"],
                        milestones=experience["milestones"],
                    )
                )
            except Exception:
                # The committed daily claim must remain valid if experience
                # notification bookkeeping has a transient failure.
                logger.exception(
                    "Streak experience event reservation failed guild=%s user=%s",
                    guild.id, author.id,
                )
                result["experience_events"] = {"stages": [], "milestones": []}
        return result

    @staticmethod
    def _reaction_int(settings, field, default, maximum):
        try:
            return max(0, min(maximum, int(settings.get(field, default))))
        except (TypeError, ValueError, OverflowError):
            logger.warning("Invalid reaction setting %s", field)
            return default

    @staticmethod
    async def _reaction_member(guild, user_id, supplied=None):
        member = guild.get_member(user_id)
        if member is None and supplied is not None and hasattr(supplied, "roles"):
            member = supplied
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
        return member

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or getattr(guild, "unavailable", False):
            return
        supplied = getattr(payload, "member", None)
        if supplied is not None and supplied.bot:
            return
        emoji = payload.emoji
        emoji_key = f"id:{emoji.id}" if emoji.id else f"unicode:{emoji.name}"
        event_key = (guild.id, payload.message_id, payload.user_id, emoji_key)
        key = (guild.id, payload.user_id)
        try:
            async with self._reaction_locks[hash(key) % len(self._reaction_locks)]:
                now_tick = self._xp_tick()
                if event_key in self._reaction_seen:
                    return
                if self._reaction_cooldowns.get(key, 0) > now_tick:
                    return
                settings = await database.get_level_settings(guild.id)
                if settings is None:
                    settings = await database.create_default_level_settings(guild.id)
                if not settings["is_enabled"] or not settings.get("reaction_xp_enabled", True) or not (
                    settings["reaction_xp_reactor"] or settings["reaction_xp_author"]
                ):
                    return
                channel = guild.get_channel(payload.channel_id)
                if channel is None:
                    channel = self.bot.get_channel(payload.channel_id)
                if channel is None:
                    return
                channel_ids = {channel.id}
                if getattr(channel, "parent_id", None):
                    channel_ids.add(channel.parent_id)
                allowed = {int(value) for value in settings["reaction_allowed_channels"]}
                if allowed and not allowed.intersection(channel_ids):
                    return
                reactor = await self._reaction_member(guild, payload.user_id, supplied)
                if reactor is None or reactor.bot:
                    return
                # Reuse the same blacklist and multiplier functions as chat/voice.
                from cogs.levels import is_blacklisted, resolve_multiplier

                blacklist = await database.get_level_blacklist(guild.id)
                reactor_roles = {role.id for role in reactor.roles}
                if is_blacklisted(blacklist, reactor_roles, channel_ids, reactor.id):
                    return
                members = {}
                if settings["reaction_xp_reactor"]:
                    members[reactor.id] = reactor
                if settings["reaction_xp_author"]:
                    try:
                        message = await channel.fetch_message(payload.message_id)
                        if not message.author.bot:
                            author = await self._reaction_member(guild, message.author.id, message.author)
                            if author is not None and not author.bot:
                                members[author.id] = author
                    except discord.HTTPException:
                        logger.warning("Cannot fetch reaction message guild=%s message=%s",
                                       guild.id, payload.message_id, exc_info=True)
                now = self._xp_now()
                base = self._reaction_int(settings, "reaction_xp_amount", 5, 10000)
                cooldown = self._reaction_int(settings, "reaction_cooldown_seconds", 60, 86400)
                multipliers = await database.get_level_multipliers(guild.id)
                awards = {}
                for user_id, member in members.items():
                    roles = {role.id for role in member.roles}
                    if is_blacklisted(blacklist, roles, channel_ids, user_id):
                        continue
                    xp = int(base * resolve_multiplier(settings, multipliers, roles, channel_ids, now))
                    if xp > 0:
                        awards[user_id] = xp
                result = await database.award_reaction_xp(
                    guild.id, payload.message_id, reactor.id, emoji_key, awards, now,
                    cooldown, bool(settings.get("overtake_alert_enabled", True)),
                )
                if result["status"] in {"awarded", "duplicate"}:
                    self._reaction_cache_put(self._reaction_seen, event_key, True)
                if result.get("remaining_seconds"):
                    self._reaction_cache_put(
                        self._reaction_cooldowns, key, self._xp_tick() + result["remaining_seconds"])
                if result["status"] == "awarded":
                    for user_id in result["cooldown_ids"]:
                        self._reaction_cache_put(
                            self._reaction_cooldowns, (guild.id, user_id), self._xp_tick() + cooldown)
                    for award in result["awards"]:
                        try:
                            await self._handle_text_award(members[award["user_id"]], settings, award)
                        except Exception:
                            logger.exception("Reaction XP event failed guild=%s member=%s",
                                             guild.id, award["user_id"])
        except Exception:
            logger.exception("Reaction XP failed guild=%s reactor=%s", *key)

    async def claim_daily_streak(self, member, channel=None, claimed_at=None):
        """Internal claim entry point for later commands; Riyadh calendar dates.

        No chat or reaction event automatically invokes a claim.
        """
        if member.bot:
            return {"status": "ignored"}
        key = (member.guild.id, member.id)
        try:
            async with self._locks[hash(key) % len(self._locks)]:
                settings = await database.get_level_settings(key[0])
                if settings is None:
                    settings = await database.create_default_level_settings(key[0])
                if not settings["is_enabled"] or not settings["streak_enabled"]:
                    return {"status": "disabled"}
                now = claimed_at or self._xp_now()
                if now.tzinfo is None:
                    now = now.replace(tzinfo=timezone.utc)
                now = now.astimezone(timezone.utc)
                from cogs.levels import is_blacklisted, resolve_multiplier

                channel_ids = {channel.id} if channel else set()
                if channel and getattr(channel, "parent_id", None):
                    channel_ids.add(channel.parent_id)
                roles = {role.id for role in member.roles}
                if is_blacklisted(await database.get_level_blacklist(key[0]), roles, channel_ids):
                    return {"status": "blacklisted"}
                factor = resolve_multiplier(
                    settings, await database.get_level_multipliers(key[0]), roles, channel_ids, now)
                result = await database.claim_level_streak(
                    *key, now, factor, bool(settings.get("overtake_alert_enabled", True)))
                if result["status"] == "claimed" and result["xp_awarded"] > 0:
                    await self._handle_text_award(member, settings, result)
                return result
        except Exception:
            logger.exception("Streak claim failed guild=%s member=%s", *key)
            return {"status": "error"}
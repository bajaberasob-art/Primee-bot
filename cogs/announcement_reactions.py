"""A bounded background queue; message listeners do no database/HTTP work."""
import asyncio
import logging
import time
from collections import OrderedDict

import discord
from discord.ext import commands

import announcement_reactions as store


logger = logging.getLogger("AnnouncementReactions")


class AnnouncementReactions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.configs = {}
        self.revisions = {}
        self.queue = asyncio.Queue(maxsize=256)
        self.workers = []
        self.dropped = 0
        self.pending = {}
        self.guild_dropped = {}
        self.errors = {}
        self.seen = OrderedDict()

    async def cog_load(self):
        for config in await store.enabled_settings():
            self.update_config(config)
        self.workers = [asyncio.create_task(self.worker()) for _ in range(2)]

    async def cog_unload(self):
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        self.configs.clear()
        self.revisions.clear()
        self.errors.clear()
        self.pending.clear()
        self.guild_dropped.clear()
        self.seen.clear()
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()

    def update_config(self, config):
        guild_id = int(config["guild_id"])
        if config["revision"] < self.revisions.get(guild_id, -1):
            return
        self.revisions[guild_id] = config["revision"]
        if config["enabled"]:
            self.configs[guild_id] = dict(config)
        else:
            self.configs.pop(guild_id, None)
        self.errors.pop(guild_id, None)

    def runtime_status(self, guild_id):
        return {"queue_size": self.pending.get(guild_id, 0), "dropped": self.guild_dropped.get(guild_id, 0)}

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        self.configs.pop(guild.id, None)
        self.revisions.pop(guild.id, None)
        self.errors.pop(guild.id, None)
        self.guild_dropped.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_message(self, message):
        guild = getattr(message, "guild", None)
        config = self.configs.get(guild.id) if guild else None
        if not config or str(message.channel.id) != config["channel_id"]:
            return
        if message.created_at.timestamp() < (config["activated_at"] or 0):
            return
        # Bot and webhook announcements also qualify; reactions cannot create a loop.
        if message.id in self.seen:
            return
        if self.queue.full():
            self.dropped += 1
            self.guild_dropped[guild.id] = self.guild_dropped.get(guild.id, 0) + 1
            await self.note_error(config, "طابور التفاعلات ممتلئ؛ تم تجاوز رسالة لحماية أداء البوت.")
            return
        self.seen[message.id] = None
        if len(self.seen) > 2048:
            self.seen.popitem(last=False)
        self.queue.put_nowait((message, config["revision"]))
        self.pending[guild.id] = self.pending.get(guild.id, 0) + 1

    def current(self, message, revision):
        config = self.configs.get(message.guild.id)
        return config if (
            config and config["revision"] == revision
            and config["channel_id"] == str(message.channel.id)
            and message.created_at.timestamp() >= (config["activated_at"] or 0)
        ) else None

    async def note_error(self, config, message):
        guild_id = int(config["guild_id"])
        old = self.errors.get(guild_id)
        now = time.monotonic()
        if old and old[0] == message and now - old[1] < 60:
            return
        self.errors[guild_id] = (message, now)
        logger.warning("guild=%s announcement_reactions: %s", guild_id, message)
        try:
            await store.record_error(guild_id, config["revision"], message)
        except Exception:
            logger.exception("Cannot persist announcement reaction status for guild=%s", guild_id)

    async def react(self, message, revision):
        config = self.current(message, revision)
        if not config:
            return
        status = store.inspect_configuration(message.guild, config)
        if status["code"] not in ("ready", "runtime_error"):
            await self.note_error(config, status["message"])
            return
        emojis = {str(emoji.id): emoji for emoji in message.guild.emojis}
        for emoji_id in config["emoji_ids"]:
            if not self.current(message, revision):
                return
            try:
                # discord.py handles bucket rate limits; no fire-and-forget HTTP fan-out.
                await asyncio.wait_for(message.add_reaction(emojis[emoji_id]), timeout=15)
            except discord.Forbidden:
                await self.note_error(config, "رفض Discord إضافة التفاعل؛ راجع صلاحيات القناة والإيموجيات.")
                return
            except discord.NotFound:
                await self.note_error(config, "حُذفت الرسالة أو الإيموجي قبل إضافة التفاعل.")
                return
            except (discord.HTTPException, asyncio.TimeoutError):
                await self.note_error(config, "تعذر إضافة التفاعل مؤقتًا؛ ستُعالج الرسائل الجديدة التالية دون إعادة القديم.")
                return
        if int(config["guild_id"]) in self.errors or config.get("last_error"):
            try:
                await store.clear_error(message.guild.id, revision)
                current = self.current(message, revision)
                if current:
                    current["last_error"] = None
                    self.errors.pop(message.guild.id, None)
            except Exception:
                logger.exception("Cannot clear recovered announcement reaction status")

    async def worker(self):
        while True:
            message, revision = await self.queue.get()
            try:
                await self.react(message, revision)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected announcement reaction failure guild=%s", message.guild.id)
                config = self.current(message, revision)
                if config:
                    await self.note_error(config, "حدث خطأ في التفاعل التلقائي؛ المحرك ما زال يعمل.")
            finally:
                left = self.pending.get(message.guild.id, 1) - 1
                if left > 0:
                    self.pending[message.guild.id] = left
                else:
                    self.pending.pop(message.guild.id, None)
                self.queue.task_done()


async def setup(bot):
    await bot.add_cog(AnnouncementReactions(bot))

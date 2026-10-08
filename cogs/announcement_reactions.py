"""Bounded asynchronous text reactions and image separators; never replay history."""
import asyncio
import logging
import io
import time
from collections import OrderedDict

import discord
from discord.ext import commands

import announcement_reactions as store
import announcement_images as images


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
        self.image_cache = OrderedDict()

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
        self.image_cache.clear()
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()

    def update_config(self, config):
        guild_id = int(config["guild_id"])
        if config["revision"] < self.revisions.get(guild_id, -1):
            return
        self.revisions[guild_id] = config["revision"]
        if config["enabled"] or config.get("line_enabled"):
            self.configs[guild_id] = dict(config)
        else:
            self.configs.pop(guild_id, None)
        for kind in ("reactions", "line"):
            self.errors.pop((guild_id, kind), None)

    def runtime_status(self, guild_id):
        return {"queue_size": self.pending.get(guild_id, 0), "dropped": self.guild_dropped.get(guild_id, 0)}

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        self.configs.pop(guild.id, None)
        self.revisions.pop(guild.id, None)
        for kind in ("reactions", "line"):
            self.errors.pop((guild.id, kind), None)
        for key in list(self.image_cache):
            if key[0] == guild.id:
                self.image_cache.pop(key, None)
        self.guild_dropped.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_message(self, message):
        guild = getattr(message, "guild", None)
        config = self.configs.get(guild.id) if guild else None
        if not config or not self.text_message(message):
            return
        if not self.current(message, config["revision"]):
            return
        if message.id in self.seen:
            return
        if self.queue.full():
            self.dropped += 1
            self.guild_dropped[guild.id] = self.guild_dropped.get(guild.id, 0) + 1
            for kind in ("reactions", "line"):
                if self.current(message, config["revision"], kind):
                    await self.note_error(config, "طابور التشغيل ممتلئ؛ تم تجاوز رسالة لحماية أداء البوت.", kind)
            return
        self.seen[message.id] = None
        if len(self.seen) > 2048:
            self.seen.popitem(last=False)
        self.queue.put_nowait((message, config["revision"]))
        self.pending[guild.id] = self.pending.get(guild.id, 0) + 1

    def text_message(self, message):
        if not (getattr(message, "content", "") or "").strip():
            return False
        if getattr(message, "attachments", ()) or getattr(message, "stickers", ()):
            return False
        if getattr(message, "type", discord.MessageType.default) not in (
            discord.MessageType.default, discord.MessageType.reply,
        ):
            return False
        if any(
            getattr(embed, "type", None) in ("image", "video", "gifv")
            or getattr(getattr(embed, "image", None), "url", None)
            or getattr(getattr(embed, "video", None), "url", None)
            for embed in getattr(message, "embeds", ())
        ):
            return False
        bot_user = getattr(self.bot, "user", None)
        return not (bot_user and getattr(getattr(message, "author", None), "id", None) == bot_user.id)

    def current(self, message, revision, kind=None):
        config = self.configs.get(message.guild.id)
        if not config or config["revision"] != revision:
            return None
        channel = str(message.channel.id)
        created = message.created_at.timestamp()
        reactions = config["enabled"] and channel in store.reaction_channels(config) and created >= (config["activated_at"] or 0)
        line = config.get("line_enabled") and channel in config.get("line_channel_ids", []) and created >= (config.get("line_activated_at") or 0)
        active = reactions if kind == "reactions" else line if kind == "line" else reactions or line
        return config if active else None

    async def note_error(self, config, message, kind="reactions"):
        guild_id = int(config["guild_id"])
        if self.revisions.get(guild_id) != config["revision"]:
            return
        key = (guild_id, kind)
        old = self.errors.get(key)
        now = time.monotonic()
        if old and old[0] == message and now - old[1] < 60:
            return
        self.errors[key] = (message, now)
        logger.warning("guild=%s announcement_%s: %s", guild_id, kind, message)
        try:
            await store.record_error(guild_id, config["revision"], message, kind)
        except Exception:
            logger.exception("Cannot persist announcement reaction status for guild=%s", guild_id)

    async def react(self, message, revision):
        # A failure in either feature does not stop the independently enabled one.
        for kind, handler in (("reactions", self.react_reactions), ("line", self.send_line)):
            try:
                await handler(message, revision)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Announcement operation failed guild=%s kind=%s", message.guild.id, kind)
                config = self.current(message, revision, kind)
                if config:
                    await self.note_error(config, "حدث خطأ مؤقت؛ المحرك ما زال يعمل للرسائل الجديدة.", kind)

    async def react_reactions(self, message, revision):
        config = self.current(message, revision, "reactions")
        if not config:
            return
        status = store.inspect_configuration(message.guild, config, channel_id=str(message.channel.id))
        if status["code"] not in ("ready", "runtime_error"):
            await self.note_error(config, status["message"])
            return
        emojis = {str(emoji.id): emoji for emoji in message.guild.emojis}
        for emoji_id in config["emoji_ids"]:
            if not self.current(message, revision, "reactions"):
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
        await self.clear_recovered(message, revision, "reactions")

    async def send_line(self, message, revision):
        config = self.current(message, revision, "line")
        if not config:
            return
        status = store.inspect_line(message.guild, config, channel_id=str(message.channel.id))
        if status["code"] not in ("ready", "runtime_error"):
            await self.note_error(config, status["message"], "line")
            return
        key = (message.guild.id, config["line_image_id"])
        image = self.image_cache.get(key)
        if image is None:
            image = await images.get_image(*key, include_payload=True)
            if image is None:
                await self.note_error(config, "صورة الفاصل غير متاحة؛ ارفع صورة جديدة واحفظ.", "line")
                return
            self.image_cache[key] = image
            if len(self.image_cache) > 8:
                self.image_cache.popitem(last=False)
        self.image_cache.move_to_end(key)
        if not self.current(message, revision, "line"):
            return
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}[image["mime"]]
        stream = io.BytesIO(image["payload"])
        file = discord.File(stream, filename=f"prime-line.{extension}")
        try:
            await asyncio.wait_for(
                message.channel.send(file=file, allowed_mentions=discord.AllowedMentions.none()),
                timeout=15,
            )
        except discord.Forbidden:
            await self.note_error(config, "رفض Discord إرسال الفاصل؛ تحقق من إرسال الرسائل وإرفاق الملفات.", "line")
            return
        except discord.NotFound:
            await self.note_error(config, "حُذفت قناة أوتو لاين قبل إرسال الفاصل.", "line")
            return
        except (discord.HTTPException, asyncio.TimeoutError):
            await self.note_error(config, "تعذر إرسال الفاصل مؤقتًا؛ لن تُعاد الرسائل القديمة.", "line")
            return
        finally:
            file.close()
            stream.close()
        await self.clear_recovered(message, revision, "line")

    async def clear_recovered(self, message, revision, kind):
        config = self.current(message, revision, kind)
        error_field = "line_last_error" if kind == "line" else "last_error"
        key = (message.guild.id, kind)
        if config and (key in self.errors or config.get(error_field)):
            try:
                await store.clear_error(message.guild.id, revision, kind)
                current = self.current(message, revision, kind)
                if current:
                    current[error_field] = None
                    self.errors.pop(key, None)
            except Exception:
                logger.exception("Cannot clear recovered announcement operation status")

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

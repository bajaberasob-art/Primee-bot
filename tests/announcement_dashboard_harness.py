"""Isolated fake Discord gateway for announcement UI verification; never production."""
import asyncio
import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
from aiohttp import web

from tests import dashboard_harness as base
from cogs.announcement_reactions import AnnouncementReactions


PERMS = SimpleNamespace(
    view_channel=True, read_message_history=True, add_reactions=True,
    use_external_emojis=False,
    send_messages=True, attach_files=True,
)


class Channel(base.Chan):
    @property
    def type(self):
        return discord.ChannelType.text

    def permissions_for(self, member):
        return PERMS


class Emoji(base.Emoji):
    def is_usable(self):
        return self.available


class Bot(base.FakeBot):
    def get_cog(self, name):
        return self.announcements if name == "AnnouncementReactions" else super().get_cog(name)


async def inject_message(request):
    data = await request.json()
    guild = base.ws.bot_ref.get_guild(base.FakeGuild.id)
    channel = guild.get_channel(int(data["channel_id"]))
    if not channel:
        raise web.HTTPBadRequest()
    reactions = []
    lines = []
    author_id = int(data.get("author_id", 100000000000000020))
    bot_user = getattr(base.ws.bot_ref, "user", None)
    if data.get("own_bot"):
        author_id = bot_user.id

    async def add_reaction(emoji):
        reactions.append(str(emoji.id))

    async def send(**kwargs):
        file = kwargs["file"]
        lines.append({"filename": file.filename, "size": len(file.fp.getvalue())})
        # Dispatch the bot's sent message too, proving no auto-line recursion.
        own_message = SimpleNamespace(
            id=time.time_ns(), guild=guild, channel=channel, content="",
            attachments=[object()], stickers=[], embeds=[], author=bot_user,
            created_at=datetime.now(timezone.utc),
        )
        await base.ws.bot_ref.announcements.on_message(own_message)

    channel.send = send
    message = SimpleNamespace(
        id=time.time_ns(), guild=guild, channel=channel,
        created_at=datetime.fromtimestamp(time.time() - float(data.get("age_seconds", 0)), timezone.utc),
        add_reaction=add_reaction,
        content=data.get("content", "رسالة نصية جديدة"),
        attachments=[object()] if data.get("attachment") else [],
        stickers=[object()] if data.get("sticker") else [], embeds=[],
        type=discord.MessageType.pins_add if data.get("system") else discord.MessageType.default,
        author=SimpleNamespace(id=author_id),
    )
    await base.ws.bot_ref.announcements.on_message(message)
    await base.ws.bot_ref.announcements.queue.join()
    return web.json_response({"reactions": reactions, "lines": lines})


async def toggle_permissions(request):
    PERMS.add_reactions = request.query.get("denied") != "1"
    PERMS.attach_files = request.query.get("no_files") != "1"
    PERMS.send_messages = request.query.get("no_send") != "1"
    return web.json_response({
        "add_reactions": PERMS.add_reactions, "attach_files": PERMS.attach_files,
        "send_messages": PERMS.send_messages,
    })


async def main():
    # Enrich ONLY this isolated fixture. Real dashboard options still come from Discord.
    base.CHANNELS[:] = [Channel(c.id, c.name, c.position, c.category) for c in base.CHANNELS]
    base.FakeGuild.emojis = []
    for index, name in enumerate(("prime_heart", "prime_fire", "prime_like", "prime_party", "prime_star", "prime_spark")):
        emoji = Emoji(400000000000000001 + index, name)
        emoji.url = f"https://cdn.discordapp.com/embed/avatars/{index}.png"
        base.FakeGuild.emojis.append(emoji)
    await base.database.init_db()
    bot = Bot()
    bot.user = SimpleNamespace(id=500000000000000001)
    bot.announcements = AnnouncementReactions(bot)
    base.ws.bot_ref = bot
    await bot.announcements.cog_load()
    app = web.Application(middlewares=[base.ws.private_responses], client_max_size=base.ws.MAX_BODY)
    app.add_routes(base.ws.routes)
    app.router.add_get("/__test_login", base.test_login)
    app.router.add_get("/__test_revoke", base.test_revoke)
    app.router.add_post("/__test_announcement_message", inject_message)
    app.router.add_get("/__test_announcement_permissions", toggle_permissions)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("HARNESS_PORT", "9000"))).start()
    print("announcement harness ready", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await bot.announcements.cog_unload()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

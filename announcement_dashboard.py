"""Authenticated dashboard endpoints for announcement reactions."""
import logging

from aiohttp import web

import announcement_reactions as store


logger = logging.getLogger("AnnouncementDashboard")


def web_context():
    # Defer the import: web_server registers these routes while it initializes.
    import web_server
    return web_server


async def snapshot(guild, bot):
    config = await store.get_settings(guild.id)
    cog = bot.get_cog("AnnouncementReactions") if bot else None
    ready = bool(cog and cog.workers and all(not task.done() for task in cog.workers))
    return {
        "config": config,
        "channels": [
            {"id": str(channel.id), "name": channel.name}
            for channel in guild.channels if store.text_channel(channel)
        ],
        "emojis": [
            {"id": str(emoji.id), "name": emoji.name, "url": str(emoji.url),
             "animated": bool(emoji.animated), "available": bool(emoji.available),
             "usable": store.emoji_usable(emoji)}
            for emoji in guild.emojis
        ],
        "status": store.inspect_configuration(guild, config, ready),
        "runtime": cog.runtime_status(guild.id) if cog else {"queue_size": 0, "dropped": 0},
    }


async def api_get(req):
    ws = web_context()
    _, guild = await ws.authorize(req, management_tier="admin")
    try:
        return web.json_response(await snapshot(guild, ws.bot_ref))
    except Exception:
        logger.exception("Cannot read announcement settings guild=%s", guild.id)
        return web.json_response(
            {"error": "unavailable", "message": "تعذر تحميل إعدادات الإعلانات مؤقتًا."}, status=503,
        )


async def api_save(req):
    ws = web_context()
    _, guild = await ws.authorize(req, write=True, management_tier="admin")
    body = await ws.read_json_body(req)
    try:
        old = await store.get_settings(guild.id)
        changes, revision = store.validate_changes(guild, body, old)
        config = await store.save_settings(guild.id, changes, revision)
        cog = ws.bot_ref.get_cog("AnnouncementReactions") if ws.bot_ref else None
        if cog:
            cog.update_config(config)
        result = await snapshot(guild, ws.bot_ref)
        return web.json_response({"ok": True, **result})
    except store.SettingsConflict as error:
        return web.json_response({
            "error": "conflict", "message": "تغيرت الإعدادات في جلسة أخرى؛ مسودتك لم تُحفظ.",
            "config": error.current,
        }, status=409)
    except ValueError as error:
        return web.json_response({"error": "invalid_settings", "message": str(error)}, status=400)
    except Exception:
        logger.exception("Cannot save announcement settings guild=%s", guild.id)
        return web.json_response({
            "error": "unavailable", "message": "تعذر حفظ إعدادات الإعلانات. أعد تحميل الحالة قبل المحاولة.",
        }, status=503)


def register_routes(routes):
    routes.get("/api/guild/{guild_id}/announcement-reactions")(api_get)
    routes.post("/api/guild/{guild_id}/announcement-reactions")(api_save)

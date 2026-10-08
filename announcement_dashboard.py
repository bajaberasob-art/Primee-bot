"""Authenticated dashboard endpoints for announcement reactions."""
import logging

from aiohttp import web

import announcement_reactions as store
import announcement_images as images


logger = logging.getLogger("AnnouncementDashboard")


def web_context():
    # Defer the import: web_server registers these routes while it initializes.
    import web_server
    return web_server


async def snapshot(guild, bot):
    config = await store.get_settings(guild.id)
    cog = bot.get_cog("AnnouncementReactions") if bot else None
    ready = bool(cog and cog.workers and all(not task.done() for task in cog.workers))
    image = await images.get_image(guild.id, config.get("line_image_id"))
    reaction_statuses = [
        {"channel_id": key, **store.inspect_configuration(guild, config, ready, key)}
        for key in store.reaction_channels(config)
    ]
    status = next((item for item in reaction_statuses if item["code"] not in ("ready", "disabled")), None)
    status = dict(status or store.inspect_configuration(guild, config, ready))
    status["channels"] = reaction_statuses
    line_statuses = [
        {"channel_id": key, **store.inspect_line(guild, config, ready, bool(image), key)}
        for key in config.get("line_channel_ids", [])
    ]
    line_status = next((item for item in line_statuses if item["code"] not in ("ready", "disabled")), None)
    line_status = dict(line_status or store.inspect_line(guild, config, ready, bool(image)))
    line_status["channels"] = line_statuses
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
        "status": status, "line_status": line_status, "line_image": image,
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
        if changes["line_image_id"] and not await images.get_image(guild.id, changes["line_image_id"]):
            # A deleted/expired draft can be retained only while safely switching off.
            if changes["line_enabled"] or changes["line_image_id"] != old.get("line_image_id"):
                raise ValueError("الصورة غير موجودة في هذا السيرفر؛ ارفعها مجددًا.")
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
    routes.post("/api/guild/{guild_id}/announcement-reactions/image")(api_upload_image)
    routes.get("/api/guild/{guild_id}/announcement-reactions/image/{image_id}")(api_get_image)


async def api_upload_image(req):
    ws = web_context()
    _, guild = await ws.authorize(req, write=True, management_tier="admin")
    if req.content_type not in ("image/png", "image/jpeg", "image/gif", "application/octet-stream"):
        return web.json_response({"message": "الصيغ المقبولة PNG أو JPG أو GIF فقط."}, status=415)
    if req.content_length and req.content_length > images.MAX_IMAGE_BYTES:
        return web.json_response({"message": "حجم الصورة يتجاوز 2 ميجابايت."}, status=413)
    payload = bytearray()
    async for chunk in req.content.iter_chunked(65536):
        payload.extend(chunk)
        if len(payload) > images.MAX_IMAGE_BYTES:
            return web.json_response({"message": "حجم الصورة يتجاوز 2 ميجابايت."}, status=413)
    try:
        image = await images.upload(guild.id, bytes(payload))
        return web.json_response({"image": image})
    except ValueError as error:
        return web.json_response({"message": str(error)}, status=400)
    except Exception:
        logger.exception("Cannot upload separator guild=%s", guild.id)
        return web.json_response({"message": "تعذر حفظ الصورة مؤقتًا؛ حاول مجددًا."}, status=503)


async def api_get_image(req):
    ws = web_context()
    _, guild = await ws.authorize(req, management_tier="admin")
    image = await images.get_image(guild.id, req.match_info["image_id"], include_payload=True)
    if image is None:
        raise web.HTTPNotFound()
    return web.Response(body=image["payload"], content_type=image["mime"],
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})

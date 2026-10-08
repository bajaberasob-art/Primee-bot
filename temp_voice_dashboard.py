"""Authenticated REST API for live temporary-voice administration."""
import hashlib
import logging
import re
import warnings

from aiohttp import web
from PIL import Image, UnidentifiedImageError

import temp_voice_store as store

log = logging.getLogger("TempVoiceDashboard")
MAX_BANNER = 2 * 1024 * 1024


def context():
    import web_server
    return web_server


async def get_banner(guild_id, image_id, payload=False):
    if not isinstance(image_id, str) or not re.fullmatch("[a-f0-9]{64}", image_id):
        return None
    import aiosqlite
    import database
    async with database.connect(aiosqlite.Row) as db:
        columns = "image_id,mime,length(payload) size"
        if payload:
            columns += ",payload"
        row = await (await db.execute(
            f"SELECT {columns} FROM temp_voice_images WHERE guild_id=? AND image_id=?",
            (guild_id, image_id),
        )).fetchone()
    if not row:
        return None
    result = {"id": row["image_id"], "mime": row["mime"], "size": row["size"],
              "url": f"api/temp-voice/{guild_id}/banner/{row['image_id']}"}
    if payload:
        result["payload"] = row["payload"]
    return result


async def upload_banner(guild_id, payload, content_type):
    if content_type not in {"image/png", "image/jpeg", "image/gif"}:
        raise ValueError("صيغ البانر المقبولة: PNG أو JPG أو GIF.")
    if not payload or len(payload) > MAX_BANNER:
        raise ValueError("يجب ألا يتجاوز حجم البانر 2 ميغابايت.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(__import__("io").BytesIO(payload)) as image:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}.get(image.format)
                if image.size[0] > 4096 or image.size[1] > 4096 or not mime:
                    raise ValueError("أبعاد البانر 4096 × 4096 كحد أقصى.")
                image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as error:
        raise ValueError("البانر ليس صورة سليمة مدعومة.") from error
    if mime != content_type:
        raise ValueError("نوع الصورة لا يطابق محتواها.")
    image_id = hashlib.sha256(payload).hexdigest()
    import database
    async with database.connect() as db:
        await db.execute(
            "INSERT INTO temp_voice_images VALUES (?,?,?,?,?) ON CONFLICT(guild_id,image_id) DO NOTHING",
            (guild_id, image_id, mime, payload, __import__("time").time()),
        )
        await db.execute(
            "DELETE FROM temp_voice_images WHERE guild_id=? AND image_id NOT IN "
            "(SELECT image_id FROM temp_voice_images WHERE guild_id=? ORDER BY created_at DESC LIMIT 4) "
            "AND image_id!=COALESCE((SELECT json_extract(config,'$.banner_image_id') "
            "FROM temp_voice_settings WHERE guild_id=?),'')",
            (guild_id, guild_id, guild_id),
        )
        await db.commit()
    return await get_banner(guild_id, image_id)


def describe_channel(ch):
    return {"id": str(ch.id), "name": ch.name,
            "category_id": str(ch.category_id) if getattr(ch, "category_id", None) else None,
            "category_name": getattr(getattr(ch, "category", None), "name", "")}


async def snapshot(guild, ws):
    import temp_voice_store
    snap = await store.get_config(guild.id)
    config = snap["config"]
    metrics, top = await store.metrics(guild.id)
    rooms = await store.rooms(guild.id)
    cog = ws.bot_ref.get_cog("TempVoice") if ws.bot_ref else None
    live = []
    for room in rooms:
        ch = guild.get_channel(room["channel_id"])
        if ch is None or not isinstance(ch, __import__("discord").VoiceChannel):
            continue
        owner = guild.get_member(room["owner_id"])
        live.append({
            "channel_id": str(ch.id), "name": ch.name, "owner_id": str(room["owner_id"]),
            "owner_name": owner.display_name if owner else "عضو غادر السيرفر",
            "members": sum(not m.bot for m in ch.members),
            "pinned": bool(room["state"].get("pinned")), "created_at": room["created_at"],
        })
    names = {}
    for item in top:
        member = guild.get_member(item["user_id"])
        user = member or (ws.bot_ref.get_user(item["user_id"]) if ws.bot_ref else None)
        names[str(item["user_id"])] = {
            "user_id": str(item["user_id"]),
            "name": getattr(user, "display_name", getattr(user, "name", "عضو سابق")),
            "avatar_url": str(user.display_avatar.url) if user else "",
            "rooms_created": item["rooms_created"], "minutes": int(item["voice_seconds"] // 60),
        }
    if config["banner_image_id"]:
        banner = await get_banner(guild.id, config["banner_image_id"])
    else:
        banner = None
    return {
        "config": config, "revision": snap["revision"], "stats": {
            **metrics, "active_rooms": len(live),
            "saved_profiles": metrics["saved_profiles"],
        },
        "leaderboard": [names[str(x["user_id"])] for x in top],
        "rooms": live, "channels": {
            "categories": [describe_channel(x) for x in guild.categories],
            "text": [describe_channel(x) for x in guild.text_channels],
            "voice": [describe_channel(x) for x in guild.voice_channels],
        },
        "roles": [{"id": str(r.id), "name": r.name, "managed": r.managed}
                  for r in guild.roles if r != guild.default_role],
        "buttons": [{"id": key, "label": value, "gear": key in {"report", "emergency", "activity", "slowmode"}}
                    for key, value in store.BUTTONS.items()],
        "limits": {"bitrate_max": guild.bitrate_limit // 1000},
        "status": {
            "ready": bool(cog and cog.ready),
            "message": getattr(cog, "last_error", {}).get(guild.id, ""),
        },
        "banner": banner,
    }


async def api_get(req):
    ws = context()
    _, guild = await ws.authorize(req, management_tier="admin")
    return web.json_response(await snapshot(guild, ws))


async def api_patch(req):
    ws = context()
    _, guild = await ws.authorize(req, write=True, management_tier="admin")
    body = await ws.read_json_body(req)
    try:
        old = (await store.get_config(guild.id))["config"]
        new, revision = store.validate_patch(guild, body, old)
        if new["banner_image_id"] and not await get_banner(guild.id, new["banner_image_id"]):
            if new["banner_image_id"] != old["banner_image_id"] or new["enabled"]:
                raise ValueError("صورة البانر غير موجودة؛ ارفعها مجددًا.")
        result = await store.save_config(guild.id, new, revision)
        cog = ws.bot_ref.get_cog("TempVoice") if ws.bot_ref else None
        if cog:
            cog.update_config(guild.id, new)
            # Reconcile delegated visibility/control on each known room. Never
            # modify unrelated rooms or override Discord's native permission checks.
            for room in list(cog.rooms.values()):
                if room["guild_id"] == guild.id:
                    channel = guild.get_channel(room["channel_id"])
                    if channel:
                        try:
                            await cog.reconcile_room_permissions(channel, room, new)
                            if room["state"].get("panel_message_id"):
                                message = await channel.fetch_message(int(room["state"]["panel_message_id"]))
                                await message.edit(embed=cog.embed(new, room["state"]),
                                                   view=__import__("temp_voice_panel").RoomPanel(cog, new))
                        except Exception:
                            log.warning("Could not refresh room settings guild=%s", guild.id, exc_info=True)
            if new["panel_message_id"]:
                try:
                    await cog.publish_panel(guild, new)
                    # Panel message IDs are configuration state; never apply an
                    # implicit revision bump for this internally managed field.
                    await store.save_config(guild.id, new, result["revision"])
                except Exception:
                    log.warning("Settings saved, but the Discord setup panel could not be refreshed guild=%s",
                                guild.id, exc_info=True)
        return web.json_response(await snapshot(guild, ws))
    except store.Conflict as error:
        return web.json_response({"error": "conflict", "config": error.current["config"],
                                  "revision": error.current["revision"]}, status=409)
    except ValueError as error:
        return web.json_response({"error": "invalid_settings", "message": str(error)}, status=400)


async def api_setup(req):
    ws = context()
    _, guild = await ws.authorize(req, write=True, management_tier="admin")
    body = await ws.read_json_body(req)
    if not isinstance(body, dict) or set(body) - {"revision", "republish"}:
        return web.json_response({"message": "طلب تجهيز غير صحيح."}, status=400)
    cog = ws.bot_ref.get_cog("TempVoice") if ws.bot_ref else None
    if not cog:
        return web.json_response({"message": "محرك الرومات المؤقتة غير جاهز."}, status=503)
    try:
        result = await cog.setup_system(guild, body.get("revision"), bool(body.get("republish", False)))
        return web.json_response(await snapshot(guild, ws))
    except store.Conflict as error:
        return web.json_response({"error": "conflict", "config": error.current["config"],
                                  "revision": error.current["revision"]}, status=409)
    except (ValueError, __import__("discord").HTTPException) as error:
        return web.json_response({"message": str(error)[:500]}, status=400)


async def api_delete_room(req):
    ws = context()
    _, guild = await ws.authorize(req, write=True, management_tier="admin")
    try:
        int(req.match_info["channel_id"])
    except ValueError:
        raise web.HTTPNotFound()
    cog = ws.bot_ref.get_cog("TempVoice") if ws.bot_ref else None
    if not cog:
        return web.json_response({"message": "محرك الرومات غير جاهز."}, status=503)
    try:
        await cog.delete_room(guild, int(req.match_info["channel_id"]))
    except ValueError as error:
        return web.json_response({"message": str(error)}, status=404)
    except __import__("discord").HTTPException as error:
        return web.json_response({"message": "تعذر حذف الروم؛ تحقق من صلاحيات البوت."}, status=503)
    return web.json_response(await snapshot(guild, ws))


async def api_upload_banner(req):
    ws = context()
    _, guild = await ws.authorize(req, write=True, management_tier="admin")
    if req.content_length and req.content_length > MAX_BANNER:
        return web.json_response({"message": "يجب ألا يتجاوز حجم البانر 2 ميغابايت."}, status=413)
    payload = await req.content.read(MAX_BANNER + 1)
    if len(payload) > MAX_BANNER:
        return web.json_response({"message": "يجب ألا يتجاوز حجم البانر 2 ميغابايت."}, status=413)
    try:
        return web.json_response({"image": await upload_banner(guild.id, payload, req.content_type)})
    except ValueError as error:
        return web.json_response({"message": str(error)}, status=400)


async def api_get_banner(req):
    ws = context()
    _, guild = await ws.authorize(req, management_tier="admin")
    item = await get_banner(guild.id, req.match_info["image_id"], payload=True)
    if not item:
        raise web.HTTPNotFound()
    return web.Response(body=item["payload"], content_type=item["mime"],
                        headers={"Cache-Control": "private,no-store", "X-Content-Type-Options": "nosniff"})


def register_routes(routes):
    routes.get("/api/temp-voice/{guild_id}")(api_get)
    routes.patch("/api/temp-voice/{guild_id}")(api_patch)
    routes.post("/api/temp-voice/{guild_id}/setup")(api_setup)
    routes.post("/api/temp-voice/{guild_id}/rooms/{channel_id}/delete")(api_delete_room)
    routes.post("/api/temp-voice/{guild_id}/banner")(api_upload_banner)
    routes.get("/api/temp-voice/{guild_id}/banner/{image_id}")(api_get_banner)

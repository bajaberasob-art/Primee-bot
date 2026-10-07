"""Read-only public PRIME leveling leaderboard routes."""
import asyncio
import re
import time
from datetime import datetime, timezone

from aiohttp import web

import database
from level_progression import text_progress


PUBLIC_SLUG_RE = re.compile(r"^(?=.{3,40}$)[a-z0-9]+(?:-[a-z0-9]+)*$")
DISCORD_ID_RE = re.compile(r"^\d{15,22}$")
PAGE_SIZE = 10
MAX_PAGE = 100_001
CACHE_TTL_SECONDS = 30
CACHE_MAX_ENTRIES = 512

_page_cache = {}
_page_cache_lock = asyncio.Lock()


def _not_found_json():
    return web.json_response(
        {"error": "not_found"},
        status=404,
        headers={"Cache-Control": "no-store"},
    )


def _unavailable_json():
    return web.json_response(
        {"error": "unavailable"},
        status=503,
        headers={"Cache-Control": "no-store"},
    )


def _serialize_ranked_member(row, member):
    if member is None:
        return {
            "rank": int(row["rank"]),
            "removed": True,
            "name": None,
            "avatarUrl": None,
            "level": None,
            "xp": None,
            "progress": None,
            "activityTotal": None,
            "streak": None,
        }

    xp = max(0, int(row.get("xp") or 0))
    progress = text_progress(xp)
    avatar = getattr(member, "display_avatar", None)
    return {
        "rank": int(row["rank"]),
        "removed": False,
        "name": (
            getattr(member, "display_name", None)
            or getattr(member, "name", None)
            or "عضو"
        ),
        "avatarUrl": str(avatar.url) if avatar and getattr(avatar, "url", None) else None,
        "level": int(progress["level"]),
        "xp": xp,
        "progress": {
            "current": int(progress["progress_xp"]),
            "required": int(progress["xp_required"]),
            "percentage": round(float(progress["percentage"]), 2),
        },
        "activityTotal": int(row.get("activity_total") or 0),
        "streak": int(row.get("current_streak") or 0),
    }


async def _build_public_page(guild, mode, page):
    offset = (page - 1) * PAGE_SIZE
    leaderboard, summary = await asyncio.gather(
        database.get_level_leaderboard_page(
            guild.id, mode=mode, limit=PAGE_SIZE, offset=offset
        ),
        database.get_public_level_summary(guild.id),
    )
    icon = getattr(guild, "icon", None)
    icon_url = str(icon.url) if icon and getattr(icon, "url", None) else None
    return {
        "slug": None,
        "mode": mode,
        "page": page,
        "pageSize": PAGE_SIZE,
        "total": int(leaderboard["total"]),
        "totalPages": min(
            MAX_PAGE,
            max(1, (int(leaderboard["total"]) + PAGE_SIZE - 1) // PAGE_SIZE),
        ),
        "server": {
            "name": str(getattr(guild, "name", "PRIME")),
            "iconUrl": icon_url,
            "memberCount": (
                int(guild.member_count)
                if getattr(guild, "member_count", None) is not None
                else None
            ),
        },
        "summary": {
            "activeMembers": int(summary["active_members"]),
            "totalXp": int(summary["total_xp"]),
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        },
        # Keep raw IDs only in this short-lived server-side cache. They are
        # resolved against the live guild cache for every public response.
        "rows": leaderboard["rows"],
    }


async def _cached_public_page(guild, mode, page):
    key = (int(guild.id), mode, page)
    now = time.monotonic()
    cached = _page_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    async with _page_cache_lock:
        now = time.monotonic()
        cached = _page_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        payload = await _build_public_page(guild, mode, page)
        if len(_page_cache) >= CACHE_MAX_ENTRIES:
            expired = [item for item, (expiry, _) in _page_cache.items() if expiry <= now]
            for item in expired:
                _page_cache.pop(item, None)
            while len(_page_cache) >= CACHE_MAX_ENTRIES:
                oldest = min(_page_cache, key=lambda item: _page_cache[item][0])
                _page_cache.pop(oldest, None)
        payload["slug"] = None
        _page_cache[key] = (now + CACHE_TTL_SECONDS, payload)
        return payload


def register_public_leaderboard_routes(routes, *, bot_getter, logger):
    async def resolve_slug(slug):
        if not PUBLIC_SLUG_RE.fullmatch(slug or ""):
            return None
        return await database.get_public_level_settings_by_slug(slug)

    @routes.get("/lb/{slug}")
    async def public_leaderboard_page(req):
        slug = req.match_info.get("slug", "")
        try:
            resolved = await resolve_slug(slug)
        except Exception:
            logger.exception("Public leaderboard slug lookup failed")
            return web.Response(
                text="اللوحة غير متاحة حالياً.",
                status=503,
                content_type="text/plain",
                headers={"Cache-Control": "no-store"},
            )
        if resolved is None:
            return web.Response(
                text="الرابط غير متاح.",
                status=404,
                content_type="text/plain",
                headers={"Cache-Control": "no-store"},
            )
        html = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>PRIME | لوحة المتصدرين</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="../dashboard/static/app.css">
</head>
<body>
  <main id="app" class="app" aria-live="polite"></main>
  <script src="../dashboard/static/app.js" defer></script>
</body>
</html>"""
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @routes.get("/lb/{slug}/data")
    async def public_leaderboard_data(req):
        slug = req.match_info.get("slug", "")
        mode = req.query.get("mode", "text")
        raw_page = req.query.get("page", "1")
        raw_user_id = req.query.get("user_id", "")
        if mode not in {"text", "voice"}:
            return web.json_response(
                {"error": "invalid_request"},
                status=400,
                headers={"Cache-Control": "no-store"},
            )
        if (
            not raw_page.isdecimal()
            or len(raw_page) > 6
            or not 1 <= int(raw_page) <= MAX_PAGE
        ):
            return web.json_response(
                {"error": "invalid_request"},
                status=400,
                headers={"Cache-Control": "no-store"},
            )
        user_id = None
        if raw_user_id:
            if not DISCORD_ID_RE.fullmatch(raw_user_id):
                return web.json_response(
                    {"error": "invalid_request"},
                    status=400,
                    headers={"Cache-Control": "no-store"},
                )
            user_id = int(raw_user_id)

        try:
            resolved = await resolve_slug(slug)
            if resolved is None:
                return _not_found_json()
            bot = bot_getter(req)
            guild = bot.get_guild(int(resolved["guild_id"])) if bot else None
            if guild is None:
                return _not_found_json()

            page = int(raw_page)
            payload = dict(await _cached_public_page(guild, mode, page))
            payload["slug"] = slug
            payload["rows"] = [
                _serialize_ranked_member(
                    row,
                    guild.get_member(int(row["user_id"])),
                )
                for row in payload["rows"]
            ]
            payload["viewerRank"] = None
            if user_id is not None:
                member = guild.get_member(user_id)
                if member is not None:
                    rank_row = await database.get_level_user_rank(
                        guild.id, user_id, mode=mode
                    )
                    if rank_row is not None:
                        payload["viewerRank"] = _serialize_ranked_member(
                            rank_row, member
                        )
            return web.json_response(
                payload,
                headers={"Cache-Control": "public, max-age=15"},
            )
        except Exception:
            logger.exception("Public leaderboard data read failed")
            return _unavailable_json()

    @routes.get("/lb/{slug}/")
    async def public_leaderboard_trailing_slash(req):
        slug = req.match_info.get("slug", "")
        if not PUBLIC_SLUG_RE.fullmatch(slug or ""):
            return web.Response(
                text="الرابط غير متاح.",
                status=404,
                content_type="text/plain",
                headers={"Cache-Control": "no-store"},
            )
        raise web.HTTPPermanentRedirect(location=req.path.rstrip("/"))
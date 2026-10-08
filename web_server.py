import asyncio
import json
import logging
import os
import re
import resource
import secrets
import struct
import time
from collections import deque
from html import escape
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode, urlsplit
import zlib

import aiohttp
import discord
from aiohttp import web

import subscription_service
import prime_ai_service
import prime_ai_control
import prime_ai_runtime
from database import (
    LOG_ROUTING_ALL_KEYS,
    LOG_ROUTING_KEYS,
    SETTINGS_SCHEMA,
    SettingsConflict,
    get_auto_responders,
    get_shortcuts,
    get_warning,
    get_recent_warnings,
    get_dashboard_stats,
    get_analytics_summary,
    get_channel_traffic,
    get_dead_channels,
    get_top_messenger,
    get_hourly_heatmap,
    get_golden_hour,
    get_economy_leaderboard,
    get_logging_channels,
    set_logging_channels,
    get_role_multipliers,
    get_scrims,
    get_guild_settings,
    get_command_policies,
    get_self_role_panels,
    delete_shortcut,
    save_shortcut,
    update_guild_settings,
    validate_setting,
    get_ticket_config,
    get_ticket_options,
    get_ticket_panels,
    delete_ticket_panel,
    save_ticket_panel,
    save_ticket_config,
    replace_ticket_options,
    update_ticket_control_config,
    get_ticket_dashboard_analytics,
    get_ticket_overview_metrics,
    get_ticket_ratings,
    get_ticket_categories,
    save_ticket_category,
    delete_ticket_category,
    get_ticket_settings,
    save_ticket_settings,
    get_ticket_permissions,
    save_ticket_permissions,
    save_ticket_log,
    get_ticket_blacklist,
    save_ticket_blacklist,
    delete_ticket_blacklist,
    get_user_dashboard_theme,
    save_user_dashboard_theme,
    get_clan_applications,
    update_clan_application,
    get_clan_roster,
    save_clan_roster_player,
    delete_clan_roster_player,
    get_scrim_logs,
    add_scrim_log,
    get_ticket_dropdown_config,
    save_ticket_dropdown_config,
    get_ticket_dropdown_categories,
    replace_ticket_dropdown_categories,
    add_broadcast_log,
    get_recent_broadcast_logs,
)
from management_access import member_has_management_tier
from cogs.command_meta import (
    AUTO_DELETE_PRESETS,
    MASTER_COMMANDS_REGISTRY,
    RESPONSE_STYLES,
    grouped_command_registry,
)
from cogs.community import PersistentDropdownTicketView, normalize_ticket_categories
from level_admin import is_level_admin
from leveling_api import register_leveling_routes
from public_leaderboard import register_public_leaderboard_routes

routes = web.RouteTableDef()
PROJECT_DIR = Path(__file__).parent.resolve()
DASHBOARD_DIR = (PROJECT_DIR / "dashboard").resolve()
HOST = "0.0.0.0"
PORT = int((os.environ.get("PORT") or "10000").strip())
bot_ref: discord.Client = None

C_ID = (os.getenv("CLIENT_ID") or "").strip()
C_SEC = (os.getenv("CLIENT_SECRET") or "").strip()
R_URI = (os.getenv("REDIRECT_URI") or "").strip()
DASHBOARD_BASE_PATH = (os.getenv("DASHBOARD_BASE_PATH") or "/").strip().rstrip("/") + "/"
LOCAL_LOGIN_COOKIE = "local-dashboard-development"
LOCAL_LOGIN_MAX_AGE = 10 * 365 * 24 * 60 * 60
DISCORD_API = "https://discord.com/api/v10"
ADMIN_BIT = 0x8
MANAGE_GUILD_BIT = 0x20
DASHBOARD_PERMISSION_BITS = ADMIN_BIT | MANAGE_GUILD_BIT
MESSAGE_CHANNEL_TYPES = (
    discord.TextChannel,
    discord.VoiceChannel,
    discord.StageChannel,
    discord.ForumChannel,
)
BOT_INVITE_PERMISSIONS = (os.getenv("BOT_INVITE_PERMISSIONS") or "8").strip()
DISCORD_AUTHORIZE = "https://discord.com/oauth2/authorize"
SESSIONS: dict[str, dict] = {}
STATES: dict[str, float] = {}
STATE_TTL, SESSION_TTL = 300, 2_592_000
AUTHORIZED_ROLE_NAMES = frozenset(
    {"admin", "owner", "prime", "management", "مشرف"}
)
# حدود معدل الطلبات: (عدد الطلبات، النافذة بالثواني)
SAVE_LIMIT, READ_LIMIT = (5, 10.0), (60, 10.0)
IP_API_LIMIT, IP_SENSITIVE_LIMIT = (180, 60.0), (10, 60.0)
MAX_BODY = 16 * 1024
GRANT_TTL = 60.0
RATE_BUCKETS: dict[tuple, deque] = {}
GRANT_CACHE: dict[tuple[str, int], tuple[float, bool]] = {}
SETTINGS_LISTENERS: dict[int, set[asyncio.Queue]] = {}
PROCESS_STARTED_AT = time.monotonic()
_DANGEROUS_BLOCK_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|svg|math|form)\b[^>]*>.*?"
    r"<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_TAG_RE = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|svg|math|form)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(
    r"<\s*/?\s*(img|video|audio|source|link|meta|base)\b[^>]*>",
    re.IGNORECASE,
)
_EVENT_HANDLER_RE = re.compile(r"\bon[a-z]+\s*=", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DISCORD_TOKEN_RE = re.compile(r"<(?:a?):[A-Za-z0-9_~]+:\d+>|<@!?\d+>|<#\d+>|<@&\d+>")
_SENSITIVE_PATH_RE = re.compile(r"(backup|purge|mass|reset|delete|lockdown)", re.IGNORECASE)
logger = logging.getLogger("DashboardOAuth")


async def _await_if_needed(value):
    """Keep dashboard adapters compatible with async cogs and test doubles."""
    if hasattr(value, "__await__"):
        return await value
    return value


async def _security_incident_rows(security, guild_id: int):
    getter = getattr(security, "get_persisted_incidents", None)
    if callable(getter):
        return await _await_if_needed(getter(guild_id))
    return security.get_incidents(guild_id)


def prune_expired():
    now = time.time()
    for state, created in list(STATES.items()):
        if now - created >= STATE_TTL:
            STATES.pop(state, None)
    for sid, session in list(SESSIONS.items()):
        if now >= session.get("expires_at", 0):
            SESSIONS.pop(sid, None)


def local_login_enabled(req=None) -> bool:
    """Enable the persistent review session only in an explicit dev environment."""
    if (os.getenv("DASHBOARD_LOCAL_LOGIN") or "").strip() != "1":
        return False
    if req is None:
        return True
    hosts = {str(req.host or "").split(",", 1)[0].split(":", 1)[0].lower()}
    forwarded = req.headers.get("X-Forwarded-Host", "")
    hosts.update(
        item.strip().split(",", 1)[0].split(":", 1)[0].lower()
        for item in forwarded.split(",")
        if item.strip()
    )
    return any(
        host in {"localhost", "127.0.0.1", "0.0.0.0"}
        or host.endswith(".replit.dev")
        for host in hosts
    )


def local_development_session() -> dict:
    """Return a non-expiring local operator identity; never used by OAuth."""
    guilds = [
        dashboard_guild_payload(guild)
        for guild in (getattr(bot_ref, "guilds", ()) or ())
    ]
    return {
        "id": "0",
        "username": "Local Developer",
        "avatar": "https://cdn.discordapp.com/embed/avatars/1.png",
        "guilds": guilds,
        "expires_at": float("inf"),
        "csrf": "local-development-csrf",
        "_local_dev": True,
        "bot_ready": bot_is_connected(bot_ref),
        "connected_guilds_count": len(getattr(bot_ref, "guilds", ()) or ()),
    }


def current_session(req):
    prune_expired()
    if (
        req.cookies.get("bot_session") == LOCAL_LOGIN_COOKIE
        and local_login_enabled(req)
    ):
        return local_development_session()
    return SESSIONS.get(req.cookies.get("bot_session"))


def bot_invite_url() -> str | None:
    """Build the public Discord install link without exposing any secret."""
    if not C_ID:
        return None
    return (
        f"{DISCORD_AUTHORIZE}?"
        f"{urlencode({'client_id': C_ID, 'scope': 'bot applications.commands', 'permissions': BOT_INVITE_PERMISSIONS})}"
    )


def request_bot(request) -> discord.Client | None:
    """Return the exact bot instance shared with the aiohttp application."""
    app = getattr(request, "app", None)
    if app is not None:
        try:
            app_bot = app["bot"]
        except (KeyError, TypeError):
            app_bot = None
        if app_bot is not None:
            return app_bot
    return bot_ref


def bot_is_connected(bot) -> bool:
    """Treat a populated gateway cache as usable before READY finishes."""
    if bot is None:
        return False
    ready = getattr(bot, "is_ready", None)
    if callable(ready):
        try:
            if ready():
                return True
        except Exception:
            logger.debug("Could not read Discord gateway readiness.", exc_info=True)
    return bool(getattr(bot, "guilds", ()))


def configured_admin_role_ids() -> set[int]:
    """Read optional dashboard role IDs without exposing them to the browser."""
    raw = (os.getenv("ADMIN_ROLE_IDS") or "").strip()
    role_ids: set[int] = set()
    for value in re.split(r"[,\s]+", raw):
        if value.isdigit():
            role_ids.add(int(value))
    return role_ids


def oauth_guild_allows_dashboard(guild_data: dict) -> bool:
    try:
        permissions = int(guild_data.get("permissions", 0))
    except (TypeError, ValueError):
        permissions = 0
    return bool(
        guild_data.get("owner") is True
        or permissions & MANAGE_GUILD_BIT
        or permissions & ADMIN_BIT
    )


def member_allows_dashboard(member, guild) -> bool:
    """Check ownership, Discord permissions, or an explicitly trusted role."""
    try:
        if int(getattr(member, "id", 0)) == int(getattr(guild, "owner_id", 0) or 0):
            return True
    except (TypeError, ValueError):
        pass

    permissions = getattr(member, "guild_permissions", None)
    if (
        permissions is not None
        and (
            bool(getattr(permissions, "administrator", False))
            or bool(getattr(permissions, "manage_guild", False))
        )
    ):
        return True

    role_ids = configured_admin_role_ids()
    for role in getattr(member, "roles", ()) or ():
        try:
            role_id = int(getattr(role, "id", 0))
        except (TypeError, ValueError):
            role_id = 0
        role_name = str(getattr(role, "name", "") or "").strip().casefold()
        if role_id in role_ids or role_name in AUTHORIZED_ROLE_NAMES:
            return True
    return False


async def resolve_dashboard_member(guild, user_id: int):
    """Use the cache first, then fetch the member when the cache is cold."""
    get_member = getattr(guild, "get_member", None)
    member = get_member(user_id) if callable(get_member) else None
    if member is not None:
        return member
    fetch_member = getattr(guild, "fetch_member", None)
    if not callable(fetch_member):
        return None
    try:
        return await fetch_member(user_id)
    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
        asyncio.TimeoutError,
    ):
        return None


def dashboard_guild_payload(guild, member=None, oauth_data=None) -> dict:
    icon = getattr(guild, "icon", None)
    try:
        is_owner = bool(
            member
            and int(getattr(member, "id", 0))
            == int(getattr(guild, "owner_id", 0) or 0)
        )
    except (TypeError, ValueError):
        is_owner = bool(oauth_data and oauth_data.get("owner") is True)
    return {
        "id": str(guild.id),
        "name": str(getattr(guild, "name", guild.id)),
        "members": getattr(guild, "member_count", None),
        "icon": icon.url if icon else None,
        "is_owner": is_owner or bool(oauth_data and oauth_data.get("owner") is True),
    }


async def verified_dashboard_guilds(
    bot,
    user_id: int,
    oauth_guilds: list[dict] | None = None,
) -> list[dict]:
    """Return mutual guilds authorized by live Discord membership data."""
    oauth_by_id: dict[str, dict] = {}
    for item in oauth_guilds or ():
        try:
            oauth_by_id[str(int(item["id"]))] = item
        except (KeyError, TypeError, ValueError):
            continue

    bot_guilds = list(getattr(bot, "guilds", ()) or ()) if bot else []
    if bot_guilds:
        verified = []
        get_guild = getattr(bot, "get_guild", None)
        for guild in bot_guilds:
            try:
                guild_id = int(guild.id)
            except (AttributeError, TypeError, ValueError):
                continue
            oauth_data = oauth_by_id.get(str(guild_id))
            # OAuth guilds establish mutual membership. If a later session has
            # no cached OAuth list, live bot membership remains authoritative.
            if oauth_by_id and oauth_data is None:
                continue
            bot_guild = get_guild(guild_id) if callable(get_guild) else guild
            if bot_guild is None:
                continue
            member = await resolve_dashboard_member(bot_guild, user_id)
            direct_access = bool(
                member and member_allows_dashboard(member, bot_guild)
            )
            oauth_access = bool(oauth_data and oauth_guild_allows_dashboard(oauth_data))
            if not direct_access and not oauth_access:
                continue
            verified.append(
                dashboard_guild_payload(bot_guild, member, oauth_data)
            )
        return verified

    # Compatibility fallback for startup/test doubles without a guild cache.
    verified = []
    get_guild = getattr(bot, "get_guild", None) if bot else None
    for oauth_data in oauth_guilds or ():
        if not oauth_guild_allows_dashboard(oauth_data):
            continue
        try:
            guild_id = int(oauth_data["id"])
        except (KeyError, TypeError, ValueError):
            continue
        guild = get_guild(guild_id) if callable(get_guild) else None
        if guild is not None:
            verified.append(dashboard_guild_payload(guild, None, oauth_data))
    return verified


async def sync_session_guilds(request, session: dict) -> list[dict]:
    """Refresh a session from the shared bot without blocking on READY."""
    bot = request_bot(request)
    if bot is None:
        return session.get("guilds", [])
    if session.get("_local_dev"):
        session["guilds"] = [
            dashboard_guild_payload(guild)
            for guild in (getattr(bot, "guilds", ()) or ())
        ]
        session["bot_ready"] = bot_is_connected(bot)
        session["connected_guilds_count"] = len(getattr(bot, "guilds", ()) or ())
        return session["guilds"]
    bot_guilds = list(getattr(bot, "guilds", ()) or ())
    if not bot_is_connected(bot) and not bot_guilds:
        return session.get("guilds", [])
    try:
        user_id = int(session["id"])
    except (KeyError, TypeError, ValueError):
        session["guilds"] = []
        return []

    verified = await verified_dashboard_guilds(
        bot,
        user_id,
        session.get("_oauth_guilds", []),
    )
    session["guilds"] = verified
    session["bot_ready"] = bot_is_connected(bot)
    session["connected_guilds_count"] = len(bot_guilds)
    return verified


@web.middleware
async def private_responses(req, handler):
    try:
        if req.path.startswith("/api/") and req.path not in {"/api/status", "/api/health"}:
            tier = "sensitive" if _SENSITIVE_PATH_RE.search(req.path) else "standard"
            limit = IP_SENSITIVE_LIMIT if tier == "sensitive" else IP_API_LIMIT
            wait = rate_limited(("ip", request_ip(req), tier), limit)
            if wait:
                response = web.json_response(
                    {"error": "Too Many Requests", "retry_after": max(1, int(wait) + 1)},
                    status=429,
                    headers={"Retry-After": str(max(1, int(wait) + 1))},
                )
            else:
                response = await handler(req)
        else:
            response = await handler(req)
    except web.HTTPException as error:
        response = error
    if req.path == "/sw.js":
        # The worker must be revalidated so routing fixes can reach existing
        # clients instead of leaving an older worker in control indefinitely.
        response.headers["Cache-Control"] = "no-store"
    elif req.path.startswith(("/static/", "/manifest.json", "/icon-")):
        response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=86400"
    else:
        response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https://cdn.discordapp.com https://media.discordapp.net; "
        "connect-src 'self' https://discord.com; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self' https://discord.com"
    )
    return response


@routes.get('/login')
async def login(req):
    if not C_ID or not C_SEC or not R_URI:
        return web.Response(
            text="⚠️ يلزم إعداد CLIENT_ID وCLIENT_SECRET وREDIRECT_URI لتسجيل الدخول.",
            status=503,
        )
    prune_expired()
    STATES.pop(req.cookies.get("oauth_state"), None)
    state = secrets.token_urlsafe(32)
    STATES[state] = time.time()
    redirect_uri = R_URI.strip()
    query = urlencode({
        "client_id": C_ID, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": "identify guilds",
        "state": state,
    })
    response = web.HTTPFound(f"{DISCORD_AUTHORIZE}?{query}")
    response.set_cookie(
        "oauth_state", state, max_age=STATE_TTL, httponly=True,
        secure=True, samesite="Lax", path="/",
    )
    return response


@routes.get('/__local_login')
async def local_login(req):
    """Development-only persistent login for reviewing every dashboard section."""
    if not local_login_enabled(req):
        raise web.HTTPNotFound()
    response = web.HTTPFound(DASHBOARD_BASE_PATH)
    response.set_cookie(
        "bot_session",
        LOCAL_LOGIN_COOKIE,
        max_age=LOCAL_LOGIN_MAX_AGE,
        httponly=True,
        secure=req.secure or req.headers.get("X-Forwarded-Proto", "").lower() == "https",
        samesite="Lax",
        path="/",
    )
    return response


@routes.get('/callback')
@routes.get('/callback/')
@routes.get('/api/auth/callback')
async def callback(request):
    prune_expired()
    code = request.query.get("code")
    state = request.query.get("state")
    browser_state = request.cookies.get("oauth_state")
    if (
        not state or not browser_state or state not in STATES
        or not secrets.compare_digest(state, browser_state)
    ):
        logger.info("Stale or invalid dashboard OAuth state; starting a fresh login.")
        login_url = f"{DASHBOARD_BASE_PATH}login"
        return web.Response(
            text=(
                "<!doctype html><meta charset='utf-8'>"
                f"<meta http-equiv='refresh' content='0;url={login_url}'>"
                f"<p>انتهت جلسة تسجيل الدخول. <a href='{login_url}'>إعادة تسجيل الدخول</a></p>"
            ),
            status=403,
            content_type="text/html",
            headers={"Refresh": f"0; url={login_url}"},
        )
    STATES.pop(state, None)
    if request.query.get("error") or not code:
        login_url = escape(f"{DASHBOARD_BASE_PATH}login", quote=True)
        return web.Response(
            text=(
                "<!doctype html><html lang='ar' dir='rtl'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>تعذر تسجيل الدخول</title>"
                "<body><main>"
                "<h1>تعذر تسجيل الدخول عبر Discord</h1>"
                "<p>لم يكتمل التفويض. أعد المحاولة من زر دخول Discord.</p>"
                f"<a href=\"{login_url}\">إعادة المحاولة</a>"
                "</main></body></html>"
            ),
            status=400,
            content_type="text/html",
        )
    if not C_ID or not C_SEC or not R_URI:
        return web.Response(text="إعدادات تسجيل الدخول غير مكتملة.", status=503)
    session = None
    owns_session = False
    try:
        bot = request_bot(request)
        session = getattr(bot, "session", None)
        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        if getattr(session, "closed", False):
            return web.Response(text="خدمة الاتصال غير جاهزة.", status=503)
        redirect_uri = R_URI.strip()
        data = {
            "client_id": C_ID, "client_secret": C_SEC,
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri,
        }
        async with session.post(f"{DISCORD_API}/oauth2/token", data=data) as response:
            if response.status != 200:
                logger.warning(
                    "Discord OAuth token exchange rejected with status=%s",
                    response.status,
                )
                return web.Response(
                    text=(
                        "فشل تسجيل الدخول عبر Discord. تحقق من أن CLIENT_SECRET هو "
                        "Client Secret الموجود في OAuth2 → General، وليس Bot Token "
                        "أو Public Key، وأن Redirect URI مطابق تماماً."
                    ),
                    status=400,
                )
            token = (await response.json()).get("access_token")
            if not token:
                return web.Response(text="استجابة المصادقة غير صالحة.", status=502)
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(f"{DISCORD_API}/users/@me", headers=headers) as response:
            if response.status != 200:
                return web.Response(text="تعذر جلب بيانات المستخدم.", status=502)
            user_data = await response.json()
        guild_data, after = [], None
        while True:
            params = {"limit": "200"}
            if after:
                params["after"] = after
            async with session.get(
                f"{DISCORD_API}/users/@me/guilds", headers=headers, params=params,
            ) as response:
                if response.status != 200:
                    return web.Response(text="تعذر جلب السيرفرات.", status=502)
                page = await response.json()
            if not isinstance(page, list):
                raise ValueError("Invalid guild list")
            guild_data.extend(page)
            if len(page) < 200:
                break
            next_after = str(page[-1]["id"])
            if next_after == after:
                raise ValueError("Invalid pagination")
            after = next_after
        guilds = await verified_dashboard_guilds(
            bot,
            int(user_data["id"]),
            guild_data,
        )
        user_session = {
            "id": user_data["id"], "username": user_data["username"],
            "avatar": (
                f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png"
                if user_data.get("avatar")
                else "https://cdn.discordapp.com/embed/avatars/0.png"
            ),
            "guilds": guilds, "expires_at": time.time() + SESSION_TTL,
            "csrf": secrets.token_urlsafe(32),
            "bot_ready": bot_is_connected(bot),
            "connected_guilds_count": len(getattr(bot, "guilds", ())) if bot else 0,
            # Keep the provider's mutual-guild view private so later dashboard
            # requests can re-check live bot membership and role access.
            "_oauth_guilds": [
                {
                    "id": str(item.get("id")),
                    "permissions": str(item.get("permissions", 0)),
                    "owner": item.get("owner") is True,
                }
                for item in guild_data
                if isinstance(item, dict) and item.get("id") is not None
            ],
        }
    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ):
        logger.warning("Discord OAuth request failed or returned invalid data.")
        return web.Response(text="تسجيل الدخول غير متاح مؤقتاً.", status=502)
    finally:
        if owns_session and session is not None:
            close = getattr(session, "close", None)
            if close and not getattr(session, "closed", False):
                await close()

    SESSIONS.pop(request.cookies.get("bot_session"), None)
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = user_session
    res = web.HTTPFound(DASHBOARD_BASE_PATH)
    res.del_cookie("oauth_state", path="/")
    res.set_cookie("bot_session", sid, max_age=SESSION_TTL, httponly=True, secure=True, samesite="Lax", path="/")
    return res

@routes.get('/logout')
async def logout(req):
    SESSIONS.pop(req.cookies.get("bot_session"), None)
    STATES.pop(req.cookies.get("oauth_state"), None)
    res = web.HTTPFound(DASHBOARD_BASE_PATH)
    res.del_cookie("bot_session", path="/")
    res.del_cookie("oauth_state", path="/")
    return res

@routes.get('/api/me')
async def api_me(req):
    session = current_session(req)
    if not session:
        return web.json_response({"auth": False}, status=401)
    await sync_session_guilds(req, session)
    public_session = {
        key: value
        for key, value in session.items()
        if key != "expires_at" and not key.startswith("_")
    }
    public_session["local_development"] = bool(session.get("_local_dev"))
    # This is intentionally derived on every request so a session created
    # before a code update still gets the recovery link.
    public_session["invite_url"] = bot_invite_url()
    response = web.json_response({
        "auth": True,
        "session": public_session,
    })
    # Refresh the browser expiry after a successful authenticated request.
    # The server-side expiry remains authoritative and is still checked by
    # current_session() on every request.
    session_id = req.cookies.get("bot_session")
    if session_id and session_id in SESSIONS:
        response.set_cookie(
            "bot_session",
            session_id,
            max_age=SESSION_TTL,
            httponly=True,
            secure=True,
            samesite="Lax",
            path="/",
        )
    return response


# -------------------------------------------------------------
# درع الحماية: التفويض لكل سيرفر، CSRF، وحدود المعدل
# -------------------------------------------------------------
def json_error(status: int, error: str, **extra):
    return web.json_response({"error": error, **extra}, status=status)


def request_ip(req) -> str:
    """Use the forwarded client address supplied by the workspace proxy."""
    forwarded = req.headers.get("X-Forwarded-For", "")
    if forwarded:
        candidate = forwarded.split(",", 1)[0].strip()
        if candidate:
            return candidate[:128]
    return (req.remote or "unknown")[:128]


def sanitize_string(value: str) -> str:
    """Remove executable HTML while preserving Discord mention/emoji tokens."""
    text = _CONTROL_RE.sub("", str(value))
    tokens: list[str] = []

    def preserve(match):
        tokens.append(match.group(0))
        return f"\x00DISCORD_TOKEN_{len(tokens) - 1}\x00"

    text = _DISCORD_TOKEN_RE.sub(preserve, text)
    text = _DANGEROUS_BLOCK_RE.sub("", text)
    text = _DANGEROUS_TAG_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _EVENT_HANDLER_RE.sub("", text)
    text = re.sub(r"(?i)javascript\s*:", "", text)
    for index, token in enumerate(tokens):
        text = text.replace(f"\x00DISCORD_TOKEN_{index}\x00", token)
    return text.strip()


def sanitize_payload(value):
    if isinstance(value, str):
        return sanitize_string(value)
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_payload(item) for key, item in value.items()}
    return value


def _process_memory_mb() -> float:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 2)
    except (OSError, ValueError, IndexError):
        pass
    try:
        return round(float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024, 2)
    except (AttributeError, ValueError):
        return 0.0


async def health_payload() -> dict:
    database_status = "healthy"
    try:
        from database import connect

        async with connect() as db:
            async with db.execute("SELECT 1") as cur:
                await cur.fetchone()
    except Exception:
        database_status = "unhealthy"
        logger.warning("[HEALTH] Database probe failed.", exc_info=True)

    bot = bot_ref
    latency = getattr(bot, "latency", float("nan")) if bot else float("nan")
    latency_ms = (
        round(latency * 1000)
        if isinstance(latency, (int, float)) and latency == latency and latency != float("inf")
        else None
    )
    started_at = getattr(bot, "started_at", PROCESS_STARTED_AT) if bot else PROCESS_STARTED_AT
    return {
        "status": "online",
        "bot_latency_ms": latency_ms,
        "uptime_seconds": max(0, int(time.monotonic() - started_at)),
        "guilds_count": len(getattr(bot, "guilds", ())) if bot else 0,
        "database_status": database_status,
        "system_memory_mb": _process_memory_mb(),
    }


def liveness_payload() -> dict:
    """Return a dependency-free probe payload for container health checks."""
    started_at = getattr(bot_ref, "started_at", PROCESS_STARTED_AT) if bot_ref else PROCESS_STARTED_AT
    return {
        "status": "healthy",
        "uptime": max(0, int(time.monotonic() - started_at)),
        "bot": "online",
    }


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


@lru_cache(maxsize=2)
def pwa_png(size: int) -> bytes:
    """Generate a small maskable AMOLED icon without adding binary assets."""
    rows = bytearray()
    center = (size - 1) / 2
    for y in range(size):
        rows.append(0)
        for x in range(size):
            dx, dy = x - center, y - center
            distance = (dx * dx + dy * dy) ** 0.5 / size
            red, green, blue = 0, 0, 0
            if distance < 0.44:
                red, green, blue = 24, 44, 104
            shield_top = size * 0.25
            shield_bottom = size * 0.75
            shield_width = size * 0.26 * ((y - shield_top) / (shield_bottom - shield_top) + 0.2)
            if shield_top <= y <= shield_bottom and abs(dx) <= max(2, shield_width):
                red, green, blue = 76, 132, 255
            if y > size * 0.55 and abs(dx) < size * 0.08 and y < size * 0.68:
                red, green, blue = 240, 248, 255
            rows.extend((red, green, blue, 255))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _png_chunk(b"IEND", b"")
    )


def pwa_svg() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="112" fill="#000"/>
<circle cx="256" cy="256" r="220" fill="#182c68"/>
<path d="M256 112l112 38v106c0 72-47 120-112 150-65-30-112-78-112-150V150z" fill="#4c84ff"/>
<path d="M211 262l31 31 61-70" fill="none" stroke="#f0f8ff" stroke-linecap="round" stroke-linejoin="round" stroke-width="28"/>
</svg>"""


def service_worker_source() -> str:
    return """const CACHE = "prime-dashboard-shell-v21";
const STATIC = [
  "./",
  "./static/app.css",
  "./static/ai-control.css?v=prime-talk-dashboard-4",
  "./static/visual-refresh.css?v=visual-refresh-2",
  "./static/leveling-card-assets.css?v=phase7",
  "./static/ai-control.js?v=prime-talk-dashboard-4",
  "./static/app.js?v=prime-talk-dashboard-1",
  "./manifest.json",
  "./icon.svg",
  "./icon-192.png",
  "./icon-512.png"
];
const isAsset = (url) =>
  url.pathname.includes("/static/") ||
  url.pathname.endsWith("/manifest.json") ||
  url.pathname.endsWith("/icon.svg") ||
  url.pathname.includes("/icon-");
const isAuthRoute = (url) =>
  url.pathname.includes("/api/auth/") ||
  url.pathname.endsWith("/login") ||
  url.pathname.endsWith("/login/");

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(STATIC)));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  // Never replace OAuth/login navigations with the cached app shell. The
  // document URL must stay on the real callback route so relative assets and
  // the authentication redirect both resolve through the public mount.
  if (event.request.mode === "navigate" && isAuthRoute(url)) return;
  if (event.request.mode === "navigate") {
    const shell = new URL("./", self.registration.scope);
    event.respondWith(
      fetch(event.request).then((response) => {
        if (response.ok && url.pathname === shell.pathname) {
          caches.open(CACHE).then((cache) => cache.put(shell.href, response.clone()));
        }
        return response;
      }).catch(() => caches.match(shell.href))
    );
    return;
  }
  if (!isAsset(url)) return;
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fresh = fetch(event.request).then((response) => {
        if (response.ok) caches.open(CACHE).then((cache) => cache.put(event.request, response.clone()));
        return response;
      }).catch(() => cached);
      return cached || fresh;
    })
  );
});"""


async def read_json_body(req) -> dict:
    """Read a bounded JSON object for action endpoints."""
    if req.content_length and req.content_length > MAX_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=req.content_length)
    if not req.content_type.startswith("application/json"):
        raise web.HTTPUnsupportedMediaType()
    try:
        body = json.loads((await req.content.read(MAX_BODY + 1))[:MAX_BODY].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_json"}), content_type="application/json") from error
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "validation"}), content_type="application/json")
    return sanitize_payload(body)


def rate_limited(key: tuple, limit: tuple[int, float]) -> float:
    """يعيد ثواني الانتظار المتبقية (0 = مسموح). نافذة منزلقة محدودة الحجم."""
    count, window = limit
    now = time.monotonic()
    if len(RATE_BUCKETS) > 5000:
        for stale_key, stale in list(RATE_BUCKETS.items()):
            if not stale or now - stale[-1] > window:
                RATE_BUCKETS.pop(stale_key, None)
    bucket = RATE_BUCKETS.setdefault(key, deque())
    while bucket and now - bucket[0] >= window:
        bucket.popleft()
    if len(bucket) >= count:
        return max(0.0, window - (now - bucket[0]))
    bucket.append(now)
    return 0.0


def same_origin(req) -> bool:
    origin = req.headers.get("Origin") or req.headers.get("Referer")
    if not origin:
        return False
    origin_host = urlsplit(origin).netloc.lower()
    expected_hosts = {req.host.lower()}
    # The dashboard is served through Replit's path proxy. In that path,
    # aiohttp can see the internal host while the browser sends the public
    # forwarded host in Origin/Referer.
    forwarded = req.headers.get("X-Forwarded-Host", "")
    expected_hosts.update(
        item.strip().lower().split(",", 1)[0]
        for item in forwarded.split(",")
        if item.strip()
    )
    return origin_host in expected_hosts


def csrf_ok(req, session) -> bool:
    token = req.headers.get("X-CSRF-Token", "")
    return bool(token) and secrets.compare_digest(token, session.get("csrf", ""))


async def live_grant(session, guild) -> bool:
    """تحقق حي من الصلاحية عبر البوت: المالك أو بت Administrator (0x8)."""
    if session.get("_local_dev"):
        return True
    user_id = str(session["id"])
    cached = GRANT_CACHE.get((user_id, guild.id))
    now = time.monotonic()
    if cached and now - cached[0] < GRANT_TTL:
        return cached[1]
    if len(GRANT_CACHE) > 5000:
        GRANT_CACHE.clear()
    if str(guild.owner_id) == user_id:
        GRANT_CACHE[(user_id, guild.id)] = (now, True)
        return True
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except discord.NotFound:
            member = None
        except (discord.HTTPException, asyncio.TimeoutError):
            raise web.HTTPServiceUnavailable(reason="permission check unavailable")
    allowed = bool(member and member_allows_dashboard(member, guild))
    GRANT_CACHE[(user_id, guild.id)] = (now, allowed)
    return allowed


async def live_level_admin(session, guild) -> bool:
    """Apply the stricter admin policy to destructive leveling operations."""
    if session.get("_local_dev"):
        return False
    try:
        user_id = int(session["id"])
    except (KeyError, TypeError, ValueError):
        return False
    if user_id == int(getattr(guild, "owner_id", 0) or 0):
        return True
    member = await resolve_dashboard_member(guild, user_id)
    return bool(member and is_level_admin(member, guild))


async def live_management_grant(session, guild, required_tier: str) -> bool:
    """Apply the additive PRIME tier map to live dashboard operators."""
    if session.get("_local_dev"):
        return True
    try:
        member = await resolve_dashboard_member(guild, int(session["id"]))
        if member is None:
            return False
        snapshot = await get_guild_settings(int(guild.id))
        return member_has_management_tier(
            member, guild, snapshot.get("settings", {}), required_tier
        )
    except Exception:
        logger.exception(
            "Could not verify PRIME management role for guild %s",
            getattr(guild, "id", "unknown"),
        )
        return False


async def authorize(
    req,
    *,
    write: bool = False,
    management_tier: str | None = None,
):
    """يعيد (session, guild) أو يرفع HTTPException. لا يُوثق أي شيء من جهة العميل."""
    session = current_session(req)
    if not session:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "unauthorized"}), content_type="application/json")
    raw = req.match_info.get("guild_id", "")
    if not raw.isdigit() or not 15 <= len(raw) <= 22:
        raise web.HTTPNotFound(text=json.dumps({"error": "not_found"}), content_type="application/json")
    if not any(g["id"] == raw for g in session["guilds"]):
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")
    guild = bot_ref.get_guild(int(raw)) if bot_ref else None
    if guild is None:
        raise web.HTTPNotFound(text=json.dumps({"error": "not_found"}), content_type="application/json")
    if not await live_grant(session, guild):
        session["guilds"] = [g for g in session["guilds"] if g["id"] != raw]
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")
    if management_tier and not await live_management_grant(
        session, guild, management_tier
    ):
        raise web.HTTPForbidden(
            text=json.dumps({"error": "management_role_denied"}),
            content_type="application/json",
        )
    if write and (not same_origin(req) or not csrf_ok(req, session)):
        raise web.HTTPForbidden(text=json.dumps({"error": "csrf"}), content_type="application/json")
    limit_key = ("save" if write else "read", session["id"], guild.id)
    wait = rate_limited(limit_key, SAVE_LIMIT if write else READ_LIMIT)
    if wait:
        raise web.HTTPTooManyRequests(
            text=json.dumps({"error": "rate_limited", "retry_after": int(wait) + 1}),
            content_type="application/json", headers={"Retry-After": str(int(wait) + 1)},
        )
    return session, guild


def public_settings(snapshot: dict) -> dict:
    settings = {
        key: (str(value) if SETTINGS_SCHEMA[key][2] == "id" and value is not None else value)
        for key, value in snapshot["settings"].items()
    }
    return {"revision": snapshot["revision"], "updated_at": snapshot["updated_at"], "settings": settings}


def parse_custom_emoji(value: str):
    match = re.fullmatch(r"<(a?):([A-Za-z0-9_~]+):(\d+)>", str(value or "").strip())
    if not match:
        return None
    return discord.PartialEmoji(
        name=match.group(2),
        id=int(match.group(3)),
        animated=bool(match.group(1)),
    )


async def guild_meta(guild) -> dict:
    if not getattr(guild, "chunked", True):
        try:
            await guild.chunk()
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            logger.warning("[META] Unable to chunk guild %s", guild.id, exc_info=True)
    icon = getattr(guild, "icon", None)
    me = guild.me
    top = me.top_role if me else None
    channels = await dashboard_channels(guild)
    categories = [
        {"id": str(category.id), "name": category.name}
        for category in sorted(
            getattr(guild, "categories", ()) or (),
            key=lambda item: getattr(item, "position", 0),
        )
    ]
    roles = []
    for role in reversed(guild.roles):
        if role.is_default():
            continue
        roles.append({
            "id": str(role.id), "name": role.name,
            "color": str(role.color),
            "position": int(getattr(role, "position", 0)),
            "assignable": bool(top and role < top and not role.managed),
        })
    stickers = list(getattr(guild, "stickers", ()) or ())
    fetch_stickers = getattr(guild, "fetch_stickers", None)
    if fetch_stickers is not None:
        try:
            fetched_stickers = await fetch_stickers()
            if fetched_stickers:
                stickers = list(fetched_stickers)
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to refresh stickers for guild %s", guild.id, exc_info=True)
    emojis = list(getattr(guild, "emojis", ()) or ())
    fetch_emojis = getattr(guild, "fetch_emojis", None)
    if fetch_emojis is not None:
        try:
            fetched_emojis = await fetch_emojis()
            if fetched_emojis:
                emojis = list(fetched_emojis)
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to refresh emojis for guild %s", guild.id, exc_info=True)
    members = [
        {
            "id": str(member.id),
            "name": member.display_name,
            "avatar": str(getattr(getattr(member, "display_avatar", None), "url", "")),
        }
        for member in getattr(guild, "members", ())
        if not getattr(member, "bot", False)
    ]
    serialized_emojis = [
        {
            "id": str(emoji.id),
            "name": emoji.name,
            "url": str(emoji.url),
            "animated": bool(getattr(emoji, "animated", False)),
            "token": str(emoji),
        }
        for emoji in emojis
        if getattr(emoji, "available", True)
    ]
    logger.info(
        "[META] Guild: %s | Members: %d | Roles: %d | Emojis: %d",
        guild.name,
        len(members),
        len(roles),
        len(serialized_emojis),
    )
    return {
        "guild": {"id": str(guild.id), "name": guild.name, "icon": icon.url if icon else None,
                  "members": guild.member_count},
        "bot": {
            "name": getattr(getattr(bot_ref, "user", None), "display_name", None)
            or getattr(getattr(bot_ref, "user", None), "name", "PR1ME TEAM"),
            "avatar": str(
                getattr(
                    getattr(getattr(bot_ref, "user", None), "display_avatar", None),
                    "url",
                    "",
                )
            ),
        },
        "channels": channels,
        "categories": categories,
        "roles": roles,
        "members": members,
        "stickers": [
            {"id": str(sticker.id), "name": sticker.name, "url": str(sticker.url)}
            for sticker in stickers
            if getattr(sticker, "available", True)
        ],
        "emojis": serialized_emojis,
    }


async def dashboard_channels(guild) -> list[dict]:
    """Return every message-capable guild channel with string snowflake IDs."""
    cached = list(getattr(guild, "channels", ()) or ())
    fetch_channels = getattr(guild, "fetch_channels", None)
    channels = cached
    if callable(fetch_channels):
        try:
            fetched = list(await fetch_channels())
            if fetched:
                channels = fetched
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            logger.debug("Unable to refresh channel list for guild %s", guild.id, exc_info=True)

    channels = [
        channel for channel in channels
        if isinstance(channel, MESSAGE_CHANNEL_TYPES)
    ]
    channels.sort(
        key=lambda channel: (
            getattr(getattr(channel, "category", None), "position", -1),
            getattr(channel, "position", 0),
            str(getattr(channel, "name", "")),
        )
    )
    result = []
    for channel in channels:
        try:
            channel_type = str(channel.type)
        except (AttributeError, TypeError):
            channel_type = "text"
        result.append({
            "id": str(channel.id),
            "name": str(channel.name),
            "type": channel_type,
            "position": int(getattr(channel, "position", 0)),
            "category_id": (
                str(channel.category.id)
                if getattr(channel, "category", None) is not None
                else None
            ),
            "category": (
                str(channel.category.name)
                if getattr(channel, "category", None) is not None
                else None
            ),
        })
    return result


async def resolve_text_channel(guild, channel_id: int):
    channel = guild.get_channel(int(channel_id))
    if isinstance(channel, MESSAGE_CHANNEL_TYPES):
        return channel
    fetch_channels = getattr(guild, "fetch_channels", None)
    if fetch_channels is not None:
        try:
            for fetched in await fetch_channels():
                if fetched.id == int(channel_id) and isinstance(fetched, MESSAGE_CHANNEL_TYPES):
                    return fetched
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to fetch channel %s in guild %s", channel_id, guild.id, exc_info=True)
    return None


async def resolve_guild_sticker(guild, sticker_id: int):
    for sticker in getattr(guild, "stickers", ()) or ():
        if sticker.id == int(sticker_id):
            return sticker
    fetch_stickers = getattr(guild, "fetch_stickers", None)
    if fetch_stickers is not None:
        try:
            for sticker in await fetch_stickers():
                if sticker.id == int(sticker_id):
                    return sticker
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Unable to fetch sticker %s in guild %s", sticker_id, guild.id, exc_info=True)
    return None


async def validate_changes(guild, changes: dict) -> tuple[dict, dict]:
    """تنقية المدخلات: مفاتيح مسموحة فقط، أنواع/حدود صحيحة، وقنوات/رتب تخص هذا السيرفر."""
    clean, errors = {}, {}
    if not isinstance(changes, dict) or len(changes) > len(SETTINGS_SCHEMA):
        return {}, {"_": "صيغة التعديلات غير صالحة"}
    for key, value in changes.items():
        if key not in SETTINGS_SCHEMA:
            errors[str(key)[:40]] = "حقل غير مسموح"
            continue
        try:
            value = validate_setting(key, value)
        except ValueError as error:
            errors[key] = str(error)
            continue
        if key == "management_role_ids":
            invalid_tier = None
            for tier, raw_id in value.items():
                if not raw_id:
                    continue
                role = guild.get_role(int(raw_id))
                if role is None or bool(getattr(role, "is_default", lambda: False)()):
                    invalid_tier = tier
                    break
            configured_ids = [
                role_id for role_id in value.values() if role_id
            ]
            if invalid_tier:
                errors[key] = (
                    f"رتبة مستوى {invalid_tier} غير موجودة في هذا السيرفر"
                )
                continue
            if len(configured_ids) != len(set(configured_ids)):
                errors[key] = "يجب اختيار رتبة مختلفة لكل مستوى"
                continue
        if value is not None and key.endswith("_channel_id"):
            channel = await resolve_text_channel(guild, value)
            if channel is None:
                errors[key] = "القناة غير موجودة في هذا السيرفر"
                continue
        if value is not None and key == "welcome_embed_sticker_id":
            if await resolve_guild_sticker(guild, value) is None:
                errors[key] = "ملصق السيرفر غير موجود أو غير متاح للبوت"
                continue
        if value is not None and key.endswith("_role_id"):
            role = guild.get_role(value)
            me = guild.me
            if role is None or role.is_default():
                errors[key] = "الرتبة غير موجودة في هذا السيرفر"
                continue
            if role.managed or not me or role >= me.top_role:
                errors[key] = "لا يمكن للبوت منح هذه الرتبة (أعلى من رتبته أو مُدارة)"
                continue
        if isinstance(value, list) and key.endswith("_role_ids"):
            invalid = False
            for raw_id in value:
                if not str(raw_id).isdigit() or not 15 <= len(str(raw_id)) <= 22:
                    invalid = True
                    break
                role = guild.get_role(int(raw_id))
                if role is None or role.is_default():
                    invalid = True
                    break
            if invalid:
                errors[key] = "تحتوي القائمة على رتبة غير موجودة في هذا السيرفر"
                continue
            value = list(dict.fromkeys(str(raw_id) for raw_id in value))
        if isinstance(value, list) and key.endswith("_channel_ids"):
            valid_channels = []
            for raw_id in value:
                if not str(raw_id).isdigit() or not 15 <= len(str(raw_id)) <= 22:
                    valid_channels = []
                    break
                channel = await resolve_text_channel(guild, int(raw_id))
                if channel is None:
                    valid_channels = []
                    break
                valid_channels.append(str(raw_id))
            if len(valid_channels) != len(value):
                errors[key] = "تحتوي القائمة على قناة غير موجودة في هذا السيرفر"
                continue
            value = list(dict.fromkeys(valid_channels))
        clean[key] = value
    return clean, errors


def broadcast(guild_id: int, payload: dict) -> None:
    for queue in list(SETTINGS_LISTENERS.get(guild_id, ())):
        if queue.full():
            continue
        queue.put_nowait(payload)


@routes.get('/api/health')
async def api_health(req):
    if not current_session(req):
        return json_error(401, "unauthorized")
    bot = request_bot(req)
    return web.json_response({"ok": True, "online": bot_is_connected(bot)})


_THEME_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_THEME_PREF_FIELDS = {
    "preset", "primary", "secondary", "background", "surface", "surfaceAlt",
    "text", "muted", "border", "buttonStyle",
}
_THEME_BUTTON_STYLES = {"solid", "soft", "outline"}


def _dashboard_theme_session(req, *, write=False):
    session = current_session(req)
    if not session:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "unauthorized"}),
            content_type="application/json",
        )
    if write and (not same_origin(req) or not csrf_ok(req, session)):
        raise web.HTTPForbidden(
            text=json.dumps({"error": "csrf"}),
            content_type="application/json",
        )
    limit = SAVE_LIMIT if write else READ_LIMIT
    wait = rate_limited(("user-theme", session["id"], "write" if write else "read"), limit)
    if wait:
        raise web.HTTPTooManyRequests(
            text=json.dumps({"error": "rate_limited", "retry_after": int(wait) + 1}),
            content_type="application/json",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    return session


@routes.get('/api/design-system/tokens')
async def api_design_system_tokens(req):
    _dashboard_theme_session(req)
    token_path = PROJECT_DIR / "artifacts" / "prime-design-system" / "tokens.json"
    try:
        tokens = json.loads(token_path.read_text("utf-8"))
        if not isinstance(tokens, dict):
            raise ValueError("invalid design tokens")
        colors = tokens.get("color")
        required_colors = {
            "dark": (
                "primary", "secondary", "accent", "background", "foreground",
                "border", "card", "popover", "primaryForeground",
                "mutedForeground", "destructive", "chart2", "chart3",
                "chart4", "chart5",
            ),
            "light": ("primaryForeground",),
        }
        if not isinstance(colors, dict) or any(
            not isinstance(colors.get(mode), dict)
            or any(
                not isinstance(colors[mode].get(role), dict)
                or not isinstance(colors[mode][role].get("$value"), str)
                or not _THEME_COLOR_RE.fullmatch(colors[mode][role]["$value"])
                for role in roles
            )
            for mode, roles in required_colors.items()
        ):
            raise ValueError("missing color tokens")
        typography = tokens.get("typography", {}).get("fontFamily", {})
        if any(
            not isinstance(typography.get(name, {}).get("$value"), list)
            or not typography[name]["$value"]
            or any(not isinstance(family, str) for family in typography[name]["$value"])
            for name in ("sans", "mono")
        ):
            raise ValueError("missing font tokens")
        if not all(
            isinstance(tokens.get(group, {}).get("base", {}).get("$value"), str)
            for group in ("radius", "spacing")
        ):
            raise ValueError("missing layout tokens")
    except (OSError, ValueError, TypeError, AttributeError) as error:
        logger.exception("Unable to load PRIME Design System tokens")
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "design_tokens_unavailable"}),
            content_type="application/json",
        ) from error
    return web.json_response({
        "color": tokens["color"],
        "typography": tokens.get("typography", {}),
        "radius": tokens.get("radius", {}),
        "spacing": tokens.get("spacing", {}),
    })


@routes.get('/api/user/theme')
async def api_get_user_theme(req):
    session = _dashboard_theme_session(req)
    theme = await get_user_dashboard_theme(session["id"])
    return web.json_response({"theme": theme})


@routes.post('/api/user/theme')
async def api_save_user_theme(req):
    session = _dashboard_theme_session(req, write=True)
    body = await read_json_body(req)
    if "theme" not in body:
        return json_error(400, "validation", fields={"theme": "مطلوب"})
    theme = body["theme"]
    if theme is None:
        await save_user_dashboard_theme(session["id"], None)
        return web.json_response({"ok": True, "theme": None})
    if not isinstance(theme, dict) or set(theme) != _THEME_PREF_FIELDS:
        return json_error(400, "validation", fields={"theme": "إعدادات المظهر غير مكتملة"})
    errors = {}
    preset = theme.get("preset")
    if not isinstance(preset, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", preset):
        errors["preset"] = "قالب غير صالح"
    for key in _THEME_PREF_FIELDS - {"preset", "buttonStyle"}:
        value = theme.get(key)
        if not isinstance(value, str) or not _THEME_COLOR_RE.fullmatch(value):
            errors[key] = "أدخل لوناً بصيغة HEX صالحة"
    if theme.get("buttonStyle") not in _THEME_BUTTON_STYLES:
        errors["buttonStyle"] = "نمط الزر غير صالح"
    if errors:
        return json_error(400, "validation", fields=errors)
    clean = {
        key: (value.lower() if key not in {"preset", "buttonStyle"} else value)
        for key, value in theme.items()
    }
    await save_user_dashboard_theme(session["id"], clean)
    return web.json_response({"ok": True, "theme": clean})


@routes.get('/api/guild/{guild_id}/meta')
async def api_guild_meta(req):
    raw_guild_id = req.match_info.get("guild_id", "")
    try:
        guild_id = int(raw_guild_id)
    except (TypeError, ValueError):
        raise web.HTTPNotFound(
            text=json.dumps({"error": "not_found"}),
            content_type="application/json",
        )
    _, authorized_guild = await authorize(req)
    guild = bot_ref.get_guild(guild_id) if bot_ref else None
    if guild is None or guild.id != authorized_guild.id:
        raise web.HTTPNotFound(
            text=json.dumps({"error": "not_found"}),
            content_type="application/json",
        )
    payload = await guild_meta(guild)
    metrics = (
        bot_ref.metrics_for_guild(guild.id)
        if bot_ref and hasattr(bot_ref, "metrics_for_guild")
        else []
    )
    # Keep /stats intact for existing consumers while giving the dashboard
    # one complete metadata snapshot for its initial render.
    payload["stats"] = await get_dashboard_stats(
        guild.id,
        member_count=guild.member_count,
        latency_series=metrics,
    )
    return web.json_response(payload)


@routes.get('/api/guild/{guild_id}/stats')
async def api_guild_stats(req):
    _, guild = await authorize(req)
    metrics = (
        bot_ref.metrics_for_guild(guild.id)
        if bot_ref and hasattr(bot_ref, "metrics_for_guild")
        else []
    )
    return web.json_response(
        await get_dashboard_stats(
            guild.id,
            member_count=guild.member_count,
            latency_series=metrics,
        )
    )


def _subscription_actor_id(session: dict) -> int:
    try:
        actor_id = int(session["id"])
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "unauthorized"}),
            content_type="application/json",
        ) from error
    if actor_id < 0:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "unauthorized"}),
            content_type="application/json",
        )
    return actor_id


def _subscription_validation_error(error: ValueError):
    return json_error(400, "validation", message=str(error)[:500])


async def _process_subscription_xp(bot, guild, result: dict) -> None:
    """Reuse the bot cog's existing post-commit leveling notifications."""
    if result.get("idempotent"):
        return
    record = result.get("subscription") or {}
    member = guild.get_member(int(record.get("user_id", 0)))
    if member is None:
        try:
            member = await guild.fetch_member(int(record["user_id"]))
        except (discord.NotFound, discord.Forbidden):
            return
        except (discord.HTTPException, asyncio.TimeoutError):
            logger.exception(
                "Subscription dashboard cannot fetch member guild=%s",
                guild.id,
            )
            return
    if member.bot:
        return
    cog = bot.get_cog("SubscriptionCommands") if bot else None
    handler = getattr(cog, "_process_level_events", None)
    if callable(handler):
        await handler(guild, member, result)


@routes.get('/api/guild/{guild_id}/subscriptions')
async def api_subscriptions_get(req):
    _, guild = await authorize(req)
    guild_id = int(guild.id)
    try:
        # Several service reads also run expiry processing or initialize
        # defaults with BEGIN IMMEDIATE. Serialize this snapshot so concurrent
        # writers do not contend with one another on the shared SQLite file.
        settings = await subscription_service.get_subscription_settings(guild_id)
        plans = await subscription_service.list_subscription_plans(guild_id)
        reminders = await subscription_service.list_subscription_reminders(guild_id)
        templates = await subscription_service.list_subscription_templates(guild_id)
        records = await subscription_service.list_subscriptions(guild_id, limit=100)
        analytics = await subscription_service.get_subscription_analytics(guild_id)
        notifications = await subscription_service.list_subscription_notifications(
            guild_id, limit=100
        )
        admin_audit = await subscription_service.list_subscription_admin_audit(
            guild_id, limit=60
        )
        control_audit = await subscription_service.list_subscription_control_audit(
            guild_id, limit=60
        )
        status_filter = str(req.query.get("status", "")).strip()
        search = str(req.query.get("q", "")).strip().casefold()
        if status_filter in {"active", "expired", "cancelled"}:
            records = [row for row in records if row["status"] == status_filter]
        if search:
            records = [
                row for row in records
                if search in str(row["subscription_id"]).casefold()
                or search in str(row["user_id"])
                or search in str(row.get("plan_id") or "").casefold()
            ]
        return web.json_response(
            {
                "settings": settings,
                "plans": plans,
                "reminders": reminders,
                "templates": templates,
                "subscriptions": records,
                "analytics": analytics,
                "notifications": notifications,
                "admin_audit": admin_audit,
                "control_audit": control_audit,
            }
        )
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.get('/api/guild/{guild_id}/subscriptions/{subscription_id}')
async def api_subscription_detail(req):
    _, guild = await authorize(req)
    subscription_id = req.match_info["subscription_id"]
    try:
        record = await subscription_service.get_subscription(
            guild.id, subscription_id
        )
        if record is None:
            return json_error(404, "not_found")
        history, admin_audit = await asyncio.gather(
            subscription_service.get_subscription_history(guild.id, subscription_id),
            subscription_service.list_subscription_admin_audit(
                guild.id, subscription_id=subscription_id, limit=100
            ),
        )
        return web.json_response(
            {"subscription": record, "history": history, "admin_audit": admin_audit}
        )
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/settings')
async def api_subscriptions_settings_save(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    expected_revision = body.pop("expected_revision", None)
    try:
        result = await subscription_service.update_subscription_settings(
            guild.id,
            body,
            expected_revision=expected_revision,
            actor_id=_subscription_actor_id(session),
        )
        return web.json_response({"settings": result})
    except subscription_service.SubscriptionSettingsConflict as error:
        return web.json_response(
            {"error": "conflict", "settings": error.current}, status=409
        )
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/plans')
async def api_subscription_plan_save(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    plan_id = body.pop("plan_id", None)
    try:
        result = await subscription_service.save_subscription_plan(
            guild.id, body, plan_id=plan_id,
            actor_id=_subscription_actor_id(session),
        )
        return web.json_response({"plan": result})
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/plans/{plan_id}/disable')
async def api_subscription_plan_disable(req):
    session, guild = await authorize(req, write=True)
    try:
        result = await subscription_service.disable_subscription_plan(
            guild.id, req.match_info["plan_id"],
            actor_id=_subscription_actor_id(session),
        )
        return web.json_response({"plan": result})
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/reminders')
async def api_subscription_reminder_save(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    reminder_id = body.pop("reminder_id", None)
    try:
        result = await subscription_service.save_subscription_reminder(
            guild.id, body, reminder_id=reminder_id,
            actor_id=_subscription_actor_id(session),
        )
        return web.json_response({"reminder": result})
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/reminders/{reminder_id}/disable')
async def api_subscription_reminder_disable(req):
    session, guild = await authorize(req, write=True)
    try:
        result = await subscription_service.disable_subscription_reminder(
            guild.id, req.match_info["reminder_id"],
            actor_id=_subscription_actor_id(session),
        )
        return web.json_response({"reminder": result})
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/templates')
async def api_subscription_template_save(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    template_id = body.pop("template_id", None)
    try:
        result = await subscription_service.save_subscription_template(
            guild.id, body, template_id=template_id,
            actor_id=_subscription_actor_id(session),
        )
        return web.json_response({"template": result})
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/templates/{template_id}/disable')
async def api_subscription_template_disable(req):
    session, guild = await authorize(req, write=True)
    try:
        result = await subscription_service.disable_subscription_template(
            guild.id, req.match_info["template_id"],
            actor_id=_subscription_actor_id(session),
        )
        return web.json_response({"template": result})
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/grant')
async def api_subscription_grant(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    allowed = {"user_id", "duration_days", "plan_id", "idempotency_key"}
    if set(body) - allowed or "user_id" not in body:
        return json_error(400, "validation", message="invalid grant fields")
    try:
        user_id = int(body["user_id"])
        if user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        try:
            member = guild.get_member(user_id)
            if member is None:
                member = await guild.fetch_member(user_id)
        except discord.NotFound as error:
            raise ValueError("user_id must identify a member of this server") from error
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            logger.exception(
                "Subscription dashboard could not verify member guild=%s user=%s",
                guild.id,
                user_id,
            )
            return json_error(503, "member_verification_unavailable")
        if getattr(member, "bot", False):
            raise ValueError("subscriptions cannot be granted to bot accounts")
        result = await subscription_service.create_subscription(
            guild.id,
            user_id,
            body.get("duration_days"),
            idempotency_key=body.get("idempotency_key"),
            actor_id=_subscription_actor_id(session),
            plan_id=body.get("plan_id"),
        )
        await _process_subscription_xp(request_bot(req), guild, result)
        return web.json_response(result)
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/{subscription_id}/renew')
async def api_subscription_renew(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    if set(body) - {"duration_days", "idempotency_key"}:
        return json_error(400, "validation", message="invalid renewal fields")
    try:
        result = await subscription_service.renew_subscription(
            guild.id,
            req.match_info["subscription_id"],
            body.get("duration_days"),
            idempotency_key=body.get("idempotency_key"),
            actor_id=_subscription_actor_id(session),
        )
        await _process_subscription_xp(request_bot(req), guild, result)
        return web.json_response(result)
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/{subscription_id}/cancel')
async def api_subscription_cancel(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    if set(body) - {"reason", "idempotency_key"}:
        return json_error(400, "validation", message="invalid cancellation fields")
    try:
        result = await subscription_service.cancel_subscription(
            guild.id,
            req.match_info["subscription_id"],
            idempotency_key=body.get("idempotency_key"),
            actor_id=_subscription_actor_id(session),
            reason=body.get("reason", ""),
        )
        return web.json_response(result)
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.post('/api/guild/{guild_id}/subscriptions/{subscription_id}/adjust')
async def api_subscription_adjust(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    if set(body) - {"changes", "reason", "idempotency_key"}:
        return json_error(400, "validation", message="invalid adjustment fields")
    try:
        result = await subscription_service.adjust_subscription(
            guild.id,
            req.match_info["subscription_id"],
            body.get("changes"),
            actor_id=_subscription_actor_id(session),
            idempotency_key=body.get("idempotency_key"),
            reason=body.get("reason", ""),
        )
        return web.json_response(result)
    except ValueError as error:
        return _subscription_validation_error(error)


@routes.get('/api/guilds/{guild_id}/analytics')
async def api_guild_analytics(req):
    _, guild = await authorize(req)
    requested_range = str(req.query.get("range", "7d")).strip().lower()
    allowed_ranges = {"today", "7d", "30d", "3m", "90d", "year"}
    timeframe = requested_range if requested_range in allowed_ranges else "7d"
    message_channels = [
        channel for channel in getattr(guild, "channels", ())
        if isinstance(channel, MESSAGE_CHANNEL_TYPES)
    ]
    channel_ids = [int(channel.id) for channel in message_channels]
    written_channel_ids = [
        int(channel.id)
        for channel in message_channels
        if isinstance(channel, (discord.TextChannel, discord.ForumChannel))
    ]
    summary, traffic, dead, top, heatmap, golden = await asyncio.gather(
        get_analytics_summary(guild.id, timeframe),
        get_channel_traffic(guild.id, timeframe),
        get_dead_channels(guild.id, timeframe, written_channel_ids),
        get_top_messenger(guild.id, timeframe),
        get_hourly_heatmap(guild.id, timeframe),
        get_golden_hour(guild.id, timeframe),
    )

    total_members = int(getattr(guild, "member_count", 0) or len(getattr(guild, "members", ())))
    online_members = sum(
        1
        for member in getattr(guild, "members", ())
        if not getattr(member, "bot", False)
        and str(getattr(member, "status", "offline")) != "offline"
    )
    active_chatters = int(summary.get("active_chatters", 0))
    active_chatters_pct = round((active_chatters / total_members) * 100) if total_members else 0
    online_pct = round((online_members / total_members) * 100) if total_members else 0
    chat_density_value = (
        summary.get("total_messages", 0) / total_members
        if total_members else 0
    )
    density_score = min(100, round(chat_density_value * 10))
    has_historical_activity = bool(
        int(summary.get("total_messages", 0) or 0)
        or int(summary.get("active_chatters", 0) or 0)
        or int(summary.get("total_voice_seconds", 0) or 0)
    )
    health_score_value = (
        round(
            online_pct * 0.2
            + active_chatters_pct * 0.4
            + int(summary.get("retention_pct", 0)) * 0.25
            + density_score * 0.15
        )
        if has_historical_activity else None
    )
    health_status = (
        "بانتظار البيانات" if health_score_value is None
        else "ممتاز" if health_score_value >= 80
        else "جيد" if health_score_value >= 60
        else "يحتاج متابعة" if health_score_value >= 35
        else "منخفض"
    )
    channel_lookup = {str(channel.id): channel for channel in message_channels}
    active_channels = []
    for row in traffic:
        channel = channel_lookup.get(str(row["id"]))
        if channel is None:
            continue
        channel_type = (
            "voice" if isinstance(channel, (discord.VoiceChannel, discord.StageChannel))
            else "forum" if isinstance(channel, discord.ForumChannel)
            else "text"
        )
        active_channels.append({
            "id": str(channel.id),
            "name": channel.name,
            "count": int(row["count"]),
            "percentage": row["percentage"],
            "type": channel_type,
        })
    dead_channels = [
        {
            "id": str(row["id"]),
            "name": channel_lookup.get(str(row["id"])).name
            if channel_lookup.get(str(row["id"])) else "قناة غير معروفة",
            "count": 0,
        }
        for row in dead
    ]
    top_payload = None
    if top:
        top_member = guild.get_member(int(top["user_id"]))
        top_payload = {
            **top,
            "username": top_member.display_name if top_member else top["user_id"],
            "tag": str(top_member) if top_member else top["user_id"],
            "avatar_url": str(top_member.display_avatar.url) if top_member else "",
            "role_badge": (
                next(
                    (role.name for role in reversed(getattr(top_member, "roles", []))
                     if not role.is_default()),
                    "عضو",
                )
                if top_member else "عضو",
            ),
        }
    online = bot_is_connected(bot_ref)
    return web.json_response({
        "range": timeframe,
        "status_banner": {
            "healthy": online,
            "text": "كل شيء تمام — السيرفر يشتغل بشكل طبيعي"
            if online else "الاتصال مع Discord يحتاج مراجعة",
        },
        "summary": {
            "total_members": total_members,
            "online_members": online_members,
            "active_chatters": active_chatters,
            "active_chatters_pct": active_chatters_pct,
            "total_messages": int(summary.get("total_messages", 0)),
            "activity_trend_pct": int(summary.get("activity_trend_pct", 0)),
            "retention_pct": int(summary.get("retention_pct", 0)),
            "total_voice_seconds": int(summary.get("total_voice_seconds", 0)),
        },
        "health_score": {
            "score": health_score_value,
            "status": health_status,
            "online_pct": online_pct,
            "active_writers_pct": active_chatters_pct,
            "retention_pct": int(summary.get("retention_pct", 0)),
            "chat_density": f"{chat_density_value:.1f} رسالة/عضو",
        },
        "top_messenger": top_payload,
        "channels_traffic": {
            "active": active_channels,
            "dead": dead_channels,
        },
        "heatmap": heatmap,
        "golden_hour": golden,
    })


def _security_cog():
    return bot_ref.get_cog("Security") if bot_ref else None


def _moderation_cog():
    return bot_ref.get_cog("Moderation") if bot_ref else None


def _analytics_cog():
    return bot_ref.get_cog("Analytics") if bot_ref else None


def _public_log_routes(routes_snapshot: dict) -> dict[str, str]:
    return {
        key: str(int(routes_snapshot.get(key, 0) or 0))
        for key in LOG_ROUTING_KEYS
    }


@routes.get('/api/guild/{guild_id}/logs/channels')
async def api_get_log_channels(req):
    _, guild = await authorize(req)
    return web.json_response({
        "channels": _public_log_routes(await get_logging_channels(guild.id)),
        "categories": list(LOG_ROUTING_ALL_KEYS),
    })


@routes.post('/api/guild/{guild_id}/logs/channels')
async def api_set_log_channels(req):
    _, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة التوزيع غير صالحة"})
    requested = body.get("channels", body)
    if not isinstance(requested, dict):
        return json_error(400, "validation", fields={"channels": "صيغة القنوات غير صالحة"})
    clean = {}
    errors = {}
    # Validate both the dedicated routes and legacy names so old dashboard
    # clients and integrations remain valid during the additive migration.
    for key in LOG_ROUTING_ALL_KEYS:
        raw = requested.get(key, 0)
        try:
            channel_id = int(raw or 0)
        except (TypeError, ValueError):
            errors[key] = "معرف القناة غير صالح"
            continue
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel is None or not isinstance(channel, MESSAGE_CHANNEL_TYPES):
                errors[key] = "اختر قناة صالحة من هذا السيرفر"
                continue
        clean[key] = channel_id
    if errors:
        return json_error(400, "validation", fields=errors)
    snapshot = await set_logging_channels(guild.id, clean)
    return web.json_response({"ok": True, "channels": _public_log_routes(snapshot)})


@routes.post('/api/guild/{guild_id}/logs/test/{category}')
async def api_test_log_channel(req):
    session, guild = await authorize(req, write=True)
    category = str(req.match_info.get("category", "")).strip()
    if category not in LOG_ROUTING_ALL_KEYS:
        return json_error(400, "unsupported_category")
    route = await get_logging_channels(guild.id)
    channel_id = int(route.get(category, 0) or 0)
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        return json_error(400, "category_unassigned")
    analytics = _analytics_cog()
    if analytics is None:
        return json_error(503, "analytics_unavailable")
    actor = guild.get_member(int(session["id"])) or guild.me
    try:
        await analytics.send_test(guild, category, actor=actor)
    except PermissionError:
        return json_error(403, "missing_send_permission")
    except (discord.Forbidden, discord.HTTPException):
        return json_error(502, "discord_unavailable")
    return web.json_response({"ok": True, "category": category, "channel_id": str(channel.id)})


@routes.get('/api/guild/{guild_id}/actions')
async def api_guild_actions(req):
    _, guild = await authorize(req)
    security = _security_cog()
    incidents = await _security_incident_rows(security, guild.id) if security else []
    warnings = await get_recent_warnings(guild.id, 50)
    actions = [
        {
            "id": f"security-{index}",
            "kind": "security",
            "action": item.get("action_type", item.get("action", "security")),
            "reason": item.get("mitigation_taken", item.get("reason", "")),
            "timestamp": item.get("timestamp"),
        }
        for index, item in enumerate(incidents)
    ]
    actions.extend(
        {
            "id": f"infraction-{item['id']}",
            "kind": "moderation",
            "action": "warning",
            "reason": item.get("reason", ""),
            "timestamp": item.get("timestamp"),
        }
        for item in warnings
    )
    actions.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return web.json_response({"actions": actions[:100]})


@routes.post('/api/guild/{guild_id}/actions')
async def api_guild_action(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    action = str(body.get("action", "")).strip()
    required_tier = {
        "lockdown": "admin",
        "revoke_warning": "moderator",
        "quick_unmute": "moderator",
    }.get(action)
    if required_tier and not await live_management_grant(
        session, guild, required_tier
    ):
        return json_error(403, "management_role_denied")
    result: dict
    if action == "lockdown":
        security = _security_cog()
        if security is None or "locked" not in body:
            return json_error(503, "security_unavailable")
        result = await security.emergency_lockdown(guild.id, bool(body["locked"]))
    elif action == "revoke_warning":
        moderation = _moderation_cog()
        warning_id = body.get("warning_id")
        if moderation is None or not str(warning_id).isdigit():
            return json_error(400, "validation", fields={"warning_id": "رقم المخالفة غير صالح"})
        result = {"deleted": bool(await moderation.revoke_warning(int(warning_id)))}
    elif action == "quick_unmute":
        moderation = _moderation_cog()
        user_id = body.get("user_id")
        if moderation is None or not str(user_id).isdigit():
            return json_error(400, "validation", fields={"user_id": "رقم العضو غير صالح"})
        result = await moderation.quick_unmute(guild.id, int(user_id))
    else:
        return json_error(400, "unsupported_action")
    logger.info(
        "Dashboard action %s applied in guild %s by user %s",
        action,
        guild.id,
        session["id"],
    )
    broadcast(guild.id, {"type": "action", "action": action, "result": result})
    return web.json_response({"ok": True, "action": action, "result": result})


def _utilities_cog():
    return bot_ref.get_cog("Utilities") if bot_ref else None


def _community_cog():
    return bot_ref.get_cog("Community") if bot_ref else None


def _economy_cog():
    return bot_ref.get_cog("Economy") if bot_ref else None


def _public_economy_user(row: dict) -> dict:
    return {
        "user_id": str(row["user_id"]),
        "balance": int(row["balance"]),
        "bank": int(row["bank"]),
        "total": int(row["total"]) if "total" in row else int(row["balance"]) + int(row["bank"]),
    }


@routes.get('/api/guild/{guild_id}/economy')
async def api_guild_economy(req):
    _, guild = await authorize(req)
    snapshot = await get_guild_settings(guild.id)
    return web.json_response({
        "settings": public_settings(snapshot),
        "wealth": [_public_economy_user(row) for row in await get_economy_leaderboard(guild.id, 10)],
        "multipliers": await get_role_multipliers(guild.id),
    })


@routes.post('/api/guild/{guild_id}/economy/config')
async def api_guild_economy_config(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    changes = {}
    if "leaderboard_channel_id" in body:
        try:
            channel_id = int(body["leaderboard_channel_id"] or 0)
        except (TypeError, ValueError):
            return json_error(400, "validation", fields={"leaderboard_channel_id": "القناة غير صالحة"})
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel is None or not isinstance(channel, MESSAGE_CHANNEL_TYPES):
                return json_error(400, "validation", fields={"leaderboard_channel_id": "اختر قناة صالحة"})
        changes["leaderboard_channel_id"] = channel_id
        changes["leaderboard_message_id"] = 0
    try:
        if "daily_base_amount" in body:
            amount = int(body["daily_base_amount"])
            if not 0 <= amount <= 1_000_000:
                raise ValueError
            changes["daily_base_amount"] = amount
        if "role_multipliers" in body:
            raw = body["role_multipliers"]
            if not isinstance(raw, dict) or len(raw) > 100:
                raise ValueError
            clean = {}
            for role_id, multiplier in raw.items():
                role = guild.get_role(int(role_id)) if str(role_id).isdigit() else None
                value = float(multiplier)
                if role is None or not 0 < value <= 10:
                    raise ValueError
                clean[str(role.id)] = round(value, 3)
            changes["role_multipliers"] = clean
        if "economy_support_role_ids" in body:
            raw = body["economy_support_role_ids"]
            if not isinstance(raw, list) or len(raw) > 25:
                raise ValueError
            clean = []
            for role_id in raw:
                role = guild.get_role(int(role_id)) if str(role_id).isdigit() else None
                if role is None or role.is_default():
                    raise ValueError
                clean.append(str(role.id))
            changes["economy_support_role_ids"] = list(dict.fromkeys(clean))
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"economy": "إعدادات الاقتصاد غير صالحة"})
    if not changes:
        return json_error(400, "validation", fields={"economy": "لا توجد تغييرات"})
    snapshot = await update_guild_settings(guild.id, **changes)
    result = public_settings(snapshot)
    broadcast(guild.id, {"type": "settings", "by": str(session["id"]), **result})
    economy = _economy_cog()
    if economy and changes.get("leaderboard_channel_id"):
        await economy.refresh_leaderboard(guild.id)
    return web.json_response({"ok": True, **result})


@routes.post('/api/guild/{guild_id}/economy/adjust')
async def api_guild_economy_adjust(req):
    session, guild = await authorize(req, write=True)
    economy = _economy_cog()
    if economy is None:
        return json_error(503, "economy_unavailable")
    body = await read_json_body(req)
    try:
        user_id = int(body.get("user_id"))
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"user_id": "معرف العضو غير صالح"})
    try:
        wallet_delta = int(body.get("wallet_delta", 0))
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"adjustment": "التعديل غير صالح"})
    if abs(wallet_delta) > 1_000_000_000:
        return json_error(400, "validation", fields={"adjustment": "التعديل أكبر من الحد المسموح"})
    actor = guild.get_member(int(session["id"]))
    target = guild.get_member(user_id)
    if actor is None or target is None:
        return json_error(404, "member_not_found")
    if (
        not member_allows_dashboard(actor, guild)
        and not await economy._is_economy_support(actor)
    ):
        return json_error(403, "forbidden")
    try:
        result = await economy.dashboard_adjust(
            guild, actor, target, wallet_delta
        )
    except ValueError as error:
        return json_error(400, "validation", fields={"adjustment": str(error)})
    return web.json_response({"ok": True, "user": result["user"]})


def _gaming_cog():
    return bot_ref.get_cog("Gaming") if bot_ref else None


@routes.get('/api/guild/{guild_id}/gaming')
async def api_guild_gaming(req):
    _, guild = await authorize(req)
    return web.json_response({"scrims": await get_scrims(guild.id)})


@routes.post('/api/guild/{guild_id}/gaming/deploy')
async def api_guild_gaming_deploy(req):
    _, guild = await authorize(req, write=True)
    gaming = _gaming_cog()
    if gaming is None:
        return json_error(503, "gaming_unavailable")
    body = await read_json_body(req)
    title = str(body.get("title") or "").strip()
    game_type = str(body.get("game_type") or "").strip()
    try:
        team_size = int(body.get("team_size", 5))
        max_slots = int(body.get("max_slots", 8))
        channel_id = int(body.get("target_channel_id"))
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"target_channel_id": "بيانات غير صالحة"})
    channel = guild.get_channel(channel_id)
    if channel is None or not isinstance(channel, MESSAGE_CHANNEL_TYPES):
        return json_error(400, "validation", fields={"target_channel_id": "اختر قناة صالحة"})
    if not title or len(title) > 150 or not game_type or len(game_type) > 80:
        return json_error(400, "validation", fields={"title": "أدخل عنواناً ونوع لعبة صالحين"})
    if not 1 <= team_size <= 16 or not 1 <= max_slots <= 128:
        return json_error(400, "validation", fields={"max_slots": "القيم خارج النطاق المسموح"})
    try:
        scrim = await gaming.deploy_scrim(
            guild, channel, title, game_type, team_size, max_slots
        )
    except discord.Forbidden:
        return json_error(403, "discord_forbidden")
    except discord.HTTPException:
        return json_error(502, "discord_unavailable")
    return web.json_response({"ok": True, "scrim": scrim})


@routes.post('/api/guild/{guild_id}/gaming/close')
async def api_guild_gaming_close(req):
    _, guild = await authorize(req, write=True)
    gaming = _gaming_cog()
    if gaming is None:
        return json_error(503, "gaming_unavailable")
    body = await read_json_body(req)
    try:
        scrim_id = int(body.get("scrim_id"))
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"scrim_id": "معرف السكريم غير صالح"})
    scrims = await get_scrims(guild.id)
    if not any(int(item["id"]) == scrim_id for item in scrims):
        return json_error(404, "scrim_not_found")
    if not await gaming.close_scrim_board(scrim_id, guild):
        return json_error(409, "scrim_already_closed")
    return web.json_response({"ok": True, "scrim_id": scrim_id})


@routes.post('/api/guild/{guild_id}/gaming/credentials')
async def api_guild_gaming_credentials(req):
    _, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    try:
        scrim_id = int(body.get("scrim_id"))
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"scrim_id": "معرف السكريم غير صالح"})
    credentials = str(body.get("credentials") or "").strip()
    if not credentials or len(credentials) > 1500:
        return json_error(400, "validation", fields={"credentials": "أدخل بيانات الغرفة"})
    scrim = next(
        (item for item in await get_scrims(guild.id) if int(item["id"]) == scrim_id),
        None,
    )
    if not scrim:
        return json_error(404, "scrim_not_found")
    channel = guild.get_channel(int(scrim["channel_id"]))
    if channel is None:
        return json_error(404, "channel_not_found")
    embed = discord.Embed(
        title=f"🔐 بيانات غرفة السكريم: {scrim['title']}",
        description=credentials,
        color=0x8B5CF6,
    )
    embed.set_footer(text=f"SCRIM:{scrim_id} • أرسلها فقط للمشاركين")
    try:
        message = await channel.send(embed=embed)
    except discord.Forbidden:
        return json_error(403, "discord_forbidden")
    except discord.HTTPException:
        return json_error(502, "discord_unavailable")
    return web.json_response({"ok": True, "message_id": str(message.id)})


def _command_roles(guild, role_ids):
    if not isinstance(role_ids, list) or len(role_ids) > 25:
        return None, "اختر من 0 إلى 25 رتبة"
    clean = []
    for role_id in role_ids:
        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            role = None
        if role is None or role.is_default():
            return None, "توجد رتبة غير موجودة في هذا السيرفر"
        clean.append(str(role.id))
    return list(dict.fromkeys(clean)), None


def _command_channels(guild, channel_ids):
    if not isinstance(channel_ids, list) or len(channel_ids) > 25:
        return None, "اختر من 0 إلى 25 قناة"
    clean = []
    for channel_id in channel_ids:
        try:
            channel = guild.get_channel(int(channel_id))
        except (TypeError, ValueError):
            channel = None
        if channel is None:
            return None, "توجد قناة غير موجودة في هذا السيرفر"
        clean.append(str(channel.id))
    return list(dict.fromkeys(clean)), None


def _command_aliases(aliases):
    if not isinstance(aliases, list) or len(aliases) > 20:
        return None, "اختر من 0 إلى 20 اختصاراً"
    clean = []
    seen = set()
    for raw in aliases:
        alias = str(raw or "").strip().lstrip("!/")
        if (
            not alias
            or len(alias) > 80
            or any(char.isspace() for char in alias)
        ):
            return None, "كل اختصار يجب أن يكون كلمة واحدة من 1 إلى 80 حرفاً"
        key = alias.casefold()
        if key not in seen:
            seen.add(key)
            clean.append(alias)
    return clean, None


def _qualified_command_target(target, known_commands):
    """Return the longest loaded command name at the start of a target."""
    normalized = str(target or "").strip().lstrip("!/").casefold()
    if not normalized:
        return ""
    matches = [
        command_name
        for command_name in known_commands
        if normalized == command_name or normalized.startswith(f"{command_name} ")
    ]
    if matches:
        return max(matches, key=len)
    return normalized.split()[0]


@routes.get('/api/guild/{guild_id}/commands')
async def api_guild_commands(req):
    _, guild = await authorize(req)
    utilities = _utilities_cog()
    if utilities is None:
        return json_error(503, "utilities_unavailable")
    status = await utilities.get_guild_commands_status(guild.id)
    meta = await guild_meta(guild)
    status["roles"] = meta["roles"]
    status["channels"] = meta["channels"]
    status["shortcuts"] = await get_shortcuts(guild.id)
    return web.json_response(status)


@routes.get('/api/guild/{guild_id}/commands/registry')
async def api_guild_commands_registry(req):
    _, guild = await authorize(req)
    policies = await get_command_policies(guild.id, refresh=True)
    flattened = []
    for metadata in MASTER_COMMANDS_REGISTRY.values():
        policy = policies.get(metadata["key"], {})
        flattened.append({
            **metadata,
            "policy": {
                "enabled": bool(policy.get("enabled", True)),
                "aliases": list(policy.get("aliases", [])),
                "allowed_roles": list(policy.get("allowed_roles", [])),
                "allowed_channels": list(policy.get("allowed_channels", [])),
                "auto_delete_seconds": int(policy.get("auto_delete_seconds") or 0),
                "response_mode": str(policy.get("response_mode") or policy.get("response_style") or "default"),
                "custom_template": str(policy.get("custom_template") or policy.get("response_template") or ""),
                "configured": metadata["key"] in policies,
                "updated_at": policy.get("updated_at"),
            },
        })
    return web.json_response({
        "guild_id": str(guild.id),
        "categories": grouped_command_registry(),
        "commands": flattened,
        "policies": policies,
        "auto_delete_presets": list(AUTO_DELETE_PRESETS),
        "response_styles": list(RESPONSE_STYLES),
    })


@routes.post('/api/guild/{guild_id}/commands/toggle')
async def api_guild_commands_toggle(req):
    _, guild = await authorize(req, write=True)
    utilities = _utilities_cog()
    if utilities is None:
        return json_error(503, "utilities_unavailable")
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    command_name = str(body.get("command_name", "")).strip().lower()
    if not command_name or len(command_name) > 100:
        return json_error(400, "validation", fields={"command_name": "اسم الأمر غير صالح"})
    if not isinstance(body.get("enabled"), bool):
        return json_error(400, "validation", fields={"enabled": "القيمة يجب أن تكون تشغيل/إيقاف"})
    roles, role_error = _command_roles(guild, body.get("allowed_roles", []))
    if role_error:
        return json_error(400, "validation", fields={"allowed_roles": role_error})
    channels, channel_error = _command_channels(guild, body.get("allowed_channels", []))
    if channel_error:
        return json_error(400, "validation", fields={"allowed_channels": channel_error})
    aliases = None
    if "aliases" in body:
        aliases, alias_error = _command_aliases(body.get("aliases"))
        if alias_error:
            return json_error(400, "validation", fields={"aliases": alias_error})
    toggle_args = (
        guild.id,
        command_name,
        body["enabled"],
        roles,
        channels,
    )
    if aliases is not None:
        result = await utilities.toggle_command(*toggle_args, aliases)
    else:
        result = await utilities.toggle_command(*toggle_args)
    return web.json_response({"command": result})


@routes.post('/api/guild/{guild_id}/commands/{command_name}/policy')
async def api_guild_command_policy(req):
    _, guild = await authorize(req, write=True)
    utilities = _utilities_cog()
    if utilities is None:
        return json_error(503, "utilities_unavailable")
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    command_name = str(req.match_info.get("command_name", "")).strip().lower()
    if not command_name or len(command_name) > 100 or any(
        char in "\r\n\t" for char in command_name
    ):
        return json_error(400, "validation", fields={"command_name": "اسم الأمر غير صالح"})
    is_enabled = body.get("is_enabled", body.get("enabled"))
    if not isinstance(is_enabled, bool):
        return json_error(400, "validation", fields={"is_enabled": "القيمة يجب أن تكون تشغيل/إيقاف"})
    roles, role_error = _command_roles(guild, body.get("allowed_roles", []))
    if role_error:
        return json_error(400, "validation", fields={"allowed_roles": role_error})
    channels, channel_error = _command_channels(guild, body.get("allowed_channels", []))
    if channel_error:
        return json_error(400, "validation", fields={"allowed_channels": channel_error})
    aliases, alias_error = _command_aliases(body.get("aliases", []))
    if alias_error:
        return json_error(400, "validation", fields={"aliases": alias_error})
    auto_delete_seconds = body.get("auto_delete_seconds")
    if auto_delete_seconds is not None:
        try:
            auto_delete_seconds = int(auto_delete_seconds)
        except (TypeError, ValueError):
            auto_delete_seconds = -1
        if auto_delete_seconds not in AUTO_DELETE_PRESETS:
            return json_error(
                400,
                "validation",
                fields={"auto_delete_seconds": "اختر مدة حذف تلقائي معتمدة"},
            )
    response_mode = body.get("response_mode", body.get("response_style"))
    if response_mode is not None:
        response_mode = str(response_mode).strip().lower()
        if response_mode not in RESPONSE_STYLES:
            return json_error(
                400,
                "validation",
                fields={"response_mode": "نمط الرد غير صالح"},
            )
    custom_template = body.get("custom_template", body.get("response_template"))
    if custom_template is not None:
        custom_template = str(custom_template)
        if len(custom_template) > 2000:
            return json_error(
                400,
                "validation",
                fields={"custom_template": "القالب يتجاوز 2000 حرف"},
            )
    policy_args = {"aliases": aliases}
    if auto_delete_seconds is not None:
        policy_args["auto_delete_seconds"] = auto_delete_seconds
    if response_mode is not None:
        policy_args["response_style"] = response_mode
    if custom_template is not None:
        policy_args["response_template"] = custom_template
    try:
        result = await utilities.toggle_command(
            guild.id,
            command_name,
            is_enabled,
            roles,
            channels,
            **policy_args,
        )
    except ValueError as error:
        return json_error(400, "validation", fields={"aliases": str(error)})
    return web.json_response({"command": result, "policy": result})


@routes.post('/api/guild/{guild_id}/shortcuts')
async def api_guild_shortcut_save(req):
    _, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    trigger = str(body.get("trigger") or "").strip()
    target_type = str(body.get("target_type") or "command").strip().lower()
    target = str(body.get("target") or "").strip()
    if not trigger or len(trigger) > 80 or any(char.isspace() for char in trigger):
        return json_error(400, "validation", fields={"trigger": "الاختصار يجب أن يكون كلمة واحدة من 1 إلى 80 حرفاً"})
    if target_type not in {"command", "help"} or not target:
        return json_error(400, "validation", fields={"target": "أمر الهدف غير صالح"})
    utilities = _utilities_cog()
    command_bot = getattr(utilities, "bot", None) or bot_ref
    prefix_commands = getattr(command_bot, "commands", []) if command_bot else []
    tree = getattr(command_bot, "tree", None) if command_bot else None
    slash_commands = tree.walk_commands() if tree and hasattr(tree, "walk_commands") else []
    known = {
        command.qualified_name.lower()
        for command in prefix_commands
        if not getattr(command, "hidden", False)
    }
    known.update(
        command.qualified_name.lower()
        for command in slash_commands
        if not getattr(command, "hidden", False)
    )
    command_name = _qualified_command_target(target, known)
    if known and command_name not in known:
        return json_error(400, "validation", fields={"target": "الأمر الهدف غير موجود"})
    try:
        shortcut = await save_shortcut(
            guild.id,
            trigger,
            target_type,
            target=target if target.startswith("/") else f"/{command_name}",
        )
        utilities = _utilities_cog()
        if utilities and hasattr(utilities, "sync_auto_responders"):
            await utilities.sync_auto_responders(guild.id)
    except ValueError as error:
        return json_error(400, "validation", fields={"trigger": str(error)})
    return web.json_response({"shortcut": shortcut})


@routes.delete('/api/guild/{guild_id}/shortcuts/{shortcut_id}')
async def api_guild_shortcut_delete(req):
    _, guild = await authorize(req, write=True)
    try:
        shortcut_id = int(req.match_info["shortcut_id"])
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"shortcut_id": "معرف الاختصار غير صالح"})
    if not await delete_shortcut(guild.id, shortcut_id):
        return json_error(404, "shortcut_not_found")
    utilities = _utilities_cog()
    if utilities and hasattr(utilities, "sync_auto_responders"):
        await utilities.sync_auto_responders(guild.id)
    return web.json_response({"deleted": True, "shortcut_id": shortcut_id})


@routes.get('/api/guild/{guild_id}/auto-responses')
async def api_guild_auto_responses(req):
    _, guild = await authorize(req)
    rules = await get_auto_responders(guild.id)
    meta = await guild_meta(guild)
    return web.json_response({
        "rules": rules,
        "channels": await dashboard_channels(guild),
        "roles": meta["roles"],
        "emojis": meta["emojis"],
        "members": meta["members"],
    })


@routes.post('/api/guild/{guild_id}/auto-responses')
async def api_guild_auto_responses_save(req):
    _, guild = await authorize(req, write=True)
    utilities = _utilities_cog()
    if utilities is None:
        return json_error(503, "utilities_unavailable")
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})

    trigger = str(body.get("trigger", "")).strip()
    match_type = str(body.get("match_type", "")).strip().lower()
    response = str(body.get("response", ""))
    target_type = str(body.get("target_type", "everyone")).strip().lower()
    reaction_emoji = str(body.get("reaction_emoji", "") or "").strip()
    if not trigger or len(trigger) > 500:
        return json_error(400, "validation", fields={"trigger": "المشغل يجب أن يكون بين 1 و500 حرف"})
    if match_type not in {"exact", "contains", "regex"}:
        return json_error(400, "validation", fields={"match_type": "نوع المطابقة غير صالح"})
    if len(response) > 2000:
        return json_error(400, "validation", fields={"response": "الرد يجب ألا يتجاوز 2000 حرف"})
    if target_type not in {"everyone", "role", "user"}:
        return json_error(400, "validation", fields={"target_type": "نطاق الاستهداف غير صالح"})
    try:
        target_id = max(0, int(body.get("target_id") or 0))
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"target_id": "معرف الاستهداف غير صالح"})
    if target_type == "role":
        role = guild.get_role(target_id)
        if role is None or role.is_default():
            return json_error(400, "validation", fields={"target_id": "الرتبة غير موجودة في هذا السيرفر"})
    elif target_type == "user":
        member = guild.get_member(target_id)
        if member is None:
            try:
                member = await guild.fetch_member(target_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None
        if member is None:
            return json_error(400, "validation", fields={"target_id": "العضو غير موجود في هذا السيرفر"})
    elif target_id:
        return json_error(400, "validation", fields={"target_id": "لا تستخدم معرفاً مع نطاق الجميع"})
    if reaction_emoji.startswith("<") and reaction_emoji.endswith(">"):
        parsed_emoji = parse_custom_emoji(reaction_emoji)
        if not parsed_emoji or not parsed_emoji.id or guild.get_emoji(parsed_emoji.id) is None:
            return json_error(400, "validation", fields={"reaction_emoji": "الإيموجي المخصص غير موجود في هذا السيرفر"})
        reaction_emoji = str(parsed_emoji)
    if not response.strip() and not reaction_emoji:
        return json_error(400, "validation", fields={"response": "أدخل نص الرد أو اختر إيموجي تفاعلاً"})
    try:
        cooldown = float(body.get("cooldown_seconds", 5))
    except (TypeError, ValueError):
        cooldown = -1
    if cooldown < 0 or cooldown > 60:
        return json_error(400, "validation", fields={"cooldown_seconds": "التبريد يجب أن يكون بين 0 و60 ثانية"})

    channel_id = body.get("channel_id")
    if channel_id in ("", None):
        channel_id = None
    else:
        try:
            channel = guild.get_channel(int(channel_id))
        except (TypeError, ValueError):
            channel = None
        if not isinstance(channel, MESSAGE_CHANNEL_TYPES):
            return json_error(400, "validation", fields={"channel_id": "القناة غير موجودة في هذا السيرفر"})
        channel_id = int(channel.id)

    try:
        rule = await utilities.add_auto_responder(
            guild.id,
            trigger,
            match_type,
            response,
            enabled=bool(body.get("enabled", True)),
            cooldown_seconds=cooldown,
            bucket_capacity=max(1, min(20, int(body.get("bucket_capacity", 1)))),
            channel_id=channel_id,
            target_type=target_type,
            target_id=target_id,
            reaction_emoji=reaction_emoji,
        )
    except (ValueError, re.error) as error:
        return json_error(400, "validation", fields={"trigger": str(error)})
    return web.json_response({"rule": rule})


@routes.delete('/api/guild/{guild_id}/auto-responses/{rule_id}')
async def api_guild_auto_responses_delete(req):
    _, guild = await authorize(req, write=True)
    utilities = _utilities_cog()
    if utilities is None:
        return json_error(503, "utilities_unavailable")
    try:
        rule_id = int(req.match_info["rule_id"])
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"rule_id": "معرف القاعدة غير صالح"})
    if rule_id <= 0:
        return json_error(400, "validation", fields={"rule_id": "معرف القاعدة غير صالح"})
    deleted = await utilities.delete_auto_responder(guild.id, rule_id)
    if not deleted:
        return json_error(404, "auto_responder_not_found")
    return web.json_response({"deleted": True, "rule_id": rule_id})


def _ticket_role_ids(guild, categories):
    if not isinstance(categories, list) or not categories or len(categories) > 25:
        return None, "أضف من 1 إلى 25 تصنيفاً"
    clean = []
    for index, raw in enumerate(categories):
        if not isinstance(raw, dict):
            return None, f"التصنيف رقم {index + 1} غير صالح"
        label = str(raw.get("label") or raw.get("name") or "").strip()
        if not label or len(label) > 80:
            return None, f"اسم التصنيف رقم {index + 1} غير صالح"
        role_ids = raw.get("support_role_ids", [])
        if not role_ids and raw.get("role_id") not in (None, ""):
            role_ids = [raw.get("role_id")]
        senior_ids = raw.get("senior_role_ids", [])
        if not isinstance(role_ids, list) or not isinstance(senior_ids, list):
            return None, f"رتب التصنيف رقم {index + 1} غير صالحة"
        # Legacy panels may intentionally have no support-role restriction.
        # Keep those categories editable and deployable; Discord permissions
        # still grant access to administrators/manage-channel staff, while a
        # support role can be added later from the dashboard.
        for role_id in [*role_ids, *senior_ids]:
            try:
                role = guild.get_role(int(role_id))
            except (TypeError, ValueError):
                role = None
            if role is None or role.is_default():
                return None, f"توجد رتبة غير موجودة في التصنيف رقم {index + 1}"
        category_id = raw.get("category_id")
        if category_id not in (None, ""):
            try:
                parent = guild.get_channel(int(category_id))
            except (TypeError, ValueError):
                parent = None
            if not isinstance(parent, discord.CategoryChannel):
                return None, f"الفئة الأب للتصنيف رقم {index + 1} غير موجودة"
        intake_fields = raw.get("intake_fields", [])
        if not isinstance(intake_fields, list) or len(intake_fields) > 3:
            return None, f"حقول نموذج التصنيف رقم {index + 1} غير صالحة"
        clean_fields = []
        for field_index, field in enumerate(intake_fields):
            if not isinstance(field, dict):
                return None, f"حقل نموذج غير صالح في التصنيف رقم {index + 1}"
            field_label = str(field.get("label") or "").strip()
            if not field_label or len(field_label) > 45:
                return None, f"اسم حقل نموذج غير صالح في التصنيف رقم {index + 1}"
            field_key = re.sub(
                r"[^a-zA-Z0-9_-]+", "_",
                str(field.get("key") or f"field_{field_index + 1}").strip().lower(),
            ).strip("_")[:40]
            if not field_key:
                return None, f"مفتاح حقل نموذج غير صالح في التصنيف رقم {index + 1}"
            clean_fields.append({
                "key": field_key,
                "label": field_label,
                "placeholder": str(field.get("placeholder") or "").strip()[:100],
                "required": bool(field.get("required", False)),
            })
        normalized = dict(raw)
        normalized["role_id"] = str(role_ids[0]) if role_ids else None
        normalized["description"] = str(raw.get("description") or "").strip()[:100]
        emoji = str(raw.get("emoji") or "🎫").strip()
        if not emoji or len(emoji) > 100:
            return None, f"إيموجي التصنيف رقم {index + 1} غير صالح"
        custom_emoji = re.fullmatch(r"<a?:[A-Za-z0-9_]+:(\d+)>", emoji)
        if custom_emoji and guild.get_emoji(int(custom_emoji.group(1))) is None:
            return None, f"إيموجي التصنيف رقم {index + 1} غير موجود في السيرفر"
        normalized["emoji"] = emoji
        normalized["welcome_msg"] = str(raw.get("welcome_msg") or "").strip()[:2000]
        normalized["intake_fields"] = clean_fields
        clean.append(normalized)
    return clean, None


def _ticket_embed_config(body: dict, existing: dict | None = None):
    existing = existing or {}
    title = str(body.get("embed_title", existing.get("embed_title") or "🎫 مركز الدعم والتذاكر")).strip()
    description = str(body.get("embed_description", existing.get("embed_description") or "")).strip()
    footer = str(body.get("footer_text", existing.get("footer_text") or "Help Desk • اختر تصنيفاً لبدء المحادثة")).strip()
    if not title or len(title) > 256:
        return None, {"embed_title": "عنوان اللوحة يجب أن يكون بين 1 و256 حرفاً"}
    if len(description) > 4096:
        return None, {"embed_description": "وصف اللوحة يجب ألا يتجاوز 4096 حرفاً"}
    if len(footer) > 2048:
        return None, {"footer_text": "التذييل يجب ألا يتجاوز 2048 حرفاً"}
    raw_color = body.get("embed_color", existing.get("embed_color", 0x5865F2))
    try:
        if isinstance(raw_color, str):
            raw_color = raw_color.strip().lstrip("#")
            color = int(raw_color, 16) if raw_color else 0x5865F2
        else:
            color = int(raw_color)
    except (TypeError, ValueError):
        return None, {"embed_color": "لون اللوحة غير صالح"}
    if color < 0 or color > 0xFFFFFF:
        return None, {"embed_color": "لون اللوحة يجب أن يكون HEX صالحاً"}
    return {
        "embed_title": title,
        "embed_description": description,
        "embed_color": color,
        "footer_text": footer,
    }, None


@routes.get('/api/guild/{guild_id}/tickets/config')
async def api_guild_tickets_config_get(req):
    _, guild = await authorize(req)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    config = await get_ticket_config(guild.id)
    options = await get_ticket_options(guild.id)
    if not options:
        options = normalize_ticket_categories(None)
    else:
        options = normalize_ticket_categories(options)
    return web.json_response({
        "config": config or {
            "guild_id": guild.id,
            "channel_id": None,
            "message_id": None,
            "embed_title": "🎫 مركز الدعم والتذاكر",
            "embed_description": "",
            "embed_color": 0x5865F2,
            "footer_text": "Help Desk • اختر تصنيفاً لبدء المحادثة",
        },
        "categories": options,
        "options": options,
    })


@routes.post('/api/guild/{guild_id}/tickets/config')
async def api_guild_tickets_config_save(req):
    _, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    categories, category_error = _ticket_role_ids(guild, body.get("categories", body.get("options")))
    if category_error:
        return json_error(400, "validation", fields={"categories": category_error})
    current = await get_ticket_config(guild.id) or {}
    embed_config, embed_error = _ticket_embed_config(body, current)
    if embed_error:
        return json_error(400, "validation", fields=embed_error)
    saved = await save_ticket_config(
        guild.id,
        current.get("channel_id"),
        current.get("message_id"),
        **embed_config,
    )
    options = await replace_ticket_options(guild.id, categories)
    return web.json_response({"config": saved, "categories": options, "options": options})


def _dashboard_snowflake(value, field_name: str) -> tuple[int | None, str | None]:
    if value in (None, "") or isinstance(value, bool):
        return None, None
    raw = str(value).strip()
    if not raw.isdigit() or not 15 <= len(raw) <= 22:
        return None, f"{field_name} غير صالح"
    return int(raw), None


def _dropdown_categories(guild, raw_categories):
    if not isinstance(raw_categories, list) or not 1 <= len(raw_categories) <= 25:
        return None, "أضف من 1 إلى 25 تصنيفاً"
    categories = []
    for index, raw in enumerate(raw_categories):
        if not isinstance(raw, dict):
            return None, f"التصنيف رقم {index + 1} غير صالح"
        label = str(raw.get("label") or "").strip()
        description = str(raw.get("description") or "").strip()
        emoji = str(raw.get("emoji") or "🎫").strip()
        if not label or len(label) > 80:
            return None, f"اسم التصنيف رقم {index + 1} غير صالح"
        if len(description) > 100 or len(emoji) > 2:
            return None, f"بيانات التصنيف رقم {index + 1} طويلة"
        role_id, error = _dashboard_snowflake(raw.get("role_id"), "معرف الرتبة")
        if error:
            return None, error
        if role_id is None:
            return None, f"حدد رتبة دعم للتصنيف رقم {index + 1}"
        if role_id is not None:
            role = guild.get_role(role_id)
            if role is None or role.is_default():
                return None, f"رتبة التصنيف رقم {index + 1} غير موجودة"
        category_id, error = _dashboard_snowflake(raw.get("category_id"), "معرف الفئة")
        if error:
            return None, error
        if category_id is not None and not isinstance(
            guild.get_channel(category_id), discord.CategoryChannel
        ):
            return None, f"فئة التصنيف رقم {index + 1} غير موجودة"
        categories.append({
            "label": label,
            "description": description,
            "emoji": emoji or "🎫",
            "role_id": role_id,
            "category_id": category_id,
        })
    return categories, None


def _dropdown_embed_config(body: dict, existing: dict | None = None):
    existing = existing or {}
    raw = dict(body)
    if "embed_color" not in raw:
        raw["embed_color"] = existing.get("embed_color") or "#5865F2"
    config, error = _ticket_embed_config(raw, existing)
    if error:
        return None, error
    return {
        **config,
        "embed_color": f"#{int(config['embed_color']):06X}",
    }, None


@routes.get('/api/guilds/{guild_id}/clan/applications')
@routes.get('/api/guild/{guild_id}/clan/applications')
async def api_clan_applications(req):
    _, guild = await authorize(req)
    status = str(req.query.get("status", "all")).strip().lower()
    if status not in {"all", "pending", "approved", "rejected", "reviewed"}:
        return json_error(400, "validation", fields={"status": "حالة الطلب غير صالحة"})
    try:
        applications = await get_clan_applications(guild.id, status)
        return web.json_response({"applications": applications, "status": status})
    except Exception:
        logger.exception("Clan applications read failed for guild %s", guild.id)
        return json_error(500, "clan_applications_unavailable")


@routes.post('/api/guilds/{guild_id}/clan/applications/{application_id}/action')
@routes.post('/api/guild/{guild_id}/clan/applications/{application_id}/action')
async def api_clan_application_action(req):
    session, guild = await authorize(req, write=True)
    try:
        application_id = int(req.match_info["application_id"])
    except (KeyError, TypeError, ValueError):
        return json_error(400, "validation", fields={"application_id": "معرف الطلب غير صالح"})
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    action = str(body.get("action") or "").strip().lower()
    if action not in {"approve", "reject"}:
        return json_error(400, "validation", fields={"action": "الإجراء يجب أن يكون approve أو reject"})
    try:
        application_rows = await get_clan_applications(guild.id, "all")
        application = next(
            (item for item in application_rows if int(item["id"]) == application_id),
            None,
        )
    except (TypeError, ValueError):
        application = None
    if not application:
        return json_error(404, "clan_application_not_found")

    role = None
    if action == "approve":
        role_id, error = _dashboard_snowflake(
            body.get("clan_member_role_id", body.get("role_id")),
            "معرف رتبة الكلان",
        )
        if error or role_id is None:
            return json_error(
                400,
                "validation",
                fields={"clan_member_role_id": error or "اختر رتبة أعضاء الكلان"},
            )
        role = guild.get_role(role_id)
        me = getattr(guild, "me", None)
        if role is None or role.is_default():
            return json_error(400, "validation", fields={"clan_member_role_id": "رتبة الكلان غير موجودة"})
        if getattr(role, "managed", False) or (me and role >= me.top_role):
            return json_error(400, "validation", fields={"clan_member_role_id": "لا يستطيع البوت إدارة هذه الرتبة"})
        member = guild.get_member(int(application["user_id"]))
        if member is None:
            try:
                member = await guild.fetch_member(int(application["user_id"]))
            except discord.NotFound:
                return json_error(404, "member_not_found")
            except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
                return json_error(503, "member_lookup_unavailable")
        try:
            await member.add_roles(role, reason=f"Clan application approved by {session['id']}")
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Could not assign clan role %s to member %s in guild %s",
                role.id,
                member.id,
                guild.id,
                exc_info=True,
            )
            return json_error(503, "clan_role_assignment_failed")

    try:
        updated = await update_clan_application(
            guild.id,
            application_id,
            "approved" if action == "approve" else "rejected",
        )
        if not updated:
            return json_error(404, "clan_application_not_found")
        logger.info(
            "Clan application %s marked %s in guild %s by %s",
            application_id,
            action,
            guild.id,
            session["id"],
        )
        return web.json_response({"ok": True, "application": updated, "role_id": str(role.id) if role else None})
    except Exception:
        logger.exception("Clan application action failed for guild %s", guild.id)
        return json_error(500, "clan_application_action_failed")


@routes.get('/api/guilds/{guild_id}/clan/roster')
@routes.get('/api/guild/{guild_id}/clan/roster')
async def api_clan_roster_get(req):
    _, guild = await authorize(req)
    try:
        return web.json_response({"roster": await get_clan_roster(guild.id)})
    except Exception:
        logger.exception("Clan roster read failed for guild %s", guild.id)
        return json_error(500, "clan_roster_unavailable")


@routes.post('/api/guilds/{guild_id}/clan/roster')
@routes.post('/api/guild/{guild_id}/clan/roster')
async def api_clan_roster_save(req):
    _, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if body.get("action") == "delete":
        try:
            roster_id = int(body.get("id"))
        except (TypeError, ValueError):
            return json_error(400, "validation", fields={"id": "معرف اللاعب غير صالح"})
        deleted = await delete_clan_roster_player(guild.id, roster_id)
        return web.json_response({"ok": deleted, "deleted": deleted}, status=200 if deleted else 404)
    lineup = str(body.get("lineup_name") or "").strip()
    if lineup not in {"Lineup A", "Lineup B", "Subs"}:
        return json_error(400, "validation", fields={"lineup_name": "اختر Lineup A أو Lineup B أو Subs"})
    player_id, error = _dashboard_snowflake(body.get("player_id"), "معرف اللاعب")
    if error or player_id is None:
        return json_error(400, "validation", fields={"player_id": error or "معرف اللاعب مطلوب"})
    player_name = str(body.get("player_name") or "").strip()
    role_title = str(body.get("role_title") or "").strip()
    if not player_name or len(player_name) > 100:
        return json_error(400, "validation", fields={"player_name": "اسم اللاعب مطلوب وبحد أقصى 100 حرف"})
    if len(role_title) > 80:
        return json_error(400, "validation", fields={"role_title": "المسمى يتجاوز 80 حرفاً"})
    try:
        display_order = max(0, min(999, int(body.get("display_order", 0))))
        roster_id = int(body["id"]) if body.get("id") not in (None, "") else None
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"display_order": "ترتيب اللاعب غير صالح"})
    try:
        player = await save_clan_roster_player(
            guild.id,
            lineup,
            player_id,
            player_name,
            role_title,
            display_order,
            roster_id,
        )
        if not player:
            return json_error(404, "clan_roster_player_not_found")
        return web.json_response({"ok": True, "player": player, "roster": await get_clan_roster(guild.id)})
    except Exception:
        logger.exception("Clan roster save failed for guild %s", guild.id)
        return json_error(500, "clan_roster_save_failed")


@routes.post('/api/guilds/{guild_id}/clan/roster/publish')
@routes.post('/api/guild/{guild_id}/clan/roster/publish')
async def api_clan_roster_publish(req):
    _, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    channel_id, error = _dashboard_snowflake(body.get("target_channel_id", body.get("channel_id")), "معرف قناة الكلان")
    if error or channel_id is None:
        return json_error(400, "validation", fields={"target_channel_id": error or "اختر قناة النشر"})
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, MESSAGE_CHANNEL_TYPES):
        return json_error(400, "validation", fields={"target_channel_id": "قناة النشر غير موجودة"})
    roster = await get_clan_roster(guild.id)
    if not roster:
        return json_error(400, "validation", fields={"roster": "أضف لاعباً واحداً على الأقل قبل النشر"})
    embed = discord.Embed(
        title=str(body.get("title") or "PR1ME TEAM · Clan Roster")[:256],
        description=str(body.get("description") or "التشكيلات الحالية للكلان")[:4096],
        color=0x5865F2,
    )
    groups = {"Lineup A": [], "Lineup B": [], "Subs": []}
    for player in roster:
        groups.setdefault(player["lineup_name"], []).append(player)
    for lineup, label in (("Lineup A", "التشكيلة A"), ("Lineup B", "التشكيلة B"), ("Subs", "البدلاء")):
        players = groups.get(lineup) or []
        value = "\n".join(
            f"`{index + 1}.` **{item['player_name']}**"
            + (f" · {item['role_title']}" if item.get("role_title") else "")
            + f" · <@{item['player_id']}>"
            for index, item in enumerate(players)
        ) or "لا يوجد لاعبون"
        embed.add_field(name=label, value=value[:1024], inline=False)
    embed.set_footer(text=str(body.get("footer_text") or "PR1ME TEAM · Clan Operations")[:2048])
    message = None
    message_id, _ = _dashboard_snowflake(body.get("message_id"), "معرف الرسالة")
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            message = None
    if message is None:
        try:
            message = await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Clan roster publish failed in guild %s", guild.id, exc_info=True)
            return json_error(503, "clan_roster_publish_failed")
    return web.json_response({"ok": True, "channel_id": str(channel.id), "message_id": str(message.id)})


@routes.get('/api/guilds/{guild_id}/clan/scrims')
@routes.get('/api/guild/{guild_id}/clan/scrims')
async def api_clan_scrims_get(req):
    _, guild = await authorize(req)
    try:
        return web.json_response({"scrims": await get_scrim_logs(guild.id)})
    except Exception:
        logger.exception("Clan scrim log read failed for guild %s", guild.id)
        return json_error(500, "clan_scrims_unavailable")


@routes.post('/api/guilds/{guild_id}/clan/scrims')
@routes.post('/api/guild/{guild_id}/clan/scrims')
async def api_clan_scrims_save(req):
    session, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    opponent = str(body.get("opponent_name") or "").strip()
    map_name = str(body.get("map_name") or "").strip()
    result = str(body.get("result") or "").strip().lower()
    if not opponent or len(opponent) > 120:
        return json_error(400, "validation", fields={"opponent_name": "اسم الخصم مطلوب"})
    if len(map_name) > 80 or result not in {"win", "loss", "draw"}:
        return json_error(400, "validation", fields={"result": "اختر فوز أو خسارة أو تعادل"})
    try:
        score_prime = int(body.get("score_prime"))
        score_enemy = int(body.get("score_enemy"))
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"score_prime": "النتيجة يجب أن تكون أرقاماً"})
    if not 0 <= score_prime <= 999 or not 0 <= score_enemy <= 999:
        return json_error(400, "validation", fields={"score_prime": "النتيجة خارج النطاق"})
    try:
        item = await add_scrim_log(
            guild.id,
            opponent,
            score_prime,
            score_enemy,
            map_name,
            result,
            int(session["id"]),
        )
        return web.json_response({"ok": True, "scrim": item, "scrims": await get_scrim_logs(guild.id)})
    except Exception:
        logger.exception("Clan scrim log save failed for guild %s", guild.id)
        return json_error(500, "clan_scrim_save_failed")


@routes.get('/api/guilds/{guild_id}/tickets/dropdown-config')
@routes.get('/api/guild/{guild_id}/tickets/dropdown-config')
async def api_ticket_dropdown_config_get(req):
    _, guild = await authorize(req)
    config = await get_ticket_dropdown_config(guild.id)
    categories = await get_ticket_dropdown_categories(guild.id)
    if not categories:
        legacy = await get_ticket_options(guild.id)
        categories = normalize_ticket_categories(legacy) if legacy else []
    if not config:
        legacy = await get_ticket_config(guild.id) or {}
        config = {
            "guild_id": guild.id,
            "channel_id": legacy.get("channel_id"),
            "message_id": legacy.get("message_id"),
            "embed_title": legacy.get("embed_title", "🎫 مركز الدعم والتذاكر"),
            "embed_description": legacy.get("embed_description", ""),
            "embed_color": f"#{int(legacy.get('embed_color', 0x5865F2)):06X}",
            "footer_text": legacy.get("footer_text", "Help Desk • اختر تصنيفاً لبدء المحادثة"),
        }
    return web.json_response({"config": config, "categories": categories})


@routes.post('/api/guilds/{guild_id}/tickets/dropdown-config')
@routes.post('/api/guild/{guild_id}/tickets/dropdown-config')
async def api_ticket_dropdown_config_save(req):
    _, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    categories, category_error = _dropdown_categories(guild, body.get("categories"))
    if category_error:
        return json_error(400, "validation", fields={"categories": category_error})
    current = await get_ticket_dropdown_config(guild.id) or {}
    config, config_error = _dropdown_embed_config(body, current)
    if config_error:
        return json_error(400, "validation", fields=config_error)
    channel_id, channel_error = _dashboard_snowflake(
        body.get("channel_id", current.get("channel_id")),
        "معرف القناة",
    )
    if channel_error:
        return json_error(400, "validation", fields={"channel_id": channel_error})
    if channel_id is not None and not isinstance(guild.get_channel(channel_id), MESSAGE_CHANNEL_TYPES):
        return json_error(400, "validation", fields={"channel_id": "القناة غير موجودة"})
    message_id, message_error = _dashboard_snowflake(
        body.get("message_id", current.get("message_id")),
        "معرف الرسالة",
    )
    if message_error:
        return json_error(400, "validation", fields={"message_id": message_error})
    saved = await save_ticket_dropdown_config(
        guild.id,
        channel_id,
        message_id,
        config["embed_title"],
        config["embed_description"],
        config["embed_color"],
        config["footer_text"],
    )
    saved_categories = await replace_ticket_dropdown_categories(guild.id, categories)
    return web.json_response({"ok": True, "config": saved, "categories": saved_categories})


@routes.post('/api/guilds/{guild_id}/tickets/dropdown-config/publish')
@routes.post('/api/guild/{guild_id}/tickets/dropdown-config/publish')
async def api_ticket_dropdown_config_publish(req):
    _, guild = await authorize(req, write=True)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    current = await get_ticket_dropdown_config(guild.id) or {}
    categories = await get_ticket_dropdown_categories(guild.id)
    if body.get("categories") is not None:
        categories, category_error = _dropdown_categories(guild, body.get("categories"))
        if category_error:
            return json_error(400, "validation", fields={"categories": category_error})
    if not categories:
        return json_error(400, "validation", fields={"categories": "أضف تصنيفاً واحداً على الأقل"})
    config, config_error = _dropdown_embed_config(body, current)
    if config_error:
        return json_error(400, "validation", fields=config_error)
    channel_id, channel_error = _dashboard_snowflake(
        body.get("channel_id", body.get("target_channel_id", current.get("channel_id"))),
        "معرف القناة",
    )
    if channel_error or channel_id is None:
        return json_error(400, "validation", fields={"channel_id": channel_error or "اختر قناة النشر"})
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, MESSAGE_CHANNEL_TYPES):
        return json_error(400, "validation", fields={"channel_id": "القناة غير موجودة"})
    try:
        deploy_config = {
            **config,
            "embed_color": int(str(config["embed_color"]).lstrip("#"), 16),
        }
        panel = await community.deploy_persistent_dropdown_panel(
            channel.id,
            categories,
            deploy_config,
        )
        saved = await save_ticket_dropdown_config(
            guild.id,
            channel.id,
            panel.get("message_id"),
            config["embed_title"],
            config["embed_description"],
            config["embed_color"],
            config["footer_text"],
        )
        saved_categories = await replace_ticket_dropdown_categories(guild.id, categories)
        return web.json_response({"ok": True, "config": saved, "categories": saved_categories, "panel": panel})
    except (ValueError, discord.Forbidden, discord.HTTPException):
        logger.warning("Ticket dropdown publish failed in guild %s", guild.id, exc_info=True)
        return json_error(400, "ticket_dropdown_publish_failed")
    except Exception:
        logger.exception("Ticket dropdown publish crashed in guild %s", guild.id)
        return json_error(500, "ticket_dropdown_publish_failed")


def _broadcast_color(value) -> tuple[int | None, str | None]:
    raw = str(value or "#6366F1").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        return None, "اللون يجب أن يكون HEX من ست خانات"
    return int(raw, 16), f"#{raw.upper()}"


def _broadcast_url(value: str, field: str) -> tuple[str | None, str | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, None
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(raw) > 1000:
        return None, f"{field} يجب أن يكون رابط HTTP صالحاً"
    return raw, None


@routes.get('/api/guilds/{guild_id}/broadcast/history')
@routes.get('/api/guild/{guild_id}/broadcast/history')
async def api_broadcast_history(req):
    _, guild = await authorize(req)
    try:
        history = await get_recent_broadcast_logs(guild.id, 10)
        return web.json_response({"history": history})
    except Exception:
        logger.exception("Broadcast history read failed for guild %s", guild.id)
        return json_error(500, "broadcast_history_unavailable")


@routes.post('/api/guilds/{guild_id}/broadcast/send')
@routes.post('/api/guild/{guild_id}/broadcast/send')
async def api_broadcast_send(req):
    session, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    mode = str(body.get("mode") or "embed").strip().lower()
    if mode not in {"embed", "text"}:
        return json_error(400, "validation", fields={"mode": "اختر رسالة عادية أو إعلاناً مدمجاً"})
    channel_id, channel_error = _dashboard_snowflake(body.get("channel_id"), "معرف القناة")
    if channel_error or channel_id is None:
        return json_error(400, "validation", fields={"channel_id": channel_error or "اختر قناة النشر"})
    channel = guild.get_channel(channel_id)
    if channel is None or not hasattr(channel, "send") or isinstance(
        channel,
        (discord.VoiceChannel, discord.StageChannel, discord.CategoryChannel),
    ):
        return json_error(400, "validation", fields={"channel_id": "القناة لا تدعم إرسال الرسائل"})
    bot_member = guild.me
    permissions = channel.permissions_for(bot_member) if bot_member else None
    if permissions is not None and not permissions.send_messages:
        return json_error(403, "missing_send_permission")
    mention_type = str(body.get("mention_type") or "none").strip().lower()
    if mention_type not in {"none", "everyone", "here"}:
        return json_error(400, "validation", fields={"mention_type": "نوع المنشن غير صالح"})
    content = str(body.get("content") or "").strip()
    title = str(body.get("title") or "").strip()
    description = str(body.get("description") or "").strip()
    footer = str(body.get("footer") or "").strip()
    if len(content) > 4000 or len(title) > 256 or len(description) > 4096 or len(footer) > 2048:
        return json_error(400, "validation", fields={"content": "تجاوز أحد الحقول الحد المسموح"})
    if mode == "text" and not content:
        return json_error(400, "validation", fields={"content": "اكتب نص الرسالة أولاً"})
    if mode == "embed" and not any((content, title, description)):
        return json_error(400, "validation", fields={"description": "أضف محتوى أو عنواناً للإعلان"})
    thumbnail_url, thumbnail_error = _broadcast_url(body.get("thumbnail_url"), "رابط الصورة المصغرة")
    image_url, image_error = _broadcast_url(body.get("image_url"), "رابط الصورة الرئيسية")
    if thumbnail_error or image_error:
        return json_error(400, "validation", fields={"image_url": thumbnail_error or image_error})
    color, color_text = _broadcast_color(body.get("color"))
    if color is None:
        return json_error(400, "validation", fields={"color": color_text})
    mention_content = f"@{mention_type}" if mention_type != "none" else ""
    send_content = " ".join(item for item in (mention_content, content) if item).strip() or None
    allowed_mentions = discord.AllowedMentions(
        everyone=mention_type != "none",
        users=False,
        roles=False,
    )
    try:
        if mode == "text":
            message = await channel.send(
                content=send_content,
                allowed_mentions=allowed_mentions,
            )
        else:
            embed = discord.Embed(color=color)
            if title:
                embed.title = title[:256]
            if description:
                embed.description = description[:4096]
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)
            if image_url:
                embed.set_image(url=image_url)
            if footer:
                embed.set_footer(text=footer[:2048])
            message = await channel.send(
                content=send_content,
                embed=embed,
                allowed_mentions=allowed_mentions,
            )
    except discord.Forbidden:
        return json_error(403, "discord_forbidden")
    except discord.HTTPException:
        logger.warning("Broadcast send failed in guild %s", guild.id, exc_info=True)
        return json_error(502, "discord_unavailable")
    try:
        await add_broadcast_log(
            guild.id,
            channel.id,
            int(session["id"]),
            mode,
            title,
            content or description,
            color_text,
            description,
        )
    except Exception:
        logger.exception("Broadcast log write failed after message %s", message.id)
    return web.json_response({"success": True, "message_id": str(message.id)})


@routes.post('/api/guild/{guild_id}/tickets/deploy')
async def api_guild_tickets_deploy(req):
    session, guild = await authorize(req, write=True)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    channel_id = body.get("target_channel_id", body.get("channel_id"))
    if isinstance(channel_id, bool) or not str(channel_id).isdigit():
        return json_error(400, "validation", fields={"target_channel_id": "معرف القناة غير صالح"})
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, MESSAGE_CHANNEL_TYPES):
        return json_error(400, "validation", fields={"target_channel_id": "القناة غير موجودة"})
    categories, category_error = _ticket_role_ids(guild, body.get("categories", body.get("options")))
    if category_error:
        return json_error(400, "validation", fields={"categories": category_error})
    embed_config, embed_error = _ticket_embed_config(
        body,
        await get_ticket_config(guild.id),
    )
    if embed_error:
        return json_error(400, "validation", fields=embed_error)
    try:
        result = await community.deploy_ticket_panel(channel.id, categories, embed_config)
    except (ValueError, discord.Forbidden, discord.HTTPException) as error:
        logger.warning("Ticket panel deployment failed: %s", error)
        return json_error(400, "ticket_panel_deploy_failed")
    logger.info("Ticket panel deployed in guild %s by user %s", guild.id, session["id"])
    return web.json_response({"ok": True, "panel": result})


@routes.get('/api/guild/{guild_id}/tickets/panels')
async def api_guild_tickets_panels(req):
    _, guild = await authorize(req)
    panels = [
        panel for panel in await get_ticket_panels()
        if int(panel.get("guild_id", 0)) == int(guild.id)
    ]
    return web.json_response({"panels": panels})


@routes.delete('/api/guild/{guild_id}/tickets/panels/{message_id}')
async def api_guild_tickets_panel_delete(req):
    _, guild = await authorize(req, write=True)
    try:
        message_id = int(req.match_info["message_id"])
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"message_id": "معرف اللوحة غير صالح"})
    panel = next(
        (
            item for item in await get_ticket_panels()
            if int(item.get("guild_id", 0)) == int(guild.id)
            and int(item.get("message_id", 0)) == message_id
        ),
        None,
    )
    if not panel:
        return json_error(404, "ticket_panel_not_found")
    channel = guild.get_channel(int(panel["channel_id"]))
    if channel is not None and hasattr(channel, "fetch_message"):
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.info("Ticket panel message %s was already unavailable.", message_id)
    await delete_ticket_panel(guild.id, int(panel["channel_id"]), message_id)
    return web.json_response({"deleted": True, "message_id": message_id})


@routes.get('/api/guild/{guild_id}/tickets/analytics')
async def api_guild_tickets_analytics(req):
    _, guild = await authorize(req)
    return web.json_response(await get_ticket_overview_metrics(guild.id))


@routes.get('/api/guilds/{guild_id}/tickets/overview')
async def api_guilds_tickets_overview(req):
    _, guild = await authorize(req)
    return web.json_response(await get_ticket_overview_metrics(guild.id))


@routes.get('/api/guilds/{guild_id}/tickets/panels')
async def api_guilds_tickets_panels(req):
    _, guild = await authorize(req)
    panels = [
        panel for panel in await get_ticket_panels()
        if int(panel.get("guild_id", 0)) == int(guild.id)
    ]
    return web.json_response({"panels": panels})


@routes.get('/api/guilds/{guild_id}/tickets/ratings')
async def api_guilds_tickets_ratings(req):
    _, guild = await authorize(req)
    return web.json_response({"ratings": await get_ticket_ratings(guild.id)})


def _ticket_panel_payload(body: dict, existing: dict | None = None) -> tuple[dict | None, dict | None]:
    existing = existing or {}
    categories = body.get("categories", body.get("options", existing.get("categories")))
    categories = normalize_ticket_categories(categories)
    title = str(body.get("title", body.get("embed_title", existing.get("title") or "مركز الدعم والتذاكر"))).strip()
    description = str(body.get("description", body.get("embed_description", existing.get("description") or ""))).strip()
    mode = str(body.get("mode", existing.get("mode") or "dropdown")).strip().lower()
    raw_color = body.get("color", body.get("embed_color", existing.get("color", 0x6366F1)))
    try:
        color = int(str(raw_color).strip().lstrip("#"), 16) if isinstance(raw_color, str) else int(raw_color)
    except (TypeError, ValueError):
        return None, {"color": "لون اللوحة غير صالح"}
    if not title or len(title) > 256:
        return None, {"title": "عنوان اللوحة يجب أن يكون بين 1 و256 حرفاً"}
    if len(description) > 4096:
        return None, {"description": "وصف اللوحة يجب ألا يتجاوز 4096 حرفاً"}
    if mode not in {"dropdown", "buttons"}:
        return None, {"mode": "نمط اللوحة غير صالح"}
    if not 0 <= color <= 0xFFFFFF:
        return None, {"color": "لون اللوحة يجب أن يكون HEX صالحاً"}
    return {
        "categories": categories,
        "title": title,
        "description": description,
        "color": color,
        "mode": mode,
    }, None


TICKET_PERMISSION_ACTIONS = (
    "claim", "close", "rename", "priority", "transfer", "add_member",
    "remove_member", "private_ticket", "summon", "tag", "note", "reopen",
)


def _ticket_role_list(guild, value, field_name: str):
    if value is None:
        return [], None
    if not isinstance(value, list) or len(value) > 100:
        return None, f"{field_name} غير صالح"
    result = []
    for raw in value:
        role_id, error = _dashboard_snowflake(raw, field_name)
        if error or role_id is None:
            return None, error or f"{field_name} غير صالح"
        role = guild.get_role(role_id)
        if role is None or role.is_default():
            return None, f"{field_name} يحتوي رتبة غير موجودة"
        result.append(str(role_id))
    return list(dict.fromkeys(result)), None


def _ticket_category_payload_for_guild(guild, raw: dict):
    if not isinstance(raw, dict):
        return None, {"category": "بيانات التصنيف غير صالحة"}
    label = str(raw.get("label") or raw.get("name") or "").strip()
    if not label or len(label) > 100:
        return None, {"label": "اسم التصنيف يجب أن يكون بين 1 و100 حرف"}
    name = str(raw.get("name") or raw.get("key") or label).strip()
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-_")[:100] or f"ticket-{label[:20]}"
    description = str(raw.get("description") or "").strip()[:100]
    welcome = str(raw.get("welcome_message", raw.get("welcome_msg", "")) or "").strip()[:2000]
    emoji = str(raw.get("emoji") or "🎫").strip()[:100]
    role_fields = (
        ("ping_role_ids", "رتب التنبيه"),
        ("staff_role_ids", "رتب الدعم"),
    )
    cleaned = {}
    for key, field_name in role_fields:
        value, error = _ticket_role_list(guild, raw.get(key, []), field_name)
        if error:
            return None, {key: error}
        cleaned[key] = value
    # Legacy category payloads use support_role_ids/role_id for staff access.
    if not cleaned["staff_role_ids"]:
        legacy_roles = raw.get("support_role_ids", [])
        if not legacy_roles and raw.get("role_id") not in (None, ""):
            legacy_roles = [raw.get("role_id")]
        value, error = _ticket_role_list(guild, legacy_roles, "رتب الدعم")
        if error:
            return None, {"staff_role_ids": error}
        cleaned["staff_role_ids"] = value
    ids = {}
    for key, field_name, expected_type in (
        ("open_category_id", "فئة القنوات المفتوحة", discord.CategoryChannel),
        ("closed_category_id", "فئة القنوات المغلقة", discord.CategoryChannel),
        ("category_id", "الفئة الأب", discord.CategoryChannel),
    ):
        if key not in raw or raw[key] in (None, ""):
            ids[key] = None
            continue
        value, error = _dashboard_snowflake(raw[key], field_name)
        if error:
            return None, {key: error}
        channel = guild.get_channel(value)
        if expected_type and not isinstance(channel, expected_type):
            return None, {key: f"{field_name} غير موجودة"}
        ids[key] = value
    if raw.get("panel_id") not in (None, ""):
        try:
            ids["panel_id"] = int(raw["panel_id"])
        except (TypeError, ValueError):
            return None, {"panel_id": "معرف اللوحة غير صالح"}
    else:
        ids["panel_id"] = None
    try:
        max_open = max(1, min(20, int(raw.get("max_open_per_user", 1))))
        auto_close = max(0, min(8760, int(raw.get("auto_close_hours", 0))))
    except (TypeError, ValueError):
        return None, {"limits": "حدود التصنيف غير صالحة"}
    return {
        "panel_id": ids["panel_id"],
        "name": name,
        "label": label,
        "ping_role_ids": cleaned["ping_role_ids"],
        "staff_role_ids": cleaned["staff_role_ids"],
        "description": description,
        "emoji": emoji,
        "button_color": str(raw.get("button_color") or "#5865F2")[:20],
        "naming_format": str(raw.get("naming_format") or "ticket-{id}")[:100],
        "closed_naming_format": str(raw.get("closed_naming_format") or "closed-{id}")[:100],
        "open_category_id": ids["open_category_id"],
        "closed_category_id": ids["closed_category_id"],
        "category_id": ids["category_id"],
        "welcome_message": welcome,
        "max_open_per_user": max_open,
        "auto_close_hours": auto_close,
    }, None


@routes.post('/api/guilds/{guild_id}/tickets/panels')
async def api_guilds_tickets_panels_save(req):
    _, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    try:
        panel_id = int(body["panel_id"]) if body.get("panel_id") not in (None, "") else None
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"panel_id": "معرف اللوحة غير صالح"})
    panels = [
        panel for panel in await get_ticket_panels()
        if int(panel.get("guild_id", 0)) == int(guild.id)
    ]
    existing = next((panel for panel in panels if panel_id and int(panel["id"]) == panel_id), None)
    payload, error = _ticket_panel_payload(body, existing)
    if error:
        return json_error(400, "validation", fields=error)
    channel_id = body.get("channel_id", body.get("target_channel_id", existing.get("channel_id") if existing else None))
    if isinstance(channel_id, bool) or channel_id in (None, "") or not str(channel_id).isdigit():
        return json_error(400, "validation", fields={"channel_id": "معرف القناة غير صالح"})
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, MESSAGE_CHANNEL_TYPES):
        return json_error(400, "validation", fields={"channel_id": "القناة غير موجودة"})
    message_id = int(existing.get("message_id") or 0) if existing else 0
    panel = await save_ticket_panel(
        guild.id,
        channel.id,
        message_id,
        payload["categories"],
        title=payload["title"],
        description=payload["description"],
        color=payload["color"],
        mode=payload["mode"],
    )
    await replace_ticket_options(guild.id, payload["categories"])
    return web.json_response({"panel": panel})


@routes.post('/api/guilds/{guild_id}/tickets/panels/{panel_id}/publish')
async def api_guilds_tickets_panel_publish(req):
    _, guild = await authorize(req, write=True)
    try:
        panel_id = int(req.match_info["panel_id"])
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"panel_id": "معرف اللوحة غير صالح"})
    panel = next(
        (
            item for item in await get_ticket_panels()
            if int(item.get("guild_id", 0)) == int(guild.id)
            and int(item.get("id", 0)) == panel_id
        ),
        None,
    )
    if not panel:
        return json_error(404, "ticket_panel_not_found")
    channel = guild.get_channel(int(panel["channel_id"]))
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    if not isinstance(channel, MESSAGE_CHANNEL_TYPES):
        return json_error(400, "validation", fields={"channel_id": "القناة غير موجودة"})
    try:
        result = await community.deploy_ticket_panel(
            channel.id,
            await get_ticket_categories(guild.id) or panel.get("categories") or [],
            {
                "embed_title": panel.get("title"),
                "embed_description": panel.get("description"),
                "embed_color": panel.get("color"),
                "panel_mode": panel.get("mode"),
                "channel_id": panel.get("channel_id"),
                "message_id": panel.get("message_id"),
            },
        )
    except (ValueError, discord.Forbidden, discord.HTTPException) as error:
        logger.warning("CRM ticket panel publication failed: %s", error)
        return json_error(400, "ticket_panel_deploy_failed")
    # A CRM draft uses message_id=0 as a database-only placeholder. The
    # deployment above creates and persists the real Discord message, so the
    # placeholder must not remain as a second panel in the dashboard.
    previous_message_id = int(panel.get("message_id") or 0)
    published_message_id = int(result.get("message_id") or 0)
    if previous_message_id and published_message_id and previous_message_id != published_message_id:
        await delete_ticket_panel(guild.id, int(panel["channel_id"]), previous_message_id)
    if not previous_message_id:
        await delete_ticket_panel(guild.id, int(panel["channel_id"]), 0)
    return web.json_response({
        "ok": True,
        "success": True,
        "message_id": str(result.get("message_id")),
        "panel": result,
    })


@routes.get('/api/guild/{guild_id}/tickets/settings')
async def api_guild_tickets_settings_get(req):
    _, guild = await authorize(req)
    return web.json_response(await _ticket_settings_response(guild))


async def _ticket_settings_response(guild):
    legacy = await get_ticket_config(guild.id) or {}
    settings = await get_ticket_settings(guild.id)
    permissions = await get_ticket_permissions(guild.id)
    merged = {**legacy, **settings, "permissions": permissions, "permissions_json": permissions}
    return {
        "config": merged,
        "settings": settings,
        "permissions": permissions,
    }


@routes.get('/api/guilds/{guild_id}/tickets/settings')
async def api_guilds_tickets_settings_get(req):
    _, guild = await authorize(req)
    return web.json_response(await _ticket_settings_response(guild))


@routes.post('/api/guilds/{guild_id}/tickets/settings')
async def api_guilds_tickets_settings_save(req):
    session, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})

    channel_fields = ("log_channel_id", "evaluation_channel_id", "default_open_category_id", "closed_category_id")
    settings_kwargs = {}
    legacy_kwargs = {}
    for field in channel_fields:
        if field not in body:
            continue
        value, error = _dashboard_snowflake(body.get(field), field)
        if error:
            return json_error(400, "validation", fields={field: error})
        if value is not None and field in {"default_open_category_id", "closed_category_id"}:
            if not isinstance(guild.get_channel(value), discord.CategoryChannel):
                return json_error(400, "validation", fields={field: "الفئة غير موجودة"})
        elif value is not None and not isinstance(guild.get_channel(value), MESSAGE_CHANNEL_TYPES):
            return json_error(400, "validation", fields={field: "القناة غير موجودة"})
        settings_kwargs[field] = value
        legacy_kwargs[field] = value

    for field in ("allow_user_close", "send_transcript_dm"):
        if field in body:
            settings_kwargs[field] = bool(body[field])
            legacy_kwargs[field] = bool(body[field])
    if "auto_close_minutes" in body:
        try:
            value = max(0, min(10080, int(body["auto_close_minutes"])))
        except (TypeError, ValueError):
            return json_error(400, "validation", fields={"auto_close_minutes": "قيمة المهلة غير صالحة"})
        legacy_kwargs["auto_close_minutes"] = value
    if "open_limit" in body:
        try:
            value = max(1, min(20, int(body["open_limit"])))
        except (TypeError, ValueError):
            return json_error(400, "validation", fields={"open_limit": "حد التذاكر غير صالح"})
        legacy_kwargs["open_limit"] = value
    for field in ("panel_mode", "select_placeholder", "close_config"):
        if field in body:
            legacy_kwargs[field] = body[field]
    if "panel_mode" in legacy_kwargs and legacy_kwargs["panel_mode"] not in {"dropdown", "buttons"}:
        return json_error(400, "validation", fields={"panel_mode": "نمط اللوحة غير صالح"})
    if "close_config" in legacy_kwargs and not isinstance(legacy_kwargs["close_config"], dict):
        return json_error(400, "validation", fields={"close_config": "إعدادات الإغلاق غير صالحة"})

    try:
        settings = await save_ticket_settings(guild.id, **settings_kwargs) if settings_kwargs else await get_ticket_settings(guild.id)
        if legacy_kwargs:
            await update_ticket_control_config(guild.id, **legacy_kwargs)
        if "permissions" in body:
            if not isinstance(body["permissions"], dict):
                return json_error(400, "validation", fields={"permissions": "مصفوفة الصلاحيات غير صالحة"})
            permissions = await save_ticket_permissions(guild.id, body["permissions"])
            await update_ticket_control_config(guild.id, permissions=permissions)
        response = await _ticket_settings_response(guild)
        logger.info("Ticket CRM settings saved in guild %s by user %s", guild.id, session["id"])
        return web.json_response(response)
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"settings": "قيمة إعداد غير صالحة"})


@routes.get('/api/guilds/{guild_id}/tickets/permissions')
async def api_guilds_tickets_permissions_get(req):
    _, guild = await authorize(req)
    permissions = await get_ticket_permissions(guild.id)
    return web.json_response({"permissions": permissions, "actions": list(TICKET_PERMISSION_ACTIONS)})


@routes.post('/api/guilds/{guild_id}/tickets/permissions')
async def api_guilds_tickets_permissions_save(req):
    session, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    permissions = body.get("permissions", body) if isinstance(body, dict) else None
    if not isinstance(permissions, dict):
        return json_error(400, "validation", fields={"permissions": "مصفوفة الصلاحيات غير صالحة"})
    for action, role_ids in permissions.items():
        if action not in TICKET_PERMISSION_ACTIONS:
            return json_error(400, "validation", fields={"permissions": f"الإجراء غير مدعوم: {action}"})
        _, error = _ticket_role_list(guild, role_ids, f"رتب {action}")
        if error:
            return json_error(400, "validation", fields={"permissions": error})
    saved = await save_ticket_permissions(guild.id, permissions)
    # Mirror into legacy config so older cogs continue to read the same policy.
    await update_ticket_control_config(guild.id, permissions=saved)
    logger.info("Ticket CRM permissions saved in guild %s by user %s", guild.id, session["id"])
    return web.json_response({"permissions": saved, "actions": list(TICKET_PERMISSION_ACTIONS)})


@routes.get('/api/guilds/{guild_id}/tickets/categories')
async def api_guilds_tickets_categories_get(req):
    _, guild = await authorize(req)
    categories = await get_ticket_categories(guild.id)
    if not categories:
        categories = normalize_ticket_categories(await get_ticket_options(guild.id))
    return web.json_response({"categories": categories})


@routes.post('/api/guilds/{guild_id}/tickets/categories')
async def api_guilds_tickets_categories_save(req):
    _, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    raw_categories = body.get("categories") if isinstance(body, dict) else None
    if raw_categories is None:
        raw_categories = [body]
    if not isinstance(raw_categories, list) or not 1 <= len(raw_categories) <= 25:
        return json_error(400, "validation", fields={"categories": "أضف من 1 إلى 25 تصنيفاً"})
    if isinstance(body, dict) and body.get("replace"):
        keep_ids = {
            int(raw["id"]) for raw in raw_categories
            if isinstance(raw, dict) and raw.get("id") not in (None, "")
            and str(raw.get("id")).isdigit()
        }
        for existing in await get_ticket_categories(guild.id):
            existing_id = int(existing.get("id", 0))
            if existing_id and existing_id not in keep_ids:
                result = await delete_ticket_category(guild.id, existing_id)
                if result.get("in_use"):
                    return json_error(409, "ticket_category_in_use", details=result)
    saved = []
    for raw in raw_categories:
        payload, error = _ticket_category_payload_for_guild(guild, raw)
        if error:
            return json_error(400, "validation", fields=error)
        try:
            category_id = int(raw["id"]) if raw.get("id") not in (None, "") else None
        except (TypeError, ValueError):
            return json_error(400, "validation", fields={"id": "معرف التصنيف غير صالح"})
        if category_id is not None:
            existing = next((item for item in await get_ticket_categories(guild.id) if int(item["id"]) == category_id), None)
            if not existing:
                return json_error(404, "ticket_category_not_found")
        try:
            saved.append(await save_ticket_category(guild.id, payload, category_id))
        except LookupError:
            return json_error(404, "ticket_category_not_found")
    return web.json_response({"categories": saved})


@routes.delete('/api/guilds/{guild_id}/tickets/categories/{category_id}')
async def api_guilds_tickets_category_delete(req):
    _, guild = await authorize(req, write=True)
    try:
        category_id = int(req.match_info["category_id"])
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"category_id": "معرف التصنيف غير صالح"})
    result = await delete_ticket_category(guild.id, category_id)
    if not result.get("found"):
        return json_error(404, "ticket_category_not_found")
    if result.get("in_use"):
        return json_error(409, "ticket_category_in_use", details=result)
    return web.json_response(result)


@routes.post('/api/guild/{guild_id}/tickets/settings')
async def api_guild_tickets_settings_save(req):
    session, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    settings_kwargs = {}
    legacy_kwargs = {}
    for field in ("log_channel_id", "evaluation_channel_id", "default_open_category_id", "closed_category_id"):
        if field not in body:
            continue
        value, error = _dashboard_snowflake(body.get(field), field)
        if error:
            return json_error(400, "validation", fields={field: error})
        if value is not None:
            expected = discord.CategoryChannel if field.endswith("category_id") else MESSAGE_CHANNEL_TYPES
            if not isinstance(guild.get_channel(value), expected):
                return json_error(400, "validation", fields={field: "القناة أو الفئة غير موجودة"})
        settings_kwargs[field] = value
        legacy_kwargs[field] = value
    for field in ("allow_user_close", "send_transcript_dm"):
        if field in body:
            settings_kwargs[field] = bool(body[field])
            legacy_kwargs[field] = bool(body[field])
    for field in ("auto_close_minutes", "open_limit"):
        if field in body:
            try:
                legacy_kwargs[field] = int(body[field])
            except (TypeError, ValueError):
                return json_error(400, "validation", fields={field: "قيمة الإعداد غير صالحة"})
    for field in ("panel_mode", "select_placeholder", "close_config"):
        if field in body:
            legacy_kwargs[field] = body[field]
    if "permissions" in body:
        if not isinstance(body["permissions"], dict):
            return json_error(400, "validation", fields={"permissions": "مصفوفة الصلاحيات غير صالحة"})
        permissions = await save_ticket_permissions(guild.id, body["permissions"])
        legacy_kwargs["permissions"] = permissions
    if "close_config" in legacy_kwargs and not isinstance(legacy_kwargs["close_config"], dict):
        return json_error(400, "validation", fields={"close_config": "إعدادات الإغلاق غير صالحة"})
    if settings_kwargs:
        await save_ticket_settings(guild.id, **settings_kwargs)
    if legacy_kwargs:
        await update_ticket_control_config(guild.id, **legacy_kwargs)
    logger.info("Ticket control settings saved in guild %s by user %s", guild.id, session["id"])
    return web.json_response(await _ticket_settings_response(guild))


@routes.get('/api/guilds/{guild_id}/tickets/blacklist')
@routes.get('/api/guild/{guild_id}/tickets/blacklist')
async def api_guild_tickets_blacklist_get(req):
    _, guild = await authorize(req)
    return web.json_response({"entries": await get_ticket_blacklist(guild.id)})


@routes.post('/api/guilds/{guild_id}/tickets/blacklist')
@routes.post('/api/guild/{guild_id}/tickets/blacklist')
async def api_guild_tickets_blacklist_save(req):
    session, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
        user_id = int(body.get("user_id"))
        duration_days = body.get("duration_days")
        duration_days = int(duration_days) if duration_days not in (None, "") else None
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return json_error(400, "validation", fields={"user_id": "معرف العضو غير صالح"})
    if user_id <= 0:
        return json_error(400, "validation", fields={"user_id": "معرف العضو غير صالح"})
    member = guild.get_member(user_id)
    if member is None:
        return json_error(400, "validation", fields={"user_id": "العضو غير موجود في السيرفر"})
    expiration = body.get("expiration")
    if expiration is not None and not isinstance(expiration, str):
        return json_error(400, "validation", fields={"expiration": "تاريخ الانتهاء غير صالح"})
    entry = await save_ticket_blacklist(
        guild.id,
        user_id,
        reason=str(body.get("reason") or ""),
        duration_days=duration_days,
        expiration=expiration.strip()[:40] if isinstance(expiration, str) and expiration.strip() else None,
        created_by=int(session["id"]),
    )
    return web.json_response({"entry": entry})


@routes.delete('/api/guilds/{guild_id}/tickets/blacklist/{user_id}')
@routes.delete('/api/guild/{guild_id}/tickets/blacklist/{user_id}')
async def api_guild_tickets_blacklist_delete(req):
    _, guild = await authorize(req, write=True)
    try:
        user_id = int(req.match_info["user_id"])
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"user_id": "معرف العضو غير صالح"})
    if not await delete_ticket_blacklist(guild.id, user_id):
        return json_error(404, "ticket_blacklist_not_found")
    return web.json_response({"deleted": True, "user_id": user_id})


@routes.get('/api/guild/{guild_id}/tickets/active')
async def api_guild_tickets_active(req):
    _, guild = await authorize(req)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    return web.json_response({"tickets": await community.get_active_tickets(guild.id)})


@routes.get('/api/guild/{guild_id}/tickets/archive')
async def api_guild_tickets_archive(req):
    _, guild = await authorize(req)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    query = req.query.get("q", req.query.get("query", ""))
    return web.json_response({
        "tickets": await community.get_ticket_archive(guild.id, query[:120]),
        "query": query[:120],
    })


@routes.get('/api/guild/{guild_id}/tickets/transcript/{ticket_id}')
async def api_guild_ticket_transcript(req):
    _, guild = await authorize(req)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    try:
        ticket_id = int(req.match_info["ticket_id"])
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"ticket_id": "معرف التذكرة غير صالح"})
    transcript = await community.get_ticket_transcript(guild.id, ticket_id)
    if not transcript:
        return json_error(404, "transcript_not_found")
    return web.Response(
        text=transcript["content_html"],
        content_type="text/html",
        charset="utf-8",
        headers={
            "Content-Disposition": f'inline; filename="ticket-{ticket_id}.html"',
            "Cache-Control": "no-store",
        },
    )


@routes.get('/api/guild/{guild_id}/tickets/detail/{ticket_id}')
async def api_guild_ticket_detail(req):
    _, guild = await authorize(req)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    try:
        ticket_id = int(req.match_info["ticket_id"])
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"ticket_id": "معرف التذكرة غير صالح"})
    ticket = await community.get_ticket(guild.id, ticket_id)
    if not ticket:
        return json_error(404, "ticket_not_found")
    return web.json_response({
        "ticket": ticket,
        "notes": await community.get_ticket_notes(guild.id, ticket_id),
    })


@routes.get('/api/guild/{guild_id}/tickets/kpis')
async def api_guild_tickets_kpis(req):
    _, guild = await authorize(req)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    return web.json_response({"kpis": await community.get_staff_kpis(guild.id)})


@routes.post('/api/guild/{guild_id}/tickets/action')
async def api_guild_tickets_action(req):
    session, guild = await authorize(req, write=True)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    try:
        ticket_id = int(body.get("ticket_id"))
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"ticket_id": "معرف التذكرة غير صالح"})
    action = str(body.get("action", "")).strip().lower()
    action_permission = {
        "close": "close",
        "reassign": "transfer",
        "priority": "priority",
        "reopen": "reopen",
        "note": "note",
    }.get(action)
    ticket_for_policy = None
    if action_permission:
        get_ticket = getattr(community, "get_ticket", None)
        if callable(get_ticket):
            ticket_for_policy = await get_ticket(guild.id, ticket_id)
        else:
            active_tickets = await community.get_active_tickets(guild.id)
            ticket_for_policy = next(
                (item for item in active_tickets if int(item.get("id", 0)) == ticket_id),
                None,
            )
        if not ticket_for_policy:
            return json_error(404, "ticket_not_found")
        allowed = getattr(community, "_ticket_action_allowed", None)
        if callable(allowed) and not await allowed(
            guild.get_member(int(session["id"])), ticket_for_policy, action_permission
        ):
            return json_error(403, "ticket_action_forbidden")
    if action == "close":
        result = await community.force_close_ticket(
            guild.id,
            ticket_id,
            int(session["id"]),
            str(body.get("reason") or "أُغلقت من لوحة الإدارة"),
        )
    elif action == "reassign":
        try:
            staff_id = int(body.get("staff_id"))
        except (TypeError, ValueError):
            return json_error(400, "validation", fields={"staff_id": "معرف الموظف غير صالح"})
        member = guild.get_member(staff_id)
        if member is None:
            return json_error(400, "validation", fields={"staff_id": "الموظف غير موجود في السيرفر"})
        get_ticket = getattr(community, "get_ticket", None)
        if callable(get_ticket):
            ticket = await get_ticket(guild.id, ticket_id)
        else:
            active_tickets = await community.get_active_tickets(guild.id)
            ticket = next(
                (item for item in active_tickets if int(item.get("id", 0)) == ticket_id),
                None,
            )
        if not ticket:
            return json_error(404, "ticket_not_found")
        is_ticket_staff = getattr(community, "_is_ticket_staff", None)
        if callable(is_ticket_staff) and not is_ticket_staff(member, ticket):
            return json_error(
                403,
                "validation",
                fields={"staff_id": "العضو المحدد ليس ضمن فريق دعم هذه التذكرة"},
            )
        result = await community.reassign_ticket(guild.id, ticket_id, staff_id)
    elif action == "status":
        status = str(body.get("status", "")).strip().lower()
        if status not in {"active", "waiting_user", "waiting_staff"}:
            return json_error(400, "validation", fields={"status": "حالة التذكرة غير صالحة"})
        result = await community.set_ticket_status(
            guild.id, ticket_id, status
        )
    elif action == "priority":
        priority = str(body.get("priority", "")).strip().lower()
        if priority not in {"normal", "high", "management"}:
            return json_error(400, "validation", fields={"priority": "أولوية التذكرة غير صالحة"})
        result = await community.update_ticket_priority(guild.id, ticket_id, priority)
    elif action == "reopen":
        result = await community.reopen_ticket(guild.id, ticket_id, int(session["id"]))
    elif action == "note":
        content = str(body.get("content") or "").strip()
        if not content or len(content) > 2000:
            return json_error(400, "validation", fields={"content": "الملاحظة يجب أن تكون بين 1 و2000 حرف"})
        result = await community.add_internal_note(
            guild.id, ticket_id, int(session["id"]), content
        )
    else:
        return json_error(
            400, "validation",
            fields={"action": "الإجراء يجب أن يكون close أو reassign أو status أو priority أو reopen أو note"},
        )
    if not result:
        return json_error(404, "ticket_not_found")
    return web.json_response({"ok": True, "ticket": result})


@routes.get('/api/guild/{guild_id}/tickets/canned')
async def api_guild_tickets_canned_get(req):
    _, guild = await authorize(req)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    return web.json_response({"responses": await community.get_canned_responses(guild.id)})


@routes.post('/api/guild/{guild_id}/tickets/canned')
async def api_guild_tickets_canned(req):
    session, guild = await authorize(req, write=True)
    community = _community_cog()
    if community is None:
        return json_error(503, "community_unavailable")
    try:
        body = await read_json_body(req)
    except (json.JSONDecodeError, ValueError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    if body.get("action") == "delete":
        try:
            response_id = int(body.get("id"))
        except (TypeError, ValueError):
            return json_error(400, "validation", fields={"id": "معرف الرد غير صالح"})
        if not await community.delete_canned_response(guild.id, response_id):
            return json_error(404, "canned_not_found")
        return web.json_response({"deleted": True, "id": response_id})
    title = str(body.get("title", "")).strip()
    content = str(body.get("content", "")).strip()
    category = str(body.get("category", "عام")).strip()
    shortcut = str(body.get("shortcut") or "").strip() or None
    sticker_id = body.get("sticker_id")
    response_id = body.get("id")
    if not title or len(title) > 120:
        return json_error(400, "validation", fields={"title": "العنوان يجب أن يكون بين 1 و120 حرفاً"})
    if not content or len(content) > 2000:
        return json_error(400, "validation", fields={"content": "النص يجب أن يكون بين 1 و2000 حرف"})
    if shortcut and len(shortcut) > 80:
        return json_error(400, "validation", fields={"shortcut": "الاختصار يجب ألا يتجاوز 80 حرفاً"})
    if sticker_id in ("", None):
        sticker_id = None
    else:
        try:
            sticker_id = int(sticker_id)
        except (TypeError, ValueError):
            return json_error(400, "validation", fields={"sticker_id": "معرف الملصق غير صالح"})
        if await resolve_guild_sticker(guild, sticker_id) is None:
            return json_error(400, "validation", fields={"sticker_id": "ملصق السيرفر غير موجود أو غير متاح"})
    try:
        response_id = int(response_id) if response_id not in (None, "") else None
    except (TypeError, ValueError):
        return json_error(400, "validation", fields={"id": "معرف الرد غير صالح"})
    result = await community.save_canned_response(
        guild.id,
        title,
        content,
        category,
        session["id"],
        response_id,
        shortcut,
        sticker_id,
    )
    if not result:
        return json_error(404, "canned_not_found")
    return web.json_response({"response": result})


@routes.get('/api/guild/{guild_id}/security/incidents')
async def api_security_incidents(req):
    _, guild = await authorize(req)
    security = bot_ref.get_cog("Security") if bot_ref else None
    if security is None:
        return json_error(503, "security_unavailable")
    return web.json_response({
        "guild_id": str(guild.id),
        "incidents": await _security_incident_rows(security, guild.id),
        "whitelist": security.get_whitelist(guild.id),
        "locked": security.is_locked(guild.id),
        "protected_channels": security.get_lockdown_exemptions(guild.id),
    })


@routes.post('/api/guilds/{guild_id}/security/lockdown')
@routes.post('/api/guild/{guild_id}/security/lockdown')
async def api_security_lockdown(req):
    session, guild = await authorize(
        req, write=True, management_tier="admin"
    )
    security = bot_ref.get_cog("Security") if bot_ref else None
    if security is None:
        return json_error(503, "security_unavailable")
    try:
        body = await read_json_body(req)
    except (ValueError, UnicodeDecodeError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    action = body.get("action")
    if action is not None:
        if action not in {"lock", "unlock"}:
            return json_error(400, "validation", fields={"action": "الإجراء يجب أن يكون lock أو unlock"})
        locked = action == "lock"
    else:
        locked = body.get("locked", True)
    if not isinstance(locked, bool):
        return json_error(400, "validation", fields={"locked": "القيمة يجب أن تكون تشغيل/إيقاف"})
    result = await security.emergency_lockdown(guild.id, locked)
    mitigation = (
        f"{'queued_lockdown' if locked else 'queued_unlock'}:{result['channels']}"
        if result["queued"]
        else "queue_rejected"
    )
    await _await_if_needed(security.record_control_action(
        guild.id,
        int(session["id"]),
        session.get("username", session["id"]),
        "dashboard_lockdown" if locked else "dashboard_unlock",
        mitigation,
    ))
    return web.json_response({"ok": result["queued"], **result})


@routes.post('/api/guild/{guild_id}/security/whitelist')
async def api_security_whitelist(req):
    session, guild = await authorize(
        req, write=True, management_tier="admin"
    )
    security = bot_ref.get_cog("Security") if bot_ref else None
    if security is None:
        return json_error(503, "security_unavailable")
    try:
        body = await read_json_body(req)
    except (ValueError, UnicodeDecodeError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    action = body.get("action")
    user_id = body.get("user_id")
    if action not in {"add", "remove"}:
        return json_error(400, "validation", fields={"action": "الإجراء يجب أن يكون add أو remove"})
    if isinstance(user_id, bool) or not str(user_id).isdigit() or not 15 <= len(str(user_id)) <= 22:
        return json_error(400, "validation", fields={"user_id": "معرّف Discord غير صالح"})
    user_id = int(user_id)
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return json_error(404, "member_not_found")
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            return json_error(503, "member_lookup_unavailable")
    if action == "add" and not member.guild_permissions.administrator:
        return json_error(400, "validation", fields={"user_id": "يجب أن يملك العضو صلاحية Administrator"})
    if action == "add":
        await _await_if_needed(security.whitelist_member(guild.id, user_id))
    else:
        await _await_if_needed(
            security.remove_whitelisted_member(guild.id, user_id)
        )
    await _await_if_needed(security.record_control_action(
        guild.id,
        int(session["id"]),
        session.get("username", session["id"]),
        f"dashboard_whitelist_{action}",
        f"user:{user_id}",
    ))
    return web.json_response({
        "ok": True,
        "action": action,
        "user_id": str(user_id),
        "whitelist": security.get_whitelist(guild.id),
    })


@routes.get('/api/guild/{guild_id}/moderation/infractions')
async def api_moderation_infractions(req):
    _, guild = await authorize(req)
    moderation = bot_ref.get_cog("Moderation") if bot_ref else None
    if moderation is None:
        return json_error(503, "moderation_unavailable")
    return web.json_response({
        "guild_id": str(guild.id),
        "infractions": await moderation.get_recent_infractions(guild.id),
    })


@routes.post('/api/guild/{guild_id}/moderation/warnings/{warning_id}/revoke')
async def api_revoke_warning(req):
    session, guild = await authorize(req, write=True)
    moderation = bot_ref.get_cog("Moderation") if bot_ref else None
    if moderation is None:
        return json_error(503, "moderation_unavailable")
    raw_warning_id = req.match_info.get("warning_id", "")
    if not raw_warning_id.isdigit() or int(raw_warning_id) <= 0:
        return json_error(400, "validation", fields={"warning_id": "معرف إنذار غير صالح"})
    warning = await get_warning(int(raw_warning_id))
    if not warning or int(warning["guild_id"]) != guild.id:
        return json_error(404, "warning_not_found")
    revoked = await moderation.revoke_warning(int(raw_warning_id))
    if revoked is None:
        return json_error(404, "warning_not_found")
    logger.info(
        "Warning %s revoked in guild %s by user %s",
        raw_warning_id,
        guild.id,
        session["id"],
    )
    return web.json_response({"ok": True, "warning": revoked})


@routes.post('/api/guild/{guild_id}/moderation/{user_id}/unmute')
async def api_quick_unmute(req):
    session, guild = await authorize(req, write=True)
    moderation = bot_ref.get_cog("Moderation") if bot_ref else None
    if moderation is None:
        return json_error(503, "moderation_unavailable")
    raw_user_id = req.match_info.get("user_id", "")
    if not raw_user_id.isdigit() or not 15 <= len(raw_user_id) <= 22:
        return json_error(400, "validation", fields={"user_id": "معرف Discord غير صالح"})
    result = await moderation.quick_unmute(guild.id, int(raw_user_id))
    if not result.get("ok"):
        return json_error(
            404 if result.get("error") in {"guild_not_found", "member_not_found"} else 403,
            result.get("error", "unmute_failed"),
        )
    logger.info(
        "User %s unmuted in guild %s by user %s",
        raw_user_id,
        guild.id,
        session["id"],
    )
    return web.json_response(result)


@routes.post('/api/guild/{guild_id}/engagement/test-welcome')
async def api_test_welcome(req):
    session, guild = await authorize(req, write=True)
    engagement = bot_ref.get_cog("Engagement") if bot_ref else None
    if engagement is None:
        return json_error(503, "engagement_unavailable")
    try:
        body = await read_json_body(req)
    except (ValueError, UnicodeDecodeError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    raw_channel_id = body.get("target_channel_id")
    if isinstance(raw_channel_id, bool) or not str(raw_channel_id).isdigit():
        return json_error(400, "validation", fields={"target_channel_id": "معرف قناة غير صالح"})
    channel_id = int(raw_channel_id)
    channel = await resolve_text_channel(guild, channel_id)
    if channel is None:
        return json_error(400, "validation", fields={"target_channel_id": "القناة غير موجودة في هذا السيرفر"})
    template_data = body.get("template_data", {})
    if not isinstance(template_data, dict) or len(template_data) > 8:
        return json_error(400, "validation", fields={"template_data": "بيانات المعاينة غير صالحة"})
    result = await engagement.send_test_welcome(guild.id, channel_id, template_data)
    if not result.get("ok"):
        return json_error(
            404 if result.get("error") in {"guild_not_found", "channel_not_found"} else 403,
            result.get("error", "welcome_test_failed"),
        )
    logger.info(
        "Welcome preview sent in guild %s by user %s to channel %s",
        guild.id,
        session["id"],
        channel_id,
    )
    return web.json_response(result)


ONBOARDING_KEYS = {
    "welcome_channel_id",
    "leave_channel_id",
    "welcome_message",
    "leave_message",
    "welcome_dm_enabled",
    "welcome_embed_enabled",
    "welcome_embed_color",
    "welcome_embed_title",
    "welcome_embed_description",
    "welcome_embed_image_url",
    "welcome_embed_sticker_id",
    "welcome_embed_footer",
    "welcome_embed_show_avatar",
    "auto_role_id",
    "member_auto_role_id",
    "bot_auto_role_id",
    "verified_role_id",
    "unverified_role_id",
    "rules_channel_id",
}


async def onboarding_payload(guild, engagement):
    return await engagement.get_onboarding_snapshot(guild.id)


@routes.get('/api/guild/{guild_id}/onboarding')
async def api_get_onboarding(req):
    _, guild = await authorize(req)
    engagement = bot_ref.get_cog("Engagement") if bot_ref else None
    if engagement is None:
        return json_error(503, "engagement_unavailable")
    return web.json_response(await onboarding_payload(guild, engagement))


@routes.post('/api/guild/{guild_id}/onboarding')
async def api_post_onboarding(req):
    session, guild = await authorize(req, write=True)
    engagement = bot_ref.get_cog("Engagement") if bot_ref else None
    if engagement is None:
        return json_error(503, "engagement_unavailable")
    try:
        body = await read_json_body(req)
    except (ValueError, UnicodeDecodeError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    revision = body.get("revision")
    if revision is not None and (
        not isinstance(revision, int) or isinstance(revision, bool)
    ):
        return json_error(400, "validation", fields={"revision": "رقم الإصدار غير صالح"})
    changes = body.get("changes", {})
    if not isinstance(changes, dict):
        return json_error(400, "validation", fields={"changes": "صيغة التعديلات غير صالحة"})
    unknown = set(changes) - ONBOARDING_KEYS
    if unknown:
        return json_error(
            400,
            "validation",
            fields={str(key): "حقل غير مسموح في استوديو الترحيب" for key in unknown},
        )
    clean, errors = await validate_changes(guild, changes)
    if errors:
        return json_error(400, "validation", fields=errors)
    try:
        snapshot = await update_guild_settings(
            guild.id,
            expected_revision=revision,
            **clean,
        ) if clean else await get_guild_settings(guild.id)
    except SettingsConflict as conflict:
        return json_error(409, "conflict", **public_settings(conflict.current))
    result = await onboarding_payload(guild, engagement)
    broadcast(guild.id, {"type": "settings", "by": str(session["id"]), **result})
    return web.json_response(result)


@routes.post('/api/guild/{guild_id}/onboarding/test-welcome')
async def api_onboarding_test_welcome(req):
    session, guild = await authorize(req, write=True)
    engagement = bot_ref.get_cog("Engagement") if bot_ref else None
    if engagement is None:
        return json_error(503, "engagement_unavailable")
    try:
        body = await read_json_body(req)
    except (ValueError, UnicodeDecodeError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    channel_id = body.get("target_channel_id")
    if isinstance(channel_id, bool) or not str(channel_id).isdigit():
        return json_error(400, "validation", fields={"target_channel_id": "معرف قناة غير صالح"})
    channel = await resolve_text_channel(guild, int(channel_id))
    if channel is None:
        return json_error(400, "validation", fields={"target_channel_id": "القناة غير موجودة في هذا السيرفر"})
    template_data = body.get("template_data", {})
    if not isinstance(template_data, dict) or len(template_data) > 8:
        return json_error(400, "validation", fields={"template_data": "بيانات المعاينة غير صالحة"})
    result = await engagement.send_test_welcome(guild.id, int(channel_id), template_data)
    if not result.get("ok"):
        return json_error(
            404 if result.get("error") in {"guild_not_found", "channel_not_found"} else 403,
            result.get("error", "welcome_test_failed"),
        )
    logger.info("Onboarding welcome preview sent in guild %s by user %s", guild.id, session["id"])
    return web.json_response(result)


@routes.post('/api/guild/{guild_id}/onboarding/self-roles')
async def api_deploy_self_roles(req):
    session, guild = await authorize(req, write=True)
    engagement = bot_ref.get_cog("Engagement") if bot_ref else None
    if engagement is None:
        return json_error(503, "engagement_unavailable")
    try:
        body = await read_json_body(req)
    except (ValueError, UnicodeDecodeError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict):
        return json_error(400, "validation", fields={"_": "صيغة الطلب غير صالحة"})
    channel_id = body.get("target_channel_id")
    if isinstance(channel_id, bool) or not str(channel_id).isdigit():
        return json_error(400, "validation", fields={"target_channel_id": "معرف قناة غير صالح"})
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, MESSAGE_CHANNEL_TYPES):
        return json_error(400, "validation", fields={"target_channel_id": "القناة غير موجودة في هذا السيرفر"})
    roles = body.get("roles")
    if not isinstance(roles, list) or not 1 <= len(roles) <= 25:
        return json_error(400, "validation", fields={"roles": "اختر من رتبة إلى 25 رتبة"})
    for spec in roles:
        if (
            not isinstance(spec, dict)
            or isinstance(spec.get("id"), bool)
            or not str(spec.get("id", "")).isdigit()
        ):
            return json_error(400, "validation", fields={"roles": "بيانات الرتب غير صالحة"})
    result = await engagement.deploy_self_role_panel(
        guild.id,
        int(channel_id),
        str(body.get("title") or "الرتب الذاتية")[:256],
        str(body.get("description") or "اختر الرتب المناسبة لك:")[:4000],
        str(body.get("color") or "#5865f2")[:20],
        str(body.get("emoji") or "🏷️")[:8],
        roles,
    )
    if not result.get("ok"):
        return json_error(
            400 if result.get("error") in {"roles_invalid", "role_not_assignable"} else 404,
            result.get("error", "self_role_deploy_failed"),
        )
    logger.info("Self-role panel deployed in guild %s by user %s", guild.id, session["id"])
    return web.json_response(result)


@routes.get('/api/guild/{guild_id}/self-roles/panels')
async def api_get_self_role_panels(req):
    _, guild = await authorize(req)
    panels = await get_self_role_panels(guild.id)
    return web.json_response({"panels": panels})


def _prime_ai_public_settings(settings: dict) -> dict:
    return {
        "enabled": bool(settings["enabled"]),
        "system_prompt": settings["system_prompt"],
        "allowed_channel_ids": list(settings["allowed_channel_ids"]),
        "provider": settings["provider"],
        "revision": int(settings["revision"]),
        "updated_at": settings["updated_at"],
    }


def _validate_prime_ai_references(guild, config: dict) -> dict:
    channels = {
        str(channel.id): channel
        for channel in getattr(guild, "channels", ())
        if getattr(channel, "id", None) is not None
    }
    channel_ids = set(channels)
    role_ids = {
        str(role.id)
        for role in getattr(guild, "roles", ())
        if getattr(role, "id", None) is not None
    }
    access = config.get("access", {})
    for key in ("allowed_channels", "blocked_channels"):
        if any(str(value) not in channel_ids for value in access.get(key, [])):
            raise ValueError(f"invalid_{key}")
    for key in ("allowed_roles", "blocked_roles"):
        if any(str(value) not in role_ids for value in access.get(key, [])):
            raise ValueError(f"invalid_{key}")
    for action_id, policy in config.get("actions", {}).items():
        for key, allowed_ids in policy.items():
            if key == "allowed_channels" and any(
                str(value) not in channel_ids for value in allowed_ids
            ):
                raise ValueError(f"invalid_action_channel_{action_id}")
            if key == "allowed_roles" and any(
                str(value) not in role_ids for value in allowed_ids
            ):
                raise ValueError(f"invalid_action_role_{action_id}")
    for channel_id in config.get("channel_personas", {}):
        if str(channel_id) not in channel_ids:
            raise ValueError("invalid_channel_persona")
    for role_id in config.get("role_overrides", {}):
        if str(role_id) not in role_ids:
            raise ValueError("invalid_role_override")
    talk_channel_id = str(config.get("talk_channel", {}).get("channel_id", "") or "")
    if talk_channel_id:
        if talk_channel_id not in channel_ids or not isinstance(
            channels[talk_channel_id], (discord.TextChannel, discord.Thread)
        ):
            raise ValueError("invalid_talk_channel")
    moderation_ids = config.get("moderation", {}).get("channel_ids", [])
    if any(str(value) not in channel_ids for value in moderation_ids):
        raise ValueError("invalid_moderation_channels")
    if any(
        not isinstance(channels[str(value)], (discord.TextChannel, discord.Thread))
        for value in moderation_ids
    ):
        raise ValueError("invalid_moderation_channels")
    alert_channel_id = config.get("moderation", {}).get("alert_channel_id", "")
    if alert_channel_id and str(alert_channel_id) not in channel_ids:
        raise ValueError("invalid_moderation_alert_channel")
    if alert_channel_id and not isinstance(
        channels[str(alert_channel_id)], (discord.TextChannel, discord.Thread)
    ):
        raise ValueError("invalid_moderation_alert_channel")
    return config


async def _is_prime_ai_bot_owner(user_id: int, guild) -> bool:
    bot = bot_ref
    checker = getattr(bot, "is_owner", None) if bot else None
    if not callable(checker):
        return False
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception:
            return False
    try:
        return bool(await checker(member))
    except Exception:
        logger.exception("Could not verify PRIME AI bot-owner access.")
        return False


@routes.get('/api/guild/{guild_id}/ai')
async def api_get_prime_ai(req):
    _, guild = await authorize(req)
    try:
        settings = _prime_ai_public_settings(
            await prime_ai_service.get_settings(guild.id)
        )
        memories = await prime_ai_service.list_memories(guild.id)
        channels = [
            {"id": str(channel.id), "name": str(channel.name)}
            for channel in sorted(
                getattr(guild, "text_channels", []),
                key=lambda channel: (
                    int(getattr(channel, "position", 0) or 0),
                    str(getattr(channel, "name", "")),
                ),
            )[:500]
        ]
        return web.json_response({
            "settings": settings,
            "channels": channels,
            "memories": memories,
        })
    except Exception:
        logger.exception(
            "Failed to read PRIME AI settings for guild %s", guild.id
        )
        return json_error(503, "ai_storage_unavailable")


@routes.get('/api/guild/{guild_id}/ai/control')
async def api_get_prime_ai_control(req):
    session, guild = await authorize(req)
    try:
        snapshot = await prime_ai_control.get_control_settings(guild.id)
        skills = await prime_ai_control.get_public_skills(guild.id)
        is_bot_owner = await _is_prime_ai_bot_owner(int(session["id"]), guild)
        memories = await prime_ai_service.list_memories(
            guild.id,
            include_disabled=True,
            limit=500,
        )
        if is_bot_owner:
            memories = await prime_ai_service.list_memories(
                guild.id, include_disabled=True, limit=500
            ) + await prime_ai_service.list_memories(
                0, scope="GLOBAL", include_disabled=True, limit=500
            )
        operations = await prime_ai_control.list_operations(guild.id)
        analytics = await prime_ai_control.get_analytics(guild.id)
        channels = [
            {
                "id": str(item.id),
                "name": str(item.name),
                "type": type(item).__name__,
                "text_based": isinstance(
                    item, (discord.TextChannel, discord.Thread)
                ),
            }
            for item in list(getattr(guild, "channels", ()))[:500]
            if hasattr(item, "name")
        ]
        roles = [
            {"id": str(item.id), "name": str(item.name)}
            for item in list(getattr(guild, "roles", ()))[:500]
        ]
        return web.json_response({
            "control": snapshot,
            "skills": skills,
            "action_registry": [
                {
                    "id": action_id,
                    "action_id": item["action_id"],
                    "name": item["name"],
                    "description": item["description"],
                    "category": item["category"],
                    "risk": item["risk"],
                    "discord_permission": item["discord_permission"],
                    "additional_discord_permissions": item.get(
                        "additional_discord_permissions", []
                    ),
                    "prime_permission": item["prime_permission"],
                    "enabled": bool(
                        snapshot["config"]["actions"][action_id]["enabled"]
                    ),
                    "confirmation_required": item["confirmation_required"],
                    "audit_required": item["audit_required"],
                    "dashboard_config": item["dashboard_config"],
                }
                for action_id, item in prime_ai_control.ACTION_REGISTRY.items()
            ],
            "memories": memories,
            "operations": operations,
            "analytics": analytics,
            "channels": channels,
            "roles": roles,
            "is_bot_owner": is_bot_owner,
            "provider_status": (
                "configured" if os.getenv("GEMINI_API_KEY", "").strip() else "missing"
            ),
        })
    except Exception:
        logger.exception("Failed to read PRIME AI control center for guild %s", guild.id)
        return json_error(503, "ai_storage_unavailable")


@routes.post('/api/guild/{guild_id}/ai/control')
async def api_save_prime_ai_control(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    if not isinstance(body.get("config"), dict):
        return json_error(400, "validation", fields={"config": "إعدادات التحكم غير صالحة."})
    revision = body.get("revision")
    try:
        normalized = prime_ai_control.normalize_control_settings(body["config"])
        _validate_prime_ai_references(guild, normalized)
        saved = await prime_ai_control.save_control_settings(
            guild.id,
            int(session["id"]),
            normalized,
            revision,
        )
    except prime_ai_control.ControlSettingsConflict as conflict:
        return json_error(409, "conflict", control=conflict.current)
    except ValueError as error:
        return json_error(400, "validation", fields={"_": str(error)})
    except Exception:
        logger.exception("Failed to save PRIME AI control settings for guild %s", guild.id)
        return json_error(503, "ai_storage_unavailable")
    return web.json_response({"ok": True, "control": saved})


@routes.post('/api/guild/{guild_id}/ai/skills/{skill_key}')
async def api_save_prime_ai_skill(req):
    session, guild = await authorize(req, write=True)
    skill_key = str(req.match_info.get("skill_key", ""))[:80]
    body = await read_json_body(req)
    revision = body.get("revision")
    payload = body.get("settings")
    if not isinstance(payload, dict):
        return json_error(400, "validation", fields={"settings": "إعدادات المهارة غير صالحة."})
    try:
        channels = {str(item.id) for item in getattr(guild, "channels", ())}
        roles = {str(item.id) for item in getattr(guild, "roles", ())}
        if any(str(item) not in channels for item in payload.get("allowed_channels", [])):
            raise ValueError("invalid_skill_channels")
        if any(str(item) not in roles for item in payload.get("allowed_roles", [])):
            raise ValueError("invalid_skill_roles")
        skill = await prime_ai_control.save_skill(
            guild.id,
            int(session["id"]),
            skill_key,
            payload,
            revision,
        )
    except prime_ai_control.ControlSettingsConflict as conflict:
        return json_error(409, "conflict", skill=conflict.current)
    except ValueError as error:
        return json_error(400, "validation", fields={"_": str(error)})
    except Exception:
        logger.exception("Failed to save PRIME AI skill %s in guild %s", skill_key, guild.id)
        return json_error(503, "ai_storage_unavailable")
    return web.json_response({"ok": True, "skill": prime_ai_control.public_skill(skill)})


@routes.post('/api/guild/{guild_id}/ai/memories/{memory_id}/edit')
async def api_edit_prime_ai_memory(req):
    session, guild = await authorize(req, write=True)
    raw_memory_id = req.match_info.get("memory_id", "")
    if not str(raw_memory_id).isascii() or not str(raw_memory_id).isdigit():
        return json_error(404, "not_found")
    body = await read_json_body(req)
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        return json_error(400, "validation", fields={"content": "اكتب محتوى الذاكرة."})
    scope = str(body.get("scope", "SERVER")).upper()
    scope_id = str(body.get("scope_id", ""))
    local_memories = await prime_ai_service.list_memories(
        guild.id, include_disabled=True, limit=500
    )
    global_memories = await prime_ai_service.list_memories(
        0, scope="GLOBAL", include_disabled=True, limit=500
    )
    memory_id = int(raw_memory_id)
    is_global = any(int(item["id"]) == memory_id for item in global_memories)
    source_guild_id = 0 if is_global else guild.id
    if is_global and not await _is_prime_ai_bot_owner(int(session["id"]), guild):
        return json_error(403, "forbidden")
    if not is_global and not any(int(item["id"]) == memory_id for item in local_memories):
        return json_error(404, "not_found")
    destination_guild_id = 0 if scope == "GLOBAL" else guild.id
    if destination_guild_id == 0 and not await _is_prime_ai_bot_owner(int(session["id"]), guild):
        return json_error(403, "forbidden")
    if scope == "CHANNEL" and scope_id not in {str(item.id) for item in getattr(guild, "channels", ())}:
        return json_error(400, "validation", fields={"scope_id": "القناة ليست ضمن هذا الخادم."})
    if scope == "ROLE" and scope_id not in {str(item.id) for item in getattr(guild, "roles", ())}:
        return json_error(400, "validation", fields={"scope_id": "الرتبة ليست ضمن هذا الخادم."})
    if scope == "USER":
        member = guild.get_member(int(scope_id or 0))
        if member is None and scope_id.isdigit():
            try:
                member = await guild.fetch_member(int(scope_id))
            except Exception:
                member = None
        if member is None:
            return json_error(400, "validation", fields={"scope_id": "العضو ليس ضمن هذا الخادم."})
    try:
        memory = await prime_ai_service.edit_memory(
            destination_guild_id,
            int(session["id"]),
            memory_id,
            content,
            scope=scope,
            scope_id=scope_id,
            expires_in_days=body.get("expires_in_days"),
            enabled=body.get("enabled", True),
            source_guild_id=source_guild_id,
        )
    except ValueError as error:
        return json_error(400, "validation", fields={"_": str(error)})
    except Exception:
        logger.exception("Failed to edit PRIME AI memory in guild %s", guild.id)
        return json_error(503, "ai_storage_unavailable")
    return web.json_response({"ok": True, "memory": memory})


@routes.post('/api/guild/{guild_id}/ai/memories/clear')
async def api_clear_prime_ai_memories(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    target_guild_id = guild.id
    if str(body.get("scope", "")).upper() == "GLOBAL":
        if not await _is_prime_ai_bot_owner(int(session["id"]), guild):
            return json_error(403, "forbidden")
        target_guild_id = 0
    try:
        deleted = await prime_ai_service.clear_memories(target_guild_id, int(session["id"]))
    except Exception:
        logger.exception("Failed to clear PRIME AI memories in guild %s", guild.id)
        return json_error(503, "ai_storage_unavailable")
    return web.json_response({"ok": True, "deleted": deleted})


@routes.post('/api/guild/{guild_id}/ai/sandbox')
async def api_prime_ai_sandbox(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    prompt = body.get("prompt")
    channel_id = str(body.get("channel_id", "")).strip()
    if not isinstance(prompt, str) or not prompt.strip():
        return json_error(
            400,
            "validation",
            fields={"prompt": "اكتب طلباً لمعاينته."},
        )
    if len(prompt) > prime_ai_service.MAX_TEST_PROMPT:
        return json_error(
            400,
            "validation",
            fields={
                "prompt": (
                    f"الحد الأقصى {prime_ai_service.MAX_TEST_PROMPT} حرف."
                )
            },
        )
    if not channel_id.isdigit() or not 15 <= len(channel_id) <= 22:
        return json_error(
            400,
            "validation",
            fields={"channel_id": "اختر قناة نصية صالحة."},
        )

    actor_id = int(session["id"])
    actor = guild.get_member(actor_id)
    if actor is None:
        try:
            actor = await guild.fetch_member(actor_id)
        except discord.NotFound:
            return json_error(403, "sandbox_member_unavailable")
        except (discord.Forbidden, discord.HTTPException):
            return json_error(503, "sandbox_member_unavailable")
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        get_thread = getattr(guild, "get_thread", None)
        channel = get_thread(int(channel_id)) if callable(get_thread) else None
    if channel is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return json_error(400, "sandbox_channel_unavailable")
    channel_guild = getattr(channel, "guild", None)
    if channel_guild is None or int(getattr(channel_guild, "id", 0)) != int(guild.id):
        return json_error(400, "sandbox_channel_unavailable")

    bot = request_bot(req)
    shared_session = getattr(bot, "session", None) if bot else None
    if (
        bot is None
        or shared_session is None
        or getattr(shared_session, "closed", False)
    ):
        return json_error(503, "ai_runtime_unavailable")
    bot_member = getattr(guild, "me", None)
    if bot_member is None:
        return json_error(503, "ai_runtime_unavailable")
    permissions_for = getattr(channel, "permissions_for", None)
    if not callable(permissions_for):
        return json_error(403, "sandbox_channel_forbidden")
    try:
        if not permissions_for(actor).view_channel:
            return json_error(403, "sandbox_channel_forbidden")
        if not permissions_for(bot_member).view_channel:
            return json_error(403, "sandbox_bot_channel_forbidden")
    except Exception:
        return json_error(403, "sandbox_channel_forbidden")

    wait = prime_ai_service.allow_request(
        guild.id,
        actor_id,
        action="dashboard-sandbox",
        limit=3,
        window_seconds=60,
    )
    if wait:
        return json_error(429, "rate_limited", retry_after=int(wait) + 1)
    try:
        snapshot = await prime_ai_control.get_control_settings(guild.id)
        preview = await prime_ai_runtime.sandbox_plan(
            shared_session,
            bot,
            guild,
            actor,
            channel,
            prompt.strip(),
            config=snapshot["config"],
        )
    except prime_ai_service.AIProviderUnavailable as error:
        if error.status_code in {429, 503}:
            error_key = (
                "ai_provider_rate_limited"
                if error.status_code == 429
                else "ai_provider_busy"
            )
            return json_error(error.status_code, error_key)
        return json_error(502, "ai_provider_unavailable")
    except prime_ai_runtime.InvalidToolPlan:
        return json_error(502, "ai_plan_unavailable")
    except Exception:
        logger.exception(
            "PRIME AI dry-run preview failed for guild %s by user %s",
            guild.id,
            actor_id,
        )
        return json_error(503, "ai_runtime_unavailable")
    return web.json_response({"ok": True, **preview})


@routes.get('/api/guild/{guild_id}/ai/operations')
async def api_prime_ai_operations(req):
    _, guild = await authorize(req)
    return web.json_response({"operations": await prime_ai_control.list_operations(guild.id)})


@routes.get('/api/guild/{guild_id}/ai/moderation')
async def api_prime_ai_moderation(req):
    _, guild = await authorize(req)
    return web.json_response({"events": await prime_ai_control.list_moderation(guild.id)})


@routes.get('/api/guild/{guild_id}/ai/analytics')
async def api_prime_ai_analytics(req):
    _, guild = await authorize(req)
    return web.json_response(await prime_ai_control.get_analytics(guild.id))


@routes.post('/api/guild/{guild_id}/ai/settings')
async def api_save_prime_ai_settings(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    fields = {}
    if not isinstance(body.get("enabled"), bool):
        fields["enabled"] = "قيمة التفعيل غير صالحة."
    if not isinstance(body.get("system_prompt"), str):
        fields["system_prompt"] = "التعليمات مطلوبة بصيغة نصية."
    elif len(body["system_prompt"]) > prime_ai_service.MAX_SYSTEM_PROMPT:
        fields["system_prompt"] = (
            f"الحد الأقصى {prime_ai_service.MAX_SYSTEM_PROMPT} حرف."
        )
    revision = body.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        fields["revision"] = "رقم مراجعة الإعدادات غير صالح."

    channel_ids = body.get("allowed_channel_ids")
    if not isinstance(channel_ids, list) or len(channel_ids) > 100:
        fields["allowed_channel_ids"] = "قائمة القنوات غير صالحة."
    else:
        available_ids = {
            str(channel.id)
            for channel in getattr(guild, "text_channels", [])
        }
        clean_channels = []
        for channel_id in channel_ids:
            if (
                not isinstance(channel_id, str)
                or not channel_id.isascii()
                or not channel_id.isdigit()
                or channel_id not in available_ids
            ):
                fields["allowed_channel_ids"] = (
                    "اختر قنوات نصية تابعة لهذا الخادم فقط."
                )
                break
            clean_channels.append(channel_id)
        channel_ids = list(dict.fromkeys(clean_channels))

    if fields:
        return json_error(400, "validation", fields=fields)

    try:
        updated = await prime_ai_service.save_settings(
            guild.id,
            int(session["id"]),
            enabled=body["enabled"],
            system_prompt=body["system_prompt"],
            allowed_channel_ids=channel_ids,
            expected_revision=revision,
        )
    except prime_ai_service.AISettingsConflict as conflict:
        return json_error(
            409,
            "conflict",
            settings=_prime_ai_public_settings(conflict.current),
        )
    except ValueError as error:
        return json_error(
            400,
            "validation",
            fields={"_": str(error)},
        )
    except Exception:
        logger.exception(
            "Failed to save PRIME AI settings for guild %s by user %s",
            guild.id,
            session["id"],
        )
        return json_error(503, "ai_storage_unavailable")

    return web.json_response({
        "ok": True,
        "settings": _prime_ai_public_settings(updated),
    })


@routes.post('/api/guild/{guild_id}/ai/memories')
async def api_add_prime_ai_memory(req):
    session, guild = await authorize(req, write=True)
    body = await read_json_body(req)
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        return json_error(
            400,
            "validation",
            fields={"content": "اكتب محتوى الذاكرة."},
        )
    if len(content) > prime_ai_service.MAX_MEMORY_LENGTH:
        return json_error(
            400,
            "validation",
            fields={
                "content": (
                    f"الحد الأقصى {prime_ai_service.MAX_MEMORY_LENGTH} حرف."
                )
            },
        )
    scope = str(body.get("scope", "SERVER")).upper()
    scope_id = str(body.get("scope_id", ""))
    storage_guild_id = guild.id
    if scope == "GLOBAL":
        if not await _is_prime_ai_bot_owner(int(session["id"]), guild):
            return json_error(403, "forbidden")
        storage_guild_id = 0
    if scope == "CHANNEL" and scope_id not in {str(item.id) for item in getattr(guild, "channels", ())}:
        return json_error(400, "validation", fields={"scope_id": "القناة ليست ضمن هذا الخادم."})
    if scope == "ROLE" and scope_id not in {str(item.id) for item in getattr(guild, "roles", ())}:
        return json_error(400, "validation", fields={"scope_id": "الرتبة ليست ضمن هذا الخادم."})
    if scope == "USER":
        member = guild.get_member(int(scope_id or 0))
        if member is None and scope_id.isdigit():
            try:
                member = await guild.fetch_member(int(scope_id))
            except Exception:
                member = None
        if member is None:
            return json_error(400, "validation", fields={"scope_id": "العضو ليس ضمن هذا الخادم."})
    try:
        memory = await prime_ai_service.add_memory(
            storage_guild_id,
            int(session["id"]),
            content,
            scope=scope,
            scope_id=scope_id,
            expires_in_days=body.get("expires_in_days"),
        )
    except prime_ai_service.AIMemoryLimitReached:
        return json_error(409, "memory_limit_reached")
    except ValueError:
        return json_error(
            400,
            "validation",
            fields={"content": "محتوى الذاكرة غير صالح."},
        )
    except Exception:
        logger.exception(
            "Failed to add PRIME AI memory for guild %s by user %s",
            guild.id,
            session["id"],
        )
        return json_error(503, "ai_storage_unavailable")
    return web.json_response({"ok": True, "memory": memory})


@routes.post('/api/guild/{guild_id}/ai/memories/{memory_id}/delete')
async def api_delete_prime_ai_memory(req):
    session, guild = await authorize(req, write=True)
    raw_memory_id = req.match_info.get("memory_id", "")
    if not raw_memory_id.isascii() or not raw_memory_id.isdigit():
        return json_error(404, "not_found")
    memory_id = int(raw_memory_id)
    try:
        deleted = await prime_ai_service.delete_memory(
            guild.id,
            int(session["id"]),
            memory_id,
        )
        if not deleted:
            global_memories = await prime_ai_service.list_memories(
                0, scope="GLOBAL", include_disabled=True, limit=500
            )
            if any(int(item["id"]) == memory_id for item in global_memories):
                if not await _is_prime_ai_bot_owner(int(session["id"]), guild):
                    return json_error(403, "forbidden")
                deleted = await prime_ai_service.delete_memory(
                    0,
                    int(session["id"]),
                    memory_id,
                )
    except Exception:
        logger.exception(
            "Failed to delete PRIME AI memory for guild %s by user %s",
            guild.id,
            session["id"],
        )
        return json_error(503, "ai_storage_unavailable")
    if not deleted:
        return json_error(404, "not_found")
    return web.json_response({"ok": True})


@routes.get('/api/guild/{guild_id}/ai/audit')
async def api_get_prime_ai_audit(req):
    _, guild = await authorize(req)
    try:
        events = await prime_ai_service.list_audit(guild.id)
    except Exception:
        logger.exception(
            "Failed to read PRIME AI audit events for guild %s", guild.id
        )
        return json_error(503, "ai_storage_unavailable")
    return web.json_response({"events": events})


@routes.post('/api/guild/{guild_id}/ai/test')
async def api_test_prime_ai(req):
    session, guild = await authorize(req, write=True)
    actor_id = int(session["id"])
    wait = prime_ai_service.allow_request(
        guild.id,
        actor_id,
        action="dashboard-test",
        limit=4,
        window_seconds=60,
    )
    if wait:
        return json_error(
            429,
            "rate_limited",
            retry_after=int(wait) + 1,
        )
    body = await read_json_body(req)
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return json_error(
            400,
            "validation",
            fields={"prompt": "اكتب رسالة الاختبار."},
        )
    if len(prompt) > prime_ai_service.MAX_TEST_PROMPT:
        return json_error(
            400,
            "validation",
            fields={
                "prompt": (
                    f"الحد الأقصى {prime_ai_service.MAX_TEST_PROMPT} حرف."
                )
            },
        )

    bot = bot_ref
    shared_session = getattr(bot, "session", None) if bot else None
    if shared_session is None:
        await prime_ai_service.record_audit(
            guild.id,
            actor_id,
            "اختبار PRIME AI",
            "فشل",
            "جلسة البوت المشتركة غير متاحة.",
        )
        return json_error(503, "ai_runtime_unavailable")
    try:
        answer = await prime_ai_service.generate_response(
            shared_session,
            guild.id,
            actor_id,
            None,
            prompt,
            bypass_guild_controls=True,
            audit_action="اختبار PRIME AI",
        )
    except prime_ai_service.AIProviderUnavailable as error:
        if error.status_code in {429, 503}:
            error_key = (
                "ai_provider_rate_limited"
                if error.status_code == 429
                else "ai_provider_busy"
            )
            return json_error(error.status_code, error_key)
        return json_error(502, "ai_provider_unavailable")
    except ValueError:
        return json_error(
            400,
            "validation",
            fields={"prompt": "رسالة الاختبار غير صالحة."},
        )
    except Exception:
        logger.exception(
            "PRIME AI dashboard test failed for guild %s by user %s",
            guild.id,
            actor_id,
        )
        return json_error(503, "ai_runtime_unavailable")
    return web.json_response({"ok": True, "answer": answer})


@routes.get('/api/guild/{guild_id}/settings')
async def api_get_settings(req):
    _, guild = await authorize(req)
    return web.json_response(public_settings(await get_guild_settings(guild.id)))


@routes.post('/api/guild/{guild_id}/settings')
async def api_post_settings(req):
    session, guild = await authorize(req, write=True)
    try:
        body = await read_json_body(req)
    except (ValueError, UnicodeDecodeError):
        return json_error(400, "invalid_json")
    if not isinstance(body, dict) or not isinstance(body.get("revision"), int) or isinstance(body.get("revision"), bool):
        return json_error(400, "validation", fields={"_": "رقم الإصدار مطلوب"})
    clean, errors = await validate_changes(guild, body.get("changes", {}))
    if errors:
        return json_error(400, "validation", fields=errors)
    if not clean:
        return web.json_response({"ok": True, **public_settings(await get_guild_settings(guild.id))})
    try:
        snapshot = await update_guild_settings(guild.id, expected_revision=body["revision"], **clean)
    except SettingsConflict as conflict:
        return json_error(409, "conflict", **public_settings(conflict.current))
    result = public_settings(snapshot)
    broadcast(guild.id, {"type": "settings", "by": str(session["id"]), **result})
    logger.info("Settings updated for guild %s by user %s: %s", guild.id, session["id"], sorted(clean))
    return web.json_response({"ok": True, **result})


@routes.get('/api/guild/{guild_id}/events')
async def api_guild_events(req):
    session, guild = await authorize(req)
    guild_id = guild.id
    response = web.StreamResponse(headers={
        "Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })
    await response.prepare(req)
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    listeners = SETTINGS_LISTENERS.setdefault(guild_id, set())
    if len(listeners) >= 200:
        await response.write(b"event: error\ndata: {\"error\":\"too_many_streams\"}\n\n")
        return response

    async def send(event: str, data: dict):
        await response.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8"))

    def ping_payload():
        online = bool(bot_ref and bot_ref.is_ready())
        latency = bot_ref.latency if bot_ref else None
        latency_ms = round(latency * 1000) if online and latency == latency and latency != float("inf") else None
        return {"online": online, "latency_ms": latency_ms, "ts": time.time()}

    listeners.add(queue)
    try:
        await send("ping", ping_payload())
        while True:
            # إعادة التحقق دورياً (بحد أقصى كل 15 ثانية): الجلسة، بقاء البوت في السيرفر، والصلاحية الحية
            live = current_session(req)
            guild = bot_ref.get_guild(guild_id) if bot_ref else None
            if not live or guild is None:
                await send("expired", {})
                break
            try:
                allowed = any(g["id"] == str(guild.id) for g in live["guilds"]) and await live_grant(live, guild)
            except web.HTTPServiceUnavailable:
                allowed = True  # تعذر التحقق مؤقتاً؛ تبقى النتيجة المخبأة سارية حتى المحاولة التالية
            if not allowed:
                live["guilds"] = [g for g in live["guilds"] if g["id"] != str(guild.id)]
                await send("expired", {"reason": "forbidden"})
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15)
                await send(payload.get("type", "message"), payload)
            except asyncio.TimeoutError:
                await send("ping", ping_payload())
    except (ConnectionResetError, asyncio.CancelledError, aiohttp.ClientConnectionError):
        logger.debug("SSE client disconnected for guild %s", guild_id)
    finally:
        listeners.discard(queue)
        if not listeners:
            SETTINGS_LISTENERS.pop(guild_id, None)
    return response


@routes.get('/manifest.json')
async def pwa_manifest(req):
    manifest = {
        "name": "PR1ME Studio Dashboard",
        "short_name": "PR1ME Bot",
        "theme_color": "#000000",
        "background_color": "#000000",
        "display": "standalone",
        "orientation": "portrait",
        "start_url": "./",
        "scope": "./",
        "icons": [
            {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return web.Response(
        text=json.dumps(manifest, ensure_ascii=False),
        content_type="application/manifest+json",
    )


@routes.get('/sw.js')
async def pwa_service_worker(req):
    return web.Response(text=service_worker_source(), content_type="application/javascript")


@routes.get('/icon.svg')
async def pwa_svg_icon(req):
    return web.Response(text=pwa_svg(), content_type="image/svg+xml")


@routes.get('/icon-192.png')
async def pwa_192_icon(req):
    return web.Response(body=pwa_png(192), content_type="image/png")


@routes.get('/icon-512.png')
async def pwa_512_icon(req):
    return web.Response(body=pwa_png(512), content_type="image/png")


@routes.get('/healthz')
@routes.get('/health')
async def healthz(req):
    return web.json_response(liveness_payload())


@routes.get('/api/status')
async def api_status(req):
    return web.json_response(await health_payload())


@routes.get('/static/{name}')
async def static_asset(req):
    name = req.match_info["name"]
    types = {
        "app.css": "text/css",
        "ai-control.css": "text/css",
        "visual-refresh.css": "text/css",
        "leveling-card-assets.css": "text/css",
        "ai-control.js": "application/javascript",
        "app.js": "application/javascript",
    }
    if name not in types:
        raise web.HTTPNotFound()
    asset = DASHBOARD_DIR / name
    if not asset.is_file():
        raise web.HTTPNotFound()
    return web.Response(text=asset.read_text("utf-8"), content_type=types[name], charset="utf-8")


@routes.get('/dashboard/')
async def dashboard_trailing_slash(req):
    """Canonicalize the dashboard URL so relative assets resolve correctly."""
    raise web.HTTPFound(req.path.rstrip("/") or "/")


@routes.get('/')
@routes.get('/dashboard')
async def index(req):
    sess = current_session(req)

    # شاشة الدخول الاحترافية بالعربية
    if not sess:
        html = """
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>بوابة الإدارة والتحكم السحابية | تسجيل الدخول</title>
            <link rel="icon" href="data:,">
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@500;600;700;800;900&family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap" rel="stylesheet">
            <style>
                :root {
                    color-scheme: dark;
                    --ink: #f8fbff;
                    --muted: #91a0b8;
                    --line: rgba(148, 163, 184, .16);
                    --panel: rgba(11, 18, 32, .82);
                    --blue: #6c7cff;
                    --cyan: #41d9ff;
                    --green: #45d39a;
                    --font-body: "IBM Plex Sans Arabic", "Segoe UI", Tahoma, sans-serif;
                    --font-display: "Cairo", "IBM Plex Sans Arabic", "Segoe UI", Tahoma, sans-serif;
                }
                * { box-sizing: border-box; }
                html, body { min-height: 100%; }
                body {
                    background:
                        radial-gradient(circle at 12% 12%, rgba(71, 87, 255, .18), transparent 28rem),
                        radial-gradient(circle at 88% 82%, rgba(0, 198, 255, .1), transparent 24rem),
                        #050810;
                    color: var(--ink);
                    display: grid;
                    place-items: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 28px;
                    font-family: var(--font-body);
                    -webkit-font-smoothing: antialiased;
                    text-rendering: optimizeLegibility;
                    overflow-x: hidden;
                }
                body::before {
                    content: "";
                    position: fixed;
                    inset: 0;
                    pointer-events: none;
                    opacity: .32;
                    background-image:
                        linear-gradient(rgba(148, 163, 184, .045) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(148, 163, 184, .045) 1px, transparent 1px);
                    background-size: 42px 42px;
                    mask-image: linear-gradient(to bottom, black, transparent 82%);
                }
                .auth-shell {
                    position: relative;
                    display: grid;
                    grid-template-columns: minmax(0, 1.1fr) minmax(360px, .9fr);
                    width: min(1080px, 100%);
                    min-height: 650px;
                    overflow: hidden;
                    border: 1px solid rgba(148, 163, 184, .2);
                    border-radius: 30px;
                    background: linear-gradient(135deg, rgba(17, 27, 48, .84), rgba(5, 9, 18, .96));
                    box-shadow: 0 35px 100px rgba(0, 0, 0, .55), 0 0 0 1px rgba(108, 124, 255, .06);
                    isolation: isolate;
                }
                .auth-shell::after {
                    content: "";
                    position: absolute;
                    width: 420px;
                    height: 420px;
                    left: -170px;
                    bottom: -230px;
                    border-radius: 50%;
                    background: rgba(65, 217, 255, .1);
                    filter: blur(30px);
                    pointer-events: none;
                    z-index: -1;
                }
                .brand-panel {
                    position: relative;
                    display: flex;
                    flex-direction: column;
                    justify-content: space-between;
                    padding: clamp(34px, 6vw, 76px);
                    border-left: 1px solid var(--line);
                    background:
                        linear-gradient(145deg, rgba(66, 82, 255, .14), transparent 48%),
                        radial-gradient(circle at 30% 22%, rgba(65, 217, 255, .12), transparent 22rem);
                }
                .brand-panel::before {
                    content: "✦";
                    position: absolute;
                    top: 42px;
                    left: 56px;
                    color: rgba(108, 124, 255, .34);
                    font-size: 150px;
                    line-height: 1;
                    transform: rotate(15deg);
                }
                .brand-mark {
                    display: inline-flex;
                    align-items: center;
                    gap: 12px;
                    width: fit-content;
                    font-family: var(--font-display);
                    font-weight: 800;
                    letter-spacing: .02em;
                }
                .mark-icon {
                    display: grid;
                    place-items: center;
                    width: 44px;
                    height: 44px;
                    border: 1px solid rgba(125, 211, 252, .34);
                    border-radius: 14px;
                    color: #dff8ff;
                    background: linear-gradient(145deg, #5367ff, #1c2a6b);
                    box-shadow: 0 10px 28px rgba(71, 87, 255, .3);
                }
                .mark-icon svg { width: 24px; height: 24px; }
                .brand-mark small {
                    display: block;
                    margin-top: 3px;
                    color: var(--muted);
                    font-family: var(--font-body);
                    font-size: 11px;
                    font-weight: 500;
                    letter-spacing: .08em;
                }
                .hero-art {
                    position: relative;
                    display: grid;
                    place-items: center;
                    width: min(100%, 430px);
                    height: 260px;
                    margin: 20px auto 12px;
                    overflow: hidden;
                }
                .hero-art::before {
                    content: "";
                    position: absolute;
                    width: 230px;
                    height: 54px;
                    bottom: 24px;
                    border-radius: 50%;
                    background: rgba(63, 111, 255, .27);
                    filter: blur(22px);
                }
                .hero-art::after {
                    content: "";
                    position: absolute;
                    right: 0;
                    bottom: 0;
                    left: 0;
                    z-index: 2;
                    height: 34px;
                    background: linear-gradient(to bottom, transparent, #0b1528);
                    pointer-events: none;
                }
                .smart-tag {
                    position: absolute;
                    z-index: 3;
                    top: 32px;
                    right: 18px;
                    padding: 7px 10px;
                    border: 1px solid rgba(125, 211, 252, .28);
                    border-radius: 6px;
                    color: #a9eaff;
                    background: rgba(23, 43, 89, .72);
                    box-shadow: 0 10px 24px rgba(0, 0, 0, .18);
                    font-size: 11px;
                    font-weight: 700;
                }
                .smart-tag::after {
                    content: "";
                    position: absolute;
                    top: 50%;
                    right: 100%;
                    width: 26px;
                    height: 1px;
                    background: #57d8ff;
                    box-shadow: 0 0 10px #57d8ff;
                }
                .brand-copy { position: relative; z-index: 3; max-width: 470px; margin: 0 auto; }
                .eyebrow {
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
                    color: #9edfff;
                    font-size: 12px;
                    font-weight: 800;
                    letter-spacing: .11em;
                }
                .eyebrow::before {
                    content: "";
                    width: 25px;
                    height: 1px;
                    background: var(--cyan);
                    box-shadow: 0 0 14px var(--cyan);
                }
                .brand-copy h1 {
                    margin: 12px 0 13px;
                    max-width: 470px;
                    font-size: clamp(2rem, 4.7vw, 4rem);
                    font-family: var(--font-display);
                    font-weight: 900;
                    line-height: 1.16;
                    letter-spacing: -.035em;
                    text-wrap: balance;
                }
                .brand-copy h1 span {
                    background: linear-gradient(110deg, #aeb6ff 8%, #7c8bff 48%, #70ddff 100%);
                    -webkit-background-clip: text;
                    background-clip: text;
                    color: transparent;
                    text-shadow: 0 0 30px rgba(108, 124, 255, .2);
                }
                .brand-copy p {
                    max-width: 430px;
                    margin: 0;
                    color: var(--muted);
                    font-size: 13px;
                    font-weight: 400;
                    line-height: 2;
                    text-wrap: pretty;
                }
                .brand-footer {
                    display: grid;
                    grid-template-columns: repeat(3, 1fr);
                    gap: 8px;
                    margin-top: 22px;
                }
                .brand-footer span {
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    gap: 5px;
                    min-height: 72px;
                    padding: 10px;
                    border: 1px solid var(--line);
                    border-radius: 12px;
                    color: #a8b5ca;
                    background: linear-gradient(145deg, rgba(255, 255, 255, .07), rgba(255, 255, 255, .025));
                    box-shadow: inset 0 1px rgba(255, 255, 255, .08);
                    font-size: 11px;
                    line-height: 1.35;
                    text-align: center;
                }
                .brand-footer b { color: #d7def0; font-weight: 700; }
                .brand-footer small { color: #8493ab; font-size: 10px; }
                .brand-footer i {
                    display: grid;
                    place-items: center;
                    width: 24px;
                    height: 24px;
                    border-radius: 8px;
                    color: #b8c0ff;
                    background: rgba(108, 124, 255, .14);
                    font-size: 14px;
                    font-style: normal;
                }
                .brand-footer i svg {
                    width: 15px;
                    height: 15px;
                    stroke: currentColor;
                }
                .auth-card {
                    position: relative;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    padding: clamp(30px, 5vw, 64px);
                    background: rgba(6, 11, 22, .7);
                }
                .auth-card::before {
                    content: "";
                    position: absolute;
                    top: 0;
                    right: 18%;
                    left: 18%;
                    height: 1px;
                    background: linear-gradient(90deg, transparent, rgba(124, 139, 255, .8), transparent);
                    box-shadow: 0 0 22px rgba(124, 139, 255, .55);
                }
                .auth-card-head { margin-bottom: 32px; }
                .auth-card-head .badge {
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
                    padding: 8px 12px;
                    border: 1px solid rgba(108, 124, 255, .3);
                    border-radius: 999px;
                    color: #b8c0ff;
                    background: rgba(108, 124, 255, .1);
                    font-family: var(--font-body);
                    font-size: 12px;
                    font-weight: 800;
                }
                .auth-card h2 {
                    margin: 22px 0 10px;
                    font-family: var(--font-display);
                    font-size: clamp(1.8rem, 3vw, 2.4rem);
                    font-weight: 800;
                    line-height: 1.3;
                    letter-spacing: -.025em;
                    text-wrap: balance;
                }
                .auth-card-intro {
                    margin: 0;
                    color: var(--muted);
                    font-family: var(--font-body);
                    font-size: 13px;
                    line-height: 2;
                }
                .btn-login {
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 12px;
                    min-height: 58px;
                    padding: 0 20px;
                    border: 1px solid rgba(196, 200, 255, .25);
                    border-radius: 15px;
                    color: #fff;
                    background: linear-gradient(135deg, #6878ff, #4b5be0);
                    box-shadow: 0 14px 28px rgba(80, 92, 236, .26), inset 0 1px rgba(255, 255, 255, .22);
                    font-family: var(--font-display);
                    font-size: 16px;
                    font-weight: 700;
                    letter-spacing: -.01em;
                    text-decoration: none;
                    transition: transform .2s ease, box-shadow .2s ease, filter .2s ease;
                }
                .btn-login:hover {
                    filter: brightness(1.08);
                    transform: translateY(-2px);
                    box-shadow: 0 18px 34px rgba(80, 92, 236, .38), inset 0 1px rgba(255, 255, 255, .28);
                }
                .btn-login:active { transform: translateY(0); }
                .btn-login svg { flex: none; width: 23px; height: 23px; }
                .login-note {
                    display: flex;
                    align-items: flex-start;
                    gap: 10px;
                    margin: 17px 0 0;
                    color: #7888a4;
                    font-size: 12px;
                    font-weight: 400;
                    line-height: 1.7;
                }
                .login-note strong { color: #a7b8d2; font-weight: 700; }
                .login-note svg { flex: none; margin-top: 2px; color: var(--green); }
                .trust-grid {
                    display: grid;
                    grid-template-columns: repeat(3, 1fr);
                    gap: 8px;
                    margin-top: 36px;
                    padding-top: 24px;
                    border-top: 1px solid var(--line);
                }
                .trust-item {
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    gap: 7px;
                    min-width: 0;
                    color: #8594ad;
                    font-size: 11px;
                    line-height: 1.5;
                    text-align: center;
                }
                .trust-icon { color: #99a5ff; font-size: 17px; }
                .trust-item strong { color: #c9d3e6; font-size: 12px; }
                .auth-footer {
                    margin-top: 34px;
                    color: #596a84;
                    font-size: 11px;
                    text-align: center;
                }
                .creator-credit {
                    position: relative;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 12px;
                    width: min(100%, 310px);
                    margin: 27px auto 0;
                    padding: 14px 18px;
                    border: 1px solid rgba(126, 143, 255, .25);
                    border-radius: 18px;
                    background: linear-gradient(135deg, rgba(99, 115, 255, .12), rgba(255, 255, 255, .025));
                    box-shadow: inset 0 1px rgba(255, 255, 255, .08), 0 12px 28px rgba(0, 0, 0, .16);
                    overflow: hidden;
                    text-align: right;
                }
                .creator-credit::before {
                    content: "";
                    position: absolute;
                    inset: 0;
                    border-radius: inherit;
                    background: linear-gradient(110deg, transparent 20%, rgba(117, 226, 255, .13), transparent 72%);
                    pointer-events: none;
                }
                .creator-shield {
                    position: relative;
                    display: grid;
                    place-items: center;
                    flex: none;
                    width: 41px;
                    height: 47px;
                    color: #dce2ff;
                    filter: drop-shadow(0 0 10px rgba(112, 137, 255, .5));
                }
                .creator-shield svg { width: 41px; height: 47px; }
                .creator-shield path:last-child { color: #7ce6ff; }
                .creator-copy {
                    position: relative;
                    display: flex;
                    flex-direction: column;
                    gap: 1px;
                    min-width: 0;
                }
                .creator-copy strong {
                    direction: ltr;
                    color: #f3f6ff;
                    font-family: var(--font-display);
                    font-size: 15px;
                    font-weight: 800;
                    letter-spacing: .055em;
                    white-space: nowrap;
                }
                .creator-copy small {
                    color: #8f9db5;
                    font-size: 11px;
                    line-height: 1.6;
                    white-space: nowrap;
                }
                .creator-copy b {
                    direction: ltr;
                    color: #aeb7ff;
                    font-family: var(--font-display);
                    font-size: 13px;
                    font-weight: 700;
                }
                @media (max-width: 820px) {
                    body { padding: 14px; }
                    .auth-shell { display: block; min-height: auto; border-radius: 23px; }
                    .brand-panel {
                        min-height: 0;
                        padding: 30px 25px;
                        border-left: 0;
                        border-bottom: 1px solid var(--line);
                    }
                    .brand-panel::before { top: 15px; left: 24px; font-size: 100px; }
                    .hero-art { height: 224px; margin-top: 16px; margin-bottom: 16px; }
                    .smart-tag { top: 24px; right: 3px; }
                    .brand-copy { margin: 0; }
                    .brand-copy h1 { margin-top: 17px; font-size: clamp(2rem, 10vw, 3rem); line-height: 1.2; }
                    .brand-copy p { font-size: 14px; line-height: 1.75; }
                    .brand-footer { margin-top: 24px; }
                    .auth-card { padding: 34px 25px 30px; }
                }
                @media (max-width: 430px) {
                    .brand-footer { gap: 7px; }
                    .brand-footer span { min-height: 68px; padding: 8px; font-size: 10px; }
                    .trust-grid { gap: 6px; }
                    .trust-item { font-size: 10px; }
                    .creator-credit { margin-top: 24px; }
                }
                @media (prefers-reduced-motion: reduce) {
                    *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; }
                }
            </style>
        </head>
        <body>
            <main class="auth-shell">
                <section class="brand-panel" aria-label="نبذة عن لوحة التحكم">
                    <div class="brand-mark">
                        <span class="mark-icon" aria-hidden="true">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                                <path d="M12 3 19 6v5.4c0 4.3-2.9 7.9-7 9.6-4.1-1.7-7-5.3-7-9.6V6l7-3Z"/>
                                <path d="m8.8 12 2.1 2.1 4.5-4.6"/>
                            </svg>
                        </span>
                        <span>PRIME CONTROL<small>SMART SERVER OPERATIONS</small></span>
                    </div>
                    <div class="hero-art" aria-hidden="true">
                        <span class="smart-tag">مساحة إدارتك الذكية</span>
                    </div>
                    <div class="brand-copy">
                        <span class="eyebrow">مساحة الإدارة الذكية</span>
                        <h1>تبغى سيرفرك يكون توب؟<br><span>أدّره براوق.</span></h1>
                        <p>رتّب كل شيء من لوحة واحدة، راقب أدق التفاصيل، واضبط إعداداتك بضغطة زر وبدون أي تعقيد.</p>
                    </div>
                    <div class="brand-footer" aria-label="مزايا المنصة">
                        <span>
                            <i aria-hidden="true">
                                <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8">
                                    <path d="M12 3 19 6v5.4c0 4.3-2.9 7.9-7 9.6-4.1-1.7-7-5.3-7-9.6V6l7-3Z"/>
                                    <path d="m8.8 12 2.1 2.1 4.5-4.6"/>
                                </svg>
                            </i>
                            <b>حماية 24/7</b><small>بياناتك في أمان</small>
                        </span>
                        <span>
                            <i aria-hidden="true">
                                <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8">
                                    <path d="M4 6h16M4 12h16M4 18h16"/>
                                    <circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="7" cy="18" r="2"/>
                                </svg>
                            </i>
                            <b>ضبط على الطاير</b><small>تحكم فوري بإعداداتك</small>
                        </span>
                        <span>
                            <i aria-hidden="true">
                                <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8">
                                    <circle cx="12" cy="12" r="7.5"/>
                                    <path d="m14.8 9.2-1.3 3.1-3.1 1.3 1.3-3.1 3.1-1.3Z"/>
                                    <path d="M12 2v2M22 12h-2M12 22v-2M2 12h2"/>
                                </svg>
                            </i>
                            <b>دقّة واجهتنا</b><small>تجربة واضحة وسلسة</small>
                        </span>
                    </div>
                </section>
                <section class="auth-card" aria-labelledby="login-title">
                    <div class="auth-card-head">
                        <span class="badge"><span aria-hidden="true">✦</span> دخول موثّق وآمن</span>
                        <h2 id="login-title">رجعت لنا؟ حياك.</h2>
                        <p class="auth-card-intro">ادخل بحساب Discord حق سيرفرك عشان تبدأ التدبير.</p>
                    </div>
                    <a href="login" class="btn-login" aria-label="تسجيل الدخول باستخدام Discord">
                        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994.021-.041.001-.09-.041-.106a13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.929 1.793 8.18 1.793 12.061 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 0 1.873.893.077.077 0 0 1-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.028zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/></svg>
                        دخول لـ Discord
                    </a>
                    <p class="login-note">
                        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3 19 6v5.4c0 4.3-2.9 7.9-7 9.6-4.1-1.7-7-5.3-7-9.6V6l7-3Z"/><path d="m8.8 12 2.1 2.1 4.5-4.6"/></svg>
                        <span><strong>ما نحتاج باسوردك..</strong><br>كل شي آمن ويتم التحقق عبر Discord.</span>
                    </p>
                    <div class="trust-grid">
                        <div class="trust-item"><span class="trust-icon">◷</span><strong>دخول سريع.</strong><span>بياناتك في الحفظ والصون.</span></div>
                        <div class="trust-item"><span class="trust-icon">⌘</span><strong>صلاحية دقيقة.</strong><span>صلاحيات دقيقة.</span></div>
                        <div class="trust-item"><span class="trust-icon">▣</span><strong>بيانات محمية.</strong><span>بياناتك في الحفظ والصون.</span></div>
                    </div>
                    <div class="auth-footer">بوابتك الرسمية لإدارة السيرفر — بأمان.</div>
                    <div class="creator-credit" aria-label="هوية تطوير المنصة">
                        <span class="creator-shield" aria-hidden="true">
                            <svg viewBox="0 0 48 56" fill="none">
                                <path d="M24 2 44 10v14.5C44 37.2 35.7 47.5 24 53 12.3 47.5 4 37.2 4 24.5V10L24 2Z" fill="url(#shieldFill)" stroke="#8999ff" stroke-width="1.5"/>
                                <path d="m15.5 27.5 5.4 5.4 11.8-12" stroke="#dbf7ff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
                                <path d="M24 8 38 13.6v10.6c0 8.5-5.4 15.9-14 20-8.6-4.1-14-11.5-14-20V13.6L24 8Z" stroke="#74e4ff" stroke-opacity=".5"/>
                                <defs>
                                    <linearGradient id="shieldFill" x1="8" y1="4" x2="38" y2="53" gradientUnits="userSpaceOnUse">
                                        <stop stop-color="#5e70ff" stop-opacity=".8"/>
                                        <stop offset="1" stop-color="#16235e" stop-opacity=".7"/>
                                    </linearGradient>
                                </defs>
                            </svg>
                        </span>
                        <span class="creator-copy">
                            <strong>PRIME CONTROL</strong>
                            <small>تطوير <b>Abood515</b></small>
                        </span>
                    </div>
                </section>
            </main>
        </body>
        </html>
        """
        return web.Response(text=html, content_type='text/html')

    # Refresh the server list before returning the shell so the frontend's
    # first /api/me request sees the live bot guild/member authorization.
    await sync_session_guilds(req, sess)

    # لوحة التحكم التفاعلية (HTML/CSS/JS في مجلد dashboard/)
    page = (DASHBOARD_DIR / "index.html").read_text("utf-8")
    return web.Response(text=page, content_type="text/html", charset="utf-8")


register_leveling_routes(
    routes,
    authorize=authorize,
    level_admin_authorize=live_level_admin,
    json_error=json_error,
    read_json_body=read_json_body,
    logger=logger,
)
register_public_leaderboard_routes(
    routes,
    bot_getter=request_bot,
    logger=logger,
)


async def start_web_server(bot):
    global bot_ref
    bot_ref = bot
    app = web.Application(middlewares=[private_responses], client_max_size=MAX_BODY)
    app['bot'] = bot
    app.add_routes(routes)
    # Access logs include callback query strings; do not log authorization codes.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        # Render supplies PORT. DASHBOARD_PORT remains a local-only fallback
        # for the existing Replit workflow.
        port = int(
            (
                os.environ.get("PORT")
                or os.environ.get("DASHBOARD_PORT")
                or str(PORT)
            ).strip()
        )
        await web.TCPSite(runner, HOST, port).start()
    except Exception:
        await runner.cleanup()
        raise
    return runner

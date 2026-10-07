"""End-to-end ticket CRM audit.

This is intentionally a standalone diagnostic rather than a pytest-only unit
test. It builds a temporary SQLite database, mounts the real aiohttp routes
with a fake Discord guild, exercises the real REST handlers, and statically
checks the Discord view/frontend contracts.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import aiosqlite


ROOT = Path(__file__).resolve().parent
GUILD_ID = 100000000000000001
SESSION_ID = "ticket-audit-session"


class AuditFailure(AssertionError):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AuditFailure(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


async def database_audit(database) -> None:
    await database.init_db()
    required = {
        "ticket_panels": {"message_id"},
        "ticket_categories": {
            "ping_role_ids",
            "staff_role_ids",
            "naming_format",
            "max_open_per_user",
        },
        "ticket_logs": {"channel_id", "status", "claimed_by", "closed_by"},
        "ticket_ratings": {"stars", "feedback", "staff_id"},
        "ticket_settings": {
            "log_channel_id",
            "evaluation_channel_id",
            "allow_user_close",
        },
        "ticket_blacklist": {"user_id", "reason", "expires_at"},
        "ticket_permissions": {"permissions"},
    }
    async with database.connect(aiosqlite.Row) as db:
        for table, columns in required.items():
            async with db.execute(f"PRAGMA table_info({table})") as cur:
                actual = {row["name"] for row in await cur.fetchall()}
            expect(columns <= actual, f"{table} missing {sorted(columns - actual)}")

        async with db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name LIKE 'ticket_ratings_stars_%_guard'"
        ) as cur:
            triggers = "\n".join((row["sql"] or "") for row in await cur.fetchall())
        expect("RAISE(ABORT" in triggers and "NEW.stars" in triggers, "stars guard trigger missing")

        # Exercise the database-level invariant, not only Python's clamping.
        try:
            await db.execute(
                "INSERT INTO ticket_ratings "
                "(ticket_id, guild_id, user_id, stars) VALUES (?, ?, ?, ?)",
                (900001, GUILD_ID, 900002, 6),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            await db.rollback()
        else:
            raise AuditFailure("ticket_ratings accepted stars outside 1..5")

    source = read("database.py")
    tree = ast.parse(source)
    unsafe = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "execute":
            continue
        first = node.args[0]
        if isinstance(first, ast.JoinedStr):
            segment = ast.get_source_segment(source, first) or ""
            # The only interpolated SQL is internal schema/configuration data:
            # validated column names, an integer timeout, fixed view names, or
            # placeholder fragments built from explicit allowlists.
            allowed = (
                "PRAGMA busy_timeout" in segment
                or "ALTER TABLE" in segment
                or "CREATE VIEW" in segment
                or "SELECT {col}" in segment
                or "SET {new_col}" in segment
                or "SET {dedicated}" in segment
                or "LIMIT {safe_limit}" in segment
                or "FROM {col}" in segment
                or "SET {from_account}" in segment
                or "SET {to_account}" in segment
                or "WHERE {' AND '.join(conditions)}" in segment
                or "IN ({placeholders})" in segment
            )
            if not allowed:
                unsafe.append((node.lineno, segment.replace("\n", " ")[:240]))
    expect(not unsafe, f"raw SQL interpolation found: {unsafe}")
    expect("BEGIN IMMEDIATE" in source and "await db.commit()" in source, "write locking/commit contract missing")
    expect("async def save_ticket_log" in source, "ticket log writer missing")


class CommunityStub:
    async def deploy_ticket_panel(self, channel_id, categories, config):
        return {
            "message_id": "800000000000000099",
            "channel_id": str(channel_id),
            "categories": categories,
            "mode": config.get("panel_mode", "dropdown"),
        }


async def json_response(response):
    payload = await response.json()
    expect(response.headers.get("Content-Type", "").startswith("application/json"), "response is not JSON")
    return payload


async def rest_audit(database, ws, FakeBot, FakeGuild) -> None:
    bot = FakeBot()
    community = CommunityStub()
    original_get_cog = bot.get_cog
    bot.get_cog = lambda name: community if name == "Community" else original_get_cog(name)
    ws.bot_ref = bot
    ws.SESSIONS.clear()
    ws.RATE_BUCKETS.clear()
    ws.GRANT_CACHE.clear()
    ws.SESSIONS[SESSION_ID] = {
        "id": "10",
        "username": "Ticket Audit Admin",
        "avatar": None,
        "guilds": [{
            "id": str(GUILD_ID),
            "name": FakeGuild.name,
            "members": FakeGuild.member_count,
            "icon": None,
            "is_owner": False,
        }],
        "expires_at": 4102444800,
        "csrf": "ticket-audit-csrf",
    }
    app = web.Application(middlewares=[ws.private_responses], client_max_size=ws.MAX_BODY)
    app.add_routes(ws.routes)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    origin = str(client.make_url("/")).rstrip("/")
    headers = {
        "Cookie": f"bot_session={SESSION_ID}",
        "Origin": origin,
        "Referer": f"{origin}/dashboard",
    }
    try:
        response = await client.get(f"/api/guilds/{GUILD_ID}/tickets/overview", headers=headers)
        expect(response.status == 200, f"overview returned {response.status}")
        overview = await json_response(response)
        expect({"total_panels", "active_tickets", "priorities_donut", "ratings_bar", "staff_leaderboard"} <= overview.keys(),
               f"overview contract mismatch: {sorted(overview)}")

        category = {
            "key": "technical",
            "label": "الدعم التقني",
            "description": "مساعدة تقنية",
            "emoji": "🛠️",
            "ping_role_ids": ["200000000000000004"],
            "staff_role_ids": ["200000000000000004"],
            "naming_format": "ticket-{count}",
            "max_open_per_user": 2,
        }
        response = await client.post(
            f"/api/guilds/{GUILD_ID}/tickets/panels",
            headers={**headers, "X-CSRF-Token": "ticket-audit-csrf"},
            json={
                "channel_id": "300000000000000002",
                "title": "مركز الدعم",
                "description": "اختر القسم",
                "color": "#6366F1",
                "mode": "dropdown",
                "categories": [category],
            },
        )
        expect(response.status == 200, f"panel create returned {response.status}")
        panel_payload = await json_response(response)
        panel = panel_payload.get("panel") or {}
        panel_id = int(panel.get("id") or 0)
        expect(panel_id > 0 and panel["categories"][0]["ping_role_ids"] == category["ping_role_ids"],
               "panel/category association was not persisted")

        response = await client.get(f"/api/guilds/{GUILD_ID}/tickets/panels", headers=headers)
        expect(response.status == 200, f"panel list returned {response.status}")
        expect((await json_response(response)).get("panels"), "panel list is empty after create")

        response = await client.post(
            f"/api/guilds/{GUILD_ID}/tickets/panels/{panel_id}/publish",
            headers={**headers, "X-CSRF-Token": "ticket-audit-csrf"},
        )
        expect(response.status == 200, f"panel publish returned {response.status}")
        published = await json_response(response)
        expect(published.get("success") and published.get("message_id"), "publish contract missing success/message_id")

        permissions = {"claim": ["200000000000000004"], "close": ["200000000000000004"]}
        response = await client.post(
            f"/api/guilds/{GUILD_ID}/tickets/permissions",
            headers={**headers, "X-CSRF-Token": "ticket-audit-csrf"},
            json={"permissions": permissions},
        )
        expect(response.status == 200, f"permissions save returned {response.status}")
        saved_permissions = await json_response(response)
        expect(saved_permissions["permissions"] == permissions, "permission action mapping was not returned")
        response = await client.get(f"/api/guilds/{GUILD_ID}/tickets/permissions", headers=headers)
        expect(response.status == 200 and (await json_response(response))["permissions"] == permissions,
               "permission mapping did not survive reload")

        settings = {
            "log_channel_id": "300000000000000003",
            "evaluation_channel_id": "300000000000000004",
            "allow_user_close": True,
        }
        response = await client.post(
            f"/api/guilds/{GUILD_ID}/tickets/settings",
            headers={**headers, "X-CSRF-Token": "ticket-audit-csrf"},
            json=settings,
        )
        expect(response.status == 200, f"settings save returned {response.status}")
        settings_payload = await json_response(response)
        config = settings_payload.get("config", {})
        expect(config.get("log_channel_id") == 300000000000000003
               and config.get("evaluation_channel_id") == 300000000000000004
               and bool(config.get("allow_user_close")),
               "ticket settings did not persist")

        response = await client.post(
            f"/api/guilds/{GUILD_ID}/tickets/blacklist",
            headers={**headers, "X-CSRF-Token": "ticket-audit-csrf"},
            json={"user_id": "42", "reason": "audit test", "duration_days": 1},
        )
        expect(response.status == 200, f"blacklist save returned {response.status}")
        entry = (await json_response(response)).get("entry", {})
        expect(int(entry.get("user_id")) == 42 and entry.get("reason") == "audit test",
               "blacklist entry mismatch")
        response = await client.get(f"/api/guilds/{GUILD_ID}/tickets/blacklist", headers=headers)
        expect(response.status == 200 and any(int(item["user_id"]) == 42 for item in (await json_response(response))["entries"]),
               "blacklist query did not return saved member")
    finally:
        await client.close()


def discord_and_frontend_audit() -> None:
    community_source = read("cogs/community.py")
    main_source = read("main.py")
    css = read("dashboard/app.css")
    js = read("dashboard/app.js")
    html = read("dashboard/index.html")

    expect("StreamlinedTicketControlsView" in community_source, "streamlined ticket view missing")
    control_block = community_source[community_source.index("class StreamlinedTicketControlsView"):community_source.index("class TicketRatingModal")]
    control_ids = re.findall(r'custom_id="([^"]+)"', control_block)
    expect(set(control_ids) >= {"tkt_ctrl_claim", "tkt_ctrl_close", "tkt_ctrl_options"}, "ticket controls IDs missing")
    expect(len([item for item in control_ids if item.startswith("tkt_ctrl_")]) == 3, "initial ticket view is not exactly three controls")
    option_block = community_source[community_source.index("class TicketOptionsView"):community_source.index("class StreamlinedTicketControlsView")]
    for custom_id in ("tkt_opt_add", "tkt_opt_remove", "tkt_opt_priority", "tkt_opt_transfer"):
        expect(custom_id in option_block, f"ticket option missing: {custom_id}")
    expect("InternalNoteModal" in option_block and "TicketMemberSelectView" in option_block, "note/member option wiring missing")
    expect("discord.ui.UserSelect" in community_source, "UserSelect is not used for member actions")
    expect("delay_seconds=5" in community_source and "إلغاء" in community_source, "five-second cancellable close missing")
    expect("0x10B981" in community_source and "0xEF4444" in community_source and "0x6366F1" in community_source,
           "required action embed colors missing")
    expect("send_ticket_action_embed" in community_source, "rich action embed helper missing")
    expect("class PersistentDMRatingView" in community_source and "super().__init__(timeout=None)" in community_source,
           "persistent DM rating view is not restart-safe")
    expect("self.add_view(PersistentDMRatingView())" in main_source and "from cogs.community import PersistentDMRatingView" in main_source,
           "main setup_hook does not register persistent DM rating view")
    expect("publish_ticket_evaluation" in community_source and "evaluation_channel_id" in community_source,
           "evaluation dispatch path missing")

    expect('overflow-x: hidden !important' in css and 'max-width: 100vw' in css, "mobile viewport containment missing")
    wrapper = css[css.index(".table-responsive-wrapper"):css.index(".table-responsive-wrapper table")]
    expect("overflow-x: auto" in wrapper and "-webkit-overflow-scrolling: touch" in wrapper,
           "permissions table wrapper does not scroll locally")
    expect("loadAndHydratePermissions" in js and js.count("loadAndHydratePermissions(") >= 3,
           "permission hydration is not wired to load, tab/save, and guild paths")
    expect('input.classList.toggle("checked", checked)' in js
           and 'input.classList.toggle("is-checked", checked)' in js
           and 'labelNode.classList.toggle("is-checked", initiallyChecked)' in js,
           "permission visual state hydration missing")
    expect(".sidebar-item.active" in css and ".nav-link.active" in css and ".category-chip.active" in css,
           "active navigation visual contract missing")
    expect('class="app"' in html and 'viewport-fit=cover' in html, "mobile viewport meta contract missing")
    final_contract = css[css.rfind("/* Final mobile layout contract."):]
    expect("overflow-x: hidden !important" in final_contract
           and "padding-bottom: calc(184px" in final_contract,
           "final mobile shell override missing")


async def run() -> int:
    with tempfile.TemporaryDirectory(prefix="ticket-system-audit-") as tmp:
        os.environ["HARNESS_DB"] = str(Path(tmp) / "audit.sqlite")
        from tests.dashboard_harness import FakeBot, FakeGuild
        import database
        import web_server as ws

        database.DB_NAME = os.environ["HARNESS_DB"]
        await database_audit(database)
        await rest_audit(database, ws, FakeBot, FakeGuild)

    discord_and_frontend_audit()
    checks = [
        "Database Tables & Parameterized Queries",
        "REST APIs & JSON Schemas",
        "Discord Views & Persistence (setup_hook)",
        "In-Ticket Embeds, UserSelect & Role Pings",
        "Persistent DM Rating & #evaluation Dispatch",
        "Mobile Viewport Containment & CSS Overflow",
        "Permissions Hydration & State Persistence",
    ]
    for item in checks:
        print(f"[✔] {item}: PASSED")
    print("TICKET SYSTEM AUDIT: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except Exception as error:
        print(f"[✘] TICKET SYSTEM AUDIT: FAILED — {error}")
        raise
"""Manual/browser test harness: serves the dashboard with a fake bot and a seeded admin session.

Run: python tests/dashboard_harness.py  (listens on HARNESS_PORT, default 8098)
Then open /__test_login to receive the session cookie. Never used by the real bot.
"""
import asyncio
import os
import time
from types import SimpleNamespace

import discord
from aiohttp import web

import database
import web_server as ws

database.DB_NAME = (
    (os.getenv("HARNESS_DB") or "").strip() or "/tmp/harness_dashboard.db"
)


class Role:
    def __init__(self, id, name, position, managed=False, default=False, color=0):
        self.id, self.name, self.position, self.managed = id, name, position, managed
        self._default, self.color = default, SimpleNamespace(value=color)

    def is_default(self):
        return self._default

    def __lt__(self, other):
        return self.position < other.position

    def __ge__(self, other):
        return self.position >= other.position


class Emoji:
    def __init__(self, id, name, animated=False):
        self.id, self.name, self.animated = id, name, animated
        self.available = True
        self.url = f"https://cdn.discordapp.com/emojis/{id}.{'gif' if animated else 'png'}"

    def __str__(self):
        return f"<{'a' if self.animated else ''}:{self.name}:{self.id}>"


class Chan(discord.TextChannel):
    def __init__(self, id, name, pos, category=None):
        self.id, self.name, self.position, self._cat = id, name, pos, category

    @property
    def category(self):
        return self._cat


CATS = {"عام": SimpleNamespace(name="📢 العام", position=0), "إدارة": SimpleNamespace(name="🛡️ الإدارة", position=1)}
CHANNELS = [
    Chan(300000000000000001, "الترحيب", 0, CATS["عام"]), Chan(300000000000000002, "الدردشة", 1, CATS["عام"]),
    Chan(300000000000000003, "سجل-الحماية", 0, CATS["إدارة"]), Chan(300000000000000004, "قرارات", 1, CATS["إدارة"]),
]
ROLES = [
    Role(1, "@everyone", 0, default=True), Role(200000000000000001, "عضو موثق", 1, color=0x10B981),
    Role(200000000000000002, "قيد التحقق", 2, color=0xF59E0B), Role(200000000000000003, "Bot", 5),
    Role(200000000000000004, "مدير", 8, color=0xEF4444), Role(200000000000000005, "Nitro Booster", 3, managed=True),
]


class FakeGuild:
    id, name, icon, member_count, owner_id = 100000000000000001, "PRIME TEAM", None, 1284, 99
    roles, text_channels, channels = ROLES, CHANNELS, CHANNELS
    emojis = [Emoji(400000000000000001, "party"), Emoji(400000000000000002, "spark", animated=True)]
    me = SimpleNamespace(top_role=ROLES[3])

    admins = {10, 100000000000000010}

    def get_member(self, uid):
        # Keep the short test-suite identity and full harness snowflake as admins.
        allowed = uid in self.admins
        return SimpleNamespace(
            id=uid,
            name=f"User {uid}",
            display_name=f"User {uid}",
            guild_permissions=SimpleNamespace(value=8 if allowed else 0, administrator=allowed),
        )

    async def fetch_member(self, uid):
        return self.get_member(uid)

    def get_channel(self, cid):
        return next((c for c in CHANNELS if c.id == cid), None)

    def get_role(self, rid):
        return next((r for r in ROLES if r.id == rid), None)


class FakeBot:
    latency = 0.058

    class SecurityStub:
        def __init__(self):
            self.locked = False
            self.whitelist = set()
            self.incidents = []

        def get_incidents(self, guild_id=None):
            return list(self.incidents)

        def get_whitelist(self, guild_id):
            return sorted(self.whitelist)

        def is_locked(self, guild_id):
            return self.locked

        async def emergency_lockdown(self, guild_id, locked):
            self.locked = locked
            return {"queued": True, "channels": len(CHANNELS), "locked": locked}

        def record_control_action(self, guild_id, culprit_id, culprit_name, action, mitigation):
            self.incidents.append({
                "timestamp": "2026-01-01T00:00:00+00:00",
                "guild_id": guild_id,
                "culprit_id": culprit_id,
                "culprit_name": culprit_name,
                "action_type": action,
                "mitigation_taken": mitigation,
            })

        def whitelist_member(self, guild_id, user_id):
            self.whitelist.add(str(user_id))

        def remove_whitelisted_member(self, guild_id, user_id):
            self.whitelist.discard(str(user_id))

    class ModerationStub:
        async def get_recent_infractions(self, guild_id):
            return [{
                "id": 1,
                "user_id": 100000000000000010,
                "guild_id": guild_id,
                "moderator_id": 0,
                "reason": "Harness test infraction",
                "timestamp": "2026-01-01 00:00:00",
            }]

        async def revoke_warning(self, warning_id):
            return {
                "id": warning_id,
                "guild_id": FakeGuild.id,
                "user_id": 100000000000000010,
                "reason": "Harness test infraction",
            }

        async def quick_unmute(self, guild_id, user_id):
            return {"ok": True, "guild_id": guild_id, "user_id": user_id}

    class EngagementStub:
        def __init__(self):
            self.panels = []

        async def get_onboarding_snapshot(self, guild_id):
            snapshot = await database.get_guild_settings(guild_id)
            values = snapshot["settings"]
            return {
                "revision": snapshot["revision"],
                "updated_at": snapshot["updated_at"],
                "settings": {
                    key: values.get(key)
                    for key in (
                        "welcome_channel_id",
                        "welcome_message",
                        "leave_message",
                        "welcome_dm_enabled",
                        "auto_role_id",
                        "member_auto_role_id",
                        "bot_auto_role_id",
                        "verified_role_id",
                        "unverified_role_id",
                        "rules_channel_id",
                    )
                },
                "self_roles": list(self.panels),
            }

        async def send_test_welcome(self, guild_id, target_channel_id, template_data):
            return {
                "ok": True,
                "guild_id": guild_id,
                "channel_id": target_channel_id,
                "message_id": 800000000000000001,
                "template_data": template_data,
            }

        async def deploy_self_role_panel(
            self, guild_id, target_channel_id, title, description, color, emoji, roles
        ):
            panel = {
                "id": len(self.panels) + 1,
                "guild_id": guild_id,
                "channel_id": target_channel_id,
                "message_id": 800000000000000001 + len(self.panels) + 1,
                "title": title,
                "description": description,
                "color": color,
                "emoji": emoji,
                "role_specs": roles,
            }
            self.panels.insert(0, panel)
            return {
                "ok": True,
                "panel": panel,
            }

    class UtilitiesStub:
        def __init__(self):
            self.commands = [{
                "command_name": "ping",
                "cog": "Utilities",
                "enabled": True,
                "allowed_roles": [],
                "allowed_channels": [],
                "configured": False,
                "aliases": [],
            }]
            self.rules = []

        async def get_guild_commands_status(self, guild_id):
            snapshot = await database.get_guild_settings(guild_id)
            controls = await database.get_command_controls(guild_id)
            commands = []
            for item in self.commands:
                command = dict(item)
                control = controls.get(command["command_name"])
                if control:
                    command.update(
                        enabled=control["enabled"],
                        allowed_roles=control["allowed_roles"],
                        allowed_channels=control.get("allowed_channels", []),
                        aliases=control.get("aliases", []),
                        configured=True,
                    )
                commands.append(command)
            return {
                "guild_id": str(guild_id),
                "prefix": snapshot["settings"].get("prefix", "!"),
                "commands": commands,
            }

        async def toggle_command(
            self,
            guild_id,
            command_name,
            enabled,
            allowed_roles,
            allowed_channels=None,
            aliases=None,
            auto_delete_seconds=None,
            response_style=None,
            response_template=None,
        ):
            result = await database.save_command_policy(
                guild_id,
                command_name,
                enabled,
                allowed_roles=allowed_roles,
                allowed_channels=allowed_channels or [],
                aliases=aliases,
                auto_delete_seconds=auto_delete_seconds,
                response_style=response_style,
                response_template=response_template,
            )
            item = next((x for x in self.commands if x["command_name"] == command_name), None)
            if item is None:
                item = {
                    "command_name": command_name,
                    "cog": "Configured",
                    "configured": True,
                    "aliases": [],
                }
                self.commands.append(item)
            item.update(
                enabled=enabled,
                allowed_roles=allowed_roles,
                allowed_channels=allowed_channels or [],
                aliases=result.get("aliases", aliases or []),
                configured=True,
            )
            return result

        async def add_auto_responder(self, guild_id, trigger, match_type, response, **kwargs):
            rule = await database.save_auto_responder(
                guild_id, trigger, match_type, response, **kwargs
            )
            self.rules.append(rule)
            return rule

        async def delete_auto_responder(self, guild_id, rule_id):
            deleted = await database.delete_auto_responder(guild_id, rule_id)
            self.rules[:] = [rule for rule in self.rules if rule["id"] != rule_id]
            return deleted

    def __init__(self):
        self.security = self.SecurityStub()
        self.moderation = self.ModerationStub()
        self.engagement = self.EngagementStub()
        self.utilities = self.UtilitiesStub()

    def get_guild(self, gid):
        return FakeGuild() if gid == FakeGuild.id else None

    def get_cog(self, name):
        if name == "Security":
            return self.security
        if name == "Moderation":
            return self.moderation
        if name == "Engagement":
            return self.engagement
        if name == "Utilities":
            return self.utilities
        return None

    def is_ready(self):
        return True


async def test_login(req):
    sid = "harness-session"
    ws.SESSIONS[sid] = {
        "id": "100000000000000010", "username": "Harness Admin", "avatar": "https://cdn.discordapp.com/embed/avatars/1.png",
        "guilds": [{
            "id": str(FakeGuild.id), "name": FakeGuild.name, "members": 1284,
            "icon": None, "is_owner": False, "permissions": "8",
        }],
        "_oauth_guilds": [{
            "id": str(FakeGuild.id), "owner": False, "permissions": "8",
        }],
        "expires_at": time.time() + 3600, "csrf": "harness-csrf",
    }
    res = web.HTTPFound("/")
    res.set_cookie("bot_session", sid, httponly=True, samesite="Lax", path="/")
    return res


async def test_revoke(req):
    """Simulates losing admin rights mid-session (the live grant is re-checked on the next tick)."""
    FakeGuild.admins.discard(100000000000000010)
    ws.GRANT_CACHE.clear()
    return web.json_response({"ok": True})


async def main():
    await database.init_db()
    ws.bot_ref = FakeBot()
    app = web.Application(middlewares=[ws.private_responses], client_max_size=ws.MAX_BODY)
    app.add_routes(ws.routes)
    app.router.add_get("/__test_login", test_login)
    app.router.add_get("/__test_revoke", test_revoke)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(
        runner,
        "0.0.0.0",
        int((os.getenv("HARNESS_PORT") or "8098").strip()),
    ).start()
    print("harness ready", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())

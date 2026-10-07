import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import database
from cogs.utilities import Utilities, dynamic_prefix


class FakeChannel:
    id = 300
    name = "general"
    mention = "#general"

    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))
        return SimpleNamespace(id=900)


class FakeAuthor:
    id = 88
    mention = "<@88>"
    bot = False

    def __init__(self, administrator=False, roles=None):
        self.guild_permissions = SimpleNamespace(administrator=administrator)
        self.roles = [SimpleNamespace(id=role_id) for role_id in (roles or [])]


class FakeGuild:
    id = 700
    name = "Orchestrator Guild"
    member_count = 42


class FakeMessage:
    def __init__(self, content, author=None):
        self.content = content
        self.author = author or FakeAuthor()
        self.guild = FakeGuild()
        self.channel = FakeChannel()


class FakeBot:
    def __init__(self):
        self.commands = []
        self.guilds = []
        self.checks = []
        self.user = SimpleNamespace(id=999)
        self.tree = SimpleNamespace(
            interaction_check=self._tree_check,
            get_command=lambda name: None,
        )

    async def _tree_check(self, interaction):
        return True

    def add_check(self, check, **kwargs):
        self.checks.append(check)

    def remove_check(self, check):
        self.checks.remove(check)

    def get_command(self, name):
        return next(
            (
                command
                for command in self.commands
                if str(getattr(command, "name", "")).casefold() == str(name).casefold()
            ),
            None,
        )

    async def get_context(self, message):
        async def invoke(command, **kwargs):
            await command.callback(context, **kwargs)

        context = SimpleNamespace(
            message=message,
            guild=message.guild,
            author=message.author,
            channel=message.channel,
            command=None,
            send=message.channel.send,
            invoke=invoke,
        )
        return context


class UtilitiesOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = "/tmp/test_utilities_orchestrator.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()
        self.bot = FakeBot()
        self.cog = Utilities(self.bot)

    async def asyncTearDown(self):
        await self.cog.cog_unload()

    async def test_dynamic_prefix_reads_shared_settings_cache(self):
        await database.update_guild_settings(700, prefix="?")
        prefixes = await dynamic_prefix(self.bot, FakeMessage("hello"))
        self.assertIn("?", prefixes)
        self.assertNotIn("!", prefixes)

    async def test_prefixless_rank_mention_is_narrowly_recognized(self):
        for content in ("لفل <@123>", "لفل <@!123>"):
            prefixes = await dynamic_prefix(self.bot, FakeMessage(content))
            self.assertEqual(prefixes[0], "")
        for content in ("لفل", "لفل عضو", "مرحبا لفل <@123>", "!لفل <@123>"):
            prefixes = await dynamic_prefix(self.bot, FakeMessage(content))
            self.assertNotIn("", prefixes)

    async def test_prefixless_rank_mention_skips_dashboard_shortcut_dispatch(self):
        message = FakeMessage("لفل <@123>")
        with (
            patch.object(self.cog, "_dispatch_policy_alias", new=AsyncMock()) as alias_dispatch,
            patch.object(self.cog, "_dispatch_shortcut", new=AsyncMock()) as shortcut_dispatch,
        ):
            await self.cog.on_message(message)
        alias_dispatch.assert_not_awaited()
        shortcut_dispatch.assert_not_awaited()

    async def test_command_interceptor_blocks_disabled_and_roleless_commands(self):
        command = SimpleNamespace(qualified_name="secret")
        ctx = SimpleNamespace(
            guild=FakeGuild(),
            command=command,
            author=FakeAuthor(),
        )
        await self.cog.toggle_command(700, "secret", False, [])
        with self.assertRaises(Exception):
            await self.cog.command_interceptor(ctx)

        await self.cog.toggle_command(700, "secret", True, [123])
        with self.assertRaises(Exception):
            await self.cog.command_interceptor(ctx)
        ctx.author = FakeAuthor(roles=[123])
        self.assertTrue(await self.cog.command_interceptor(ctx))

    async def test_exact_contains_regex_variables_and_cooldown(self):
        await self.cog.add_auto_responder(
            700,
            "hello",
            "exact",
            "Hi {user} in {channel} at {server} ({members})",
            cooldown_seconds=60,
        )
        await self.cog.add_auto_responder(
            700,
            r"^price\\s+\\d+$",
            "regex",
            "price",
        )
        await database.save_auto_responder(
            700,
            "keyword",
            "contains",
            "{random:one|two}",
            cooldown_seconds=0,
        )
        await self.cog.sync_auto_responders(700)
        self.assertTrue(
            self.cog._matches(
                {"trigger": "hello", "match_type": "exact"},
                " Hello ",
            )
        )
        self.assertTrue(
            self.cog._matches(
                {
                    "trigger": r"^price\s+\d+$",
                    "match_type": "regex",
                    "_compiled": re.compile(r"^price\s+\d+$", re.I),
                },
                "price 20",
            )
        )
        rendered = self.cog.render_response(
            "Hi {user} {channel} {server} {members} {random:a|b}",
            FakeMessage("hello"),
        )
        self.assertIn("<@88>", rendered)
        self.assertIn("#general", rendered)
        self.assertIn("Orchestrator Guild", rendered)
        self.assertRegex(rendered, r"\b(?:a|b)\b")

        message = FakeMessage("hello")
        await self.cog.on_message(message)
        await self.cog.on_message(message)
        self.assertEqual(len(message.channel.sent), 1)

    async def test_shortcut_announcement_is_cached_and_rendered(self):
        await self.cog.add_shortcut(
            700,
            "rules",
            "announcement",
            announcement="Read the rules in {channel}, {user}.",
        )
        message = FakeMessage("rules")
        await self.cog.on_message(message)
        self.assertEqual(len(message.channel.sent), 1)
        self.assertIn("Read the rules", message.channel.sent[0][1]["embed"].description)

    async def test_command_help_shortcut_renders_a_smart_embed(self):
        command = SimpleNamespace(
            name="warn",
            description="تحذير عضو",
            aliases=[],
            parameters=[
                SimpleNamespace(name="member", required=True),
                SimpleNamespace(name="reason", required=True),
            ],
        )
        self.bot.tree.get_command = lambda name: command if name == "warn" else None
        await self.cog.add_shortcut(700, "عيب", "help", target="/warn")
        await self.cog.add_shortcut(700, "تحذير", "help", target="/warn")

        message = FakeMessage("عيب")
        await self.cog.on_message(message)
        embed = message.channel.sent[0][1]["embed"]
        self.assertEqual(embed.title, "تحذير 📖")
        self.assertIn("عيب", embed.fields[0].value)
        self.assertIn("عيب @أحمد", embed.fields[1].value)
        self.assertIn("تحذير", embed.fields[2].value)
        self.assertIn("طرد الأعضاء", embed.fields[3].value)

    async def test_command_shortcut_executes_the_real_slash_callback(self):
        async def callback(interaction):
            await interaction.response.send_message("نفّذ الأمر فعلياً")

        command = SimpleNamespace(
            name="warn",
            qualified_name="warn",
            callback=callback,
            binding=None,
        )
        self.bot.tree.get_command = lambda name: command if name == "warn" else None
        await self.cog.add_shortcut(700, "تحذير", "command", target="/warn")

        message = FakeMessage("تحذير")
        await self.cog.on_message(message)

        self.assertEqual(len(message.channel.sent), 1)
        self.assertEqual(message.channel.sent[0][0], "نفّذ الأمر فعلياً")

    async def test_grouped_command_alias_keeps_the_qualified_slash_target(self):
        executed = []

        async def callback(interaction):
            executed.append(interaction.command.qualified_name)
            await interaction.response.send_message("نفّذ الأمر المجمع فعلياً")

        async def check_can_run(interaction):
            return True

        command = SimpleNamespace(
            name="lock",
            qualified_name="chat lock",
            callback=callback,
            binding=None,
            parameters=[],
            _check_can_run=check_can_run,
        )
        self.bot.tree.get_command = lambda name: command if name == "chat lock" else None
        self.bot.tree.walk_commands = lambda: [command]
        await self.cog.toggle_command(700, "chat lock", True, aliases=["قفل_المحادثة"])

        message = FakeMessage("قفل_المحادثة")
        await self.cog.on_message(message)

        self.assertEqual(executed, ["chat lock"])
        self.assertEqual(message.channel.sent[0][0], "نفّذ الأمر المجمع فعلياً")

    async def test_command_shortcut_executes_prefix_command_with_context(self):
        executed = []

        async def callback(ctx):
            executed.append(ctx)
            await ctx.send("نفّذ أمر Prefix فعلياً")

        command = SimpleNamespace(
            name="راتب",
            qualified_name="راتب",
            callback=callback,
            aliases=["يومي"],
            clean_params={},
        )
        self.bot.commands.append(command)
        await self.cog.add_shortcut(700, "راتب", "command", target="/راتب")

        message = FakeMessage("راتب")
        await self.cog.on_message(message)

        self.assertEqual(len(executed), 1)
        self.assertEqual(message.channel.sent[0][0], "نفّذ أمر Prefix فعلياً")

    async def test_command_shortcut_cannot_bypass_channel_policy(self):
        executed = []

        async def callback(interaction):
            executed.append(True)
            await interaction.response.send_message("لا يجب أن يصل هنا")

        command = SimpleNamespace(
            name="warn",
            qualified_name="warn",
            callback=callback,
            binding=None,
        )
        self.bot.tree.get_command = lambda name: command if name == "warn" else None
        await self.cog.toggle_command(700, "warn", True, [], [301])
        await self.cog.add_shortcut(700, "تحذير", "command", target="/warn")

        message = FakeMessage("تحذير")
        await self.cog.on_message(message)

        self.assertEqual(executed, [])
        self.assertIn("غير مسموح", message.channel.sent[0][0])

    async def test_bare_command_keyword_shows_adaptive_help_when_arguments_are_missing(self):
        command = SimpleNamespace(
            name="warn",
            qualified_name="warn",
            description="تحذير عضو",
            aliases=["تحذير"],
            parameters=[SimpleNamespace(name="member", required=True)],
        )
        self.bot.tree.walk_commands = lambda: [command]
        self.bot.tree.get_command = lambda name: command if name == "warn" else None

        message = FakeMessage("warn")
        await self.cog.on_message(message)

        self.assertEqual(len(message.channel.sent), 1)
        embed = message.channel.sent[0][1]["embed"]
        self.assertIn("الصيغة", embed.fields[0].name)
        self.assertIn("warn", embed.fields[0].value)

    async def test_policy_alias_parses_typed_slash_arguments_and_runs_checks(self):
        target = SimpleNamespace(
            id=42,
            name="Ahmed",
            display_name="Ahmed",
            global_name="Ahmed",
            mention="<@42>",
            bot=False,
        )
        checks = []

        async def callback(interaction, member, minutes, reason="غير محدد"):
            checks.append((member, minutes, reason, interaction.namespace.member))
            await interaction.response.send_message("تم التنفيذ")

        async def check_can_run(interaction):
            checks.append(("checked", interaction.permissions))
            return True

        command = SimpleNamespace(
            name="demo",
            qualified_name="demo",
            callback=callback,
            binding=None,
            parameters=[
                SimpleNamespace(name="member", required=True, type=6),
                SimpleNamespace(name="minutes", required=True, type=4),
                SimpleNamespace(name="reason", required=False, type=3),
            ],
            _check_can_run=check_can_run,
        )
        self.bot.tree.get_command = lambda name: command if name == "demo" else None
        self.bot.tree.walk_commands = lambda: [command]
        await self.cog.toggle_command(700, "demo", True, aliases=["run"])
        message = FakeMessage("run 42 2h policy reason")
        message.guild.get_member = lambda member_id: target if member_id == 42 else None

        await self.cog.on_message(message)

        self.assertEqual(checks[0][0], "checked")
        self.assertIs(checks[1][0], target)
        self.assertEqual(checks[1][1], 120)
        self.assertEqual(checks[1][2], "policy reason")
        self.assertIs(checks[1][3], target)
        self.assertEqual(len(message.channel.sent), 2)
        self.assertIn("تفاصيل العملية", message.channel.sent[1][1]["embed"].fields[0].name)

    async def test_policy_alias_does_not_confirm_callback_error(self):
        async def callback(interaction):
            await interaction.response.send_message("❌ تعذر تنفيذ العملية")

        async def check_can_run(interaction):
            return True

        command = SimpleNamespace(
            name="demo",
            qualified_name="demo",
            callback=callback,
            binding=None,
            parameters=[],
            _check_can_run=check_can_run,
        )
        self.bot.tree.get_command = lambda name: command if name == "demo" else None
        await self.cog.toggle_command(700, "demo", True, aliases=["run"])

        message = FakeMessage("run")
        await self.cog.on_message(message)

        self.assertEqual(len(message.channel.sent), 2)
        self.assertIn("❌", message.channel.sent[0][0])
        self.assertEqual(message.channel.sent[1][1]["embed"].title, "تعذر تنفيذ الاختصار")


if __name__ == "__main__":
    unittest.main()
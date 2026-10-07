import asyncio
import inspect
import logging
import os
import random
import re
import shlex
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    add_warning,
    get_auto_responders,
    get_command_controls,
    get_command_policies,
    get_guild_settings,
    get_shortcuts,
    delete_auto_responder,
    record_auto_responder_execution,
    save_auto_responder,
    save_command_control,
    save_command_policy,
    save_shortcut,
)
from interaction_runtime import InteractionProxy, defer_if_needed


HUB_NAME = "➕ اضغط للإنشاء"
LOGGER = logging.getLogger("UtilitiesOrchestrator")
MATCH_TYPES = {"exact", "contains", "regex"}
SHORTCUT_TYPES = {"command", "help", "announcement"}
COMMAND_HELP_LABELS = {
    "warn": "تحذير",
    "timeout": "تايم أوت",
    "untimeout": "فك التايم أوت",
    "warnings": "سجل التحذيرات",
    "unwarn": "إلغاء تحذير",
    "clear": "مسح الرسائل",
    "lockdown": "قفل المحادثة",
    "slowmode": "الوضع البطيء",
}
COMMAND_PERMISSION_LABELS = {
    "warn": "طرد الأعضاء",
    "timeout": "إدارة الأعضاء",
    "untimeout": "إدارة الأعضاء",
    "warnings": "إدارة الرسائل",
    "unwarn": "إدارة الرسائل",
    "clear": "إدارة الرسائل",
    "lockdown": "إدارة القنوات",
    "slowmode": "إدارة القنوات",
}
BOT_ACTION_PERMISSIONS = {
    "timeout": "moderate_members",
    "untimeout": "moderate_members",
    "warn": "moderate_members",
    "clear": "manage_messages",
    "slowmode": "manage_channels",
    "lockdown": "manage_channels",
}
TARGET_HIERARCHY_COMMANDS = {"timeout", "untimeout", "warn"}
COMMAND_PARAMETER_LABELS = {
    "member": "عضو",
    "user": "عضو",
    "reason": "السبب",
    "minutes": "الدقائق",
    "duration": "المدة",
    "channel": "القناة",
    "category": "القسم",
    "question": "السؤال",
}
SENSITIVE_COMMAND_NAMES = {
    "ban",
    "clear",
    "kick",
    "lockdown",
    "mute",
    "nuke",
    "purge",
    "unban",
    "unmute",
    "warn",
}


async def dynamic_prefix(
    bot: commands.Bot,
    message: discord.Message,
) -> list[str]:
    """Resolve mentions plus the guild prefix from the shared settings cache."""
    prefix = "!"
    if message.guild is not None:
        try:
            snapshot = await get_guild_settings(message.guild.id)
            configured = snapshot["settings"].get("prefix")
            if isinstance(configured, str) and configured.strip():
                prefix = configured.strip()[:5]
        except Exception:
            LOGGER.exception("[PREFIX] تعذر قراءة بادئة السيرفر %s", message.guild.id)
    prefixes = commands.when_mentioned_or(prefix)(bot, message)
    if message.guild is not None and is_bare_rank_mention(message.content):
        # Keep the no-prefix exception narrowly scoped to the exact Arabic rank
        # alias followed by a Discord member mention.
        return ["", *prefixes]
    return prefixes


def is_bare_rank_mention(content: str | None) -> bool:
    """Match only the requested prefixless ``لفل @member`` message form."""
    return bool(re.fullmatch(r"لفل\s+<@!?[0-9]+>", str(content or "").strip()))


class CommandIntercepted(commands.CheckFailure):
    """A command was intentionally blocked by a guild policy."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class TokenBucket:
    tokens: float
    updated_at: float


class ShortcutInteractionResponse:
    def __init__(self, interaction: "ShortcutInteraction"):
        self.interaction = interaction
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, **kwargs):
        self._done = True

    async def send_message(self, content=None, **kwargs):
        self._done = True
        self.interaction._record_response(content, kwargs)
        # Discord does not support ephemeral messages in a regular channel.
        # Shortcut execution is a message-driven adapter, so discard this
        # interaction-only flag instead of passing it to TextChannel.send().
        kwargs.pop("ephemeral", None)
        await self.interaction.channel.send(content, **kwargs)


class ShortcutFollowup:
    def __init__(self, interaction: "ShortcutInteraction"):
        self.interaction = interaction

    async def send(self, content=None, **kwargs):
        self.interaction._record_response(content, kwargs)
        kwargs.pop("ephemeral", None)
        return await self.interaction.channel.send(content, **kwargs)


class ShortcutInteraction:
    """Interaction adapter used when a command is triggered by message text."""

    def __init__(self, message: discord.Message, command=None):
        self.message = message
        self.user = message.author
        self.guild = message.guild
        self.channel = message.channel
        self.channel_id = message.channel.id
        self.command = command
        self.client = message._state._get_client() if getattr(message, "_state", None) else None
        self.namespace = SimpleNamespace()
        self.permissions = getattr(message.author, "guild_permissions", None)
        if self.permissions is None:
            permissions_for = getattr(message.channel, "permissions_for", None)
            self.permissions = (
                permissions_for(message.author)
                if permissions_for is not None
                else discord.Permissions.none()
            )
        self._shortcut_adapter = True
        self.response_error = False
        self.response_messages: list[tuple[Any, dict[str, Any]]] = []
        self.response = ShortcutInteractionResponse(self)
        self.followup = ShortcutFollowup(self)

    def _record_response(self, content: Any, kwargs: dict[str, Any]) -> None:
        self.response_messages.append((content, dict(kwargs)))
        embed = kwargs.get("embed")
        values = [str(content or "")]
        if embed is not None:
            values.extend(
                str(getattr(embed, attribute, "") or "")
                for attribute in ("title", "description")
            )
        text = " ".join(values)
        failure_markers = (
            "❌",
            "⛔",
            "⚠️ الاستخدام",
            "غير مسموح",
            "معطل",
            "تعذر",
            "فشل",
            "لا يمكن",
            "لا تملك",
            "ليس لديك",
            "لم أجد",
        )
        if any(marker in text for marker in failure_markers):
            self.response_error = True


class VoiceControl(discord.ui.View):
    def __init__(self, channel: discord.VoiceChannel, owner: discord.Member):
        super().__init__(timeout=None)
        self.channel, self.owner = channel, owner

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if (
            itx.user.id != self.owner.id
            and not itx.user.guild_permissions.administrator
        ):
            await itx.response.send_message(
                "❌ التحكم متاح لمالك الروم فقط!",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="قفل الروم",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
    )
    async def lock(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        overwrite = self.channel.overwrites_for(itx.guild.default_role)
        overwrite.connect = False
        await self.channel.set_permissions(
            itx.guild.default_role,
            overwrite=overwrite,
        )
        await itx.response.send_message(
            "🔒 تم قفل الروم ومنع الدخول.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="فتح الروم",
        style=discord.ButtonStyle.success,
        emoji="🔓",
    )
    async def unlock(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        overwrite = self.channel.overwrites_for(itx.guild.default_role)
        overwrite.connect = None
        await self.channel.set_permissions(
            itx.guild.default_role,
            overwrite=overwrite,
        )
        await itx.response.send_message(
            "🔓 تم فتح الروم للجميع.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="تحديد 5 أعضاء",
        style=discord.ButtonStyle.secondary,
        emoji="👥",
    )
    async def limit(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        await self.channel.edit(user_limit=5)
        await itx.response.send_message(
            "👥 تم ضبط الحد الأقصى إلى 5 أعضاء.",
            ephemeral=True,
        )


class Utilities(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.temp_voice = {}
        self.auto_responders: dict[int, list[dict[str, Any]]] = {}
        self.shortcuts: dict[int, list[dict[str, Any]]] = {}
        self.command_controls: dict[int, dict[str, dict[str, Any]]] = {}
        self.command_aliases: dict[int, dict[str, tuple[str, dict[str, Any]]]] = {}
        self._cooldowns: dict[tuple[int, int, int], TokenBucket] = {}
        self._check_registered = False
        self._original_tree_check = getattr(self.bot.tree, "interaction_check", None)
        self.bot.tree.interaction_check = self.app_command_interceptor
        self.bot.add_check(self.command_interceptor)
        self._check_registered = True

    async def cog_unload(self):
        if self._check_registered:
            self.bot.remove_check(self.command_interceptor)
            self._check_registered = False
        if self._original_tree_check is not None:
            self.bot.tree.interaction_check = self._original_tree_check

    async def get_guild_commands_status(self, guild_id: int) -> dict[str, Any]:
        """Return command policy state merged with the commands currently loaded."""
        guild_id = int(guild_id)
        controls = await self._load_command_controls(guild_id)
        snapshot = await get_guild_settings(guild_id)
        known = {}
        for command in self.bot.commands:
            if command.hidden:
                continue
            known[command.qualified_name] = {
                "command_name": command.qualified_name,
                "cog": getattr(command, "cog_name", None) or "Commands",
                "enabled": True,
                "allowed_roles": [],
                "allowed_channels": [],
                "configured": False,
                "aliases": list(command.aliases),
                "permission_warnings": [],
            }
        for command in self.bot.tree.walk_commands():
            if getattr(command, "hidden", False):
                continue
            binding = getattr(command, "binding", None)
            known.setdefault(
                command.qualified_name,
                {
                    "command_name": command.qualified_name,
                    "cog": binding.__class__.__name__ if binding else "Slash Commands",
                    "enabled": True,
                    "allowed_roles": [],
                    "allowed_channels": [],
                    "configured": False,
                    "aliases": [],
                    "permission_warnings": [],
                },
            )
        for name, control in controls.items():
            item = known.setdefault(
                name,
                {
                    "command_name": name,
                    "cog": "Configured",
                    "enabled": True,
                    "allowed_roles": [],
                    "allowed_channels": [],
                    "configured": False,
                    "aliases": [],
                    "permission_warnings": [],
                },
            )
            item.update(
                enabled=bool(control["enabled"]),
                allowed_roles=list(control["allowed_roles"]),
                allowed_channels=list(control.get("allowed_channels", [])),
                custom_aliases=list(control.get("aliases", [])),
                auto_delete_seconds=int(control.get("auto_delete_seconds") or 0),
                response_style=str(control.get("response_style") or "default"),
                response_template=str(control.get("response_template") or ""),
                configured=True,
                updated_at=control.get("updated_at"),
            )
        for item in known.values():
            command_name = str(item["command_name"]).lower().split()[-1]
            if (
                item["enabled"]
                and not item["allowed_roles"]
                and not item.get("allowed_channels")
                and command_name in SENSITIVE_COMMAND_NAMES
            ):
                item["permission_warnings"] = [
                    "أمر حساس متاح لجميع الأعضاء؛ قيّده برتبة إدارة أو إشراف."
                ]
        return {
            "guild_id": str(guild_id),
            "prefix": snapshot["settings"].get("prefix", "!"),
            "commands": sorted(known.values(), key=lambda item: item["command_name"]),
        }

    async def _load_command_controls(
        self,
        guild_id: int,
        *,
        refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        guild_id = int(guild_id)
        controls = await get_command_policies(guild_id, refresh=refresh)
        self.command_controls[guild_id] = controls
        aliases = {}
        for command_name, policy in controls.items():
            for alias in policy.get("aliases", []):
                aliases[str(alias).casefold()] = (command_name, policy)
        self.command_aliases[guild_id] = aliases
        return controls

    async def toggle_command(
        self,
        guild_id: int,
        command_name: str,
        enabled: bool,
        allowed_roles: list[int | str] | None = None,
        allowed_channels: list[int | str] | None = None,
        aliases: list[str] | None = None,
        auto_delete_seconds: int | None = None,
        response_style: str | None = None,
        response_template: str | None = None,
    ) -> dict[str, Any]:
        """Persist and publish a command's enabled/role policy."""
        name = str(command_name).strip().lower()
        if not name or len(name) > 100:
            raise ValueError("command_name must be a non-empty command name")
        roles = [str(role_id) for role_id in (allowed_roles or []) if str(role_id).isdigit()]
        if len(roles) > 25:
            raise ValueError("allowed_roles cannot contain more than 25 roles")
        channels = [str(channel_id) for channel_id in (allowed_channels or []) if str(channel_id).isdigit()]
        if len(channels) > 25:
            raise ValueError("allowed_channels cannot contain more than 25 channels")
        if aliases is not None:
            aliases = [
                str(alias).strip().lstrip("!/")
                for alias in aliases
                if str(alias).strip()
            ]
            if len(aliases) > 20 or any(
                len(alias) > 80 or any(char.isspace() for char in alias)
                for alias in aliases
            ):
                raise ValueError("aliases must contain up to 20 single words")
        result = await save_command_policy(
            guild_id,
            name,
            bool(enabled),
            allowed_roles=roles,
            allowed_channels=channels,
            aliases=aliases,
            auto_delete_seconds=auto_delete_seconds,
            response_style=response_style,
            response_template=response_template,
        )
        result["custom_aliases"] = list(result.get("aliases", []))
        self.command_controls.setdefault(int(guild_id), {})[name] = result
        self.command_aliases[int(guild_id)] = {
            str(alias).casefold(): (command, policy)
            for command, policy in self.command_controls[int(guild_id)].items()
            for alias in policy.get("aliases", [])
        }
        return result

    async def sync_auto_responders(self, guild_id: int) -> dict[str, int]:
        """Refresh the in-memory trigger and shortcut registries for one guild."""
        guild_id = int(guild_id)
        responders = await get_auto_responders(guild_id)
        valid = []
        for responder in responders:
            if responder["match_type"] not in MATCH_TYPES:
                LOGGER.warning("[AUTORESPONDER] نوع مطابقة غير معروف: %s", responder)
                continue
            if responder["match_type"] == "regex":
                try:
                    responder["_compiled"] = re.compile(
                        responder["trigger"], re.IGNORECASE
                    )
                except re.error:
                    LOGGER.warning(
                        "[AUTORESPONDER] Regex غير صالح في السيرفر %s: %s",
                        guild_id,
                        responder["trigger"],
                    )
                    continue
            valid.append(responder)
        self.auto_responders[guild_id] = valid
        self.shortcuts[guild_id] = await get_shortcuts(guild_id)
        self._prune_buckets(guild_id)
        return {
            "responders": len(valid),
            "shortcuts": len(self.shortcuts[guild_id]),
        }

    async def add_auto_responder(
        self,
        guild_id: int,
        trigger: str,
        match_type: str,
        response: str,
        *,
        enabled: bool = True,
        cooldown_seconds: float = 5.0,
        bucket_capacity: int = 1,
        channel_id: int | str | None = None,
        target_type: str = "everyone",
        target_id: int | str | None = 0,
        reaction_emoji: str = "",
    ) -> dict[str, Any]:
        """Management helper for creating a trigger without touching SQL."""
        match_type = str(match_type).strip().lower()
        trigger = str(trigger).strip()
        if match_type not in MATCH_TYPES:
            raise ValueError(f"match_type must be one of {sorted(MATCH_TYPES)}")
        if not trigger or len(trigger) > 500:
            raise ValueError("trigger must contain 1-500 characters")
        if match_type == "regex":
            re.compile(trigger, re.IGNORECASE)
        if len(str(response)) > 2000:
            raise ValueError("response must contain at most 2000 characters")
        target_type = str(target_type).strip().lower()
        if target_type not in {"everyone", "role", "user"}:
            raise ValueError("target_type must be one of everyone, role, user")
        try:
            target_id = max(0, int(target_id or 0))
        except (TypeError, ValueError) as error:
            raise ValueError("target_id must be a numeric Discord ID") from error
        if target_type != "everyone" and target_id <= 0:
            raise ValueError("target_id is required for role and user targets")
        item = await save_auto_responder(
            guild_id,
            trigger,
            match_type,
            response,
            enabled=enabled,
            cooldown_seconds=cooldown_seconds,
            bucket_capacity=bucket_capacity,
            channel_id=channel_id,
            target_type=target_type,
            target_id=target_id,
            reaction_emoji=reaction_emoji,
        )
        await self.sync_auto_responders(guild_id)
        return item

    async def delete_auto_responder(self, guild_id: int, rule_id: int) -> bool:
        deleted = await delete_auto_responder(guild_id, rule_id)
        if deleted:
            await self.sync_auto_responders(guild_id)
        return deleted

    async def add_shortcut(
        self,
        guild_id: int,
        trigger: str,
        target_type: str,
        *,
        target: str = "",
        announcement: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        target_type = str(target_type).strip().lower()
        trigger = str(trigger).strip()
        if target_type not in SHORTCUT_TYPES:
            raise ValueError(f"target_type must be one of {sorted(SHORTCUT_TYPES)}")
        if not trigger or len(trigger) > 100:
            raise ValueError("shortcut trigger must contain 1-100 characters")
        if target_type == "command" and not str(target).strip():
            raise ValueError("command shortcuts need a target command")
        if target_type == "announcement" and not str(announcement).strip():
            raise ValueError("announcement shortcuts need announcement text")
        item = await save_shortcut(
            guild_id,
            trigger,
            target_type,
            target=target,
            announcement=announcement,
            enabled=enabled,
        )
        await self.sync_auto_responders(guild_id)
        return item

    async def command_interceptor(self, ctx: commands.Context) -> bool:
        """Apply the guild command matrix before a prefix command is invoked."""
        if ctx.guild is None or ctx.command is None:
            return True
        guild_id = int(ctx.guild.id)
        controls = self.command_controls.get(guild_id)
        if controls is None:
            controls = await self._load_command_controls(guild_id)
        control = controls.get(ctx.command.qualified_name.lower())
        if not control:
            return True
        member = ctx.author
        permissions = getattr(member, "guild_permissions", None)
        if permissions and getattr(permissions, "administrator", False):
            return True
        if not control["enabled"]:
            raise CommandIntercepted(
                f"الأمر `{ctx.command.qualified_name}` معطّل في هذا السيرفر."
            )
        allowed_channels = {
            str(channel_id) for channel_id in control.get("allowed_channels", [])
        }
        if allowed_channels and str(ctx.channel.id) not in allowed_channels:
            raise CommandIntercepted(
                "⚠️ هذا الأمر مخصص للعمل في قنوات محددة فقط."
            )
        allowed_roles = {str(role_id) for role_id in control["allowed_roles"]}
        if allowed_roles:
            member_roles = {
                str(role.id) for role in getattr(member, "roles", [])
            }
            if not member_roles.intersection(allowed_roles):
                raise CommandIntercepted(
                    "⛔ ليس لديك الرتبة المسموح لها باستخدام هذا الأمر."
                )
        return True

    async def app_command_interceptor(self, interaction: discord.Interaction) -> bool:
        """Apply the same policy to Slash Commands before Discord invokes them."""
        if interaction.guild is None or interaction.command is None:
            return True
        # ACK before loading policy state or touching any guild data. The
        # command runtime will pass a follow-up-aware proxy to the callback.
        await defer_if_needed(interaction)
        if self._original_tree_check is not None:
            try:
                original_allowed = await self._original_tree_check(
                    InteractionProxy(interaction, deferred=True)
                )
            except Exception:
                LOGGER.exception("[COMMAND_POLICY] original interaction check failed")
                return False
            if original_allowed is False:
                return False
        environment_error = self._command_environment_error(interaction)
        if environment_error:
            await self._send_policy_denial(interaction, environment_error)
            return False
        guild_id = int(interaction.guild.id)
        controls = self.command_controls.get(guild_id)
        if controls is None:
            controls = await self._load_command_controls(guild_id)
        command_name = interaction.command.qualified_name.lower()
        leaf_name = str(getattr(interaction.command, "name", "")).lower()
        control = controls.get(command_name) or controls.get(leaf_name)
        if not control:
            return True
        permissions = getattr(interaction.user, "guild_permissions", None)
        if permissions and getattr(permissions, "administrator", False):
            return True
        reason = None
        if not control["enabled"]:
            reason = f"الأمر `/{interaction.command.qualified_name}` معطّل في هذا السيرفر."
        else:
            allowed_channels = {
                str(channel_id) for channel_id in control.get("allowed_channels", [])
            }
            if allowed_channels and str(interaction.channel_id) not in allowed_channels:
                reason = (
                    "⚠️ هذا الأمر مخصص للعمل في قنوات محددة فقط. "
                    "غير مسموح في هذه القناة."
                )
            allowed_roles = {str(role_id) for role_id in control["allowed_roles"]}
            member_roles = {
                str(role.id) for role in getattr(interaction.user, "roles", [])
            }
            if not reason and allowed_roles and not member_roles.intersection(allowed_roles):
                reason = (
                    "⛔ ليس لديك الرتبة المسموح لها باستخدام هذا الأمر."
                )
        if reason:
            await self._send_policy_denial(interaction, reason)
            return False
        return True

    @staticmethod
    def _command_environment_error(interaction: discord.Interaction) -> str | None:
        """Check channel bot permissions and target hierarchy before callbacks."""
        command_name = str(
            getattr(getattr(interaction, "command", None), "qualified_name", "")
        ).lower()
        required_permission = BOT_ACTION_PERMISSIONS.get(command_name)
        guild = getattr(interaction, "guild", None)
        channel = getattr(interaction, "channel", None)
        me = getattr(guild, "me", None)
        if required_permission and me is not None and channel is not None:
            try:
                permissions = channel.permissions_for(me)
            except (AttributeError, TypeError):
                permissions = None
            if permissions is not None and not getattr(permissions, required_permission, False):
                return f"❌ البوت لا يملك صلاحية `{required_permission}` في هذه القناة."

        if command_name not in TARGET_HIERARCHY_COMMANDS:
            return None
        namespace = getattr(interaction, "namespace", None)
        target = getattr(namespace, "member", None)
        if target is None or me is None:
            return None
        target_top = getattr(target, "top_role", None)
        bot_top = getattr(me, "top_role", None)
        user_top = getattr(getattr(interaction, "user", None), "top_role", None)
        if target_top is None or bot_top is None:
            return None
        if getattr(target, "id", None) == getattr(me, "id", None):
            return "❌ لا يمكن للبوت تنفيذ هذا الإجراء على نفسه."
        if target_top >= bot_top:
            return "❌ رتبة البوت يجب أن تكون أعلى من رتبة العضو المستهدف."
        guild_owner_id = getattr(guild, "owner_id", None)
        if (
            getattr(interaction.user, "id", None) != guild_owner_id
            and user_top is not None
            and target_top >= user_top
        ):
            return "❌ رتبتك يجب أن تكون أعلى من رتبة العضو المستهدف."
        return None

    @staticmethod
    async def _send_policy_denial(interaction: discord.Interaction, reason: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(reason, ephemeral=True)
            else:
                await interaction.response.send_message(reason, ephemeral=True)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            LOGGER.error("[COMMAND_POLICY] تعذر إرسال رسالة الرفض", exc_info=True)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in list(self.bot.guilds):
            try:
                await self.sync_auto_responders(guild.id)
                await self.get_guild_commands_status(guild.id)
            except Exception:
                LOGGER.exception(
                    "[ORCHESTRATOR] تعذر مزامنة إعدادات السيرفر %s", guild.id
                )

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, CommandIntercepted):
            try:
                await ctx.send(f"⛔ {error.reason}", delete_after=7)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.debug("[COMMAND_POLICY] تعذر إرسال رسالة الحظر", exc_info=True)
            return
        LOGGER.error(
            "[COMMAND] فشل أمر Prefix %s في السيرفر %s",
            getattr(getattr(ctx, "command", None), "qualified_name", "unknown"),
            getattr(getattr(ctx, "guild", None), "id", None),
            exc_info=(type(error), error, error.__traceback__),
        )
        try:
            await ctx.send(
                "⚠️ تعذر تنفيذ الأمر. تم تسجيل الخطأ للمراجعة.",
                delete_after=7,
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.error("[COMMAND] تعذر إرسال رسالة الخطأ", exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if is_bare_rank_mention(message.content):
            # The bot's normal prefix-command dispatcher owns this exact
            # built-in alias; do not also route it through dashboard shortcuts.
            return
        guild_id = int(message.guild.id)
        if await self._dispatch_policy_alias(message):
            return
        if guild_id not in self.auto_responders:
            await self.sync_auto_responders(guild_id)
        content = str(message.content or "")
        if not content.strip():
            return
        if await self._dispatch_shortcut(message):
            return
        candidates = []
        for responder in self.auto_responders.get(guild_id, []):
            if (
                responder.get("channel_id") is not None
                and str(responder["channel_id"]) != str(message.channel.id)
            ):
                continue
            if not self._matches(responder, content):
                continue
            candidates.append(responder)
        responder = self._select_priority_responder(candidates, message)
        if responder is None:
            return
        # Priority is strict: a selected rule consumes the message even when
        # its cooldown is exhausted; never fall through to a lower tier.
        if not self._consume_bucket(message, responder):
            return
        rendered = self.render_response(responder.get("response", ""), message)
        reaction = self._resolve_reaction(responder.get("reaction_emoji", ""))
        if not rendered.strip() and reaction is None:
            return
        executed = False
        if rendered.strip():
            try:
                await message.channel.send(
                    rendered,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
                executed = True
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning(
                    "[AUTORESPONDER] تعذر إرسال رد في السيرفر %s",
                    guild_id,
                    exc_info=True,
                )
        if reaction is not None:
            try:
                await message.add_reaction(reaction)
                executed = True
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.warning(
                    "[AUTORESPONDER] تعذر إضافة التفاعل %s في السيرفر %s",
                    responder.get("reaction_emoji", ""),
                    guild_id,
                    exc_info=True,
                )
        if executed:
            responder["execution_count"] = await record_auto_responder_execution(
                guild_id, int(responder["id"])
            )

    async def _dispatch_policy_alias(self, message: discord.Message) -> bool:
        """Execute a dashboard alias from the O(1) in-memory policy index."""
        if message.guild is None or getattr(message.author, "bot", False):
            return False
        content = str(message.content or "").strip()
        if not content or not getattr(message.channel, "send", None):
            return False
        parts = content.split(maxsplit=1)
        token = parts[0].lstrip("!/")
        if not token:
            return False
        guild_id = int(message.guild.id)
        if guild_id not in self.command_aliases:
            await self._load_command_controls(guild_id)
        binding = self.command_aliases.get(guild_id, {}).get(token.casefold())
        if binding is None:
            return False
        command_name, policy = binding
        if not policy.get("enabled", True):
            await self._send_alias_denial(
                message,
                "⚠️ هذا الأمر معطل حالياً من إعدادات السيرفر.",
            )
            return True

        permissions = getattr(message.author, "guild_permissions", None)
        is_admin = bool(permissions and getattr(permissions, "administrator", False))
        allowed_channels = {str(item) for item in policy.get("allowed_channels", [])}
        if allowed_channels and str(message.channel.id) not in allowed_channels and not is_admin:
            await self._send_alias_denial(
                message,
                "⚠️ هذا الأمر مخصص للعمل في قنوات محددة فقط. "
                "غير مسموح بهذا الاختصار في هذه القناة.",
            )
            return True
        allowed_roles = {str(item) for item in policy.get("allowed_roles", [])}
        member_roles = {
            str(role.id) for role in getattr(message.author, "roles", [])
        }
        if allowed_roles and not is_admin and not member_roles.intersection(allowed_roles):
            await self._send_alias_denial(
                message,
                "⛔ ليس لديك الرتبة المسموح لها باستخدام هذا الأمر.",
            )
            return True

        arguments = parts[1] if len(parts) > 1 else ""
        alias = parts[0].lstrip("!/")
        succeeded = await self._execute_alias_command(
            message,
            command_name,
            arguments,
        )
        if succeeded:
            await self._send_alias_confirmation(
                message,
                alias,
                command_name,
                details=arguments.strip() or "بدون وسائط إضافية",
            )
        return True

    @staticmethod
    async def _send_alias_denial(message: discord.Message, content: str) -> None:
        try:
            await message.channel.send(content, delete_after=7)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.debug("[COMMAND_ALIAS] تعذر إرسال الرفض", exc_info=True)

    async def _execute_alias_command(
        self,
        message: discord.Message,
        command_name: str,
        arguments: str,
    ) -> bool:
        """Resolve and invoke a registered Slash command from message text."""
        # Keep the complete qualified name.  Reducing ``/chat lock`` to
        # ``lock`` can resolve a different top-level command (or its synonym)
        # and bypass the grouped Slash command the dashboard configured.
        requested = str(command_name).strip().lstrip("!/").casefold()
        command = self._registered_command(requested)
        if command is None:
            await self._send_alias_error(
                message, requested, "الأمر المطلوب غير موجود حالياً أو لم تتم مزامنته."
            )
            return False
        if not hasattr(command, "_check_can_run"):
            return await self._execute_prefix_alias(message, command, arguments, requested)

        values, error = await self._parse_alias_arguments(
            message, command, arguments, requested
        )
        if error:
            await self._send_alias_error(message, requested, error, command=command)
            return False

        interaction = ShortcutInteraction(message, command)
        for name, value in values.items():
            setattr(interaction.namespace, name, value)
        try:
            if not await self.app_command_interceptor(interaction):
                return False
            if not await command._check_can_run(interaction):
                raise app_commands.CheckFailure()
            callback = getattr(command, "callback", None)
            if callback is None:
                raise RuntimeError(f"Slash command /{requested} has no callback")
            binding = getattr(command, "binding", None)
            if binding is not None:
                await callback(binding, interaction, **values)
            else:
                await callback(interaction, **values)
            if interaction.response_error:
                await self._send_alias_error(
                    message,
                    requested,
                    "⚠️ أبلغ الأمر عن تعذر تنفيذ العملية.",
                    command=command,
                )
                return False
            return True
        except discord.Forbidden:
            LOGGER.exception("[COMMAND_ALIAS] Discord رفض تنفيذ /%s", requested)
            await self._send_alias_error(
                message,
                requested,
                "❌ رفض Discord العملية؛ تحقق من صلاحيات البوت ورتبته.",
                command=command,
            )
        except discord.HTTPException:
            LOGGER.exception("[COMMAND_ALIAS] طلب Discord فشل أثناء تنفيذ /%s", requested)
            await self._send_alias_error(
                message,
                requested,
                "❌ تعذر إكمال العملية بسبب خطأ من Discord.",
                command=command,
            )
        except app_commands.CheckFailure:
            LOGGER.exception("[COMMAND_ALIAS] فشل فحص الأمر /%s", requested)
            await self._send_alias_error(
                message, requested, "❌ لا تملك صلاحية تنفيذ هذا الأمر.", command=command
            )
        except Exception:
            LOGGER.exception("[COMMAND_ALIAS] خطأ غير متوقع أثناء تنفيذ /%s", requested)
            await self._send_alias_error(
                message,
                requested,
                "⚠️ تعذر تنفيذ الاختصار. تم تسجيل الخطأ للمراجعة.",
                command=command,
            )
        return False

    async def _execute_prefix_alias(self, message, command, arguments, requested):
        """Use the normal Context/check pipeline for legacy prefix commands."""
        get_context = getattr(self.bot, "get_context", None)
        if get_context is None:
            return await self._dispatch_command_shortcut(
                message, f"/{getattr(command, 'qualified_name', requested)}"
            )
        parameters = []
        for name, parameter in (getattr(command, "clean_params", {}) or {}).items():
            annotation = getattr(parameter, "annotation", str)
            annotation_name = str(getattr(annotation, "__name__", annotation)).casefold()
            kind = {
                "int": 4,
                "integer": 4,
                "float": 10,
                "bool": 5,
                "member": 6,
                "user": 6,
                "role": 8,
                "textchannel": 7,
                "voicechannel": 7,
                "categorychannel": 7,
            }.get(annotation_name, 3)
            parameters.append(
                SimpleNamespace(
                    name=name,
                    type=kind,
                    required=parameter.default is inspect.Parameter.empty,
                )
            )
        values, error = await self._parse_alias_arguments(
            message, SimpleNamespace(parameters=parameters), arguments, requested
        )
        if error:
            await self._send_alias_error(message, requested, error)
            return False
        ctx = await get_context(message)
        ctx.command = command
        try:
            can_run = getattr(command, "can_run", None)
            if can_run is not None and not await can_run(ctx):
                raise commands.CheckFailure()
            await ctx.invoke(command, **values)
            return True
        except discord.Forbidden:
            LOGGER.exception("[COMMAND_ALIAS] Discord رفض تنفيذ prefix %s", requested)
            await self._send_alias_error(
                message, requested, "❌ رفض Discord العملية؛ تحقق من صلاحيات البوت."
            )
        except discord.HTTPException:
            LOGGER.exception("[COMMAND_ALIAS] طلب Discord فشل أثناء prefix %s", requested)
            await self._send_alias_error(
                message, requested, "❌ تعذر إكمال العملية بسبب خطأ من Discord."
            )
        except commands.CheckFailure:
            LOGGER.exception("[COMMAND_ALIAS] فشل فحص prefix %s", requested)
            await self._send_alias_error(message, requested, "❌ لا تملك صلاحية تنفيذ هذا الأمر.")
        except Exception:
            LOGGER.exception("[COMMAND_ALIAS] خطأ غير متوقع أثناء prefix %s", requested)
            await self._send_alias_error(
                message, requested, "⚠️ تعذر تنفيذ الاختصار. تم تسجيل الخطأ للمراجعة."
            )
        return False

    def _registered_command(self, requested: str):
        """Find a loaded leaf command rather than maintaining a second registry."""
        normalized = str(requested or "").strip().lstrip("!/").casefold()
        synonyms = {
            "purge": "clear",
            "mute": "timeout",
            "unmute": "untimeout",
            "lock": "lockdown",
            "unlock": "lockdown",
            "قفل": "lockdown",
        }
        wanted = {normalized, synonyms.get(normalized, normalized)}
        tree = getattr(self.bot, "tree", None)
        if tree is not None:
            getter = getattr(tree, "get_command", None)
            if getter is not None:
                for name in wanted:
                    try:
                        command = getter(name)
                    except (AttributeError, TypeError):
                        command = None
                    if command is not None:
                        return command
            walker = getattr(tree, "walk_commands", None)
            if walker is not None:
                try:
                    candidates = list(walker())
                except (AttributeError, TypeError):
                    candidates = []
                for command in candidates:
                    names = {
                        str(getattr(command, "name", "")).casefold(),
                        str(getattr(command, "qualified_name", "")).casefold(),
                    }
                    if wanted.intersection(names):
                        return command
        for command in getattr(self.bot, "commands", []) or []:
            names = {
                str(getattr(command, "name", "")).casefold(),
                str(getattr(command, "qualified_name", "")).casefold(),
            }
            if wanted.intersection(names):
                return command
        return None

    @staticmethod
    def _parameter_kind(parameter) -> str:
        value = getattr(getattr(parameter, "type", None), "value", getattr(parameter, "type", None))
        return {
            3: "string",
            4: "integer",
            5: "boolean",
            6: "member",
            7: "channel",
            8: "role",
            10: "number",
        }.get(value, "string")

    async def _parse_alias_arguments(self, message, command, arguments, requested):
        try:
            tokens = shlex.split(str(arguments or ""))
        except ValueError:
            return {}, "⚠️ تعذر قراءة الوسائط؛ استخدم علامات اقتباس متوازنة."
        values, index = {}, 0
        parameters = list(getattr(command, "parameters", []) or [])
        for position, parameter in enumerate(parameters):
            name = str(getattr(parameter, "name", "") or "").strip()
            if not name:
                continue
            required = bool(getattr(parameter, "required", False))
            kind = self._parameter_kind(parameter)
            remaining = tokens[index:]
            if kind == "string":
                value = " ".join(remaining).strip()
                if value:
                    values[name], index = value, len(tokens)
                elif required:
                    return {}, self._alias_usage(command, requested)
                continue
            if not remaining:
                if required and kind == "boolean" and name.casefold() == "lock":
                    values[name] = requested not in {"unlock"}
                elif required:
                    return {}, self._alias_usage(command, requested)
                continue
            token = remaining[0]
            if kind == "boolean":
                parsed = self._parse_boolean(token)
                if parsed is None:
                    if required:
                        return {}, f"⚠️ قيمة `{name}` يجب أن تكون نعم/لا أو true/false."
                    continue
                values[name], index = parsed, index + 1
                continue
            if kind in {"integer", "number"}:
                parsed = self._parse_numeric(token, name, kind)
                if parsed is None:
                    if required:
                        return {}, f"⚠️ قيمة `{name}` يجب أن تكون رقماً صالحاً."
                    continue
                values[name], index = parsed, index + 1
                continue
            resolver = {
                "member": self._resolve_alias_member,
                "role": self._resolve_alias_role,
                "channel": self._resolve_alias_channel,
            }.get(kind)
            entity = resolver(message, token) if resolver else None
            if entity is None:
                if required:
                    label = COMMAND_PARAMETER_LABELS.get(name, name)
                    return {}, f"⚠️ لم أجد {label}: `{token}`."
                continue
            values[name], index = entity, index + 1
        if index < len(tokens):
            return {}, self._alias_usage(command, requested)
        return values, None

    @staticmethod
    def _parse_boolean(value):
        value = str(value).strip().casefold()
        if value in {"true", "1", "yes", "y", "on", "نعم", "صح", "قفل"}:
            return True
        if value in {"false", "0", "no", "n", "off", "لا", "خطأ", "فتح"}:
            return False
        return None

    @staticmethod
    def _parse_numeric(value, name, kind):
        match = re.fullmatch(
            r"(\d+(?:\.\d+)?)(s|sec|m|min|h|d)?",
            str(value).strip().casefold(),
        )
        if not match:
            return None
        number = float(match.group(1))
        unit = match.group(2)
        if unit:
            multiplier = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "d": 86400}[unit]
            if name.casefold() in {"minutes", "minute", "duration"}:
                number = number * multiplier / 60
            elif name.casefold() in {"seconds", "second", "slowmode"}:
                number *= multiplier
        if kind == "integer":
            return int(number) if number.is_integer() else None
        return number

    @staticmethod
    def _entity_id(value, pattern):
        match = re.fullmatch(pattern, str(value).strip())
        if match:
            return int(match.group(1))
        return int(value) if str(value).isdigit() else None

    @staticmethod
    def _resolve_alias_member(message, value):
        guild = message.guild
        member_id = Utilities._entity_id(value, r"<@!?(\d+)>")
        getter = getattr(guild, "get_member", None)
        if member_id is not None and getter:
            member = getter(member_id)
            if member is not None:
                return member
        query = str(value).casefold()
        for member in getattr(message, "mentions", []) or []:
            if query in {str(getattr(member, "mention", "")).casefold(), str(getattr(member, "id", "")).casefold()}:
                return member
        return next(
            (
                member for member in getattr(guild, "members", []) or []
                if query in {
                    str(getattr(member, "name", "")).casefold(),
                    str(getattr(member, "display_name", "")).casefold(),
                    str(getattr(member, "global_name", "")).casefold(),
                }
            ),
            None,
        )

    @staticmethod
    def _resolve_alias_role(message, value):
        guild = message.guild
        role_id = Utilities._entity_id(value, r"<@&(\d+)>")
        getter = getattr(guild, "get_role", None)
        if role_id is not None and getter:
            role = getter(role_id)
            if role is not None:
                return role
        query = str(value).lstrip("#").casefold()
        return next(
            (role for role in getattr(guild, "roles", []) or [] if str(getattr(role, "name", "")).casefold() == query),
            None,
        )

    @staticmethod
    def _resolve_alias_channel(message, value):
        guild = message.guild
        channel_id = Utilities._entity_id(value, r"<#(\d+)>")
        getter = getattr(guild, "get_channel", None)
        if channel_id is not None and getter:
            channel = getter(channel_id)
            if channel is not None:
                return channel
        query = str(value).lstrip("#").casefold()
        return next(
            (channel for channel in getattr(guild, "channels", []) or [] if str(getattr(channel, "name", "")).casefold() == query),
            None,
        )

    @staticmethod
    def _alias_usage(command, requested):
        syntax = Utilities._command_argument_text(command)
        return f"⚠️ الاستخدام: `{requested}{(' ' + syntax) if syntax else ''}`"

    async def _send_alias_error(self, message, command_name, detail, *, command=None):
        embed = discord.Embed(
            title="تعذر تنفيذ الاختصار",
            description=detail,
            color=0xEF4444,
            timestamp=discord.utils.utcnow(),
        )
        syntax = self._command_argument_text(command) if command is not None else ""
        if syntax:
            embed.add_field(name="الصيغة", value=f"`{command_name} {syntax}`", inline=False)
        try:
            await message.channel.send(detail, embed=embed, delete_after=7)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("[COMMAND_ALIAS] تعذر إرسال خطأ الاختصار")

    async def _send_alias_confirmation(
        self,
        message: discord.Message,
        alias: str,
        command_name: str,
        *,
        details: str = "بدون وسائط إضافية",
    ) -> None:
        embed = discord.Embed(
            title="⚡ تم تنفيذ الأمر بنجاح",
            description=(
                f"تم استخدام الاختصار: **{alias}** ➔ تنفيذ أمر "
                f"**{command_name}** بنجاح بواسطة {message.author.mention}."
            ),
            color=0x10B981,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="تفاصيل العملية", value=f"`{details[:1000]}`", inline=False)
        embed.add_field(
            name="المسؤول عن التنفيذ",
            value=f"{message.author.mention} (`{message.author.id}`)",
            inline=False,
        )
        try:
            await message.channel.send(embed=embed, reference=message)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.warning("[COMMAND_ALIAS] تعذر إرسال تأكيد التنفيذ", exc_info=True)

    @staticmethod
    def _matches(responder: dict[str, Any], content: str) -> bool:
        trigger = str(responder.get("trigger", ""))
        match_type = responder.get("match_type")
        if match_type == "exact":
            return content.strip().casefold() == trigger.casefold()
        if match_type == "contains":
            return trigger.casefold() in content.casefold()
        if match_type == "regex":
            compiled = responder.get("_compiled")
            return bool(compiled and compiled.search(content))
        return False

    @staticmethod
    def _target_matches(responder: dict[str, Any], message: discord.Message) -> bool:
        target_type = responder.get("target_type", "everyone")
        target_id = int(responder.get("target_id") or 0)
        if target_type == "everyone":
            return True
        if target_type == "user":
            return int(message.author.id) == target_id
        if target_type == "role":
            return any(int(role.id) == target_id for role in message.author.roles)
        return False

    @staticmethod
    def _select_priority_responder(
        candidates: list[dict[str, Any]],
        message: discord.Message,
    ) -> dict[str, Any] | None:
        """Choose exactly one matching rule using member > role > everyone."""
        if not candidates:
            return None
        author_id = int(message.author.id)
        user_rules = [
            item for item in candidates
            if item.get("target_type", "everyone") == "user"
            and int(item.get("target_id") or 0) == author_id
        ]
        if user_rules:
            return user_rules[0]

        member_roles = {
            int(role.id): getattr(role, "position", 0)
            for role in getattr(message.author, "roles", [])
        }
        role_rules = [
            item for item in candidates
            if item.get("target_type", "everyone") == "role"
            and int(item.get("target_id") or 0) in member_roles
        ]
        if role_rules:
            return max(
                role_rules,
                key=lambda item: (
                    member_roles[int(item.get("target_id") or 0)],
                    -int(item.get("id") or 0),
                ),
            )

        return next(
            (item for item in candidates if item.get("target_type", "everyone") == "everyone"),
            None,
        )

    @staticmethod
    def _parse_reaction(value: Any):
        raw = str(value or "").strip()
        if not raw:
            return None
        custom = re.fullmatch(r"<(a?):([A-Za-z0-9_~]+):(\d+)>", raw)
        if custom:
            return discord.PartialEmoji(
                name=custom.group(2),
                id=int(custom.group(3)),
                animated=bool(custom.group(1)),
            )
        if raw.startswith("<") and raw.endswith(">"):
            return None
        return raw

    def _resolve_reaction(self, value: Any):
        parsed = self._parse_reaction(value)
        if isinstance(parsed, discord.PartialEmoji) and parsed.id:
            return self.bot.get_emoji(parsed.id) or parsed
        return parsed

    def _consume_bucket(
        self,
        message: discord.Message,
        responder: dict[str, Any],
    ) -> bool:
        trigger_id = int(responder.get("id") or hash(responder.get("trigger", "")))
        key = (int(message.guild.id), trigger_id, int(message.author.id))
        now = time.monotonic()
        capacity = max(1, int(responder.get("bucket_capacity", 1)))
        refill = max(0.0, float(responder.get("cooldown_seconds", 5.0)))
        bucket = self._cooldowns.get(key)
        if bucket is None:
            bucket = TokenBucket(float(capacity), now)
        elif refill > 0:
            bucket.tokens = min(
                float(capacity),
                bucket.tokens + ((now - bucket.updated_at) / refill),
            )
        bucket.updated_at = now
        if bucket.tokens < 1:
            self._cooldowns[key] = bucket
            return False
        bucket.tokens -= 1
        self._cooldowns[key] = bucket
        if len(self._cooldowns) > 20000:
            self._prune_buckets()
        return True

    def _prune_buckets(self, guild_id: int | None = None) -> None:
        now = time.monotonic()
        for key, bucket in list(self._cooldowns.items()):
            if guild_id is not None and key[0] != int(guild_id):
                continue
            if now - bucket.updated_at > 3600:
                self._cooldowns.pop(key, None)
        if len(self._cooldowns) > 20000:
            oldest = sorted(
                self._cooldowns.items(), key=lambda item: item[1].updated_at
            )[:5000]
            for key, _ in oldest:
                self._cooldowns.pop(key, None)

    @staticmethod
    def render_response(template: str, message: discord.Message) -> str:
        guild = message.guild
        channel = message.channel
        values = {
            "user": getattr(message.author, "mention", f"<@{message.author.id}>"),
            "channel": getattr(channel, "mention", f"#{getattr(channel, 'name', 'channel')}"),
            "server": getattr(guild, "name", "السيرفر"),
            "members": f"{getattr(guild, 'member_count', 0):,}",
        }
        rendered = str(template or "")
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))

        def choose(match: re.Match) -> str:
            options = [item.strip() for item in match.group(1).split("|") if item.strip()]
            return random.choice(options) if options else ""

        return re.sub(r"\{random:([^{}|]+(?:\|[^{}|]+)+)\}", choose, rendered)[:2000]

    async def _dispatch_shortcut(self, message: discord.Message) -> bool:
        trigger = message.content.strip().casefold()
        for shortcut in self.shortcuts.get(int(message.guild.id), []):
            if str(shortcut.get("trigger", "")).strip().casefold() != trigger:
                continue
            if shortcut["target_type"] == "announcement":
                embed = discord.Embed(
                    description=self.render_response(shortcut["announcement"], message),
                    color=0x5865F2,
                )
                await message.channel.send(embed=embed)
                return True
            if shortcut["target_type"] == "help":
                await self._send_command_help(message, shortcut["target"])
                return True
            return await self._dispatch_command_shortcut(message, shortcut["target"])
        command = self._text_command_for_trigger(trigger)
        if command is None:
            return False
        target = f"/{command.qualified_name}"
        required = [
            parameter
            for parameter in getattr(command, "parameters", []) or []
            if getattr(parameter, "required", False)
        ]
        if required:
            await self._send_command_help(message, target)
            return True
        return await self._dispatch_command_shortcut(message, target)

    def _text_command_for_trigger(self, trigger: str):
        """Resolve a bare chat keyword to a loaded Slash/Prefix command."""
        candidates = []
        tree = getattr(self.bot, "tree", None)
        if tree and hasattr(tree, "walk_commands"):
            candidates.extend(tree.walk_commands())
        candidates.extend(getattr(self.bot, "commands", []) or [])
        arabic_labels = {
            label.casefold(): name
            for name, label in COMMAND_HELP_LABELS.items()
        }
        for command in candidates:
            if getattr(command, "hidden", False):
                continue
            names = {
                str(getattr(command, "name", "")).casefold(),
                str(getattr(command, "qualified_name", "")).casefold(),
            }
            names.update(
                str(alias).strip().casefold()
                for alias in getattr(command, "aliases", []) or []
            )
            command_name = str(getattr(command, "name", "")).casefold()
            if arabic_labels.get(trigger) == command_name:
                return command
            if trigger in names:
                return command
        return None
        return False

    def _command_for_target(self, target: str):
        command_target = str(target).strip().lstrip("!/").lower()
        command_name = command_target.split()[0] if command_target else ""
        if not command_name:
            return None
        slash_command = self._registered_command(command_target)
        if slash_command is None and command_target != command_name:
            slash_command = self._registered_command(command_name)
        prefix_command = self.bot.get_command(command_name) if hasattr(self.bot, "get_command") else None
        return slash_command or prefix_command

    @staticmethod
    def _command_argument_text(command) -> str:
        values = []
        for parameter in getattr(command, "parameters", []) or []:
            name = str(getattr(parameter, "name", "")).lower()
            label = COMMAND_PARAMETER_LABELS.get(name, name.replace("_", " "))
            if not label:
                continue
            required = bool(getattr(parameter, "required", False))
            values.append(f"<{label}>" if required else f"[{label}]")
        return " ".join(values)

    async def _send_command_help(self, message: discord.Message, target: str):
        command = self._command_for_target(target)
        target_name = str(target).strip().lstrip("!/").casefold()
        command_name = target_name.split()[0] if target_name else ""
        resolved_name = str(
            getattr(command, "qualified_name", "") or target_name or command_name
        ).casefold()
        if command is None:
            await message.channel.send(f"⚠️ الأمر `{command_name}` غير موجود حالياً.")
            return
        current_alias = message.content.strip()
        label = COMMAND_HELP_LABELS.get(
            command_name,
            str(getattr(command, "name", command_name)).replace("_", " ").title(),
        )
        description = getattr(command, "description", None) or "لا يوجد وصف لهذا الأمر بعد."
        arguments = self._command_argument_text(command)
        syntax = f"{current_alias}{(' ' + arguments) if arguments else ''}"
        aliases = [
            str(item["trigger"])
            for item in self.shortcuts.get(int(message.guild.id), [])
            if item.get("target_type") == "help"
            and str(item.get("target", "")).lstrip("!/").casefold() == resolved_name
            and str(item.get("trigger", "")).casefold() != current_alias.casefold()
        ]
        aliases.extend(str(item) for item in getattr(command, "aliases", []) if item)
        permission = COMMAND_PERMISSION_LABELS.get(command_name, "صلاحيات Discord الخاصة بالأمر")
        dashboard_path = (
            (os.getenv("DASHBOARD_BASE_PATH") or "/").strip().rstrip("/")
            + "/?view=commands"
        )

        embed = discord.Embed(
            title=f"{label} 📖",
            description=f"{description}\n\nاستخدم الصيغة التالية لتنفيذ الأمر من البادئة الحالية.",
            color=0xF3A6C7,
        )
        embed.add_field(name="الصيغة ⌨️", value=f"`{syntax}`", inline=False)
        example = f"{current_alias} @أحمد {('لغة غير لائقة' if command_name == 'warn' else '10')}".strip()
        embed.add_field(name="مثال ✅", value=f"`{example}`", inline=False)
        embed.add_field(
            name="تكتبه أيضاً 🔀",
            value="، ".join(f"`{item}`" for item in aliases[:12]) if aliases else "لا توجد اختصارات أخرى",
            inline=False,
        )
        embed.add_field(name="الصلاحية المطلوبة 🛡️", value=permission, inline=False)
        embed.add_field(name="⚙️ إعدادات الأمر في الداشبورد", value=f"[فتح مركز الأوامر]({dashboard_path})", inline=False)
        embed.set_footer(text="مساعد الأوامر الذكي · اكتب أي اختصار لعرض الشرح")
        await message.channel.send(embed=embed, reference=message)

    async def _dispatch_command_shortcut(
        self,
        message: discord.Message,
        target: str,
    ) -> bool:
        target = str(target).strip()
        command_target = target.lstrip("!/").strip() if target else ""
        command_name = command_target.casefold()
        if not command_name:
            return False
        slash_command = None
        tree = getattr(self.bot, "tree", None)
        getter = getattr(tree, "get_command", None)
        if getter is not None:
            try:
                slash_command = getter(command_target)
            except (AttributeError, TypeError):
                slash_command = None
        is_slash_command = slash_command is not None
        if is_slash_command and target.startswith("/"):
            callback = slash_command.callback
            interaction = ShortcutInteraction(message, slash_command)
            try:
                if not await self.app_command_interceptor(interaction):
                    return True
                binding = getattr(slash_command, "binding", None)
                if binding is not None:
                    await callback(binding, interaction)
                else:
                    await callback(interaction)
                return True
            except Exception:
                LOGGER.exception(
                    "[SHORTCUT] فشل تشغيل الأمر Slash /%s", command_name
                )
                if not interaction.response.is_done():
                    await message.channel.send(f"⚠️ تعذر تنفيذ الاختصار للأمر `/{command_name}`.")
                return True
        command = self.bot.get_command(command_name.split()[0])
        if command is None:
            await message.channel.send(f"⚠️ الأمر `{command_name}` غير موجود حالياً.")
            return True
        ctx = await self.bot.get_context(message)
        ctx.command = command
        try:
            if not await self.command_interceptor(ctx):
                return True
            await ctx.invoke(command)
            return True
        except Exception:
            LOGGER.exception("[SHORTCUT] فشل تشغيل الأمر %s", command_name)
            return True

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        mem: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        # 1. إنشاء روم صوتي مؤقت مع لوحة التحكم
        if after.channel and after.channel.name == HUB_NAME:
            guild, category = mem.guild, after.channel.category
            new_channel = await guild.create_voice_channel(
                name=f"🔊・{mem.display_name}",
                category=category,
                user_limit=10,
            )
            self.temp_voice[new_channel.id] = mem.id
            try:
                await mem.move_to(new_channel)
                embed = discord.Embed(
                    title="🎛️ تحكم برومك الصوتي",
                    description=(
                        "يمكنك إدارة وتأمين الروم عبر الأزرار أدناه:"
                    ),
                    color=0x2ECC71,
                )
                await new_channel.send(
                    f"{mem.mention}",
                    embed=embed,
                    view=VoiceControl(new_channel, mem),
                )
            except Exception:
                LOGGER.exception("Failed to create temporary voice room")

        # 2. حذف الروم عند مغادرة الجميع
        if before.channel and before.channel.id in self.temp_voice:
            if len(before.channel.members) == 0:
                self.temp_voice.pop(before.channel.id, None)
                try:
                    await before.channel.delete()
                except Exception:
                    LOGGER.exception("Failed to delete empty temporary voice room")

    @app_commands.command(
        name="setup_voice",
        description="تهيئة رومات صوتية مؤقتة ذاتية الإدارة",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_voice(self, itx: discord.Interaction):
        category = discord.utils.get(itx.guild.categories, name="🔊 القنوات التفاعلية")
        if category is None:
            category = await itx.guild.create_category("🔊 القنوات التفاعلية")
        hub = discord.utils.get(category.voice_channels, name=HUB_NAME)
        if hub is None:
            hub = await itx.guild.create_voice_channel(
                name=HUB_NAME,
                category=category,
            )
        await itx.response.send_message(
            f"✅ نظام الرومات المؤقتة جاهز: {hub.mention}",
            ephemeral=True,
        )

    @app_commands.command(
        name="help",
        description="عرض دليل أوامر PRIME حسب الأقسام",
    )
    @app_commands.describe(category="القسم المطلوب، اتركه فارغاً لعرض الأقسام")
    async def help_command(
        self,
        itx: discord.Interaction,
        category: str = None,
    ):
        commands_list = [
            command
            for command in self.bot.tree.walk_commands()
            if not getattr(command, "hidden", False)
            and command.name != "help"
        ]
        aliases = {
            "mod": {"timeout", "untimeout", "warn", "unwarn", "warnings", "clear", "lockdown", "slowmode"},
            "security": {"setup_captcha"},
            "tickets": {"setup_tickets", "suggest"},
            "ai": {"ask_ai", "imagine", "summarize", "transcript", "backup_structure"},
            "economy": {"profile", "daily", "work", "pay", "leaderboard", "deposit", "withdraw", "rob", "giveaway"},
            "tournament": {"scrim_split", "scrim_teams", "map_randomizer", "tournament_open", "match_record", "standings"},
            "server": {"ping", "status", "serverinfo", "avatar", "say", "setup_voice", "radio", "stop_radio"},
            "community": {"poll", "remind", "reminders", "reminder_cancel", "setup_counters"},
        }
        selected = category.strip().lower() if category else None
        if selected and selected not in aliases:
            return await itx.response.send_message(
                "❌ القسم غير معروف. الأقسام المتاحة: "
                + ", ".join(f"`{key}`" for key in aliases),
                ephemeral=True,
            )
        embed = discord.Embed(
            title="📚 دليل أوامر PRIME",
            description=(
                "استخدم `/help category:<القسم>` للحصول على أوامر قسم محدد.\n"
                "الأوامر الحساسة تتطلب صلاحيات Discord مناسبة."
            ),
            color=0x5865F2,
        )
        if selected is None:
            for key, names in aliases.items():
                available = [f"`/{name}`" for name in sorted(names) if any(command.name == name for command in commands_list)]
                if available:
                    embed.add_field(name=f"• {key}", value=" ".join(available), inline=False)
        else:
            allowed = aliases[selected]
            for command in sorted(commands_list, key=lambda item: item.name):
                if command.name in allowed:
                    embed.add_field(
                        name=f"/{command.name}",
                        value=command.description or "لا يوجد وصف",
                        inline=False,
                    )
        await itx.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="status",
        description="عرض صحة البوت والاتصال والخدمات",
    )
    async def status(self, itx: discord.Interaction):
        websocket = round(self.bot.latency * 1000) if self.bot.latency != float("inf") else 0
        embed = discord.Embed(title="🛰️ حالة PRIME", color=0x2ECC71)
        embed.add_field(name="Discord Gateway", value=f"🟢 متصل · `{websocket}ms`", inline=True)
        embed.add_field(name="السيرفرات", value=f"`{len(self.bot.guilds)}`", inline=True)
        embed.add_field(name="الأعضاء", value=f"`{sum(g.member_count or 0 for g in self.bot.guilds):,}`", inline=True)
        embed.add_field(name="الأوامر", value=f"`{len(list(self.bot.tree.walk_commands()))}` أمر Slash", inline=True)
        await itx.response.send_message(embed=embed, ephemeral=True)

    async def ping(self, itx: discord.Interaction):
        started_at = time.perf_counter()
        await itx.response.defer()
        finished_at = time.perf_counter()
        websocket_latency = round(self.bot.latency * 1000)
        api_latency = round((finished_at - started_at) * 1000)

        embed = discord.Embed(
            title="🏓 استجابة النظام",
            color=0x2ECC71,
        )
        embed.add_field(
            name="بوابة ديسكورد (WebSocket)",
            value=f"`{websocket_latency}ms`",
            inline=True,
        )
        embed.add_field(
            name="معالجة الأوامر (API/DB)",
            value=f"`{api_latency}ms`",
            inline=True,
        )
        await itx.followup.send(embed=embed)

    @app_commands.command(
        name="radio",
        description="تشغيل إذاعة القرآن الكريم المباشرة في الروم الصوتي",
    )
    @app_commands.checks.has_permissions(connect=True)
    async def radio(self, itx: discord.Interaction):
        if not itx.user.voice or not itx.user.voice.channel:
            return await itx.response.send_message(
                "❌ يجب أن تكون متواجداً في روم صوتي أولاً!",
                ephemeral=True,
            )

        channel = itx.user.voice.channel
        await itx.response.defer()
        try:
            voice_client = itx.guild.voice_client or await channel.connect()
            if voice_client.channel != channel:
                await voice_client.move_to(channel)
            if voice_client.is_playing():
                voice_client.stop()

            stream_url = "https://backup.qurango.net/radio/tarteel"
            voice_client.play(discord.FFmpegPCMAudio(stream_url))
            await itx.followup.send(
                f"📻 تم بدء البث الصوتي المباشر في: {channel.mention}"
            )
        except Exception as error:
            await itx.followup.send(
                f"⚠️ تعذر تشغيل الراديو: {error}",
                ephemeral=True,
            )

    @app_commands.command(
        name="stop_radio",
        description="إيقاف البث ومغادرة الروم الصوتي",
    )
    @app_commands.checks.has_permissions(connect=True)
    async def stop_radio(self, itx: discord.Interaction):
        if voice_client := itx.guild.voice_client:
            await voice_client.disconnect()
            await itx.response.send_message(
                "⏹️ تم إيقاف البث ومغادرة الروم."
            )
        else:
            await itx.response.send_message(
                "❌ البوت غير متصل بأي روم صوتي.",
                ephemeral=True,
            )

    @app_commands.command(
        name="ask",
        description="طرح سؤال أو طلب مساعدة ذكية من البوت",
    )
    async def ask(self, itx: discord.Interaction, question: str):
        ai_cog = self.bot.get_cog("AITools")
        if ai_cog is not None:
            await ai_cog.answer_ai(itx, question)
            return
        await itx.response.send_message(
            "⚠️ محرك الذكاء الاصطناعي غير متاح حالياً. جرّب لاحقاً.",
            ephemeral=True,
        )

    async def serverinfo(self, itx: discord.Interaction):
        guild = itx.guild
        embed = discord.Embed(
            title=f"📊 إحصائيات: {guild.name}",
            color=0x3498DB,
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(
            name="👑 المالك",
            value=guild.owner.mention if guild.owner else "غير معروف",
            inline=True,
        )
        embed.add_field(
            name="👥 الأعضاء",
            value=f"`{guild.member_count}`",
            inline=True,
        )
        embed.add_field(
            name="💬 الرومات",
            value=f"`{len(guild.channels)}`",
            inline=True,
        )
        embed.add_field(
            name="🛡️ الرتب",
            value=f"`{len(guild.roles)}`",
            inline=True,
        )
        embed.add_field(
            name="🚀 مستوى التعزيز",
            value=(
                f"`Tier {guild.premium_tier}` "
                f"({guild.premium_subscription_count} Boosts)"
            ),
            inline=True,
        )
        embed.add_field(
            name="📅 تاريخ الإنشاء",
            value=f"<t:{int(guild.created_at.timestamp())}:D>",
            inline=True,
        )
        await itx.response.send_message(embed=embed)

    async def avatar(
        self,
        itx: discord.Interaction,
        member: discord.Member = None,
    ):
        target = member or itx.user
        embed = discord.Embed(
            title=f"🖼️ صورة: {target.display_name}",
            color=0x2ECC71,
        )
        embed.set_image(url=target.display_avatar.url)
        await itx.response.send_message(embed=embed)

    @app_commands.command(
        name="say",
        description="إرسال رسالة رسمية باسم البوت",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def say(self, itx: discord.Interaction, text: str):
        await itx.channel.send(text)
        await itx.response.send_message(
            "✅ تم الإرسال بنجاح.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Utilities(bot))

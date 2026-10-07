"""Admin-only Discord commands and durable workers for subscriptions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database
import subscription_service
from interaction_runtime import send_interaction_message

logger = logging.getLogger("SubscriptionCommands")


def _format_date(value: str | None) -> str:
    if not value:
        return "غير محدد"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return f"<t:{int(parsed.timestamp())}:F>"
    except (TypeError, ValueError, OverflowError):
        return str(value)[:80]


class SubscriptionCommands(commands.Cog):
    """Manage server subscriptions without exposing dashboard or payment APIs."""

    subscription = app_commands.Group(
        name="subscription",
        description="إدارة اشتراكات أعضاء السيرفر",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        if not self.subscription_worker.is_running():
            self.subscription_worker.start()

    def cog_unload(self):
        self.subscription_worker.cancel()

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        guild = getattr(interaction, "guild", None)
        member = getattr(interaction, "user", None)
        permissions = getattr(member, "guild_permissions", None)
        owner = getattr(guild, "owner_id", None) == getattr(member, "id", None)
        allowed = bool(
            owner
            or getattr(permissions, "administrator", False)
            or getattr(permissions, "manage_guild", False)
        )
        if guild is None or not allowed:
            await send_interaction_message(
                interaction,
                "هذا الأمر متاح لمالك السيرفر أو لمن لديه صلاحية إدارة السيرفر.",
                ephemeral=True,
            )
            return False
        return True

    @staticmethod
    async def _defer(interaction: discord.Interaction) -> None:
        response = getattr(interaction, "response", None)
        if response is not None and not response.is_done():
            await response.defer(ephemeral=True, thinking=True)

    async def _report_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        if isinstance(error, ValueError):
            message = str(error)
        else:
            logger.error(
                "Subscription command failed guild=%s",
                getattr(getattr(interaction, "guild", None), "id", None),
                exc_info=(type(error), error, error.__traceback__),
            )
            message = "تعذر تنفيذ العملية الآن؛ لم تُحذف أو تُعاد تهيئة أي بيانات."
        await send_interaction_message(interaction, message[:1800], ephemeral=True)

    async def _process_level_events(
        self,
        guild: discord.Guild,
        user: discord.Member,
        result: dict[str, Any],
    ) -> None:
        """Run the existing post-commit level rewards/events after subscription XP."""
        xp = result.get("xp") or {}
        if int(xp.get("amount", 0) or 0) <= 0:
            return
        levels = self.bot.get_cog("Levels")
        handler = getattr(levels, "_handle_text_award", None)
        if not callable(handler):
            return
        try:
            settings = await database.get_level_settings(guild.id)
            if settings and settings.get("is_enabled", True):
                await handler(user, settings, xp)
        except Exception:
            # The XP and its ledger have already committed; a retry must not
            # award the subscription XP a second time.
            logger.exception(
                "Subscription XP post-processing failed guild=%s user=%s",
                guild.id,
                user.id,
            )

    @subscription.command(name="grant", description="إنشاء اشتراك لعضو")
    @app_commands.describe(
        member="العضو المستفيد",
        duration_days="مدة الاشتراك بالأيام (من 1 إلى 36500)",
        plan_id="معرّف اختياري للتصنيف فقط، وليس كتالوج خطط",
    )
    async def grant(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration_days: app_commands.Range[int, 1, 36_500],
        plan_id: str | None = None,
    ):
        if not await self._authorized(interaction):
            return
        if member.bot:
            await send_interaction_message(
                interaction, "لا يمكن إنشاء اشتراك لحساب بوت.", ephemeral=True
            )
            return
        await self._defer(interaction)
        try:
            result = await subscription_service.create_subscription(
                interaction.guild.id,
                member.id,
                int(duration_days),
                idempotency_key=f"discord-interaction:{interaction.id}",
                actor_id=interaction.user.id,
                plan_id=plan_id,
            )
            if not result["idempotent"]:
                await self._process_level_events(
                    interaction.guild, member, result
                )
            record = result["subscription"]
            message = (
                f"تم إنشاء الاشتراك `{record['subscription_id']}` لـ "
                f"{member.mention} حتى {_format_date(record['end_date'])}. "
                f"XP الاشتراك: **{int((result.get('xp') or {}).get('amount', 0)):,}**."
            )
            await send_interaction_message(interaction, message, ephemeral=True)
        except Exception as error:
            await self._report_error(interaction, error)

    @subscription.command(name="renew", description="تجديد اشتراك قائم")
    @app_commands.describe(
        subscription_id="معرّف الاشتراك الذي تريد تجديده",
        duration_days="المدة المضافة بالأيام (من 1 إلى 36500)",
    )
    async def renew(
        self,
        interaction: discord.Interaction,
        subscription_id: str,
        duration_days: app_commands.Range[int, 1, 36_500],
    ):
        if not await self._authorized(interaction):
            return
        await self._defer(interaction)
        try:
            result = await subscription_service.renew_subscription(
                interaction.guild.id,
                subscription_id,
                int(duration_days),
                idempotency_key=f"discord-interaction:{interaction.id}",
                actor_id=interaction.user.id,
            )
            if not result["idempotent"]:
                record = result["subscription"]
                member = interaction.guild.get_member(int(record["user_id"]))
                if member is None:
                    try:
                        member = await interaction.guild.fetch_member(
                            int(record["user_id"])
                        )
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        member = None
                if member is not None and not member.bot:
                    await self._process_level_events(interaction.guild, member, result)
            record = result["subscription"]
            message = (
                f"تم تجديد `{record['subscription_id']}` حتى "
                f"{_format_date(record['end_date'])}. "
                f"XP التجديد: **{int((result.get('xp') or {}).get('amount', 0)):,}**."
            )
            await send_interaction_message(interaction, message, ephemeral=True)
        except Exception as error:
            await self._report_error(interaction, error)

    @subscription.command(name="cancel", description="إلغاء اشتراك نشط")
    @app_commands.describe(
        subscription_id="معرّف الاشتراك",
        reason="سبب إداري اختياري يسجل في السجل",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        subscription_id: str,
        reason: str | None = None,
    ):
        if not await self._authorized(interaction):
            return
        await self._defer(interaction)
        try:
            result = await subscription_service.cancel_subscription(
                interaction.guild.id,
                subscription_id,
                idempotency_key=f"discord-interaction:{interaction.id}",
                actor_id=interaction.user.id,
                reason=reason or "",
            )
            await send_interaction_message(
                interaction,
                f"حالة الاشتراك `{subscription_id}`: **{result['status']}**.",
                ephemeral=True,
            )
        except Exception as error:
            await self._report_error(interaction, error)

    @subscription.command(name="list", description="عرض اشتراكات عضو أو السيرفر")
    @app_commands.describe(member="اتركه فارغاً لعرض أحدث اشتراكات السيرفر")
    async def list_records(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        if not await self._authorized(interaction):
            return
        await self._defer(interaction)
        try:
            records = await subscription_service.list_subscriptions(
                interaction.guild.id,
                user_id=member.id if member else None,
                limit=20,
            )
            if not records:
                text = "لا توجد اشتراكات مسجلة."
            else:
                lines = []
                for record in records[:15]:
                    lines.append(
                        f"• `{record['subscription_id']}` · <@{record['user_id']}> · "
                        f"**{record['status']}** · حتى {_format_date(record['end_date'])}"
                    )
                text = "\n".join(lines)
            await send_interaction_message(
                interaction,
                text[:1900],
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as error:
            await self._report_error(interaction, error)

    @subscription.command(name="history", description="عرض سجل اشتراك")
    @app_commands.describe(subscription_id="معرّف الاشتراك")
    async def history(self, interaction: discord.Interaction, subscription_id: str):
        if not await self._authorized(interaction):
            return
        await self._defer(interaction)
        try:
            rows = await subscription_service.get_subscription_history(
                interaction.guild.id, subscription_id, limit=15
            )
            if not rows:
                text = "لا يوجد سجل لهذا الاشتراك في هذا السيرفر."
            else:
                lines = [
                    f"• **{row['event_type']}** · {row['status']} · "
                    f"{_format_date(row['created_at'])}"
                    for row in rows
                ]
                text = "\n".join(lines)
            await send_interaction_message(interaction, text[:1900], ephemeral=True)
        except Exception as error:
            await self._report_error(interaction, error)

    @subscription.command(name="stats", description="إحصائيات اشتراكات السيرفر")
    async def stats(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        await self._defer(interaction)
        try:
            stats = await subscription_service.get_subscription_analytics(
                interaction.guild.id
            )
            text = (
                f"نشطة: **{stats['active_subscriptions']}** · "
                f"منتهية: **{stats['expired_subscriptions']}** · "
                f"تنتهي خلال 7 أيام: **{stats['expiring_soon']}**\n"
                f"جديدة: **{stats['new_subscriptions']}** "
                f"(اليوم {stats['new_subscriptions_today']}، "
                f"هذا الشهر {stats['new_subscriptions_this_month']})\n"
                f"تجديدات: **{stats['renewals']}** "
                f"(اليوم {stats['renewals_today']}، "
                f"هذا الشهر {stats['renewals_this_month']})\n"
                f"XP الاشتراكات: **{stats['total_subscription_xp']:,}** "
                f"(اليوم {stats['xp_today']:,}، هذا الشهر {stats['xp_this_month']:,})"
            )
            await send_interaction_message(interaction, text, ephemeral=True)
        except Exception as error:
            await self._report_error(interaction, error)

    @subscription.command(name="settings", description="عرض أو تعديل إعدادات الاشتراك")
    @app_commands.describe(
        xp_enabled="تفعيل أو إيقاف XP الاشتراك",
        notifications_enabled="تفعيل أو إيقاف رسائل DM",
        new_xp_base="XP الأساسي عند الإنشاء (0 إلى 100000)",
        renewal_xp_base="XP الأساسي عند التجديد (0 إلى 100000)",
    )
    async def settings(
        self,
        interaction: discord.Interaction,
        xp_enabled: bool | None = None,
        notifications_enabled: bool | None = None,
        new_xp_base: app_commands.Range[int, 0, 100_000] | None = None,
        renewal_xp_base: app_commands.Range[int, 0, 100_000] | None = None,
    ):
        if not await self._authorized(interaction):
            return
        await self._defer(interaction)
        changes = {
            key: value
            for key, value in {
                "xp_enabled": xp_enabled,
                "notifications_enabled": notifications_enabled,
                "new_xp_base": new_xp_base,
                "renewal_xp_base": renewal_xp_base,
            }.items()
            if value is not None
        }
        try:
            result = (
                await subscription_service.update_subscription_settings(
                    interaction.guild.id,
                    changes,
                    actor_id=interaction.user.id,
                )
                if changes
                else await subscription_service.get_subscription_settings(
                    interaction.guild.id
                )
            )
            templates_state = "مفعلة" if result["notifications_enabled"] else "متوقفة"
            xp_state = "مفعل" if result["xp_enabled"] else "متوقف"
            text = (
                f"الإعدادات الحالية:\n"
                f"• XP: **{xp_state}** · أساس الإنشاء **{result['new_xp_base']}** · "
                f"أساس التجديد **{result['renewal_xp_base']}**\n"
                f"• إشعارات DM: **{templates_state}**"
            )
            await send_interaction_message(interaction, text, ephemeral=True)
        except Exception as error:
            await self._report_error(interaction, error)

    @subscription.command(name="template", description="عرض أو تعديل قالب رسالة الاشتراك")
    @app_commands.describe(
        event="نوع الرسالة",
        template="استخدم الحقول: {user} {server} {xp} {level} {message} {remaining}",
    )
    @app_commands.choices(
        event=[
            app_commands.Choice(name="إنشاء", value="created"),
            app_commands.Choice(name="تجديد", value="renewal"),
            app_commands.Choice(name="تذكير", value="expiring"),
            app_commands.Choice(name="انتهاء", value="expired"),
        ]
    )
    async def template(
        self,
        interaction: discord.Interaction,
        event: str,
        template: str | None = None,
    ):
        if not await self._authorized(interaction):
            return
        await self._defer(interaction)
        try:
            settings = (
                await subscription_service.update_subscription_settings(
                    interaction.guild.id,
                    {"templates": {event: template}},
                    actor_id=interaction.user.id,
                )
                if template is not None
                else await subscription_service.get_subscription_settings(
                    interaction.guild.id
                )
            )
            await send_interaction_message(
                interaction,
                f"قالب **{event}**:\n{settings['notification_templates'][event]}",
                ephemeral=True,
            )
        except Exception as error:
            await self._report_error(interaction, error)

    async def _deliver_notification(self, notification: dict[str, Any]) -> None:
        notification_id = str(notification["notification_id"])
        if not await subscription_service.notification_is_current(notification_id):
            return
        delivery_results: list[bool] = []
        errors: list[str] = []
        try:
            user_id = int(notification["user_id"])
            user = self.bot.get_user(user_id)
            if user is None:
                user = await self.bot.fetch_user(user_id)
            guild = self.bot.get_guild(int(notification["guild_id"]))
            server_name = getattr(guild, "name", None) or "السيرفر"
            subscription = await subscription_service.get_subscription(
                int(notification["guild_id"]),
                str(notification["subscription_id"]),
            )
            if subscription is None:
                raise ValueError("subscription record is missing")
            settings = await subscription_service.get_subscription_settings(
                int(notification["guild_id"])
            )
            plans = await subscription_service.list_subscription_plans(
                int(notification["guild_id"])
            )
            plan = next(
                (
                    item for item in plans
                    if item["plan_id"] == subscription.get("plan_id")
                ),
                None,
            )
            try:
                level_row = await database.get_user_level(
                    int(notification["guild_id"]), user_id
                )
                level = int(level_row["text_level"]) if level_row else 0
            except Exception:
                logger.exception(
                    "Cannot load subscription notification level user=%s",
                    user_id,
                )
                level = 0
            payload = notification.get("payload") or {}
            end_date = str(
                payload.get("end_date") or notification["reference_end_date"]
            )
            start_date = str(subscription.get("start_date") or "")
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            days_remaining = max(
                0,
                int(
                    (
                        end_dt - datetime.now(timezone.utc)
                    ).total_seconds()
                    + 86_399
                )
                // 86_400,
            )
            remaining = {
                168: "7 أيام",
                72: "3 أيام",
                24: "24 ساعة",
            }.get(
                notification.get("reminder_hours"),
                payload.get("reminder") or "وقت قصير",
            )
            values = {
                "user": f"<@{user_id}>",
                "name": discord.utils.escape_mentions(
                    getattr(user, "display_name", None)
                    or getattr(user, "name", "عضو")
                )[:100],
                "server": discord.utils.escape_mentions(server_name)[:100],
                "plan": discord.utils.escape_mentions(
                    plan["name"] if plan else "اشتراك"
                )[:100],
                "start_date": _format_date(start_date),
                "end_date": _format_date(end_date),
                "days_remaining": days_remaining,
                "subscription_id": str(subscription["subscription_id"])[:100],
                "xp": int(payload.get("xp", 0) or 0),
                "level": level,
                "message": _format_date(end_date),
                "remaining": remaining,
            }
            selected_template = await subscription_service.get_subscription_template(
                int(notification["guild_id"]), payload.get("template_id")
            )
            template = (
                selected_template["content"]
                if selected_template
                else settings["notification_templates"].get(
                    notification["event_type"],
                    subscription_service.DEFAULT_NOTIFICATION_TEMPLATES[
                        notification["event_type"]
                    ],
                )
            )
            try:
                content = template.format_map(values)
            except (KeyError, ValueError, IndexError):
                logger.exception(
                    "Invalid stored subscription template event=%s",
                    notification["event_type"],
                )
                content = subscription_service.DEFAULT_NOTIFICATION_TEMPLATES[
                    notification["event_type"]
                ].format_map(values)
            dm_enabled = bool(payload.get("dm_enabled", True))
            channel_enabled = bool(payload.get("channel_enabled", False))
            destinations = int(dm_enabled) + int(channel_enabled)
            if destinations == 0:
                raise ValueError("notification has no enabled delivery destination")
            if dm_enabled:
                try:
                    await user.send(
                        content[:1900],
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    delivery_results.append(True)
                except Exception as exc:
                    errors.append(f"DM {type(exc).__name__}: {exc}"[:400])
                    delivery_results.append(False)
            if channel_enabled:
                try:
                    channel_id = int(payload.get("channel_id") or 0)
                    if channel_id <= 0:
                        raise ValueError("channel route is missing")
                    channel = guild.get_channel(channel_id) if guild else None
                    if channel is None:
                        channel = await self.bot.fetch_channel(channel_id)
                    channel_guild = getattr(channel, "guild", None)
                    if (
                        guild is None
                        or channel_guild is None
                        or int(channel_guild.id) != int(guild.id)
                    ):
                        raise ValueError("notification channel is not in the target server")
                    sender = getattr(channel, "send", None)
                    if not callable(sender):
                        raise ValueError("notification channel cannot receive messages")
                    await sender(
                        content[:1900],
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    delivery_results.append(True)
                except Exception as exc:
                    errors.append(f"Channel {type(exc).__name__}: {exc}"[:400])
                    delivery_results.append(False)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}"[:400])
            logger.warning(
                "Subscription DM failed notification=%s user=%s: %s",
                notification_id,
                notification.get("user_id"),
                errors[-1],
            )
        delivered = any(delivery_results)
        error_text = "; ".join(errors)[:1000] if errors else None
        await subscription_service.complete_notification(
            notification_id,
            delivered=delivered,
            error=error_text,
        )

    @tasks.loop(minutes=5)
    async def subscription_worker(self):
        try:
            await subscription_service.process_due_subscriptions()
            notifications = await subscription_service.claim_due_notifications()
            for notification in notifications:
                try:
                    await self._deliver_notification(notification)
                except Exception:
                    logger.exception(
                        "Subscription notification processing failed id=%s",
                        notification.get("notification_id"),
                    )
        except Exception:
            logger.exception("Subscription background worker failed")

    @subscription_worker.before_loop
    async def before_subscription_worker(self):
        await self.bot.wait_until_ready()

    @subscription_worker.error
    async def subscription_worker_error(self, error: Exception):
        logger.error(
            "Subscription background worker stopped",
            exc_info=(type(error), error, error.__traceback__),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(SubscriptionCommands(bot))


async def publish_subscription_commands(bot, guild=None) -> bool:
    """Upsert only /subscription without replacing other remote commands."""
    try:
        command = bot.tree.get_command("subscription")
        if command is None:
            raise RuntimeError("local /subscription command group is missing")
        payload = command.to_dict(bot.tree)
        remote = await bot.tree.fetch_commands(guild=guild)
        previous = next(
            (item for item in remote if item.name == "subscription"), None
        )
        fields = (
            "name",
            "description",
            "type",
            "options",
            "default_member_permissions",
            "dm_permission",
        )
        if previous and all(
            previous.to_dict().get(field) == payload.get(field)
            for field in fields
        ):
            logger.info("PRIME subscription command registration is unchanged")
            return True
        if guild is None:
            await bot.http.upsert_global_command(bot.application_id, payload)
        else:
            await bot.http.upsert_guild_command(
                bot.application_id, guild.id, payload
            )
        logger.info("PRIME subscription command group registered")
        return True
    except Exception:
        logger.exception(
            "Cannot register PRIME subscription command; unrelated remote commands are untouched"
        )
        return False
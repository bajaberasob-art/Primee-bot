import asyncio
import datetime
import logging
import random
import re
import time
from collections import defaultdict, deque
from threading import RLock
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    SETTINGS_DEFAULTS,
    complete_security_lockdown_unlock,
    get_all_security_whitelists,
    get_guild_settings,
    get_security_incidents,
    get_security_lockdown_snapshots,
    get_security_lockdown_status,
    record_security_incident,
    save_security_lockdown_snapshots,
    set_security_lockdown_status,
    set_security_whitelist_member,
    update_guild_settings,
)


logger = logging.getLogger("SecurityCog")

# فحص روابط التصيد وسرقة الحسابات
SCAM_REGEX = re.compile(
    r"(https?://)?(www\.)?(discord\.gift|nitro-gift|steamcommunity-link|"
    r"grabify\.link|iplogger\.(org|com)|bit\.ly|tinyurl\.com|t\.co)[^\s]+",
    re.I,
)

THREAT_LIMIT = 3
THREAT_WINDOW = 10.0
THREAT_RETENTION = 60.0
AUDIT_ENTRY_MAX_AGE = 20.0
LOCKDOWN_INTERVAL = 0.35
INCIDENT_BUFFER_SIZE = 50


def restore_lockdown_overwrite(overwrite, original: dict[str, Any]):
    """Restore only permission bits still set to the lockdown's explicit deny."""
    for permission in ("send_messages", "send_messages_in_threads"):
        if getattr(overwrite, permission) is False:
            setattr(overwrite, permission, original[permission])
    return overwrite


class MathCaptchaModal(discord.ui.Modal, title="بوابة التحقق البشري الذكية"):
    def __init__(self, a: int, b: int, role_id: int):
        super().__init__()
        self.answer = str(a + b)
        self.role_id = role_id
        self.input = discord.ui.TextInput(
            label=f"حل المسألة التالية: {a} + {b} = ؟",
            placeholder="اكتب الناتج فقط هنا...",
            min_length=1,
            max_length=4,
        )
        self.add_item(self.input)

    async def on_submit(self, itx: discord.Interaction):
        if self.input.value.strip() == self.answer:
            role = itx.guild.get_role(self.role_id)
            if role and itx.guild.me and role < itx.guild.me.top_role:
                await itx.user.add_roles(role)
                return await itx.response.send_message(
                    "✅ تم التحقق البشري بنجاح ومُنحت رتبة الدخول!",
                    ephemeral=True,
                )
            return await itx.response.send_message(
                "⚠️ خطأ: رتبة التفعيل أعلى من صلاحيات البوت.",
                ephemeral=True,
            )
        await itx.response.send_message(
            "❌ إجابة خاطئة! أعد المحاولة مرة أخرى.",
            ephemeral=True,
        )


class CaptchaView(discord.ui.View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id
        # A role-specific ID prevents one server's CAPTCHA view from routing
        # interactions to another server's verified role.
        button = next(
            item for item in self.children if isinstance(item, discord.ui.Button)
        )
        button.custom_id = f"btn_sec_cap:{role_id}"

    @discord.ui.button(
        label="بدء التحقق 🛡️",
        style=discord.ButtonStyle.success,
        custom_id="btn_sec_cap",
    )
    async def verify(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        a, b = random.randint(1, 20), random.randint(1, 20)
        await itx.response.send_modal(MathCaptchaModal(a, b, self.role_id))


class Security(commands.Cog):
    """محرك دفاع تهديدات متعدد المستويات مع إعدادات حية وسجل حوادث."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # (guild_id, actor_id) -> recent destructive action timestamps.
        self._threats: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._mitigated: dict[tuple[int, int], float] = {}
        self._whitelist: dict[int, set[int]] = defaultdict(set)

        # This is intentionally a real deque so the dashboard and diagnostics
        # can inspect a bounded, in-memory incident history.
        self.incidents: deque[dict[str, Any]] = deque(maxlen=INCIDENT_BUFFER_SIZE)
        self._incident_lock = RLock()

        self._lockdown_queue: Optional[asyncio.Queue] = None
        self._lockdown_worker: Optional[asyncio.Task] = None
        self._lockdown_states: dict[int, bool] = {}
        self._lockdown_statuses: dict[int, str] = {}
        self._lockdown_operation_locks: dict[int, asyncio.Lock] = {}
        self._captcha_views: dict[int, CaptchaView] = {}

    # ------------------------------------------------------------------
    # Dynamic configuration and incident stream
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        """Restore durable operator controls and persistent CAPTCHA callbacks."""
        cached_statuses = dict(self._lockdown_statuses)
        try:
            all_whitelists = await get_all_security_whitelists()
        except Exception:
            logger.exception("[SECURITY_STATE] تعذر تحميل قوائم المشرفين الموثوقين")
            all_whitelists = None

        for guild in list(getattr(self.bot, "guilds", ()) or ()):
            guild_id = int(guild.id)
            if all_whitelists is not None:
                self._whitelist[guild_id] = set(
                    all_whitelists.get(guild_id, set())
                )
            try:
                status = await get_security_lockdown_status(guild_id)
            except Exception:
                logger.exception(
                    "[SECURITY_STATE] تعذر تحميل حالة الإغلاق للسيرفر %s",
                    guild_id,
                )
                status = self._lockdown_statuses.get(guild_id, "lock_partial")
            self._lockdown_statuses[guild_id] = status
            self._lockdown_states[guild_id] = status != "unlocked"

            settings = await self.security_settings(guild_id)
            role_id = settings.get("captcha_role_id")
            if settings.get("captcha_enabled") and role_id:
                self._captcha_view(int(role_id))

            if status in {"locking", "lock_partial"} and cached_statuses.get(guild_id) not in {
                "locking",
                "lock_partial",
            }:
                await self.emergency_lockdown(guild_id, True)
            elif status in {"unlocking", "unlock_partial"} and cached_statuses.get(guild_id) not in {
                "unlocking",
                "unlock_partial",
            }:
                await self.emergency_lockdown(guild_id, False)

    async def security_settings(self, guild_id: int) -> dict[str, Any]:
        """Read security values through database.py's TTL/LRU cache."""
        try:
            snapshot = await get_guild_settings(int(guild_id))
            values = snapshot["settings"]
            return {
                "anti_nuke": bool(values["anti_nuke"]),
                "anti_alt_days": int(values["anti_alt_days"]),
                "captcha_enabled": bool(values["captcha_enabled"]),
                "captcha_role_id": values["captcha_role_id"],
                "anti_links": bool(values.get("anti_links", True)),
            }
        except Exception:
            # Security listeners must stay alive if SQLite is briefly
            # unavailable. The safe defaults keep anti-nuke enabled and use
            # the original three-day account-age threshold.
            logger.exception(
                "[SECURITY_CONFIG] تعذر قراءة إعدادات السيرفر %s",
                guild_id,
            )
            return {
                "anti_nuke": bool(SETTINGS_DEFAULTS["anti_nuke"]),
                "anti_alt_days": int(SETTINGS_DEFAULTS["anti_alt_days"]),
                "captcha_enabled": bool(SETTINGS_DEFAULTS["captcha_enabled"]),
                "captcha_role_id": SETTINGS_DEFAULTS["captcha_role_id"],
                "anti_links": bool(SETTINGS_DEFAULTS.get("anti_links", True)),
            }

    def get_incidents(self, guild_id: Optional[int] = None) -> list[dict[str, Any]]:
        """Return a consistent snapshot of the thread-safe ring buffer."""
        with self._incident_lock:
            rows = list(self.incidents)
        if guild_id is not None:
            rows = [row for row in rows if row["guild_id"] == int(guild_id)]
        return [dict(row) for row in rows]

    async def get_persisted_incidents(self, guild_id: int) -> list[dict[str, Any]]:
        return await get_security_incidents(int(guild_id), limit=200)

    def get_whitelist(self, guild_id: int) -> list[str]:
        return [str(user_id) for user_id in sorted(self._whitelist.get(int(guild_id), set()))]

    def is_locked(self, guild_id: int) -> bool:
        status = self._lockdown_statuses.get(int(guild_id))
        if status is not None:
            return status != "unlocked"
        return bool(self._lockdown_states.get(int(guild_id), False))

    async def record_control_action(
        self,
        guild_id: int,
        culprit_id: int,
        culprit_name: str,
        action_type: str,
        mitigation_taken: str,
    ) -> dict[str, Any]:
        """Record a dashboard/operator action without treating it as an attack."""
        incident = await self._record_incident(
            guild_id,
            culprit_id,
            action_type,
            mitigation_taken,
            culprit_name=str(culprit_name),
        )
        logger.info(
            "[SECURITY_CONTROL] guild=%s operator=%s action=%s mitigation=%s",
            guild_id,
            culprit_id,
            action_type,
            mitigation_taken,
        )
        return incident

    async def _record_incident(
        self,
        guild_id: int,
        culprit: Any,
        action_type: str,
        mitigation_taken: str,
        *,
        culprit_name: Optional[str] = None,
    ) -> dict[str, Any]:
        culprit_id = int(getattr(culprit, "id", culprit))
        name = culprit_name or getattr(culprit, "display_name", None) or getattr(
            culprit, "name", None
        ) or str(culprit_id)
        try:
            incident = await record_security_incident(
                guild_id,
                culprit_id,
                str(name),
                action_type,
                mitigation_taken,
            )
        except Exception:
            # Audit persistence must not interrupt a Discord-side protection.
            logger.exception(
                "[SECURITY_INCIDENT] Failed to persist event for guild %s",
                guild_id,
            )
            incident = {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "guild_id": int(guild_id),
                "culprit_id": culprit_id,
                "culprit_name": str(name),
                "action_type": str(action_type),
                "mitigation_taken": str(mitigation_taken),
            }
        with self._incident_lock:
            self.incidents.append(incident)
        logger.warning(
            "[SECURITY_INCIDENT] guild=%s culprit=%s action=%s mitigation=%s",
            guild_id,
            culprit_id,
            action_type,
            mitigation_taken,
        )
        return dict(incident)

    async def whitelist_member(self, guild_id: int, user_id: int) -> None:
        """Persist and cache an explicitly trusted operator."""
        await set_security_whitelist_member(guild_id, user_id, True)
        self._whitelist[int(guild_id)].add(int(user_id))

    async def remove_whitelisted_member(self, guild_id: int, user_id: int) -> None:
        await set_security_whitelist_member(guild_id, user_id, False)
        self._whitelist[int(guild_id)].discard(int(user_id))

    def _is_whitelisted(self, guild: discord.Guild, user_id: int) -> bool:
        bot_id = getattr(self.bot.user, "id", None)
        return (
            int(user_id) == int(guild.owner_id)
            or (bot_id is not None and int(user_id) == int(bot_id))
            or int(user_id) in self._whitelist.get(int(guild.id), set())
        )

    # ------------------------------------------------------------------
    # Emergency lockdown queue
    # ------------------------------------------------------------------
    def _ensure_lockdown_worker(self) -> None:
        if self._lockdown_worker and not self._lockdown_worker.done():
            return
        self._lockdown_queue = asyncio.Queue(maxsize=64)
        self._lockdown_worker = asyncio.create_task(self._lockdown_worker_loop())
        self._lockdown_worker.add_done_callback(self._lockdown_worker_done)

    def _lockdown_worker_done(self, task: asyncio.Task) -> None:
        """Consume worker failures so shutdown never leaves an unobserved task."""
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("[SECURITY_LOCKDOWN] عامل الإغلاق انتهى بخطأ")
        finally:
            if self._lockdown_worker is task:
                self._lockdown_worker = None
                self._lockdown_queue = None

    async def emergency_lockdown(self, guild_id: int, locked: bool) -> dict[str, Any]:
        """Queue lockdown changes while preserving pre-existing permissions."""
        guild_id = int(guild_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return {"queued": False, "channels": 0, "locked": bool(locked)}

        everyone = guild.default_role
        operation_lock = self._lockdown_operation_locks.setdefault(
            guild_id, asyncio.Lock()
        )
        async with operation_lock:
            try:
                previous_status = await get_security_lockdown_status(guild_id)
                snapshots_by_channel = await get_security_lockdown_snapshots(guild_id)
            except Exception:
                logger.exception(
                    "[SECURITY_LOCKDOWN] تعذر قراءة الحالة المحفوظة للسيرفر %s",
                    guild_id,
                )
                return {
                    "queued": False,
                    "channels": 0,
                    "locked": self.is_locked(guild_id),
                    "error": "state_unavailable",
                }

            if locked:
                channels = []
                snapshots = []
                for channel in guild.text_channels:
                    try:
                        if self.is_lockdown_exempt(channel):
                            continue
                        if not channel.permissions_for(everyone).view_channel:
                            continue
                        overwrite = channel.overwrites_for(everyone)
                        channels.append(int(channel.id))
                        snapshots.append({
                            "channel_id": int(channel.id),
                            "send_messages": overwrite.send_messages,
                            "send_messages_in_threads": (
                                overwrite.send_messages_in_threads
                            ),
                        })
                    except (AttributeError, discord.DiscordException):
                        logger.debug(
                            "تعذر فحص خصوصية القناة %s",
                            getattr(channel, "id", "unknown"),
                            exc_info=True,
                        )
                try:
                    await save_security_lockdown_snapshots(guild_id, snapshots)
                except Exception:
                    logger.exception(
                        "[SECURITY_LOCKDOWN] تعذر حفظ صلاحيات القنوات قبل الإغلاق "
                        "في السيرفر %s",
                        guild_id,
                    )
                    return {
                        "queued": False,
                        "channels": 0,
                        "locked": self.is_locked(guild_id),
                        "error": "snapshot_save_failed",
                    }
            else:
                if not snapshots_by_channel:
                    if previous_status == "unlocked":
                        self._lockdown_statuses[guild_id] = "unlocked"
                        self._lockdown_states[guild_id] = False
                        return {
                            "queued": True,
                            "channels": 0,
                            "locked": False,
                        }
                    logger.error(
                        "[SECURITY_LOCKDOWN] رفض فتح السيرفر %s لغياب نسخة "
                        "الصلاحيات الأصلية",
                        guild_id,
                    )
                    return {
                        "queued": False,
                        "channels": 0,
                        "locked": True,
                        "error": "restore_snapshot_missing",
                    }
                channels = list(snapshots_by_channel)

            self._ensure_lockdown_worker()
            if self._lockdown_queue is None or self._lockdown_queue.full():
                logger.error(
                    "[SECURITY_LOCKDOWN] طابور الإغلاق ممتلئ للسيرفر %s",
                    guild_id,
                )
                return {
                    "queued": False,
                    "channels": len(channels),
                    "locked": self.is_locked(guild_id),
                    "error": "queue_full",
                }

            next_status = "locking" if locked else "unlocking"
            try:
                await set_security_lockdown_status(guild_id, next_status)
            except Exception:
                logger.exception(
                    "[SECURITY_LOCKDOWN] تعذر حفظ حالة العملية للسيرفر %s",
                    guild_id,
                )
                return {
                    "queued": False,
                    "channels": len(channels),
                    "locked": self.is_locked(guild_id),
                    "error": "state_save_failed",
                }
            self._lockdown_statuses[guild_id] = next_status
            self._lockdown_states[guild_id] = True
            try:
                self._lockdown_queue.put_nowait(
                    (guild_id, bool(locked), channels)
                )
            except asyncio.QueueFull:
                self._lockdown_statuses[guild_id] = previous_status
                self._lockdown_states[guild_id] = previous_status != "unlocked"
                try:
                    await set_security_lockdown_status(
                        guild_id, previous_status
                    )
                except Exception:
                    logger.exception(
                        "[SECURITY_LOCKDOWN] تعذر التراجع عن الحالة المحفوظة "
                        "للسيرفر %s",
                        guild_id,
                    )
                return {
                    "queued": False,
                    "channels": len(channels),
                    "locked": self.is_locked(guild_id),
                    "error": "queue_full",
                }

        await self._record_incident(
            guild_id,
            getattr(self.bot, "user", 0) or 0,
            "emergency_lockdown",
            f"queued:{'locked' if locked else 'unlocked'}:{len(channels)}",
        )
        return {
            "queued": True,
            "channels": len(channels),
            "locked": self.is_locked(guild_id),
        }

    @staticmethod
    def is_lockdown_exempt(channel) -> bool:
        """Keep obviously administrative channels available during a lockdown."""
        channel_name = str(getattr(channel, "name", "")).casefold()
        category_name = str(
            getattr(getattr(channel, "category", None), "name", "")
        ).casefold()
        staff_markers = (
            "staff", "admin", "management", "moderator", "mod-only",
            "logs", "audit", "إدارة", "مشرف", "خاص", "سجل",
        )
        return any(marker in f"{channel_name} {category_name}" for marker in staff_markers)

    def get_lockdown_exemptions(self, guild_id: int) -> list[dict[str, str]]:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return []
        return [
            {"id": str(channel.id), "name": str(channel.name)}
            for channel in guild.text_channels
            if self.is_lockdown_exempt(channel)
        ]

    async def _lockdown_worker_loop(self) -> None:
        assert self._lockdown_queue is not None
        while True:
            guild_id, locked, channel_ids = await self._lockdown_queue.get()
            try:
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    continue
                everyone = guild.default_role
                snapshots = await get_security_lockdown_snapshots(guild_id)
                failed = False
                for channel_id in channel_ids:
                    channel = guild.get_channel(channel_id)
                    if channel is None:
                        continue
                    try:
                        overwrite = channel.overwrites_for(everyone)
                        if locked:
                            if channel_id not in snapshots:
                                failed = True
                                logger.error(
                                    "[SECURITY_LOCKDOWN] لا توجد نسخة صلاحيات "
                                    "للقناة %s في %s",
                                    channel_id,
                                    guild_id,
                                )
                                continue
                            overwrite.send_messages = False
                            overwrite.send_messages_in_threads = False
                        else:
                            original = snapshots.get(channel_id)
                            if original is None:
                                failed = True
                                logger.error(
                                    "[SECURITY_LOCKDOWN] لا توجد صلاحيات أصلية "
                                    "للقناة %s في %s",
                                    channel_id,
                                    guild_id,
                                )
                                continue
                            # Restore only values still carrying our lockdown
                            # deny. This leaves later staff edits untouched.
                            overwrite = restore_lockdown_overwrite(
                                overwrite, original
                            )
                        await channel.set_permissions(
                            everyone,
                            overwrite=overwrite,
                            reason="Emergency security lockdown"
                            if locked
                            else "Emergency security lockdown restored",
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        failed = True
                        logger.warning(
                            "[SECURITY_LOCKDOWN] فشل تحديث القناة %s في %s",
                            channel_id,
                            guild_id,
                            exc_info=True,
                        )
                    await asyncio.sleep(LOCKDOWN_INTERVAL)
                if locked:
                    status = "lock_partial" if failed else "locked"
                elif failed:
                    status = "unlock_partial"
                else:
                    status = "unlocked"
                if status == "unlocked":
                    await complete_security_lockdown_unlock(guild_id)
                else:
                    await set_security_lockdown_status(guild_id, status)
                self._lockdown_statuses[guild_id] = status
                self._lockdown_states[guild_id] = status != "unlocked"
                if status in {"lock_partial", "unlock_partial"}:
                    await self._record_incident(
                        guild_id,
                        getattr(self.bot, "user", 0) or 0,
                        "lockdown_partial",
                        f"{status}:{len(channel_ids)}",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[SECURITY_LOCKDOWN] خطأ في طابور الإغلاق")
            finally:
                self._lockdown_queue.task_done()

    def cog_unload(self):
        if self._lockdown_worker and not self._lockdown_worker.done():
            self._lockdown_worker.cancel()
        self._lockdown_states.clear()
        self._lockdown_statuses.clear()
        self._lockdown_operation_locks.clear()
        self._captcha_views.clear()

    def _captcha_view(self, role_id: int) -> CaptchaView:
        role_id = int(role_id)
        view = self._captcha_views.get(role_id)
        if view is None:
            view = CaptchaView(role_id)
            self._captcha_views[role_id] = view
            self.bot.add_view(view)
        return view

    # ------------------------------------------------------------------
    # Threat tracking and mitigation
    # ------------------------------------------------------------------
    def _track_threat(self, guild_id: int, actor_id: int) -> int:
        now = time.monotonic()
        key = (int(guild_id), int(actor_id))
        timestamps = self._threats[key]
        while timestamps and now - timestamps[0] > THREAT_WINDOW:
            timestamps.popleft()
        timestamps.append(now)
        # Keep the dictionaries bounded even if an attacker changes IDs.
        for stale_key, last in list(self._mitigated.items()):
            if now - last > THREAT_RETENTION:
                self._mitigated.pop(stale_key, None)
                self._threats.pop(stale_key, None)
        return len(timestamps)

    async def _resolve_member(
        self,
        guild: discord.Guild,
        user_id: int,
    ) -> Optional[discord.Member]:
        member = guild.get_member(int(user_id))
        if member is not None:
            return member
        try:
            return await guild.fetch_member(int(user_id))
        except discord.NotFound:
            return None
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            logger.warning(
                "[SECURITY_MEMBER] تعذر جلب العضو %s من السيرفر %s",
                user_id,
                guild.id,
                exc_info=True,
            )
            return None

    async def _handle_admin_action(
        self,
        guild: discord.Guild,
        actor: discord.abc.User,
        action_type: str,
    ) -> None:
        if actor is None or self._is_whitelisted(guild, actor.id):
            return
        member = await self._resolve_member(guild, actor.id)
        if member is None or not member.guild_permissions.administrator:
            return

        count = self._track_threat(guild.id, actor.id)
        await self._record_incident(
            guild.id, actor, action_type, f"observed:{count}"
        )
        if count <= THREAT_LIMIT:
            return

        key = (guild.id, actor.id)
        now = time.monotonic()
        if now - self._mitigated.get(key, 0) < THREAT_RETENTION:
            return
        self._mitigated[key] = now

        lockdown = await self.emergency_lockdown(guild.id, True)
        reason = f"تجاوز {THREAT_LIMIT} إجراءات إدارية خلال {int(THREAT_WINDOW)} ثوان"
        await self.quarantine_admin(guild, member, reason, action_type, lockdown)

    async def _audit_actor(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int,
    ):
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                target = getattr(entry, "target", None)
                if getattr(target, "id", None) != int(target_id):
                    continue
                created_at = getattr(entry, "created_at", None)
                if created_at is not None:
                    age = (discord.utils.utcnow() - created_at).total_seconds()
                    if age > AUDIT_ENTRY_MAX_AGE or age < -5:
                        continue
                return entry.user
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            logger.warning(
                "[SECURITY_AUDIT] تعذر قراءة سجل التدقيق للسيرفر %s",
                guild.id,
                exc_info=True,
            )
        return None

    async def quarantine_admin(
        self,
        guild: discord.Guild,
        member: discord.Member,
        reason: str,
        action_type: str = "admin_abuse",
        lockdown: Optional[dict[str, Any]] = None,
    ):
        """Strip Administrator roles, ban the actor, and record every outcome."""
        if self._is_whitelisted(guild, member.id) or member.id == guild.owner_id:
            return

        mitigation = []
        admin_roles = [
            role
            for role in member.roles
            if not role.is_default() and role.permissions.administrator
        ]
        try:
            if admin_roles:
                retained = [
                    role for role in member.roles if role not in admin_roles
                ]
                await member.edit(
                    roles=retained,
                    reason=f"Anti-Nuke: {reason}",
                )
                mitigation.append(f"admin_roles_stripped:{len(admin_roles)}")
            else:
                mitigation.append("admin_roles_stripped:0")
        except (discord.Forbidden, discord.HTTPException):
            mitigation.append("admin_roles_strip_failed")
            logger.warning(
                "[SECURITY_CRITICAL] فشل تجريد رتب %s في %s",
                member.id,
                guild.id,
                exc_info=True,
            )

        try:
            await guild.ban(
                member,
                reason=f"Anti-Nuke: {reason}",
                delete_message_seconds=0,
            )
            mitigation.append("account_banned")
        except (discord.Forbidden, discord.HTTPException):
            mitigation.append("account_ban_failed")
            logger.warning(
                "[SECURITY_CRITICAL] فشل حظر الحساب %s في %s",
                member.id,
                guild.id,
                exc_info=True,
            )

        if lockdown and lockdown.get("queued"):
            mitigation.append(f"lockdown_queued:{lockdown['channels']}")
        await self._record_incident(
            guild.id,
            member,
            action_type,
            ",".join(mitigation),
        )

    # ------------------------------------------------------------------
    # Existing protection listeners, now dynamically configured
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, mem: discord.Member):
        config = await self.security_settings(mem.guild.id)
        age_days = (discord.utils.utcnow() - mem.created_at).total_seconds() / 86400
        if config["anti_alt_days"] > 0 and age_days < config["anti_alt_days"]:
            try:
                await mem.kick(
                    reason=(
                        f"حساب جديد مشبوه (عمره أقل من {config['anti_alt_days']} أيام)"
                    )
                )
                channel = mem.guild.system_channel
                if channel:
                    await channel.send(
                        f"🛡️ تم طرد الحساب المشبوه {mem.mention} تلقائياً "
                        "(تاريخ الإنشاء حديث)."
                    )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "[SECURITY_ALT] فشل التعامل مع الحساب الجديد %s",
                    mem.id,
                    exc_info=True,
                )
            return

        if config["captcha_enabled"] and config["captcha_role_id"]:
            role = mem.guild.get_role(int(config["captcha_role_id"]))
            if role and mem.guild.me and role < mem.guild.me.top_role:
                channel = mem.guild.system_channel
                if channel:
                    view = self._captcha_view(role.id)
                    try:
                        await channel.send(
                            f"🛡️ {mem.mention} أكمل التحقق البشري للحصول على رتبة الدخول.",
                            view=view,
                            delete_after=600,
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        logger.warning(
                            "[SECURITY_CAPTCHA] فشل إرسال التحقق للسيرفر %s",
                            mem.guild.id,
                            exc_info=True,
                        )

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if (
            msg.author.bot
            or not msg.guild
            or msg.author.guild_permissions.manage_guild
        ):
            return
        if not (await self.security_settings(msg.guild.id))["anti_links"]:
            return
        if SCAM_REGEX.search(msg.content):
            try:
                await msg.delete()
                timeout_time = discord.utils.utcnow() + datetime.timedelta(hours=2)
                await msg.author.timeout(
                    timeout_time,
                    reason="إرسال روابط تصيد مشبوهة",
                )
                await msg.channel.send(
                    f"🚨 {msg.author.mention} تم حجب الرابط وكتمك لمدة ساعتين "
                    "لحماية الأعضاء!",
                    delete_after=6,
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "[SECURITY_LINK] تعذر حذف أو كتم رسالة مشبوهة",
                    exc_info=True,
                )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not (await self.security_settings(channel.guild.id))["anti_nuke"]:
            return
        actor = await self._audit_actor(
            channel.guild,
            discord.AuditLogAction.channel_delete,
            channel.id,
        )
        if actor:
            await self._handle_admin_action(channel.guild, actor, "channel_delete")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if not (await self.security_settings(role.guild.id))["anti_nuke"]:
            return
        actor = await self._audit_actor(
            role.guild,
            discord.AuditLogAction.role_delete,
            role.id,
        )
        if actor:
            await self._handle_admin_action(role.guild, actor, "role_delete")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not (await self.security_settings(member.guild.id))["anti_nuke"]:
            return
        actor = await self._audit_actor(
            member.guild,
            discord.AuditLogAction.kick,
            member.id,
        )
        if actor:
            await self._handle_admin_action(member.guild, actor, "member_kick")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.User):
        if not (await self.security_settings(guild.id))["anti_nuke"]:
            return
        actor = await self._audit_actor(
            guild,
            discord.AuditLogAction.ban,
            member.id,
        )
        if actor:
            await self._handle_admin_action(guild, actor, "member_ban")

    @app_commands.command(
        name="setup_captcha",
        description="تثبيت بوابة التحقق البشري الذكية",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(
        manage_roles=True,
        send_messages=True,
        embed_links=True,
    )
    async def setup_captcha(
        self,
        itx: discord.Interaction,
        verified_role: discord.Role,
    ):
        guild = itx.guild
        bot_member = guild.me
        if bot_member is None or verified_role >= bot_member.top_role:
            await itx.response.send_message(
                "⚠️ رتبة التفعيل يجب أن تكون أسفل أعلى رتبة للبوت.",
                ephemeral=True,
            )
            return

        try:
            await update_guild_settings(
                guild.id,
                captcha_enabled=True,
                captcha_role_id=verified_role.id,
            )
        except Exception:
            logger.exception("[SECURITY_CAPTCHA] فشل حفظ إعدادات الكابتشا")
            await itx.response.send_message(
                "❌ تعذر حفظ إعدادات الكابتشا في قاعدة البيانات.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🛡️ بوابة التحقق البشري والأمان الفائق",
            description=(
                "لحماية السيرفر من حسابات السبام وغارات البوتات المخربة:\n"
                "اضغط على الزر أدناه وقم بحل المسألة الرياضية البسيطة "
                "لتفعيل حسابك."
            ),
            color=0x2ECC71,
        )
        embed.set_footer(text="نظام الحماية المركزي النشط")
        view = self._captcha_view(verified_role.id)
        await itx.channel.send(embed=embed, view=view)
        await itx.response.send_message(
            "✅ تم نشر بوابة الكابتشا وحفظ إعداداتها بنجاح.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Security(bot))
"""Upgrade the existing Utilities voice system, with durable ownership and controls."""
import asyncio
import io
import logging
import time

import discord
from discord.ext import commands, tasks

import temp_voice_store as store
from temp_voice_panel import (
    ChoicePicker, DeleteConfirmation, MemberPicker, RoomPanel, ValueModal, render_template,
)

log = logging.getLogger("TempVoice")
MEMBER_ACTIONS = {"kick", "invite", "trust", "untrust", "ban", "unban", "mute", "deafen", "transfer"}


class TempVoice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.configs = {}
        self.rooms = {}
        self.locks = {}
        self.ready = False
        self.restoring = None
        self.last_error = {}
        self.member_counts = {}
        self.report_times = {}

    async def cog_load(self):
        # All custom IDs are registered, even if some are not in the current layout.
        # Old message callbacks survive config changes and bot restarts.
        actions = list(store.BUTTONS)
        for start in range(0, len(actions), 20):
            self.bot.add_view(RoomPanel(self, {"buttons": actions[start:start + 20]}))
        self.restoring = asyncio.create_task(self.restore())

    async def cog_unload(self):
        self.heartbeat.cancel()
        if self.restoring:
            self.restoring.cancel()
            await asyncio.gather(self.restoring, return_exceptions=True)

    def lock_for(self, guild_id):
        return self.locks.setdefault(guild_id, asyncio.Lock())

    async def config(self, guild_id):
        if guild_id not in self.configs:
            self.configs[guild_id] = (await store.get_config(guild_id))["config"]
        return self.configs[guild_id]

    def update_config(self, guild_id, config):
        self.configs[guild_id] = config

    async def restore(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                snap = await store.get_config(guild.id)
                cfg = snap["config"]
                # Adopt the existing creation hub, never create channels on startup.
                if snap["revision"] == 0:
                    hub = next((ch for ch in guild.voice_channels if ch.name == "➕ اضغط للإنشاء"), None)
                    if hub:
                        cfg.update(enabled=True, creation_channel_id=str(hub.id),
                                   category_id=str(hub.category_id) if hub.category_id else None)
                        await store.save_config(guild.id, cfg, 0)
                self.configs[guild.id] = cfg
            except Exception:
                log.exception("Cannot restore temp voice config guild=%s", guild.id)
                self.last_error[guild.id] = "تعذر استعادة الإعدادات."
        for room in await store.rooms():
            guild = self.bot.get_guild(room["guild_id"])
            if guild is None:
                continue  # Guild temporarily unavailable is not proof of deletion.
            channel = guild.get_channel(room["channel_id"])
            if channel is None:
                await store.delete_room(guild.id, room["channel_id"])
                continue
            room["last_tick"] = time.time()  # No invented minutes during downtime.
            self.rooms[channel.id] = room
            self.member_counts[channel.id] = self.humans(channel)
            await store.update_room(room)
            if not channel.members and not room["state"].get("pinned"):
                try:
                    await self.delete_room(guild, channel.id)
                except discord.HTTPException:
                    log.exception("Cannot clean empty restored room guild=%s", guild.id)
        # Recover old Utilities rooms only from the bot's own exact legacy panel.
        for guild in self.bot.guilds:
            cfg = await self.config(guild.id)
            if not cfg["category_id"]:
                continue
            candidates = [ch for ch in guild.voice_channels if ch.category_id == int(cfg["category_id"])
                          and ch.name.startswith("🔊・") and ch.id not in self.rooms][:50]
            for ch in candidates:
                try:
                    async for message in ch.history(limit=5):
                        if (message.author.id == self.bot.user.id and message.mentions
                                and any(e.title == "🎛️ تحكم برومك الصوتي" for e in message.embeds)):
                            owner = message.mentions[0]
                            state = {"trusted": [], "banned": [], "pinned": False, "panel_message_id": str(message.id)}
                            await store.add_room(guild.id, ch.id, owner.id, state, count_created=False)
                            room = next(x for x in await store.rooms(guild.id) if x["channel_id"] == ch.id)
                            self.rooms[ch.id] = room
                            self.member_counts[ch.id] = self.humans(ch)
                            await message.edit(view=RoomPanel(self, cfg))
                            if not ch.members:
                                await self.delete_room(guild, ch.id)
                            break
                except discord.HTTPException:
                    log.warning("Could not adopt legacy room guild=%s channel=%s", guild.id, ch.id)
        self.ready = True
        self.heartbeat.start()

    @staticmethod
    def humans(channel):
        return sum(not member.bot for member in channel.members)

    async def tick(self, room, count=None):
        now = time.time()
        n = self.member_counts.get(room["channel_id"], 0) if count is None else count
        delta = max(0, min(now - room["last_tick"], 120)) * n
        room["last_tick"] = now
        cfg = await self.config(room["guild_id"])
        await store.accrue(room, delta if cfg["voice_analytics"] else 0)

    @tasks.loop(seconds=60)
    async def heartbeat(self):
        for room in list(self.rooms.values()):
            try:
                async with self.lock_for(room["guild_id"]):
                    if room["channel_id"] not in self.rooms:
                        continue
                    guild = self.bot.get_guild(room["guild_id"])
                    channel = guild.get_channel(room["channel_id"]) if guild else None
                    if not channel:
                        continue
                    await self.tick(room)
                    self.member_counts[channel.id] = self.humans(channel)
                    if not channel.members and not room["state"].get("pinned"):
                        await self.delete_room(guild, channel.id)
            except Exception:
                log.exception("Temp voice heartbeat failed")

    def is_admin(self, member, config):
        return (member.id == member.guild.owner_id or member.guild_permissions.administrator
                or bool({str(role.id) for role in member.roles} & set(config["admin_role_ids"])))

    @staticmethod
    def allowed_to_create(member, config):
        roles = {str(role.id) for role in member.roles}
        return not roles.intersection(config["blacklisted_role_ids"]) and (
            not config["whitelisted_role_ids"] or bool(roles.intersection(config["whitelisted_role_ids"]))
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not self.ready or member.bot or before.channel == after.channel:
            return
        async with self.lock_for(member.guild.id):
            cfg = await self.config(member.guild.id)
            for channel in (before.channel, after.channel):
                room = self.rooms.get(channel.id) if channel else None
                if room:
                    await self.tick(room)
                    self.member_counts[channel.id] = self.humans(channel)
            if before.channel and before.channel.id in self.rooms and not before.channel.members:
                room = self.rooms[before.channel.id]
                if not room["state"].get("pinned"):
                    try:
                        await self.delete_room(member.guild, before.channel.id)
                    except discord.HTTPException:
                        self.last_error[member.guild.id] = "تعذر حذف روم فارغ؛ سيعاد التنظيف تلقائيًا."
            if (after.channel and cfg["enabled"]
                    and str(after.channel.id) == cfg["creation_channel_id"]):
                try:
                    await self.create_room(member, cfg)
                except (discord.HTTPException, ValueError) as error:
                    self.last_error[member.guild.id] = str(error)[:250]
                    log.warning("Temp voice creation rejected guild=%s: %s", member.guild.id, error)
            # Voice reconnections must respect the room's explicit banned members.
            room = self.rooms.get(after.channel.id) if after.channel else None
            if room and str(member.id) in room["state"].get("banned", []):
                if not (cfg["admin_protection"] and self.is_admin(member, cfg)):
                    try:
                        await member.move_to(None, reason="Temporary room ban")
                    except discord.HTTPException:
                        log.warning("Could not enforce room ban")

    async def create_room(self, member, cfg):
        if not self.allowed_to_create(member, cfg):
            raise ValueError("رولات العضو لا تسمح بإنشاء روم.")
        guild = member.guild
        if any(r["guild_id"] == guild.id and r["owner_id"] == member.id for r in self.rooms.values()):
            existing = next(r for r in self.rooms.values() if r["guild_id"] == guild.id and r["owner_id"] == member.id)
            channel = guild.get_channel(existing["channel_id"])
            if channel:
                await member.move_to(channel)
                return
        if sum(r["guild_id"] == guild.id for r in self.rooms.values()) >= 100:
            raise ValueError("وصل السيرفر إلى حد 100 روم مؤقت.")
        profile = await store.profile(guild.id, member.id)
        if time.time() - profile["last_created"] < cfg["cooldown"]:
            raise ValueError("انتظر انتهاء فترة إنشاء الرومات.")
        category = guild.get_channel(int(cfg["category_id"])) if cfg["category_id"] else None
        if not isinstance(category, discord.CategoryChannel):
            raise ValueError("اختر كاتيجوري صحيحًا أو اضغط تجهيز الآن.")
        perms = category.permissions_for(guild.me)
        if not perms.manage_channels or not guild.me.guild_permissions.move_members:
            raise ValueError("البوت يحتاج إدارة القنوات ونقل الأعضاء.")
        metrics, _ = await store.metrics(guild.id)
        prefs = profile["preferences"] if cfg["permanent_memory"] else {}
        name = prefs.get("name") or render_template(cfg["name_template"], member, metrics["rooms_created"] + 1)
        trusted = list(prefs.get("trusted", []))[:100]
        banned = list(prefs.get("banned", []))[:100]
        overwrites = dict(category.overwrites)
        overwrites[guild.default_role] = discord.PermissionOverwrite(
            view_channel=cfg["privacy"] == "public", connect=cfg["privacy"] == "public",
        )
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True,
                                                          send_messages=True, read_message_history=True)
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True, send_messages=True,
                                                        manage_channels=cfg["owner_manage_channel"])
        for key in cfg["admin_role_ids"]:
            role = guild.get_role(int(key))
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, connect=True)
        for key in trusted + banned:
            target = guild.get_member(int(key))
            if target:
                if key in banned and cfg["admin_protection"] and self.is_admin(target, cfg):
                    continue
                overwrites[target] = discord.PermissionOverwrite(view_channel=key in trusted, connect=key in trusted)
        channel = await guild.create_voice_channel(
            name=name[:100], category=category, overwrites=overwrites,
            user_limit=int(prefs.get("limit", cfg["user_limit"])),
            bitrate=min(cfg["bitrate"] * 1000, guild.bitrate_limit),
            reason="PRIME temporary voice",
        )
        state = {"trusted": trusted, "banned": banned, "pinned": False,
                 "privacy": cfg["privacy"], "name": channel.name, "limit": channel.user_limit}
        try:
            await member.move_to(channel)
            await store.add_room(guild.id, channel.id, member.id, state)
        except Exception:
            try:
                await channel.delete(reason="Failed temp voice creation rollback")
            except discord.HTTPException:
                log.exception("Could not roll back untracked temp room")
            raise
        room = next(r for r in await store.rooms(guild.id) if r["channel_id"] == channel.id)
        self.rooms[channel.id] = room
        self.member_counts[channel.id] = self.humans(channel)
        self.last_error.pop(guild.id, None)
        if cfg["in_room_interface"]:
            try:
                message = await channel.send(
                    render_template(cfg["welcome_template"], member, metrics["rooms_created"] + 1),
                    embed=self.embed(cfg, state), view=RoomPanel(self, cfg),
                    allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
                )
                state["panel_message_id"] = str(message.id)
                await store.update_room(room)
            except discord.HTTPException:
                self.last_error[guild.id] = "الروم أُنشئ لكن تعذر إرسال واجهته؛ استخدم البانل العام."

    @staticmethod
    def embed(config, state=None):
        state = state or {}
        embed = discord.Embed(title=config["panel_title"], description=config["panel_description"],
                              color=int(state.get("color", config["embed_color"]).lstrip("#"), 16))
        if config["banner_url"]:
            embed.set_image(url=config["banner_url"])
        return embed

    async def setup_system(self, guild, revision, republish=False):
        async with self.lock_for(guild.id):
            snap = await store.get_config(guild.id)
            if snap["revision"] != revision:
                raise store.Conflict(snap)
            cfg = snap["config"]
            if not guild.me.guild_permissions.manage_channels:
                raise ValueError("البوت يحتاج صلاحية إدارة القنوات.")
            category = guild.get_channel(int(cfg["category_id"])) if cfg["category_id"] else None
            if category is None:
                category = await guild.create_category("🔊 القنوات التفاعلية", reason="PRIME temp voice setup")
                cfg["category_id"] = str(category.id)
            hub = guild.get_channel(int(cfg["creation_channel_id"])) if cfg["creation_channel_id"] else None
            if hub is None:
                hub = next((ch for ch in category.voice_channels if ch.name == "➕ اضغط للإنشاء"), None)
                if hub is None:
                    hub = await guild.create_voice_channel("➕ اضغط للإنشاء", category=category)
                cfg["creation_channel_id"] = str(hub.id)
            panel = guild.get_channel(int(cfg["panel_channel_id"])) if cfg["panel_channel_id"] else None
            if panel is None:
                panel = next((ch for ch in category.text_channels if ch.name == "تحكم-الرومات"), None)
                if panel is None:
                    panel = await guild.create_text_channel("تحكم-الرومات", category=category)
                cfg["panel_channel_id"] = str(panel.id)
            cfg["enabled"] = True
            # Persist resource IDs before posting; a retry reuses them instead of making duplicates.
            snap = await store.save_config(guild.id, cfg, revision)
            self.update_config(guild.id, cfg)
            await self.publish_panel(guild, cfg, republish)
            snap = await store.save_config(guild.id, cfg, snap["revision"])
            return snap

    async def publish_panel(self, guild, cfg, republish=False):
        channel = guild.get_channel(int(cfg["panel_channel_id"])) if cfg["panel_channel_id"] else None
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("اختر قناة بانل نصية صحيحة.")
        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise ValueError("البوت يحتاج إرسال الرسائل وتضمين الروابط في قناة البانل.")
        embed = self.embed(cfg)
        file = stream = None
        if cfg["banner_image_id"]:
            from temp_voice_dashboard import get_banner
            image = await get_banner(guild.id, cfg["banner_image_id"], payload=True)
            if not image:
                raise ValueError("صورة البانر لم تعد متاحة.")
            if not permissions.attach_files:
                raise ValueError("البوت يحتاج إرفاق الملفات لرفع البانر.")
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}[image["mime"]]
            stream = io.BytesIO(image["payload"])
            file = discord.File(stream, filename=f"prime-voice-banner.{ext}")
            embed.set_image(url=f"attachment://prime-voice-banner.{ext}")
        try:
            old = None
            if cfg["panel_message_id"]:
                try:
                    old = await channel.fetch_message(int(cfg["panel_message_id"]))
                except discord.NotFound:
                    pass
            if old and old.author.id == self.bot.user.id and not republish:
                await old.edit(embed=embed, view=RoomPanel(self, cfg), attachments=[file] if file else [])
            else:
                message = await channel.send(embed=embed, view=RoomPanel(self, cfg),
                                             **({"file": file} if file else {}),
                                             allowed_mentions=discord.AllowedMentions.none())
                cfg["panel_message_id"] = str(message.id)
                if old and old.author.id == self.bot.user.id:
                    await old.edit(view=None)
        finally:
            if file:
                file.close()
                stream.close()

    async def delete_room(self, guild, channel_id):
        room = self.rooms.get(channel_id)
        if not room or room["guild_id"] != guild.id:
            raise ValueError("الروم غير مسجل كروم مؤقت في هذا السيرفر.")
        cfg = await self.config(guild.id)
        if str(channel_id) == cfg["creation_channel_id"]:
            raise ValueError("لا يمكن حذف قناة الإنشاء من مدير الرومات.")
        channel = guild.get_channel(channel_id)
        await self.tick(room)
        waiting_id = room["state"].get("waiting_channel_id")
        if waiting_id:
            waiting = guild.get_channel(int(waiting_id))
            if waiting:
                try:
                    await waiting.delete(reason="Temporary room waiting cleanup")
                except discord.NotFound:
                    pass
        if channel:
            try:
                await channel.delete(reason="PRIME temporary room cleanup")
            except discord.NotFound:
                pass
        await store.delete_room(guild.id, channel_id)
        self.rooms.pop(channel_id, None)
        self.member_counts.pop(channel_id, None)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if channel.id in self.rooms:
            async with self.lock_for(channel.guild.id):
                await self.delete_room(channel.guild, channel.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        self.configs.pop(guild.id, None)
        self.locks.pop(guild.id, None)
        self.last_error.pop(guild.id, None)
        for key, room in list(self.rooms.items()):
            if room["guild_id"] == guild.id:
                self.rooms.pop(key, None)
                self.member_counts.pop(key, None)

    def resolve_room(self, interaction, channel_id=None):
        guild = interaction.guild
        if not guild:
            raise ValueError("الأزرار متاحة داخل السيرفر فقط.")
        if channel_id is not None:
            room = self.rooms.get(int(channel_id))
        else:
            voice = interaction.user.voice
            room = self.rooms.get(voice.channel.id) if voice and voice.channel else None
        if not room or room["guild_id"] != guild.id:
            raise ValueError("ادخل رومًا مؤقتًا أولًا.")
        channel = guild.get_channel(room["channel_id"])
        if not isinstance(channel, discord.VoiceChannel):
            raise ValueError("الروم لم يعد موجودًا.")
        return room, channel

    async def dispatch(self, itx, action, channel_id=None, value=None):
        try:
            cfg = await self.config(itx.guild.id) if itx.guild else None
            room, channel = self.resolve_room(itx, channel_id)
            # Submissions are not authority: check ownership and live config every time.
            if action not in cfg["buttons"]:
                raise ValueError("هذا الزر أُزيل من إعدادات البانل.")
            actor = itx.guild.get_member(itx.user.id)
            if not actor:
                raise ValueError("العضو لم يعد في السيرفر.")
            admin = self.is_admin(actor, cfg)
            owner = room["owner_id"] == actor.id
            if not owner and not admin and action not in {"claim", "status", "report"}:
                raise ValueError("التحكم متاح لمالك الروم أو أدمن الرومات فقط.")
            if action == "claim" and not cfg["ownership_claim"]:
                raise ValueError("أخذ الملكية معطل.")
            if action == "color" and not cfg["owner_embed_color"] and not admin:
                raise ValueError("تغيير لون المالك معطل.")
            if action == "waiting" and not cfg["waiting_room"]:
                raise ValueError("غرفة الانتظار معطلة.")
            if action == "meeting" and not cfg["meeting_mode"]:
                raise ValueError("وضع الاجتماع معطل.")
            cid = channel.id
            if action in MEMBER_ACTIONS and value is None:
                await itx.response.send_message("اختر العضو لتنفيذ الإجراء.",
                    view=MemberPicker(self, cid, action, actor.id), ephemeral=True)
                return
            if action in {"rename", "limit", "color"} and value is None:
                default = {"rename": channel.name, "limit": channel.user_limit,
                           "color": room["state"].get("color", cfg["embed_color"])}[action]
                await itx.response.send_modal(ValueModal(self, cid, action, store.BUTTONS[action], default))
                return
            if action == "region" and value is None:
                regions = await self.bot.fetch_voice_regions()
                options = [discord.SelectOption(label="تلقائي", value="auto")]
                options += [discord.SelectOption(label=x.name[:100], value=x.id) for x in regions if not x.deprecated]
                await itx.response.send_message("اختر منطقة الصوت.", view=ChoicePicker(self, cid, action, options, actor.id), ephemeral=True)
                return
            if action == "delete" and value != "confirmed":
                await itx.response.send_message("سيتم حذف الروم وغرفة انتظاره وفصل الموجودين. تأكيد؟",
                    view=DeleteConfirmation(self, cid, actor.id), ephemeral=True)
                return
            await itx.response.defer(ephemeral=True)
            async with self.lock_for(itx.guild.id):
                # A second click/claim/transfer might have changed ownership while waiting.
                room, channel = self.resolve_room(itx, cid)
                cfg = await self.config(itx.guild.id)
                if action not in cfg["buttons"]:
                    raise ValueError("الزر لم يعد مفعّلًا.")
                if room["owner_id"] != actor.id and not self.is_admin(actor, cfg) and action not in {"claim", "status", "report"}:
                    raise ValueError("تغيّرت ملكية الروم؛ لا يمكنك تنفيذ الإجراء.")
                message = await self.perform(actor, channel, room, cfg, action, value)
            await itx.followup.send(message, ephemeral=True)
        except (ValueError, discord.HTTPException) as error:
            message = str(error) if isinstance(error, ValueError) else "رفض Discord الإجراء؛ راجع صلاحيات البوت وتسلسل الرولات."
            if itx.response.is_done():
                await itx.followup.send(message, ephemeral=True)
            else:
                await itx.response.send_message(message, ephemeral=True)

    async def persist_preferences(self, channel, room, cfg):
        if cfg["permanent_memory"]:
            state = room["state"]
            prefs = {"name": channel.name, "limit": channel.user_limit,
                     "trusted": state.get("trusted", []), "banned": state.get("banned", [])}
            await store.save_profile(room["guild_id"], room["owner_id"], prefs)

    async def set_owner(self, channel, room, cfg, target):
        old = channel.guild.get_member(room["owner_id"])
        if old:
            overwrite = channel.overwrites_for(old)
            overwrite.manage_channels = None
            await channel.set_permissions(old, overwrite=overwrite)
        overwrite = channel.overwrites_for(target)
        overwrite.view_channel = overwrite.connect = overwrite.send_messages = True
        overwrite.manage_channels = bool(cfg["owner_manage_channel"])
        await channel.set_permissions(target, overwrite=overwrite)
        await self.tick(room)
        room["owner_id"] = target.id

    async def reconcile_room_permissions(self, channel, room, cfg):
        guild = channel.guild
        old = self.configs.get(guild.id, {})
        for role_id in set(old.get("admin_role_ids", [])) - set(cfg["admin_role_ids"]):
            role = guild.get_role(int(role_id))
            if role and role in channel.overwrites:
                await channel.set_permissions(role, overwrite=None, reason="Temporary voice access updated")
        for role_id in cfg["admin_role_ids"]:
            role = guild.get_role(int(role_id))
            if role:
                await channel.set_permissions(role, view_channel=True, connect=True,
                                              reason="Temporary voice administrator access")
        owner = guild.get_member(room["owner_id"])
        if owner:
            overwrite = channel.overwrites_for(owner)
            overwrite.manage_channels = bool(cfg["owner_manage_channel"])
            await channel.set_permissions(owner, overwrite=overwrite)

    async def perform(self, actor, channel, room, cfg, action, value):
        guild, state = channel.guild, room["state"]
        if action == "rename":
            if not value or not value.strip() or len(value) > 100:
                raise ValueError("اسم الروم بين 1 و100 حرف.")
            await channel.edit(name=value.strip())
        elif action == "limit":
            if not value.isdigit() or not 0 <= int(value) <= 99:
                raise ValueError("حد الأعضاء بين 0 و99.")
            await channel.edit(user_limit=int(value))
        elif action in {"lock", "quick_lock", "unlock", "quick_unlock", "privacy", "emergency"}:
            overwrite = channel.overwrites_for(guild.default_role)
            locked = action in {"lock", "quick_lock", "emergency"} and not (
                action == "emergency" and state.get("emergency")
            )
            if action == "privacy":
                private = not (state.get("privacy", cfg["privacy"]) == "private")
                state["privacy"] = "private" if private else "public"
                overwrite.view_channel = overwrite.connect = not private
            else:
                if action in {"quick_unlock", "unlock"}:
                    overwrite.view_channel = state.get("privacy", cfg["privacy"]) == "public"
                elif action != "emergency" or not state.get("emergency"):
                    overwrite.view_channel = None
                overwrite.connect = (
                    (state.get("privacy", cfg["privacy"]) == "public" if action in {"quick_unlock", "unlock"} else True)
                    if action == "emergency" and state.get("emergency")
                    else (False if locked else (state.get("privacy", cfg["privacy"]) == "public" if action in {"quick_unlock", "unlock"} else None))
                )
            await channel.set_permissions(guild.default_role, overwrite=overwrite)
            if action == "emergency":
                state["emergency"] = not state.get("emergency", False)
        elif action in MEMBER_ACTIONS:
            target = guild.get_member(int(value)) if value and value.isdigit() else None
            if not target:
                raise ValueError("العضو غير موجود في السيرفر.")
            punitive = action in {"kick", "ban", "mute", "deafen"}
            if punitive:
                if target.id in {guild.owner_id, room["owner_id"], guild.me.id}:
                    raise ValueError("لا يمكن استهداف مالك السيرفر أو الروم أو البوت.")
                if cfg["admin_protection"] and self.is_admin(target, cfg):
                    raise ValueError("هذا العضو محمي بوضع الأدمن.")
                if target.top_role > actor.top_role and actor.id != guild.owner_id:
                    raise ValueError("لا يمكن استهداف عضو رتبته أعلى منك.")
                if target.top_role >= guild.me.top_role:
                    raise ValueError("رتبة العضو أعلى من رتبة البوت.")
            if action in {"kick", "mute", "deafen", "transfer"}:
                if not target.voice or target.voice.channel != channel:
                    raise ValueError("العضو يجب أن يكون داخل هذا الروم.")
            if action == "kick":
                await target.move_to(None, reason="Temporary room owner kick")
            elif action == "mute":
                await target.edit(mute=not target.voice.mute, reason="Temporary room control")
            elif action == "deafen":
                await target.edit(deafen=not target.voice.deaf, reason="Temporary room control")
            elif action == "transfer":
                if target.bot or target.id == actor.id:
                    raise ValueError("اختر عضوًا آخر وليس بوتًا.")
                await self.set_owner(channel, room, cfg, target)
            else:
                trusted, banned = state.setdefault("trusted", []), state.setdefault("banned", [])
                key = str(target.id)
                overwrite = channel.overwrites_for(target)
                if action in {"invite", "trust", "unban"}:
                    if key in banned:
                        banned.remove(key)
                    overwrite.view_channel = overwrite.connect = True
                    if action == "trust" and key not in trusted:
                        if len(trusted) >= 100:
                            raise ValueError("الحد الأقصى 100 عضو موثوق.")
                        trusted.append(key)
                elif action == "ban":
                    if key in trusted:
                        trusted.remove(key)
                    if key not in banned:
                        if len(banned) >= 100:
                            raise ValueError("الحد الأقصى 100 عضو محظور.")
                        banned.append(key)
                    overwrite.view_channel = overwrite.connect = False
                else:
                    if key in trusted:
                        trusted.remove(key)
                    overwrite.view_channel = overwrite.connect = None
                await channel.set_permissions(target, overwrite=overwrite)
                if action == "ban" and target.voice and target.voice.channel == channel:
                    await target.move_to(None, reason="Temporary room ban")
                if action == "invite":
                    waiting_id = state.get("waiting_channel_id")
                    if target.voice and waiting_id and str(target.voice.channel.id) == waiting_id:
                        await target.move_to(channel, reason="Waiting room admission")
                    else:
                        invite = await channel.create_invite(max_age=900, max_uses=1, unique=True)
                        try:
                            await target.send(f"دعوة إلى روم {actor.display_name}: {invite.url}")
                        except discord.Forbidden:
                            await store.update_room(room)
                            return f"تم السماح للعضو بالدخول، لكن رسائله الخاصة مغلقة. رابط الدعوة: {invite.url}"
        elif action == "status":
            return (f"الروم: {channel.name}\nالمالك: <@{room['owner_id']}>\n"
                    f"الأعضاء: {len(channel.members)} / {channel.user_limit or 'بلا حد'}\n"
                    f"الجودة: {channel.bitrate // 1000} kbps\n"
                    f"الموثوقون: {len(state.get('trusted', []))} — المحظورون: {len(state.get('banned', []))}\n"
                    f"مثبّت: {'نعم' if state.get('pinned') else 'لا'}")
        elif action == "claim":
            owner = guild.get_member(room["owner_id"])
            if owner and owner.voice and owner.voice.channel == channel:
                raise ValueError("المالك ما زال داخل الروم.")
            if not actor.voice or actor.voice.channel != channel:
                raise ValueError("يجب أن تكون داخل الروم لأخذ ملكيته.")
            await self.set_owner(channel, room, cfg, actor)
        elif action == "pin":
            state["pinned"] = not state.get("pinned", False)
        elif action == "age":
            await channel.edit(nsfw=not channel.nsfw)
        elif action == "slowmode":
            seconds = cfg["button_settings"].get("slowmode", {}).get("seconds", 10)
            await channel.edit(slowmode_delay=0 if channel.slowmode_delay else seconds)
        elif action == "activity":
            app_id = cfg["button_settings"].get("activity", {}).get("application_id") or "880218394199220334"
            invite = await channel.create_invite(
                target_type=discord.InviteTarget.embedded_application,
                target_application_id=int(app_id), max_age=900, unique=True)
            return f"بدء النشاط: {invite.url}"
        elif action == "region":
            regions = {r.id for r in await self.bot.fetch_voice_regions() if not r.deprecated}
            if value != "auto" and value not in regions:
                raise ValueError("منطقة صوت غير صالحة.")
            await channel.edit(rtc_region=None if value == "auto" else value)
        elif action == "color":
            if not store.HEX.fullmatch(value):
                raise ValueError("لون بصيغة #RRGGBB.")
            state["color"] = value
            if state.get("panel_message_id"):
                message = await channel.fetch_message(int(state["panel_message_id"]))
                await message.edit(embed=self.embed(cfg, state))
        elif action == "meeting":
            enabled = not state.get("meeting", False)
            overwrite = channel.overwrites_for(guild.default_role)
            overwrite.speak = False if enabled else None
            await channel.set_permissions(guild.default_role, overwrite=overwrite)
            overwrite = channel.overwrites_for(guild.get_member(room["owner_id"]))
            overwrite.speak = True
            await channel.set_permissions(guild.get_member(room["owner_id"]), overwrite=overwrite)
            state["meeting"] = enabled
        elif action == "waiting":
            waiting = guild.get_channel(int(state["waiting_channel_id"])) if state.get("waiting_channel_id") else None
            if waiting:
                await waiting.delete(reason="Waiting room disabled")
                state.pop("waiting_channel_id", None)
            else:
                overwrites = dict(channel.overwrites)
                overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=False)
                waiting = await guild.create_voice_channel(f"انتظار • {channel.name}"[:100], category=channel.category,
                                                           overwrites=overwrites, reason="Temporary waiting room")
                state["waiting_channel_id"] = str(waiting.id)
                overwrite = channel.overwrites_for(guild.default_role)
                overwrite.connect = False
                await channel.set_permissions(guild.default_role, overwrite=overwrite)
                state["privacy"] = "private"
        elif action == "delete":
            await self.delete_room(guild, channel.id)
            return "تم حذف الروم وغرفة انتظاره."
        elif action == "report":
            key = (guild.id, actor.id)
            if time.monotonic() - self.report_times.get(key, -1000) < 60:
                raise ValueError("انتظر دقيقة بين بلاغات الإدارة.")
            settings = cfg["button_settings"].get("report", {})
            if not settings.get("log_channel_id"):
                raise ValueError("يجب أن يختار المسؤول قناة البلاغات من إعدادات زر إبلاغ الإدارة.")
            self.report_times[key] = time.monotonic()
            if len(self.report_times) > 2048:
                self.report_times = {k: v for k, v in self.report_times.items() if time.monotonic() - v < 60}
        else:
            raise ValueError("إجراء غير معروف.")
        await store.update_room(room)
        await self.persist_preferences(channel, room, cfg)
        settings = cfg["button_settings"].get(action, {})
        if settings.get("log_channel_id"):
            destination = guild.get_channel(int(settings["log_channel_id"]))
            if destination:
                role_ids = settings.get("alert_role_ids", []) if action in {"report", "emergency"} else []
                roles = [r for x in role_ids if (r := guild.get_role(int(x)))]
                mentions = " ".join(role.mention for role in roles)
                await destination.send(f"{mentions}\n{store.BUTTONS[action]} • <#{channel.id}> • العضو <@{actor.id}>",
                    allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=roles))
        return "تم تنفيذ الإجراء: " + store.BUTTONS[action]


async def setup(bot):
    await bot.add_cog(TempVoice(bot))

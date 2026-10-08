"""Persistent Discord controls. Every submission is checked against live ownership."""
import logging

import discord

import temp_voice_store as store

log = logging.getLogger("TempVoicePanel")


class ValueModal(discord.ui.Modal):
    def __init__(self, cog, channel_id, action, label, default=""):
        super().__init__(title=label, timeout=180)
        self.cog, self.channel_id, self.action = cog, channel_id, action
        self.value = discord.ui.TextInput(label=label, default=str(default),
                                          max_length=100, required=True)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        await self.cog.dispatch(interaction, self.action, self.channel_id, str(self.value))


class MemberPicker(discord.ui.View):
    def __init__(self, cog, channel_id, action, actor_id):
        super().__init__(timeout=120)
        self.actor_id = actor_id
        selector = discord.ui.UserSelect(placeholder="اختر العضو", min_values=1, max_values=1)

        async def callback(itx):
            if itx.user.id != actor_id:
                await itx.response.send_message("هذه القائمة ليست لك.", ephemeral=True)
                return
            await cog.dispatch(itx, action, channel_id, str(selector.values[0].id))
        selector.callback = callback
        self.add_item(selector)


class ChoicePicker(discord.ui.View):
    def __init__(self, cog, channel_id, action, options, actor_id):
        super().__init__(timeout=120)
        selector = discord.ui.Select(placeholder="اختر الإعداد", options=options[:25])

        async def callback(itx):
            if itx.user.id != actor_id:
                await itx.response.send_message("هذه القائمة ليست لك.", ephemeral=True)
                return
            await cog.dispatch(itx, action, channel_id, selector.values[0])
        selector.callback = callback
        self.add_item(selector)


class DeleteConfirmation(discord.ui.View):
    def __init__(self, cog, channel_id, actor_id):
        super().__init__(timeout=60)
        button = discord.ui.Button(label="تأكيد حذف الروم", style=discord.ButtonStyle.danger)

        async def confirm(itx):
            if itx.user.id != actor_id:
                await itx.response.send_message("هذا التأكيد ليس لك.", ephemeral=True)
                return
            await cog.dispatch(itx, "delete", channel_id, "confirmed")
        button.callback = confirm
        self.add_item(button)


class RoomPanel(discord.ui.View):
    def __init__(self, cog, config):
        super().__init__(timeout=None)
        for index, action in enumerate(config["buttons"]):
            button = discord.ui.Button(
                label=store.BUTTONS[action], custom_id="prime:tv:" + action,
                row=index // 5,
                style=discord.ButtonStyle.danger if action in {"delete", "emergency"}
                else discord.ButtonStyle.primary if action in {"claim", "invite"}
                else discord.ButtonStyle.secondary,
            )

            async def callback(itx, key=action):
                await cog.dispatch(itx, key)
            button.callback = callback
            self.add_item(button)

    async def on_error(self, interaction, error, item):
        log.exception("Temporary room control failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("تعذر تنفيذ الإجراء. راجع صلاحيات البوت.", ephemeral=True)
        else:
            await interaction.response.send_message("تعذر تنفيذ الإجراء. راجع صلاحيات البوت.", ephemeral=True)


def render_template(template, member, count):
    return (template.replace("{OWNER_NAME}", member.display_name)
            .replace("{OWNER_MENTION}", member.mention).replace("{COUNT}", str(count)))

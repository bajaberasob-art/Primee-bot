import os
import unittest
from types import SimpleNamespace

import database
from cogs.community import (
    Community,
    TicketControlView,
    TicketPanelView,
    normalize_ticket_categories,
)


class FakeMessage:
    id = 800

    async def pin(self):
        return None


class FakeChannel:
    id = 300
    guild = SimpleNamespace(id=700)

    async def send(self, **kwargs):
        return FakeMessage()


class FakeBot:
    def __init__(self):
        self.channel = FakeChannel()
        self.views = []

    def get_channel(self, channel_id):
        return self.channel if channel_id == self.channel.id else None

    def add_view(self, view, **kwargs):
        self.views.append((view, kwargs))


class CommunityTicketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = "/tmp/test_community_tickets.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()

    async def test_persistent_views_have_stable_custom_ids(self):
        categories = normalize_ticket_categories([
            {"key": "billing", "label": "دعم الشحن", "emoji": "💳"},
        ])
        panel = TicketPanelView(categories)
        controls = TicketControlView()
        self.assertEqual(
            [item.custom_id for item in panel.children],
            ["ticket:category:billing"],
        )
        self.assertEqual(
            {item.custom_id for item in controls.children},
            {
                "ticket:claim",
                "ticket:escalate",
                "ticket:close",
                "ticket:waiting-user",
                "ticket:internal-note",
                "ticket:add-member",
                "ticket:remove-member",
                "ticket:unclaim",
                "ticket:transfer",
                "ticket:delete",
            },
        )

    async def test_custom_guild_emoji_token_survives_normalization(self):
        token = "<:support_badge:400000000000000001>"
        categories = normalize_ticket_categories([
            {"key": "support", "label": "الدعم", "emoji": token},
        ])
        self.assertEqual(categories[0]["emoji"], token)
        view = __import__("cogs.community", fromlist=["TicketSelectView"]).TicketSelectView(categories, 700)
        self.assertEqual(str(view.children[0].options[0].emoji), token)

    async def test_deploy_panel_and_staff_kpi_helpers_persist_data(self):
        bot = FakeBot()
        cog = Community.__new__(Community)
        cog.bot = bot
        panel = await cog.deploy_ticket_panel(
            300,
            [{"key": "questions", "label": "استفسارات", "support_role_ids": ["9"]}],
        )
        self.assertEqual((panel["guild_id"], panel["message_id"]), ("700", "800"))
        self.assertEqual(len(bot.views), 1)

        ticket = await database.create_ticket(
            700, 301, 55, "questions", "استفسارات", "Help", "Need help", ["9"]
        )
        await database.claim_ticket(700, ticket["id"], 99)
        released = await database.unclaim_ticket(700, ticket["id"], 99)
        self.assertEqual((released["claimed_by"], released["status"]), (None, "waiting_staff"))
        await database.claim_ticket(700, ticket["id"], 99)
        await database.record_ticket_response(700, ticket["id"])
        await database.close_ticket(700, ticket["id"], 99, "Solved")
        await database.save_ticket_rating(ticket["id"], 700, 55, 5)
        kpis = await cog.get_staff_kpis(700)
        self.assertEqual(kpis[0]["staff_id"], 99)
        self.assertEqual(kpis[0]["avg_rating"], 5.0)

        await database.save_ticket_transcript(
            ticket["id"], 700, 301, "Billing question", "<p>Billing question</p>"
        )
        transcripts = await cog.get_ticket_transcripts(700, "Billing")
        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0]["ticket_id"], ticket["id"])


if __name__ == "__main__":
    unittest.main()
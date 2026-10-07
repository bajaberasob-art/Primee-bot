"""Dashboard-only authorization and leveling handoff tests for subscriptions."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import web_server as ws


class SubscriptionDashboardApiTests(unittest.IsolatedAsyncioTestCase):
    def make_request(self, body, bot):
        return SimpleNamespace(app={"bot": bot}, match_info={})

    async def call_grant(self, member, *, fetch_error=None):
        if fetch_error:
            async def fetch_member(_user_id):
                raise fetch_error
        else:
            async def fetch_member(_user_id):
                return member

        guild = SimpleNamespace(
            id=123456789012345678,
            get_member=lambda _user_id: member,
            fetch_member=fetch_member,
        )
        bot = SimpleNamespace()
        request = self.make_request({}, bot)
        body = {
            "user_id": "234567890123456789",
            "idempotency_key": "test:subscription-grant",
        }
        result = {
            "status": "created",
            "subscription": {"subscription_id": "sub-test"},
            "xp": {"amount": 42},
        }
        with (
            patch.object(ws, "authorize", new=AsyncMock(return_value=({"id": "10"}, guild))),
            patch.object(ws, "read_json_body", new=AsyncMock(return_value=body)),
            patch.object(
                ws.subscription_service,
                "create_subscription",
                new=AsyncMock(return_value=result),
            ) as create,
            patch.object(ws, "_process_subscription_xp", new=AsyncMock()) as process_xp,
        ):
            response = await ws.api_subscription_grant(request)
        return response, create, process_xp, bot, guild, result

    async def test_grant_refuses_accounts_that_are_not_server_members(self):
        response, create, _process_xp, _bot, _guild, _result = await self.call_grant(
            None,
            fetch_error=discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                "member not found",
            ),
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.text)["error"], "validation")
        create.assert_not_awaited()

    async def test_grant_refuses_bot_members(self):
        response, create, _process_xp, _bot, _guild, _result = await self.call_grant(
            SimpleNamespace(bot=True)
        )
        self.assertEqual(response.status, 400)
        self.assertIn("bot accounts", json.loads(response.text)["message"])
        create.assert_not_awaited()

    async def test_valid_grant_passes_the_live_bot_to_existing_level_events(self):
        member = SimpleNamespace(bot=False)
        response, create, process_xp, bot, guild, result = await self.call_grant(member)
        self.assertEqual(response.status, 200)
        create.assert_awaited_once_with(
            guild.id,
            234567890123456789,
            None,
            idempotency_key="test:subscription-grant",
            actor_id=10,
            plan_id=None,
        )
        process_xp.assert_awaited_once_with(bot, guild, result)


if __name__ == "__main__":
    unittest.main()
import unittest
from types import SimpleNamespace

from cogs.utilities import command_management_tier
from management_access import (
    member_has_management_tier,
    member_management_tier,
    required_tier_for_permission,
)


GUILD = SimpleNamespace(owner_id=100)
ROLE_MAP = {
    "admin": "300000000000000003",
    "moderator": "300000000000000002",
    "staff": "300000000000000001",
}


def member(user_id, role_ids=(), administrator=False):
    return SimpleNamespace(
        id=user_id,
        roles=[SimpleNamespace(id=role_id) for role_id in role_ids],
        guild_permissions=SimpleNamespace(administrator=administrator),
    )


class ManagementAccessTests(unittest.TestCase):
    def test_mapped_roles_resolve_to_the_highest_configured_tier(self):
        moderator = member(200, [ROLE_MAP["staff"], ROLE_MAP["moderator"]])
        self.assertEqual(
            member_management_tier(moderator, GUILD, {"management_role_ids": ROLE_MAP}),
            "moderator",
        )
        self.assertTrue(
            member_has_management_tier(
                moderator, GUILD, {"management_role_ids": ROLE_MAP}, "staff"
            )
        )
        self.assertFalse(
            member_has_management_tier(
                moderator, GUILD, {"management_role_ids": ROLE_MAP}, "admin"
            )
        )

    def test_owner_and_discord_administrator_are_platform_privileged(self):
        settings = {"management_role_ids": ROLE_MAP}
        self.assertTrue(
            member_has_management_tier(member(100), GUILD, settings, "admin")
        )
        self.assertTrue(
            member_has_management_tier(
                member(200, administrator=True), GUILD, settings, "admin"
            )
        )

    def test_empty_map_preserves_existing_policy_behavior(self):
        self.assertTrue(
            member_has_management_tier(
                member(200), GUILD, {"management_role_ids": {}}, "moderator"
            )
        )

    def test_native_permissions_still_determine_the_minimum_prime_tier(self):
        self.assertEqual(required_tier_for_permission("manage_roles"), "admin")
        self.assertEqual(required_tier_for_permission("ban_members"), "moderator")
        self.assertEqual(required_tier_for_permission("manage_messages"), "staff")
        self.assertIsNone(required_tier_for_permission("send_messages"))

    def test_slash_and_prefix_management_commands_share_tier_classification(self):
        self.assertEqual(
            command_management_tier(
                SimpleNamespace(name="ban", qualified_name="ban")
            ),
            "moderator",
        )
        self.assertEqual(
            command_management_tier(
                SimpleNamespace(name="lockdown", qualified_name="lockdown")
            ),
            "admin",
        )


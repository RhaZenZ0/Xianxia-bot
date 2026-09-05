"""The Bugslayer Sword (v0.19.32): a one-of-a-kind, indestructible GM reward.

The Go engine owns the mechanics (durability immunity, the Heavenly Flawfinder
passive in 1v1 and boss combat - see go_core/internal/game/bugslayer_reward_test.go).
These tests cover the Python half: the catalog entry, the /admin player grant
guards that keep it unique, and the player-facing text that must not lie about
what the engine does. discord.py is not importable in the test environment, so
the bot-facing checks read the source, as the rest of this suite does.
"""

import json
import unittest

from tests.support import PROJECT_ROOT, bot_function_source, bot_package_source

WORLD_JSON = PROJECT_ROOT / "content" / "world.json"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        from app.rules.advanced_runtime import EQUIPMENT_DEFINITIONS

        self.definition = EQUIPMENT_DEFINITIONS["bugslayer_sword"]
        self.world = json.loads(WORLD_JSON.read_text(encoding="utf-8"))

    def test_it_is_a_unique_indestructible_weapon_with_a_named_passive(self):
        self.assertEqual(self.definition["slot"], "weapon")
        self.assertTrue(self.definition["unique"])
        self.assertTrue(self.definition["indestructible"])
        self.assertEqual(self.definition["passive_name"], "Heavenly Flawfinder")
        self.assertTrue(str(self.definition["passive_description"]).strip())

    def test_no_other_item_is_unique_or_indestructible(self):
        from app.rules.advanced_runtime import EQUIPMENT_DEFINITIONS

        flagged = {k for k, d in EQUIPMENT_DEFINITIONS.items() if d.get("unique") or d.get("indestructible")}
        self.assertEqual(flagged, {"bugslayer_sword"})

    def test_world_catalog_entry_exists_and_is_market_excluded(self):
        item = self.world["items"]["bugslayer_sword"]
        self.assertEqual(item["name"], "Bugslayer Sword")
        self.assertTrue(item.get("market_excluded"))

    def test_nothing_else_in_the_world_produces_it(self):
        # It is granted by a GM and nothing else: no recipe, market stock,
        # drop table, quest reward or shop may mention it.
        others = {k: v for k, v in self.world.items() if k != "items"}
        self.assertNotIn("bugslayer_sword", json.dumps(others))


class AdminGrantGuardTests(unittest.TestCase):
    def setUp(self):
        self.grant = bot_function_source("admin_grant")

    def test_grant_dispatches_items_through_the_engine_adjust_item_action(self):
        self.assertIn('"admin.player.adjust_item"', self.grant)
        self.assertIn('"admin.player.grant_currency"', self.grant)

    def test_unique_items_can_only_be_granted_one_at_a_time(self):
        # Anchored to the line start so the branch cannot be quietly disabled
        # (`if False and ...`) while the substring still matches.
        self.assertRegex(self.grant, r'(?m)^\s*if definition\.get\("unique"\):\s*$')
        self.assertRegex(self.grant, r"(?m)^\s*if int\(amount\) != 1:\s*$")

    def test_unique_items_check_both_inventory_and_bound_equipment(self):
        # A bound sword leaves the inventory table, so checking only
        # get_inventory would let a second copy through.
        self.assertIn("DB.get_inventory(member.id)", self.grant)
        self.assertIn("DB.get_equipment(member.id)", self.grant)
        self.assertIn("already_owned", self.grant)

    def test_grant_writes_the_discord_side_audit_row(self):
        self.assertIn('"player.grant"', self.grant)
        self.assertIn("database_log=False", self.grant)

    def test_grant_announces_the_passive(self):
        self.assertIn('get("passive_name")', self.grant)
        self.assertIn("Passive:", self.grant)

    def test_grant_is_never_ephemeral(self):
        self.assertNotIn("ephemeral=True", self.grant)


class PlayerFacingTextTests(unittest.TestCase):
    def test_equipment_status_shows_indestructible_instead_of_a_durability_fraction(self):
        status = bot_function_source("equipment_status")
        self.assertIn('definition.get("indestructible")', status)
        self.assertIn("**indestructible**", status)
        self.assertIn('definition.get("passive_name")', status)

    def test_battle_narration_reports_the_passive_and_does_not_misattribute_it(self):
        main = bot_package_source()
        self.assertIn('result.get("bugslayer_passive")', main)
        self.assertIn("bugslayer_bonus_damage", main)
        # The passive suppresses the counter through the same mechanism as
        # spatial techniques; the "spatial control" line must defer to it.
        self.assertIn(
            'result.get("counter_suppressed") and not result.get("bugslayer_passive")',
            main,
        )


if __name__ == "__main__":
    unittest.main()

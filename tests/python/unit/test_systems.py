from tests.support import PROJECT_ROOT
import json
import unittest
from pathlib import Path

from app.rules.effects import aggregate_modifiers, normalize_effect_payload
from app.rules.game import World
from app.rules.worldtime import from_game_minutes

ROOT = PROJECT_ROOT


class ExpandedSystemsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(ROOT / "content" / "world.json")
        cls.raw = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))

    def test_world_time_periods(self):
        self.assertEqual(from_game_minutes(8 * 60).period, "Morning")
        self.assertEqual(from_game_minutes(18 * 60).period, "Evening")
        self.assertEqual(from_game_minutes(23 * 60).period, "Night")

    def test_effect_aggregation(self):
        payload = normalize_effect_payload({
            "name": "Test",
            "modifiers": [
                {"stat": "will", "operation": "add", "value": 2},
                {"stat": "cultivation_gain", "operation": "mul", "value": 1.2},
            ],
        })
        mods = aggregate_modifiers([{**payload, "stacks": 1}])
        self.assertEqual(mods["will"], 2)
        self.assertAlmostEqual(mods["cultivation_gain_mult"], 1.2)

    def test_auction_house_is_protected_and_has_door(self):
        house = self.raw["auction_houses"]["golden_pavilion"]
        location = self.raw["locations"][house["location"]]
        self.assertTrue(location["safe_zone"])
        self.assertTrue(house["door_rule"])
        self.assertEqual(house["entrance_location"], "Greenriver Town")

    def test_world_rulers_resolve_to_valid_npcs_and_locations(self):
        self.assertEqual(set(self.raw["world_rulers"]), {
            "Mortal World", "Spiritual World", "Immortal World", "Celestial World"
        })
        for _, npc_name in self.raw["world_rulers"].items():
            self.assertIn(npc_name, self.raw["npcs"])
            self.assertIn(self.raw["npcs"][npc_name]["location"], self.raw["locations"])

    def test_sect_rank_ladder_is_rigid_and_ordered(self):
        ranks = self.raw["sect_system"]["ranks"]
        self.assertEqual([r["name"] for r in ranks[:3]], ["Outer Disciple", "Inner Disciple", "Core Disciple"])
        self.assertEqual([r["level"] for r in ranks], sorted(r["level"] for r in ranks))
        self.assertEqual(ranks[-1]["name"], "Ancestor")

    def test_currency_catalog_covers_four_worlds(self):
        worlds = {v["world"] for v in self.raw["currencies"].values()}
        self.assertEqual(worlds, {"Mortal World", "Spiritual World", "Immortal World", "Celestial World"})
        self.assertIn("low_spirit_stone", self.raw["currencies"])

    def test_npc_schedules_only_use_known_locations(self):
        for npc_name, npc in self.raw["npcs"].items():
            for period, location in npc.get("schedule", {}).items():
                self.assertIn(period, {"Dawn", "Morning", "Afternoon", "Evening", "Night"}, npc_name)
                self.assertIn(location, self.raw["locations"], npc_name)

    def test_storage_treasures_have_capacity(self):
        for item_id in ("spatial_pouch", "spatial_ring", "living_world_ring"):
            upgrade = self.raw["items"][item_id]["storage_upgrade"]
            self.assertGreater(upgrade["slot_capacity"], 0)

    def test_useable_pills_have_effect_or_instant_behavior(self):
        for item_id in ("recovery_pill", "qi_replenishment_pill", "qi_pill", "heart_calming_pill", "purging_phoenix_pill"):
            use = self.raw["items"][item_id].get("use")
            self.assertIsNotNone(use, item_id)
            self.assertTrue(use.get("instant") or use.get("effect"), item_id)

if __name__ == "__main__":
    unittest.main()

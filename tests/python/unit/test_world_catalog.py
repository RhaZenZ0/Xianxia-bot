"""The shipped world content and the rules that read it.

Merged from:

test_world_catalog_materialised.py — The catalog on disk is the catalog (v0.21.3).

`augment_advanced_catalog` used to run only in Python's memory, so the Go
engine - which reads `content/world.json` raw - had 6 manuals where Python
had 148, refused the other 142 in `manual.study`, and no righteous manual was
obtainable at all. `scripts/materialize_world_catalog.py` writes the expansion
into the file; these tests fail the moment the file drifts from it, so the
two sides can never disagree again.


test_systems.py — (no module docstring)

test_game.py — (no module docstring)
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

from tests.support import PROJECT_ROOT

from app.rules.advanced_catalog import augment_advanced_catalog
from app.rules.effects import aggregate_modifiers, normalize_effect_payload
from app.rules.game import World
from app.rules.worldtime import from_game_minutes


# --- from test_world_catalog_materialised.py ---

WORLD = PROJECT_ROOT / "content" / "world.json"
SCRIPT = PROJECT_ROOT / "scripts" / "materialize_world_catalog.py"


class MaterialisedCatalogTests(unittest.TestCase):
    def test_disk_equals_expansion(self):
        raw = WORLD.read_text(encoding="utf-8")
        data = json.loads(raw)
        augment_advanced_catalog(data)
        self.assertEqual(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", raw,
            "content/world.json is not materialised: run scripts/materialize_world_catalog.py",
        )

    def test_the_check_script_agrees(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_documented_scale_is_on_disk(self):
        data = json.loads(WORLD.read_text(encoding="utf-8"))
        system = data["technique_system"]
        # 148 generated on the six-path cycle, one authored entry manual per
        # public sect (twelve since v0.39.0), 23 for the seventh path the cycle
        # never reached and 13 household traditions (both v1.0.3).
        self.assertEqual(len(system["manuals"]), 197)  # +1 the Stygian Ghost Scripture (v1.3.4)
        self.assertEqual(len(system["techniques"]), 681)
        demonic = [m for m in system["manuals"].values() if str(m.get("alignment", "")).casefold() == "demonic"]
        self.assertEqual(len(demonic), 53)  # +6 for the Ghost Cultivator (v1.0.3), +1 the Stygian Ghost Scripture (v1.3.4)

    def test_every_manual_has_an_item_and_none_is_market_stock(self):
        data = json.loads(WORLD.read_text(encoding="utf-8"))
        items = data["items"]
        for manual_id, manual in data["technique_system"]["manuals"].items():
            with self.subTest(manual=manual_id):
                item = items[manual["item_id"]]
                self.assertEqual(item.get("type"), "manual")
                self.assertTrue(item.get("market_excluded"), "an inheritance is given or found, never town-market stock")

    def test_every_public_sect_has_exactly_one_tier_zero_entry_manual(self):
        """v0.21.4: "every sect has a genuine tier-0 entry manual". Authored,
        named, aligned with the sect, path-agnostic, studyable the day you join."""
        data = json.loads(WORLD.read_text(encoding="utf-8"))
        manuals = data["technique_system"]["manuals"]
        techniques = data["technique_system"]["techniques"]
        items = data["items"]
        for sect, definition in data["sects"].items():
            entries = {mid: m for mid, m in manuals.items() if m.get("sect") == sect}
            with self.subTest(sect=sect):
                if definition.get("hidden"):
                    self.assertEqual(entries, {}, "a hidden sect has no public entry manual")
                    continue
                self.assertEqual(len(entries), 1, f"{sect} needs exactly one entry manual, has {sorted(entries)}")
                (mid, manual), = entries.items()
                self.assertEqual(manual["min_realm_index"], 0)
                self.assertEqual(manual["alignment"], definition["alignment"])
                self.assertEqual(manual["path"], "Any")
                self.assertGreaterEqual(len(manual["techniques"]), 3)
                for tid in manual["techniques"]:
                    self.assertEqual(techniques[tid]["manual"], mid)
                    self.assertIn(techniques[tid]["min_mastery"], (0, 1, 2))
                item = items[manual["item_id"]]
                self.assertTrue(item.get("market_excluded"))
                self.assertEqual(item.get("legal_status"), "forbidden" if manual["alignment"] == "Demonic" else "clean")
                self.assertNotIn("generated_advanced_catalog", manual, "authored, not generated")

    def test_the_hidden_sect_is_on_disk_and_flagged(self):
        data = json.loads(WORLD.read_text(encoding="utf-8"))
        sect = data["sects"]["Heaven-Devouring Demon Sect"]
        self.assertTrue(sect.get("hidden"))
        public = [name for name, s in data["sects"].items() if not s.get("hidden")]
        self.assertEqual(len(public), 12)  # two per higher world since v0.39.0


# --- from test_systems.py ---

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

    def test_auction_house_is_protected_and_has_a_door_onto_the_street(self):
        """v1.0.6 retired `door_rule` - all 48 set it true, no house's prose can
        differ, and the engine already ends the protection at the door by
        creating the incident battle at `entrance_location`. The claim that
        replaces it is the pair a sanctuary actually needs, held for all 48 in
        `tests/python/contracts/test_violence_is_suppressed_where_content_says.py`."""
        house = self.raw["auction_houses"]["golden_pavilion"]
        location = self.raw["locations"][house["location"]]
        self.assertTrue(location["safe_zone"])
        self.assertTrue(house["protected_interior"])
        self.assertNotIn("door_rule", house)
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


# --- from test_game.py ---

WORLD_GAME = World(PROJECT_ROOT / "content" / "world.json")


class FixedD10Source:
    def __init__(self, *values):
        self.values = iter(values)

    def d10(self):
        return next(self.values)


class GameRulesTests(unittest.TestCase):
    def test_gendered_titles_are_cosmetic(self):
        self.assertEqual(WORLD_GAME.realm_name(25, "male"), "Celestial Duke")
        self.assertEqual(WORLD_GAME.realm_name(25, "female"), "Celestial Duchess")
        self.assertEqual(WORLD_GAME.body_realm_name(25, "male"), "Celestial Body Duke")
        self.assertEqual(WORLD_GAME.body_realm_name(25, "female"), "Celestial Body Duchess")


if __name__ == "__main__":
    unittest.main()

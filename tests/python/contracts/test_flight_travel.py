"""v1.0.0-rc.15: travel scales with cultivation, and the artifacts that fake it.

In this genre distance is a question of realm before it is a question of
roads. Until now it was neither: realm shaved at most a third off a walk,
so an Ascension Realm ancestor and a mortal porter crossed the same valley
at the same speed, and the flying sword the setting is built on was a word
in a description. Now a cultivator below Core Formation walks unless they
carry something that flies, Core Formation and above fly, and the top of
the mortal world folds the distance instead of crossing it.

Go owns which of the three it is and what it costs; this side asserts the
Python boundary - the artifacts are real, obtainable goods, and the reply
says how the journey was made.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

EXPLORATION = (PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
FLIGHT = {key: item for key, item in WORLD["items"].items() if item.get("flight")}


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineOwnsHowTheGroundIsCrossed(unittest.TestCase):
    def test_the_three_modes_and_the_artifact_lookup_live_in_go(self):
        exploration = (GO / "game" / "exploration_actions.go").read_text(encoding="utf-8")
        for needle in (
            "travelFlightRealm = 3",
            "travelFoldRealm   = 7",
            "func travelModeFor(effectiveRealm int64) travelMode {",
            "func bestFlightArtifact(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (int64, string, error) {",
            "func canonicalRoadTravelProfileRiding(",
            "func canonicalRoadRouteRiding(",
            '"travel_mode":                   travelModeName,',
            '"travel_mount":                  travelMount,',
        ):
            self.assertIn(needle, exploration, needle)
        self.assertIn('Flight         int64          `json:"flight"`', (GO / "worlddata" / "catalog.go").read_text(encoding="utf-8"))

    def test_python_neither_decides_nor_recomputes_the_mode(self):
        # The words come off the engine's own answer; nothing here maps a
        # realm index to a speed.
        line = _body(EXPLORATION, "_travel_mode_line")
        self.assertIn('result.get("travel_mode")', line)
        self.assertIn('result.get("travel_mount")', line)
        for forbidden in ("realm_index", "/ 3", "// 3"):
            self.assertNotIn(forbidden, line, forbidden)


class TheReplySaysHowTheyWent(unittest.TestCase):
    def test_the_travel_reply_carries_the_mode_line(self):
        self.assertIn("road=_travel_mode_line(result)+(", EXPLORATION)
        line = _body(EXPLORATION, "_travel_mode_line")
        self.assertIn("folding space", line)
        self.assertIn("You ride **{mount}**", line)
        # The walking line is the pointer to a flying artifact, and it is
        # shown to exactly the cultivators who cannot yet leave the ground.
        # It names Here, not Browse (v1.1.0): the road ends on a street, and
        # Browse is drawn only inside a shop.
        self.assertIn("/economy → City Shops → Here", line)


class TheSwordIsStillASword(unittest.TestCase):
    """The genre's reason the flying sword is the default: it carries you
    there and is still a weapon when you arrive. It is the one flight
    artifact that also takes an equipment slot, in all three stat tables."""

    def test_the_flying_sword_carries_weapon_stats_everywhere(self):
        from app.rules.advanced_runtime import EQUIPMENT_DEFINITIONS

        sword = EQUIPMENT_DEFINITIONS.get("azure_flying_sword")
        self.assertIsNotNone(sword, "the flying sword is not equipment")
        self.assertEqual(sword["slot"], "weapon")
        self.assertGreater(int(sword["attack"]), 0, "a sword that cannot cut")
        # The parity contract holds the three tables to each other; this only
        # asserts the flying sword is in the Go pair at all.
        group = (GO / "game" / "group_combat_actions.go").read_text(encoding="utf-8")
        self.assertIn('"azure_flying_sword": {"weapon", 220, 7, 0, 3, 3, false},', group)
        self.assertIn('"azure_flying_sword": {7, 0, 3, 3}', (GO / "game" / "combat_actions.go").read_text(encoding="utf-8"))

    def test_binding_the_sword_does_not_ground_it(self):
        # `equipment.bind` removes the item from `inventory`, so the mount
        # lookup has to read the equipment table too or the sword stops
        # flying the moment its owner makes it theirs.
        exploration = (GO / "game" / "exploration_actions.go").read_text(encoding="utf-8")
        self.assertIn("UNION SELECT item_id FROM equipment_instances WHERE user_id=? AND durability>0", exploration)


class TheArtifactsAreRealGoods(unittest.TestCase):
    def test_every_flying_artifact_is_a_described_priced_item(self):
        self.assertGreaterEqual(len(FLIGHT), 6, "the setting needs more than one way off the ground")
        for key, item in FLIGHT.items():
            with self.subTest(item=key):
                self.assertEqual(item.get("type"), "flight")
                self.assertGreater(len(str(item.get("description") or "").strip()), 30, "an artifact nobody can picture")
                self.assertGreater(int(item.get("base_price") or 0), 0)
                self.assertGreater(int(item.get("sect_value") or 0), 0)
                self.assertGreaterEqual(int(item["flight"]), 3, "a flying artifact must at least fly")
                self.assertTrue(str(item.get("flight_name") or "").strip(), "the reply has nothing to name")

    def test_every_flying_artifact_is_either_bought_or_inherited(self):
        # An artifact nobody can obtain is scenery. There are exactly two ways
        # to come by one: a shop sells it, or a birth family sends you out
        # with it. An heirloom is deliberately unbuyable - market_excluded,
        # on no shelf - so the rule is a disjunction, not a shelf check.
        stocked = {}
        for key, shop in WORLD["shops"].items():
            for line in shop["sells"]:
                if line["item_id"] in FLIGHT:
                    stocked.setdefault(line["item_id"], []).append(shop)
        inherited = {str(entry["item"]) for entry in WORLD["birth_family_sendoff"].values()}
        for key in FLIGHT:
            with self.subTest(item=key):
                self.assertTrue(key in stocked or key in inherited, f"{key} can be neither bought nor inherited")
                if key in inherited:
                    self.assertTrue(FLIGHT[key].get("market_excluded"), f"{key} is an heirloom and must not be stock")
                    self.assertNotIn(key, stocked, f"{key} is an heirloom and is on a shelf")
        # The cheapest *stocked* one still has to be reachable in the world
        # players start in, so the walking hint is never a dead end.
        entry = min(stocked, key=lambda k: int(FLIGHT[k]["base_price"]))
        self.assertTrue(
            any(shop["world"] == "Mortal World" for shop in stocked[entry]),
            "the first flying artifact must be reachable from the world players start in",
        )

    def test_the_keeper_buys_back_what_the_keeper_sells(self):
        for key, shop in WORLD["shops"].items():
            for line in shop["sells"]:
                if line["item_id"] not in FLIGHT:
                    continue
                with self.subTest(shop=key, item=line["item_id"]):
                    self.assertIn(line["item_id"], shop["buys"], "sold but not bought back")
                    self.assertLess(int(shop["buys"][line["item_id"]]), int(line["price"]), "a keeper does not pay retail")


if __name__ == "__main__":
    unittest.main()

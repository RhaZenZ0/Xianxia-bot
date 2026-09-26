"""Every inn's common room is somewhere its city's cultivators can read (v1.7.2).

Reported as "no access to common room channel", with the inn card reading
"Common room: #unknown". `_inn_thread` hung every inn's thread in its world's
capital channel, which only the capital's presence role can see - so the one
place it worked was the capital, and only while its presence role survived the
South Gate (it did not; `presence_world_for` matched the name exactly). Forty-
four of the forty-eight inns stand in a city that is not a capital, and every
one of them linked a thread its own patrons could not open.

A capital's inn keeps the capital channel, whose presence role now follows the
whole city; every other inn hangs in its world's own feed (`event_scene_parent`),
gated by the access role everybody in that world holds.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT, code_only

from app.rules.realm_hubs import REALM_HUBS, city_of_place, realm_hub_by_location

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LOCATIONS = CONTENT["locations"]
EXPLORATION = PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py"


def _inn_thread_source() -> str:
    tree = ast.parse(EXPLORATION.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_inn_thread":
            return code_only(ast.get_source_segment(EXPLORATION.read_text(encoding="utf-8"), node) or "")
    return ""


class EveryInnHasACommonRoomYouCanSee(unittest.TestCase):
    def setUp(self):
        self.inns = sorted(n for n, d in LOCATIONS.items() if d.get("district") == "inn")
        self.assertGreater(len(self.inns), len(REALM_HUBS), "the walk found no inns; the reader is broken, not the tree")

    def test_every_inns_city_is_placed_in_a_world(self):
        """A non-capital inn hangs in its world's feed, so its city must name a world."""
        for inn in self.inns:
            city = city_of_place(inn, LOCATIONS)
            self.assertNotEqual(city, inn, f"{inn} resolved to itself; it is not part of a city")
            world = str((LOCATIONS.get(city) or {}).get("world") or "")
            self.assertIn(world, REALM_HUBS, f"{inn} stands in {city}, which names no world")

    def test_only_a_capitals_inn_is_anchored_in_a_capital(self):
        capitals = {str(hub["location"]) for hub in REALM_HUBS.values()}
        in_capital = [inn for inn in self.inns if realm_hub_by_location(city_of_place(inn, LOCATIONS), LOCATIONS)]
        self.assertEqual(sorted(in_capital), sorted(i for i in self.inns if LOCATIONS[i]["outside_location"] in capitals))
        self.assertEqual(len(in_capital), len(REALM_HUBS), "each capital keeps one inn")

    def test_the_thread_is_anchored_by_capital_or_by_the_worlds_feed(self):
        source = _inn_thread_source()
        self.assertTrue(source, "could not read _inn_thread; the gate is broken, not the tree")
        self.assertIn("realm_hub_by_location(city, WORLD.locations)", source,
                      "the capital channel is only for a capital's own inn")
        self.assertIn("event_scene_parent(guild, city)", source,
                      "every other inn hangs where its world's cultivators can read it")
        self.assertNotIn('WORLD.locations.get(city, {}).get("world")', source,
                         "keying the capital channel on the world sends every inn in that world to it")


if __name__ == "__main__":
    unittest.main()

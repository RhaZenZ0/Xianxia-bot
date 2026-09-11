"""v0.36.0: city gates and districts.

Go owns the walk (arrival at the facing gate, the parts of a city a step
apart, the door rules); this side asserts the Python boundary: the picker
offers a city's parts, the travel reply names the gate, /city look is
there, and the shops price a capital higher.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
EXPLORATION = (BOT / "commands" / "exploration.py").read_text(encoding="utf-8")
LOCATIONS = (BOT / "locations.py").read_text(encoding="utf-8")
SURFACE = (BOT / "surface.py").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineOwnsTheWalk(unittest.TestCase):
    def test_arrival_is_at_the_facing_gate_and_the_parts_are_known(self):
        exploration = (GO / "game" / "exploration_actions.go").read_text(encoding="utf-8")
        for needle in ("func cityOf(", "func cityPartsOf(", "func gateFacing(", 'gateFacing(catalog, p.Destination, route[len(route)-2])', '"arrived_at":', '"left_by_gate":', '"city_parts":', "canonicalRoadRoute(catalog, originCity, p.Destination, c.RealmIndex)"):
            self.assertIn(needle, exploration, needle)
        # Shops, halls and merchants are reached from any part of the city.
        self.assertIn("cityShopKeys(catalog, cityOf(catalog, c.Location))", (GO / "game" / "shop_actions.go").read_text(encoding="utf-8"))
        self.assertIn('here := cityOf(catalog, fmt.Sprint(c["location"]))', (GO / "game" / "economy_actions.go").read_text(encoding="utf-8"))
        self.assertIn('location = cityOf(catalog, fmt.Sprint(row["location"]))', (GO / "game" / "merchant_actions.go").read_text(encoding="utf-8"))


class TheSurface(unittest.TestCase):
    def test_the_picker_offers_the_parts_of_the_city_you_are_in(self):
        known = _body(LOCATIONS, "_known_locations")
        self.assertIn('data.get("district") and str(data.get("outside_location")) == city', known)

    def test_the_travel_reply_names_the_gate(self):
        self.assertIn('arrived_at=str(result.get("arrived_at") or "")', EXPLORATION)
        self.assertIn("You arrive at the **{arrived_at}**", EXPLORATION)
        self.assertIn("You leave by the **{result.get('left_by_gate')} Gate**", EXPLORATION)
        self.assertIn("{desc}{gate_line}{envoy_line}{shop_line}", EXPLORATION)

    def test_city_look_is_under_the_world_hub(self):
        self.assertIn('city_group = app_commands.Group(name="city"', EXPLORATION)
        self.assertIn('@registered_group_command(city_group, name="look"', EXPLORATION)
        self.assertIn('_hub_page("city", "City"', SURFACE)
        self.assertIn('"city": city_group,', SURFACE)


class TheCapitalsChargeMore(unittest.TestCase):
    def test_a_capital_shop_is_a_tier_up_and_dearer_than_its_world(self):
        shops = WORLD["shops"]
        locations = WORLD["locations"]
        for key, shop in shops.items():
            if not locations[shop["city"]].get("realm_hub"):
                continue
            # Same kind, same world, ordinary city: every shared item costs less there.
            for other_key, other in shops.items():
                if other["kind"] != shop["kind"] or other["world"] != shop["world"] or locations[other["city"]].get("realm_hub"):
                    continue
                theirs = {line["item_id"]: line["price"] for line in other["sells"]}
                for line in shop["sells"]:
                    if line["item_id"] in theirs:
                        with self.subTest(capital=key, city=other_key, item=line["item_id"]):
                            self.assertGreater(line["price"], theirs[line["item_id"]])
                self.assertGreater(shop["tier"], other["tier"])
                break

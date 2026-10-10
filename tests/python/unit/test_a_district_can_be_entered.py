"""A district can be entered from the city page (v1.12.1).

Asked from play as *"Enter the District can we do that?"*. City → Look listed
a city's gates and districts and said to find them in **/travel**'s list; there
was nothing to press. On the owner's call there are two doors now:
`/world → City → Enter` (a picker, also `/city enter`) and a button per place
under City → Look. Both walk through the registry's binding of `/travel go`,
so neither decides anything: the engine's travel is the whole of the rule, and
the places offered are the ones `door_allows` says it will open.
"""

from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

import pytest

from tests.support import PROJECT_ROOT as ROOT

pytestmark = pytest.mark.unit

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
EXPLORATION = ROOT / "app" / "bot" / "commands" / "exploration.py"
SURFACE = ROOT / "app" / "bot" / "surface.py"


def _function(name: str) -> ast.AST:
    tree = ast.parse(EXPLORATION.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found; the reader is broken, not the tree")


class ThePlacesAreTheOnesTravelOpens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, ENV):
            cls.ex = importlib.import_module("app.bot.commands.exploration")
            cls.loc = importlib.import_module("app.bot.locations")
        cls.world = cls.ex.WORLD.locations

    def _names(self, here):
        return [name for name, _emoji, _what in self.ex._places_to_enter(here)]

    def test_a_capital_offers_its_districts_and_gates(self):
        city = "Azure Crown Imperial City"
        parts = self.ex._city_parts(city)
        self.assertGreater(len(parts), 3, "the reader found no parts of a capital; the test is broken")
        self.assertEqual(sorted(self._names(city)), sorted(parts), "from the centre: every part, not the centre")

    def test_a_district_offers_the_streets_back(self):
        city = "Azure Crown Imperial City"
        district = next(p for p in self.ex._city_parts(city) if not self.world[p].get("gate"))
        names = self._names(district)
        self.assertEqual(names[0], city, "the streets come first")
        self.assertNotIn(district, names, "the place you stand in is not offered")

    def test_every_offer_anywhere_is_one_the_engine_opens(self):
        offered = 0
        for here in self.world:
            for name in self._names(here):
                offered += 1
                self.assertTrue(self.loc.door_allows(here, name), f"{here} offers {name}, which travel refuses")
                self.assertEqual(self.ex._city_of(name), self.ex._city_of(here), f"{here} offers {name} in another city")
                self.assertNotEqual(name, here)
        self.assertGreater(offered, 100, "the sweep offered almost nothing; the reader is broken, not the tree")

    def test_a_shop_offers_only_its_street(self):
        shop = next(n for n, d in self.world.items() if d.get("shop") and not d.get("road_site"))
        self.assertEqual(self._names(shop), [str(self.world[shop]["outside_location"])])

    def test_a_road_site_and_a_private_room_offer_nothing(self):
        site = next(n for n, d in self.world.items() if d.get("road_site"))
        self.assertEqual(self._names(site), [])
        self.assertEqual(self._names("birth_family:tomb_watch_clan"), [])


class BothDoorsWalkThroughTravel(unittest.TestCase):
    def test_enter_is_the_travel_handler(self):
        body = ast.unparse(_function("city_enter"))
        self.assertIn("ACTIONS.handler_for(travel)(interaction, place)", body)

    def test_the_look_buttons_are_the_travel_handler(self):
        tree = ast.parse(EXPLORATION.read_text(encoding="utf-8"))
        button = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "CityEnterButton")
        self.assertIn("ACTIONS.handler_for(travel)(interaction, self.place)", ast.unparse(button))

    def test_look_sends_the_buttons_when_there_is_somewhere_to_go(self):
        body = ast.unparse(_function("city_look"))
        # The places for *this* spot (the first argument), and what the player
        # knows of the city - not the spelling of the call (v1.0.8).
        offers = [c for c in ast.walk(_function("city_look"))
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "_places_to_enter"]
        self.assertEqual(len(offers), 1, "City → Look no longer asks _places_to_enter")
        self.assertEqual(ast.unparse(offers[0].args[0]), "here")
        self.assertIn("CityLookView(", body, "City → Look lost its buttons")

    def test_enter_is_hidden_in_a_private_room(self):
        tree = ast.parse(SURFACE.read_text(encoding="utf-8"))
        gates = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                     and ast.unparse(n.target) == "LOCATION_GATES")
        self.assertIn("'city enter'", ast.unparse(gates.value))


if __name__ == "__main__":
    unittest.main()

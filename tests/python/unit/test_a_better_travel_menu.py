"""A better travel menu (v1.26.0).

One picker held every known place in one list of 25, and every road site
anywhere sorted above every city - so a player who had walked the roads could
not pick most cities at all. Measured before the change: from Azure Crown
Imperial City, with the first world known, the top 25 were the city's own parts
and thirteen road sites, and all four cities one road away fell off the end.

Held here:

- **The kinds partition the list.** `destinations_of_kind` is
  `destination_groups` cut four ways, so no place is lost and none is offered
  twice; a city's list is nearest first and holds every known city of the
  capital's world within 25.
- **A private part stays a sponsor's.** The street does not reveal a sect's
  hidden gate (the engine's `knownLocationsTx` skips it), and a known part of
  a city is a known city - two twins this side lacked.
- **A road trip is previewed, through the engine's own planner**, and the
  kind pickers go through the registry's binding of `/travel go`, so the lock,
  the meter and the preview are that one command's.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
EXPLORATION = PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py"


def _locations():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.locations")


def _first_world_places(loc) -> set[str]:
    return {name for name, data in loc.WORLD.locations.items()
            if data.get("world") == "Mortal World" and int(data.get("min_realm_index") or 0) == 0}


class TheKindsPartitionTheList(unittest.TestCase):
    def test_every_row_is_one_kind_and_none_is_lost(self):
        loc = _locations()
        known = _first_world_places(loc)
        for here in ("Azure Crown Imperial City", "Greenriver Town", "Riverguard City"):
            rows = loc.destination_groups(here, known, 0)
            self.assertTrue(rows, f"nothing is offered from {here}; the reader is broken, not the tree")
            split = [row for kind, _label in loc.DESTINATION_KINDS for row in loc.destinations_of_kind(here, known, 0, kind)]
            self.assertEqual(sorted(split), sorted(rows), f"the kinds lost or doubled a place from {here}")

    def test_a_capital_sees_every_city_of_its_world_nearest_first(self):
        loc = _locations()
        known = _first_world_places(loc)
        cities = loc.destinations_of_kind("Azure Crown Imperial City", known, 0, "city")
        names = [row[0] for row in cities]
        for neighbour in ("Ashenwall City", "Greenriver Town", "Jadewood Medicine City", "Riverguard City"):
            self.assertIn(neighbour, names, f"{neighbour}, one road from the capital, is not on the city list")
        self.assertLessEqual(len(cities), 25, "the capital's world has more known cities than one picker shows")
        self.assertEqual([row[3] for row in cities], sorted(row[3] for row in cities), "the city list is not nearest first")
        self.assertFalse([row for row in cities if row[1] == "🛤️"], "a road site is on the city list")

    def test_a_road_site_is_never_on_the_city_list(self):
        loc = _locations()
        known = _first_world_places(loc)
        sites = loc.destinations_of_kind("Greenriver Town", known, 0, "road")
        self.assertTrue(sites, "no road site is known in the first world; the test is vacuous")
        self.assertTrue(all(row[1] == "🛤️" for row in sites))


class _Db:
    def __init__(self, discovered=()):
        self._discovered = list(discovered)

    async def get_discovered_locations(self, _uid):
        return [{"location": name} for name in self._discovered]


class APrivatePartStaysASponsors(unittest.TestCase):
    def _known(self, loc, location, discovered=()):
        with patch.object(loc, "DB", _Db(discovered)):
            return asyncio.run(loc._known_locations(7, {"location": location, "realm_index": 0, "phase": 1}))

    def test_the_street_does_not_reveal_a_hidden_gate(self):
        loc = _locations()
        private = [(name, str(data["outside_location"])) for name, data in loc.WORLD.locations.items()
                   if data.get("private") and data.get("district")]
        self.assertTrue(private, "the content has no private district; the test is vacuous")
        for gate, city in private:
            self.assertNotIn(gate, self._known(loc, city), f"standing in {city} put the hidden {gate} on the map")

    def test_a_known_part_of_a_city_is_a_known_city(self):
        loc = _locations()
        gate, city = next((name, str(data["outside_location"])) for name, data in loc.WORLD.locations.items()
                          if data.get("private") and data.get("district"))
        self.assertIn(city, self._known(loc, "Greenriver Town", discovered=[gate]),
                      f"a sponsor revealed {gate} and the city it stands in, {city}, is not on the map")


def _functions(path) -> dict[str, ast.AST]:
    return {node.name: node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


class TheTripIsShownFirst(unittest.TestCase):
    def test_go_asks_the_preview_before_it_travels(self):
        body = ast.unparse(_functions(EXPLORATION)["travel"])
        self.assertIn("exploration.travel_preview", body)
        self.assertLess(body.index("exploration.travel_preview"), body.index("_travel_now"),
                        "the journey is taken before its preview is asked")

    def test_every_kind_picker_is_the_one_travel_command(self):
        functions = _functions(EXPLORATION)
        for name in ("travel_city", "travel_nearby", "travel_road", "travel_wilds"):
            body = ast.unparse(functions[name])
            self.assertIn("ACTIONS.handler_for(travel)", body, f"{name} travels by a road of its own")
            self.assertNotIn("authoritative_action", body)

    def test_the_reply_names_the_toll_and_the_road(self):
        body = ast.unparse(_functions(EXPLORATION)["_travel_now"])
        for key in ("travel_cost_spirit_stones", "travel_cost_currency", "road_route"):
            self.assertIn(key, body, f"the travel reply does not read {key}")

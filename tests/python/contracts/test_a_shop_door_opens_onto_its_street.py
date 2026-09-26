"""The travel picker never offers a place the engine refuses at the door (v1.7.1).

Reported twice from live play, the second time as *"Travel failed: the shop
door opens onto Cloudblade City"* from inside Cloudblade Talisman Hall.
v1.0.13 had fixed the first report by moving the street up the list, and never
stopped the picker offering the rest: from inside a shop the engine allows the
street and the city's other shops, and the picker listed every gate, district,
road site and city the player knew. The same shape reached another city's gates,
districts and shops from anywhere, and the inside of an auction hall.

`door_allows` in `app/bot/locations.py` is the picker's twin of the four door
checks at the top of `explorationTravelAction`. This file computes them a third
time off the raw content file, so two wrong halves cannot agree with each other
and pass (v1.0.9), and holds the twin to it over every pair of locations in the
catalogue. It also holds that the Go checks it is a twin of are still there.
"""
from __future__ import annotations

import importlib
import json
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT as ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
CONTENT = ROOT / "content" / "world.json"
TRAVEL = ROOT / "go_core" / "internal" / "game" / "exploration_actions.go"


def _load() -> dict:
    return json.loads(CONTENT.read_text(encoding="utf-8"))["locations"]


def city_of(locations: dict, name: str) -> str:
    loc = locations.get(name) or {}
    if loc.get("outside_location") and (loc.get("district") or loc.get("shop") or loc.get("auction_house")):
        return str(loc["outside_location"])
    return name


def engine_door(locations: dict, current: str, destination: str) -> bool:
    """`explorationTravelAction`'s door checks, restated from the Go."""
    cur = locations.get(current) or {}
    dest = locations.get(destination) or {}
    if dest.get("auction_house"):
        return False
    if cur.get("auction_house"):
        return destination == (cur.get("outside_location") or "Greenriver Town")
    origin = city_of(locations, current)
    if dest.get("shop") and not dest.get("road_site"):
        return origin == dest.get("outside_location")
    if cur.get("shop") and not cur.get("road_site"):
        return destination == cur.get("outside_location")
    if dest.get("district"):
        return origin == dest.get("outside_location")
    return True


class AShopDoorOpensOntoItsStreet(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with patch.dict(os.environ, ENV):
            cls.picker = importlib.import_module("app.bot.locations")
        cls.locations = _load()

    def test_the_go_checks_this_twins_are_still_there(self):
        source = TRAVEL.read_text(encoding="utf-8")
        for refusal in (
            "auction houses must be entered through their warded doors",
            "warded auction exit leads first to",
            "the shop door opens onto",
            "travel there first",
        ):
            self.assertIn(refusal, source, f"the engine no longer says {refusal!r}; restate the twin")

    def test_the_reader_found_the_shop_that_was_reported(self):
        hall = self.locations.get("Cloudblade Talisman Hall") or {}
        self.assertTrue(hall.get("shop") and hall.get("outside_location") == "Cloudblade City",
                        "the reported shop is not in the content file; the reader is broken, not the tree")

    def test_the_picker_twin_agrees_with_the_engine_over_every_pair(self):
        names = sorted(self.locations)
        wrong = [
            (a, b) for a in names for b in names
            if a != b and self.picker.door_allows(a, b) != engine_door(self.locations, a, b)
        ]
        self.assertEqual(wrong[:10], [], f"{len(wrong)} pairs disagree with the engine's door rules")

    def test_from_every_shop_the_picker_offers_only_the_street_and_the_citys_shops(self):
        names = sorted(self.locations)
        shops = [n for n in names if self.locations[n].get("shop") and not self.locations[n].get("road_site")]
        self.assertGreater(len(shops), 100, "the reader found too few shops; it is broken, not the tree")
        offenders = {}
        for shop in shops:
            rows = self.picker.destination_groups(shop, names, 99)
            street = self.locations[shop]["outside_location"]
            offered = [name for name, *_ in rows]
            refused = [n for n in offered if not engine_door(self.locations, shop, n)]
            if refused or not offered or offered[0] != street:
                offenders[shop] = (offered[:3], refused[:3])
        self.assertEqual(dict(list(offenders.items())[:5]), {}, f"{len(offenders)} shops offer a refused door or bury the street")

    def test_from_anywhere_the_picker_offers_nothing_refused_at_the_door(self):
        names = sorted(self.locations)
        offenders = []
        for here in names:
            for name, *_ in self.picker.destination_groups(here, names, 99):
                if not engine_door(self.locations, here, name):
                    offenders.append((here, name))
        self.assertEqual(offenders[:10], [], f"{len(offenders)} offered rows the engine refuses at the door")

    def test_the_slash_command_and_the_panel_read_the_same_rows(self):
        """`/travel go` used the shared `location_autocomplete`, every known
        place in alphabetical order, so the slash command offered what the
        panel had stopped offering. Both read `destination_groups` now."""
        import ast

        source = (ROOT / "app" / "bot" / "commands" / "exploration.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        travel = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "travel")
        autocompletes = [
            kw.value.id for d in travel.decorator_list if isinstance(d, ast.Call)
            for kw in d.keywords if kw.arg == "destination" and isinstance(kw.value, ast.Name)
        ]
        self.assertEqual(autocompletes, ["travel_destination_autocomplete"])
        picker = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "travel_destination_autocomplete")
        calls = {c.func.id for c in ast.walk(picker) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        self.assertIn("destination_groups", calls)


if __name__ == "__main__":
    unittest.main()

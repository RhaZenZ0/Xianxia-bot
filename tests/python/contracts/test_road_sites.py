"""v0.39.0: the roads between the cities.

A place on every road - a waystation with a stall, a hunting ground, a
ruin, a shrine - found by walking the road or exploring from either end,
reached as half a leg from either end, leading nowhere but back to them.
Go owns discovery, the hop, the hunt's edge and the merchants' road; this
side asserts the Python boundary.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
EXPLORATION = (BOT / "commands" / "exploration.py").read_text(encoding="utf-8")
LOCATIONS = (BOT / "locations.py").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal"


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineOwnsTheRoad(unittest.TestCase):
    def test_a_site_is_found_walking_the_road_and_reached_as_half_a_leg(self):
        sites = (GO / "game" / "road_site_actions.go").read_text(encoding="utf-8")
        for needle in ("func roadSiteEndpoints(", "func roadSitesOnLeg(", "func roadSiteHop(", "func discoverRoadSitesTx(", "func roadSiteCandidates(", "func roadFacingNeighbour(", "profile.TravelMinutes = maxI64(1, profile.TravelMinutes/2)", "if roll >= 50 {"):
            self.assertIn(needle, sites, needle)
        exploration = (GO / "game" / "exploration_actions.go").read_text(encoding="utf-8")
        for needle in ('"road_sites_found":', '"site_kind":', '"site_leg":', "roadSiteHop(catalog, originCity, p.Destination, c.RealmIndex)", "roadFacingNeighbour(catalog, p.Destination, route[len(route)-2])", "no beast is hunted on a shrine's ground", "siteBonus = huntingGroundRollBonus", "roadSiteCandidates(catalog, known, world, c.RealmIndex)", '"discovered_site":'):
            self.assertIn(needle, exploration, needle)
        self.assertIn('RoadSite string   `json:"road_site"`', (GO / "worlddata" / "catalog.go").read_text(encoding="utf-8"))
        # At a site the player stands on its road, and meets whoever walks it.
        self.assertIn('if a, b, ok := roadSiteEndpoints(catalog, location); ok {\n\t\treturn "", a, b, nil', (GO / "game" / "merchant_actions.go").read_text(encoding="utf-8"))


class ThePythonBoundary(unittest.TestCase):
    def test_the_travel_reply_tells_what_the_site_offers_and_what_was_found(self):
        self.assertIn("{meeting}{site_line}{sites_found}", EXPLORATION)
        line = _body(EXPLORATION, "_road_site_line")
        for hint in ("**/economy → City Shops → Browse**", "**/world → Hunt**", "**/world → Explore**", "The road leads back to"):
            self.assertIn(hint, line)
        travel = _body(EXPLORATION, "travel")
        self.assertIn('result.get("road_sites_found")', travel)
        self.assertIn("On the way you find", travel)

    def test_explore_and_hunt_show_the_sites_edge(self):
        explore = _body(EXPLORATION, "explore")
        self.assertIn('outcome.get("discovered_site")', explore)
        self.assertIn("The ruin gives up twice", explore)
        hunt = _body(EXPLORATION, "hunt")
        self.assertIn('site_bonus', hunt)

    def test_look_describes_a_site_and_the_picker_knows_both_ends(self):
        self.assertIn('if here_data.get("road_site"):', _body(EXPLORATION, "city_look"))
        known = _body(LOCATIONS, "_known_locations")
        self.assertIn('current_data.get("road_leg")', known)
        self.assertIn("known.update(leg)", known)

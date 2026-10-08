"""The city: its districts, shops, roads and inns.

Merged from:

test_city_districts.py — v0.36.0: city gates and districts.

Go owns the walk (arrival at the facing gate, the parts of a city a step
apart, the door rules); this side asserts the Python boundary: the picker
offers a city's parts, the travel reply names the gate, /city look is
there, and the shops price a capital higher.

test_city_shops.py — v0.35.0: city shops.

Go owns the shops (finding one, the door, the shelf, the refill); this side
asserts the Python boundary: the slash group is there and guided, explore
and travel replies tell the player what they found and where the door
goes, the Quest Forge leaves shopfronts off its lists, and the schema
carries the shelf.

test_road_sites.py — v0.39.0: the roads between the cities.

A place on every road - a waystation with a stall, a hunting ground, a
ruin, a shrine - found by walking the road or exploring from either end,
reached as half a leg from either end, leading nowhere but back to them.
Go owns discovery, the hop, the hunt's edge and the merchants' road; this
side asserts the Python boundary.

test_every_inn_has_a_common_room_you_can_see.py — Every inn's common room is somewhere its city's cultivators can read (v1.7.2).

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

from tests.support import code_only, PROJECT_ROOT

from app.rules.realm_hubs import city_of_place, realm_hub_by_location, REALM_HUBS


# --- from test_city_districts.py ---

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
        # Since v1.26.0 the route is planned in planTravelTx and the ends are
        # travelEnds (travel_preview.go), shared by the journey and its preview.
        exploration = "\n".join((GO / "game" / name).read_text(encoding="utf-8") for name in ("exploration_actions.go", "travel_preview.go"))
        for needle in ("func cityOf(", "func cityPartsOf(", "func gateFacing(", 'gateFacing(catalog, destination, roadFacingNeighbour(catalog, destination, route[len(route)-2]))', "travelEnds(catalog, p.Destination, originCity, route)", '"arrived_at":', '"left_by_gate":', '"city_parts":', "canonicalRoadRouteRiding(catalog, originCity, destination, c.accessRealmIndex(), riding, mount)", "planTravelTx(conn, catalog, userID, p.Destination, p.Mode)"):
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


# --- from test_city_shops.py ---

BOT_SHOPS = PROJECT_ROOT / "app" / "bot"
ECONOMY = (BOT_SHOPS / "commands" / "economy.py").read_text(encoding="utf-8")
EXPLORATION_SHOPS = (BOT_SHOPS / "commands" / "exploration.py").read_text(encoding="utf-8")
SURFACE_SHOPS = (BOT_SHOPS / "surface.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
FORGE = (PROJECT_ROOT / "app" / "ai" / "quest_forge.py").read_text(encoding="utf-8")
GO_SHOPS = PROJECT_ROOT / "go_core" / "internal"


def _body_shops(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineOwnsTheShops(unittest.TestCase):
    def test_the_operations_are_registered_and_explore_and_travel_know_shops(self):
        authoritative = (GO_SHOPS / "game" / "authoritative.go").read_text(encoding="utf-8")
        for op in ('"shop.buy":                        true,', '"shop.sell":                       true,', '"shop.here":                 true,', '"shop.browse":               true,', 'case "shop.here", "shop.browse":'):
            self.assertIn(op, authoritative, op)
        registry = (GO_SHOPS / "game" / "late_migration_registry.go").read_text(encoding="utf-8")
        self.assertIn('case "shop.buy":', registry)
        self.assertIn('case "shop.sell":', registry)
        exploration = (GO_SHOPS / "game" / "exploration_actions.go").read_text(encoding="utf-8")
        self.assertIn("discoverCityShopTx(conn, catalog, userID, c, p.GameMinute, now)", exploration)
        self.assertIn('"discovered_shop": discoveredShop', exploration)
        self.assertIn("travel there first", exploration)
        self.assertIn("the shop door opens onto", exploration)

    def test_the_schema_carries_the_shelf(self):
        migration = CORE.split('"city_shops"')[1].split("),")[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS shop_state", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS shop_stock", migration)
        for source in (ECONOMY, EXPLORATION_SHOPS, CORE):
            self.assertNotIn("INSERT INTO shop_", source)
            self.assertNotIn("UPDATE shop_", source)


class TheSlashSurface(unittest.TestCase):
    def test_the_group_has_four_leaves_and_guided_items(self):
        self.assertIn('shop_group=app_commands.Group(name="shop"', ECONOMY)
        for leaf in ("here", "browse", "buy", "sell"):
            self.assertIn(f'@registered_group_command(shop_group, name="{leaf}"', ECONOMY)
        self.assertIn('@shop_buy.autocomplete("item")', ECONOMY)
        self.assertIn('@shop_sell.autocomplete("item")', ECONOMY)
        self.assertIn("@serialized_user_action\nasync def shop_buy(", ECONOMY)
        self.assertIn("@serialized_user_action\nasync def shop_sell(", ECONOMY)
        self.assertIn('ENGINE.authoritative_action("shop.buy"', _body_shops(ECONOMY, "shop_buy"))
        self.assertIn('ENGINE.authoritative_action("shop.sell"', _body_shops(ECONOMY, "shop_sell"))
        self.assertIn('ENGINE.action("shop.here"', _body_shops(ECONOMY, "shop_here"))
        self.assertIn('ENGINE.action("shop.browse"', _body_shops(ECONOMY, "_shop_browse"))
        # Selling offers only what the keeper wants and the player carries.
        sell_picker = _body_shops(ECONOMY, "shop_sell_item_autocomplete")
        self.assertIn('shop.get("buys")', sell_picker)
        self.assertIn("DB.get_inventory(interaction.user.id)", sell_picker)

    def test_the_economy_hub_has_the_page(self):
        self.assertIn('_hub_page("shop", "City Shops"', SURFACE_SHOPS)
        self.assertIn('"shop": shop_group,', SURFACE_SHOPS)
        self.assertIn('"merchant", "shop", "trade", "blackmarket",', SURFACE_SHOPS)


class FindingAndEntering(unittest.TestCase):
    def test_the_explore_reply_names_the_shop_found(self):
        self.assertIn('outcome.get("discovered_shop")', EXPLORATION_SHOPS)
        self.assertIn("You find {discovered_shop.get('name')}", EXPLORATION_SHOPS)
        self.assertIn("/economy → City Shops → Browse", EXPLORATION_SHOPS)

    def test_the_travel_reply_says_you_stepped_inside(self):
        self.assertIn('.get("shop") or "")', EXPLORATION_SHOPS)
        self.assertIn("looks up from the counter", EXPLORATION_SHOPS)
        self.assertIn("{desc}{gate_line}{envoy_line}{shop_line}{road}", EXPLORATION_SHOPS)

    def test_the_forge_leaves_shopfronts_off_its_lists(self):
        self.assertIn('loc.get("auction_house") or loc.get("shop") or loc.get("district")', FORGE)


# --- from test_road_sites.py ---

BOT_SITES = PROJECT_ROOT / "app" / "bot"
EXPLORATION_SITES = (BOT_SITES / "commands" / "exploration.py").read_text(encoding="utf-8")
LOCATIONS_SITES = (BOT_SITES / "locations.py").read_text(encoding="utf-8")
GO_SITES = PROJECT_ROOT / "go_core" / "internal"


def _body_sites(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineOwnsTheRoad(unittest.TestCase):
    def test_a_site_is_found_walking_the_road_and_reached_as_half_a_leg(self):
        sites = (GO_SITES / "game" / "road_site_actions.go").read_text(encoding="utf-8")
        for needle in ("func roadSiteEndpoints(", "func roadSitesOnLeg(", "func roadSiteHop(", "func discoverRoadSitesTx(", "func roadSiteCandidates(", "func roadFacingNeighbour(", "profile.TravelMinutes = maxI64(1, profile.TravelMinutes/2)", "if roll >= 50 {"):
            self.assertIn(needle, sites, needle)
        # The planner (v1.26.0, `planTravelTx`) and the arrival gates
        # (`travelEnds`, travel_preview.go) are shared by the journey and its
        # preview, so the road is read across both files.
        exploration = (GO_SITES / "game" / "exploration_actions.go").read_text(encoding="utf-8") + (GO_SITES / "game" / "travel_preview.go").read_text(encoding="utf-8")
        for needle in ('"road_sites_found":', '"site_kind":', '"site_leg":', "roadSiteHop(catalog, originCity, destination, c.accessRealmIndex())", "roadFacingNeighbour(catalog, destination, route[len(route)-2])", "no beast is hunted on a shrine's ground", "siteBonus = huntingGroundRollBonus", "roadSiteCandidates(catalog, known, world, realmIndex)", "discoveryCandidates(catalog, known, currentWorld(c, catalog), c.accessRealmIndex())", '"discovered_site":'):
            self.assertIn(needle, exploration, needle)
        self.assertIn('RoadSite string   `json:"road_site"`', (GO_SITES / "worlddata" / "catalog.go").read_text(encoding="utf-8"))
        # At a site the player stands on its road, and meets whoever walks it.
        self.assertIn('if a, b, ok := roadSiteEndpoints(catalog, location); ok {\n\t\treturn "", a, b, nil', (GO_SITES / "game" / "merchant_actions.go").read_text(encoding="utf-8"))


class ThePythonBoundary(unittest.TestCase):
    def test_the_travel_reply_tells_what_the_site_offers_and_what_was_found(self):
        self.assertIn("{meeting}{site_line}{sites_found}", EXPLORATION_SITES)
        line = _body_sites(EXPLORATION_SITES, "_road_site_line")
        for hint in ("**/economy → City Shops → Browse**", "**/world → Act → Hunt**",
                     "**/world → Act → Explore**", "The road leads back to"):
            self.assertIn(hint, line)
        travel = _body_sites(EXPLORATION_SITES, "_travel_now")
        self.assertIn('result.get("road_sites_found")', travel)
        self.assertIn("On the way you find", travel)

    def test_explore_and_hunt_show_the_sites_edge(self):
        explore = _body_sites(EXPLORATION_SITES, "explore")
        self.assertIn('outcome.get("discovered_site")', explore)
        self.assertIn("The ruin gives up twice", explore)
        hunt = _body_sites(EXPLORATION_SITES, "hunt")
        self.assertIn('site_bonus', hunt)

    def test_look_describes_a_site_and_the_picker_knows_both_ends(self):
        self.assertIn('if here_data.get("road_site"):', _body_sites(EXPLORATION_SITES, "city_look"))
        known = _body_sites(LOCATIONS_SITES, "_known_locations")
        self.assertIn('current_data.get("road_leg")', known)
        self.assertIn("known.update(leg)", known)


# --- from test_every_inn_has_a_common_room_you_can_see.py ---

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LOCATIONS_INNS = CONTENT["locations"]
EXPLORATION_INNS = PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py"


def _inn_thread_source() -> str:
    tree = ast.parse(EXPLORATION_INNS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_inn_thread":
            return code_only(ast.get_source_segment(EXPLORATION_INNS.read_text(encoding="utf-8"), node) or "")
    return ""


class EveryInnHasACommonRoomYouCanSee(unittest.TestCase):
    def setUp(self):
        self.inns = sorted(n for n, d in LOCATIONS_INNS.items() if d.get("district") == "inn")
        self.assertGreater(len(self.inns), len(REALM_HUBS), "the walk found no inns; the reader is broken, not the tree")

    def test_every_inns_city_is_placed_in_a_world(self):
        """A non-capital inn hangs in its world's feed, so its city must name a world."""
        for inn in self.inns:
            city = city_of_place(inn, LOCATIONS_INNS)
            self.assertNotEqual(city, inn, f"{inn} resolved to itself; it is not part of a city")
            world = str((LOCATIONS_INNS.get(city) or {}).get("world") or "")
            self.assertIn(world, REALM_HUBS, f"{inn} stands in {city}, which names no world")

    def test_only_a_capitals_inn_is_anchored_in_a_capital(self):
        capitals = {str(hub["location"]) for hub in REALM_HUBS.values()}
        in_capital = [inn for inn in self.inns if realm_hub_by_location(city_of_place(inn, LOCATIONS_INNS), LOCATIONS_INNS)]
        self.assertEqual(sorted(in_capital), sorted(i for i in self.inns if LOCATIONS_INNS[i]["outside_location"] in capitals))
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

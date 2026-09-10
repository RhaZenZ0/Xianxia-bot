"""The content the narrator and /explore read is complete (v0.21.5).

The 1.0 roadmap's v0.25 gate, landed early: every location has sense
hints, every location /explore can be used in has encounters (the engine
hard-errors on an empty list - `exploration_actions.go`: "current location
has no exploration encounters"), and every NPC the narrator is handed has
the fields it reads - personality, speech, want, fear, secret. Before this
release six locations hard-errored, seven had no hints, and the four world
rulers shared one copy-pasted speech/want/fear.
"""
from __future__ import annotations

import json
import unittest

from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
NARRATOR_NPC_FIELDS = ("personality", "speech", "want", "fear", "secret")


class LocationContentTests(unittest.TestCase):
    def test_every_location_has_sense_hints(self):
        for name, loc in WORLD["locations"].items():
            with self.subTest(location=name):
                hints = loc.get("sense_hints") or []
                self.assertGreaterEqual(len(hints), 1, f"{name} has no sense_hints")
                for hint in hints:
                    self.assertGreater(len(hint.strip()), 20)

    def test_every_location_has_encounters(self):
        for name, loc in WORLD["locations"].items():
            with self.subTest(location=name):
                encounters = loc.get("encounters") or []
                self.assertGreaterEqual(len(encounters), 3, f"/explore hard-errors at {name}: no encounters")
                self.assertEqual(len(set(encounters)), len(encounters), "duplicate encounter")
                for text in encounters:
                    self.assertGreater(len(text.strip()), 20)

    def test_every_location_has_at_least_one_npc(self):
        # A location the narrator can be asked about needs someone to voice
        # it. The three samsara arrival grounds were the last without one.
        homes = {npc["location"] for npc in WORLD["npcs"].values()}
        for name in WORLD["locations"]:
            with self.subTest(location=name):
                self.assertIn(name, homes, f"{name} has no NPC")

    def test_safe_zone_encounters_do_not_start_violence(self):
        # A protected interior forbids violence (system prompt rule 15); its
        # encounters must be things that happen around you, not attacks on you.
        attack_words = ("attacks you", "ambushes you", "lunges at you", "strikes you")
        for name, loc in WORLD["locations"].items():
            if not loc.get("safe_zone"):
                continue
            for text in loc.get("encounters") or []:
                with self.subTest(location=name, encounter=text[:40]):
                    self.assertFalse(any(w in text.lower() for w in attack_words), text)


class AuctionHouseContentTests(unittest.TestCase):
    def test_every_city_has_an_auction_house_and_capitals_have_grand_ones(self):
        # v0.33.1: a house per city. A capital's is grand, with a live channel
        # of its own; an ordinary city's is local - smaller, and its world's
        # local floors share one channel, so a world is one channel, not twelve.
        from app.rules.realm_hubs import REALM_HUBS

        houses = WORLD["auction_houses"]
        by_entrance = {str(h.get("entrance_location")): h for h in houses.values()}
        for name, loc in WORLD["locations"].items():
            if loc.get("auction_house") or loc.get("private"):
                continue
            if "City" in name or "Town" in name or loc.get("realm_hub"):
                with self.subTest(city=name):
                    self.assertIn(name, by_entrance, f"{name} has no auction house")
        for world, hub in REALM_HUBS.items():
            with self.subTest(world=world):
                self.assertEqual(by_entrance[hub["location"]].get("size"), "grand")
        grand = [h for h in houses.values() if h.get("size") == "grand"]
        local = [h for h in houses.values() if h.get("size") == "local"]
        self.assertEqual(len(grand) + len(local), len(houses), "every house is grand or local")
        grand_channels = [h["channel_name"] for h in grand]
        self.assertEqual(len(grand_channels), len(set(grand_channels)), "grand houses have channels of their own")
        local_channels = {WORLD["locations"][h["location"]]["world"]: set() for h in local}
        for h in local:
            local_channels[WORLD["locations"][h["location"]]["world"]].add(h["channel_name"])
        for world, names in local_channels.items():
            with self.subTest(world=world):
                self.assertEqual(len(names), 1, f"the local floors of {world} share one channel")
        self.assertTrue(set(grand_channels).isdisjoint(set().union(*local_channels.values())))
        for key, house in houses.items():
            with self.subTest(house=key):
                interior = WORLD["locations"][house["location"]]
                self.assertTrue(interior.get("safe_zone"), "an auction floor is protected")
                self.assertEqual(interior.get("auction_house"), key)
                self.assertEqual(interior.get("outside_location"), house["entrance_location"])
                self.assertIn(house["default_currency"], WORLD["currencies"])
                self.assertTrue(house.get("channel_name"))
                if house["size"] == "local":
                    self.assertLess(int(house["max_active_lots"]), 10, "a local floor is small")
                    self.assertLessEqual(int(house["max_lot_minutes"]), 720, "a local lot is short")
                else:
                    self.assertGreaterEqual(int(house["max_active_lots"]), 20)


class NpcContentTests(unittest.TestCase):
    def test_every_npc_has_the_narrator_fields(self):
        for name, npc in WORLD["npcs"].items():
            for field in NARRATOR_NPC_FIELDS:
                with self.subTest(npc=name, field=field):
                    self.assertTrue(str(npc.get(field) or "").strip(), f"{name} lacks {field}")

    def test_the_world_rulers_are_not_one_template(self):
        rulers = [n for n, npc in WORLD["npcs"].items() if str(npc.get("role", "")).startswith("Public ruler of")]
        self.assertEqual(len(rulers), 4)
        for field in ("personality", "speech", "want", "fear", "secret"):
            values = {WORLD["npcs"][n][field] for n in rulers}
            with self.subTest(field=field):
                self.assertEqual(len(values), 4, f"rulers share a {field}")

    def test_steward_qiao_is_ready_to_be_a_giver(self):
        qiao = WORLD["npcs"]["Steward Qiao"]
        self.assertIn("commission", qiao["secret"].lower())
        self.assertEqual(qiao["location"], "Golden Pavilion Auction House")


if __name__ == "__main__":
    unittest.main()


class TravellingMerchantContentTests(unittest.TestCase):
    """v0.34.1: a merchant is content - a loop of cities in one world, a
    purse in that world's low currency, and an NPC that is its face."""

    def test_every_merchant_walks_real_cities_of_one_world_and_has_a_face(self):
        merchants = WORLD["merchants"]
        self.assertGreaterEqual(len(merchants), 8)
        locations = WORLD["locations"]
        faces = {str(npc.get("merchant")): name for name, npc in WORLD["npcs"].items() if npc.get("merchant")}
        for key, m in merchants.items():
            with self.subTest(merchant=key):
                self.assertIn(m["home"], m["route"], "home must be a route stop")
                self.assertGreaterEqual(len(m["route"]), 3)
                self.assertEqual(len(set(m["route"])), len(m["route"]), "a loop visits each stop once")
                for stop in m["route"]:
                    self.assertIn(stop, locations)
                    self.assertEqual(locations[stop]["world"], m["world"])
                    self.assertFalse(locations[stop].get("private"))
                self.assertGreater(int(m["budget"]), 0)
                self.assertGreaterEqual(int(m["markup_percent"]), 100)
                self.assertGreater(int(m["dwell_minutes"]), 0)
                self.assertIn(m["currency"], WORLD["currencies"])
                self.assertEqual(WORLD["currencies"][m["currency"]]["world"], m["world"])
                self.assertIn(key, faces, "no NPC fronts this merchant")
                self.assertEqual(WORLD["npcs"][faces[key]]["location"], m["home"])

    def test_every_world_with_an_auction_house_has_a_merchant_on_its_floors(self):
        houses = WORLD["auction_houses"]
        locations = WORLD["locations"]
        route_stops = {m["world"]: set() for m in WORLD["merchants"].values()}
        for m in WORLD["merchants"].values():
            route_stops[m["world"]].update(m["route"])
        for house_id, house in houses.items():
            city = house["entrance_location"]
            world = locations[city]["world"]
            with self.subTest(house=house_id):
                self.assertIn(world, route_stops, f"{world} has no merchant at all")
        # Every grand house (a capital) lies on at least one merchant loop, so
        # an unsold lot in a capital always has a buyer with a route there.
        for house_id, house in houses.items():
            if house.get("size") != "grand":
                continue
            city = house["entrance_location"]
            with self.subTest(grand_house=house_id):
                self.assertIn(city, route_stops[locations[city]["world"]], f"no merchant passes {city}")

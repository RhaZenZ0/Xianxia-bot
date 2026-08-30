import json
import tempfile
import time
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT

install_aiosqlite_shim()

import aiosqlite

from app.database import Database
from app.game import World


ROOT = PROJECT_ROOT
WORLD_PATH = ROOT / "content" / "world.json"

REQUIRED_EVENT_IDS = {
    "ancient_ruin_appears",
    "heavenly_treasure_descends",
    "sect_recruitment_delegation",
    "beast_tide",
    "secret_inheritance_tomb",
    "lightning_tribulation_nearby",
    "demonic_village_ritual",
    "auction_house_chaos",
    "young_master_confrontation",
    "mysterious_old_beggar",
    "celestial_descendant_disguise",
    "spatial_rift",
    "bloodline_awakening_omen",
    "sect_tournament",
    "spirit_herb_mutation",
    "meteor_from_heavens",
    "soul_possession",
    "demon_invasion",
    "heavenly_phenomenon",
    "marriage_arrangement",
    "cultivation_market_scam",
    "time_distortion",
    "dead_city",
    "dragon_appears",
    "fate_encounter",
    "heavens_chosen_appears",
    "forbidden_zone_opens",
    "reincarnated_expert_awakens",
    "artifact_chooses_master",
    "heavenly_punishment",
    "cultivation_festival",
}

REQUIRED_CATEGORIES = {
    "Ancient Ruin",
    "Auction House",
    "Beast Tide",
    "Bloodline Awakening",
    "Demon Incident",
    "Demon Invasion",
    "Fate Encounter",
    "Heavenly Phenomenon",
    "Heavenly Treasure",
    "Inheritance",
    "Mysterious Person",
    "Sect Recruitment",
    "Spatial Rift",
    "Tournament",
    "Tribulation",
}


class RandomEventCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(WORLD_PATH.read_text(encoding="utf-8"))
        cls.events = cls.data["unexpected_events"]
        cls.by_id = {event["id"]: event for event in cls.events}
        cls.world = World(WORLD_PATH)

    def test_requested_catalog_is_complete_and_unique(self):
        ids = [event["id"] for event in self.events]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 39)
        self.assertTrue(REQUIRED_EVENT_IDS.issubset(ids))
        self.assertTrue(REQUIRED_CATEGORIES.issubset({event["category"] for event in self.events}))

    def test_catalog_references_and_effect_bounds(self):
        allowed_region = {
            "population_percent",
            "prosperity",
            "security",
            "spirit_resources",
            "food_supply",
            "migration_pressure",
            "unrest",
        }
        allowed_market = {"supply", "demand", "price_index"}
        allowed_sect = {
            "influence",
            "cohesion",
            "resources",
            "recruitment_pressure",
            "doctrine_pressure",
        }
        items = set(self.data["items"])
        secret_realms = set(self.data["secret_realms"])

        for event in self.events:
            with self.subTest(event=event["id"]):
                self.assertIn(event["kind"], {"personal", "world_event", "secret_realm"})
                self.assertGreater(int(event.get("weight", 0)), 0)
                self.assertTrue(str(event.get("category", "")).strip())
                self.assertTrue(str(event.get("description", "")).strip())
                for item_id, quantity in event.get("player_reward", {}).get("items", {}).items():
                    self.assertIn(item_id, items)
                    self.assertGreater(int(quantity), 0)

                if event["kind"] == "secret_realm":
                    self.assertIn(event["secret_realm_id"], secret_realms)
                if event["kind"] != "world_event":
                    continue

                self.assertTrue(1 <= int(event["severity"]) <= 10)
                self.assertGreater(int(event["duration_hours"]), 0)
                self.assertTrue(str(event.get("consequence_text", "")).strip())
                effect = event.get("world_effect", {})
                self.assertTrue(effect)
                self.assertTrue(str(effect.get("history", "")).strip())
                self.assertTrue(set(effect.get("region", {})).issubset(allowed_region))
                self.assertTrue(set(effect.get("market", {})).issubset(allowed_market))
                self.assertTrue(set(effect.get("sect", {})).issubset(allowed_sect))
                if "population_percent" in effect.get("region", {}):
                    self.assertTrue(-25 <= float(effect["region"]["population_percent"]) <= 25)
                if "price_index" in effect.get("market", {}):
                    self.assertTrue(-1.5 <= float(effect["market"]["price_index"]) <= 1.5)

    def test_location_world_and_realm_filters(self):
        mortal = {event["id"] for event in self.world.eligible_unexpected_events(
            location="Greenriver Town", character={"realm_index": 0}
        )}
        immortal = {event["id"] for event in self.world.eligible_unexpected_events(
            location="Nine-Heavens Immortal Court", character={"realm_index": 20}
        )}
        pavilion = {event["id"] for event in self.world.eligible_unexpected_events(
            location="Golden Pavilion Auction House", character={"realm_index": 0}
        )}
        foothills = {event["id"] for event in self.world.eligible_unexpected_events(
            location="Cloudspine Foothills", character={"realm_index": 0}
        )}

        self.assertIn("beast_tide", mortal)
        self.assertNotIn("beast_tide", immortal)
        self.assertIn("auction_house_chaos", pavilion)
        self.assertNotIn("auction_house_chaos", mortal)
        self.assertIn("ancient_ruin_appears", foothills)
        self.assertNotIn("ancient_ruin_appears", mortal)


class RandomEventPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "events.sqlite3")
        await self.db.init()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_active_event_deduplication_is_atomic_per_location(self):
        ends_at = time.time() + 3600
        first = await self.db.activate_world_event(
            event_key="beast-tide:one",
            dedupe_key="random:beast_tide",
            event_type="random_event",
            title="Beast Tide",
            location="Greenriver Town",
            payload={"definition_id": "beast_tide"},
            ends_at=ends_at,
        )
        duplicate = await self.db.activate_world_event(
            event_key="beast-tide:two",
            dedupe_key="random:beast_tide",
            event_type="random_event",
            title="Beast Tide",
            location="Greenriver Town",
            payload={"definition_id": "beast_tide"},
            ends_at=ends_at,
        )
        other_location = await self.db.activate_world_event(
            event_key="beast-tide:three",
            dedupe_key="random:beast_tide",
            event_type="random_event",
            title="Beast Tide",
            location="Cloudspine Foothills",
            payload={"definition_id": "beast_tide"},
            ends_at=ends_at,
        )

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertTrue(other_location)
        active = await self.db.get_active_world_events()
        self.assertEqual({row["event_key"] for row in active}, {"beast-tide:one", "beast-tide:three"})


if __name__ == "__main__":
    unittest.main()

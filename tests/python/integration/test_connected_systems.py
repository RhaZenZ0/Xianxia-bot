import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION
from app.fate import fate_label
from app.realm_hubs import REALM_HUBS, realm_hub_by_location
from app.game import World


ATTRS = {"body": 4, "agility": 3, "spirit": 5, "insight": 4, "will": 5, "presence": 2}
ROOT = PROJECT_ROOT


class ConnectedSystemsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "connected.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        for uid, name in ((101, "Junior"), (202, "Senior")):
            ok = await seed_character(self.db, 
                user_id=uid, discord_name=name.lower(), name=name, origin="Greenriver Town",
                path="Formation Adept", spiritual_root="Wind", concept="connected systems test",
                location="Greenriver Town", attributes=ATTRS, qi_max=30, vitality_max=30,
            )
            self.assertTrue(ok)
        async with self.db._connect() as conn:
            await conn.execute("UPDATE characters SET realm_index=1,phase=2 WHERE user_id=202")
            await conn.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_schema_v7_contains_connected_system_tables_and_partner_echo_columns(self):
        self.assertEqual(SCHEMA_VERSION, 24)
        with sqlite3.connect(self.path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in {
                "character_fate", "fate_ledger", "disciple_requests", "deployed_location_arrays",
                "black_market_posts", "black_market_stock", "realm_hub_channels", "dao_partnerships",
            }:
                self.assertIn(name, tables)
            reinc_cols = {row[1] for row in conn.execute("PRAGMA table_info(reincarnation_state)")}
            self.assertTrue({"partner_echo", "partner_name"}.issubset(reinc_cols))






    async def test_four_realm_hubs_map_discord_channels_to_canonical_locations(self):
        self.assertEqual(set(REALM_HUBS), {"Mortal World", "Spiritual World", "Immortal World", "Celestial World"})
        for index, (world, hub) in enumerate(REALM_HUBS.items(), 1):
            matched = realm_hub_by_location(hub["location"])
            self.assertEqual(matched[0], world)
            await self.db.set_realm_hub_channel(
                guild_id=999, world_name=world, location=hub["location"], channel_id=1000 + index, category_id=777,
            )
        rows = await self.db.get_realm_hub_channels(999)
        self.assertEqual(len(rows), 4)
        mapped = await self.db.get_realm_hub_by_channel(999, 1001)
        self.assertEqual(mapped["location"], REALM_HUBS["Mortal World"]["location"])

    def test_content_catalog_connects_realm_hubs_and_formation_inscription(self):
        world = World(ROOT / "content" / "world.json")
        for hub in REALM_HUBS.values():
            loc = world.locations[hub["location"]]
            self.assertTrue(loc.get("realm_hub"))
            self.assertGreaterEqual(len(loc.get("encounters", [])), 5)
        formation_recipes = [r for r in world.recipes.values() if r.get("profession") == "Formation"]
        self.assertGreaterEqual(len(formation_recipes), 5)
        self.assertIn("minor_qi_gathering_array_disk", world.items)
        self.assertIn("swift_wind_talisman", world.items)


if __name__ == "__main__":
    unittest.main()

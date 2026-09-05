import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION
from app.rules.game import World

ROOT = PROJECT_ROOT
ATTRS = {"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 4}


class PlayerPropertySystemTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "property.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        for uid, name in ((1901, "Owner"), (1902, "Guest")):
            self.assertTrue(await seed_character(self.db,
                user_id=uid, discord_name=name.lower(), name=name,
                origin="Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
                concept="property-system test", location="Greenriver Town", attributes=ATTRS,
                qi_max=20, vitality_max=20, created_game_minute=10,
            ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_schema_v10_adds_general_property_columns_and_info_message(self):
        self.assertEqual(SCHEMA_VERSION, 27)
        with sqlite3.connect(self.path) as conn:
            abode_cols = {row[1] for row in conn.execute("PRAGMA table_info(cave_abodes)")}
            server_cols = {row[1] for row in conn.execute("PRAGMA table_info(server_config)")}
        self.assertTrue({
            "property_type", "storage_level", "herb_garden_level", "beast_pen_level", "merchant_level"
        }.issubset(abode_cols))
        self.assertIn("info_message_id", server_cols)


    async def test_info_guide_message_id_is_persistent(self):
        await self.db.set_server_channels(
            88, announcement_channel_id=1, event_scene_channel_id=2,
            home_scene_channel_id=3, log_channel_id=4, begin_channel_id=5,
            info_channel_id=6, exploration_channel_id=7,
        )
        await self.db.set_info_message_id(88, 123456)
        cfg = await self.db.get_server_config(88)
        self.assertEqual(cfg["info_message_id"], 123456)

    def test_world_has_six_player_property_archetypes(self):
        world = World(ROOT / "content" / "world.json")
        types = world.abode_system.get("property_types", {})
        self.assertEqual(set(types), {
            "cave_abode", "alchemy_estate", "spirit_herb_estate",
            "spirit_beast_ranch", "merchant_pavilion", "clan_estate",
        })
        self.assertEqual(types["alchemy_estate"]["defaults"]["alchemy"], 1)
        self.assertEqual(types["spirit_beast_ranch"]["defaults"]["beast_pen"], 2)


if __name__ == "__main__":
    unittest.main()

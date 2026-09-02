import tempfile
import time
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, seed_character
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION

ATTRS = {"body": 4, "agility": 4, "spirit": 6, "insight": 6, "will": 5, "presence": 4, "heart": 5}


class EventSpecificGuiPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "event_gui.sqlite3")
        await self.db.init()
        self.assertTrue(await seed_character(self.db,
            user_id=7001, discord_name="Zi Dian", name="Zi Dian", origin="Greenriver Town",
            path="Qi Refiner", spiritual_root="Lightning", concept="event tester",
            location="Greenriver Town", attributes=ATTRS, qi_max=25, vitality_max=25,
            created_game_minute=0,
        ))
        self.assertTrue(await self.db.activate_world_event(
            event_key="beast-tide:test", dedupe_key="", event_type="random_event", title="Beast Tide",
            location="Greenriver Town",
            payload={"definition_id":"beast_tide","category":"Beast Tide","severity":6,"consequence_text":"Roads and farms are threatened."},
            ends_at=time.time()+3600,
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_schema_is_v17(self):
        self.assertEqual(SCHEMA_VERSION, 26)


if __name__ == "__main__":
    unittest.main()

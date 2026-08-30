import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, seed_character
install_aiosqlite_shim()

from app.database import Database
from app.progression_systems import condition_effect, condition_definition, ascension_gate, profession_rank


class MissingSystemsDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "systems.sqlite3")
        await self.db.init()
        ok = await seed_character(self.db, 
            user_id=501, discord_name="systems", name="Systems Test", origin="Greenriver Town",
            path="Sword Cultivator", spiritual_root="Fire", concept="integration test",
            location="Greenriver Town",
            attributes={"body":4,"agility":3,"spirit":4,"insight":4,"will":4,"presence":2},
            qi_max=30, vitality_max=30, created_game_minute=0,
        )
        self.assertTrue(ok)

    async def asyncTearDown(self):
        self.tmp.cleanup()






if __name__ == "__main__":
    unittest.main()

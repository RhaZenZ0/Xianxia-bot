import json
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
install_aiosqlite_shim()

from app.database import Database
from app.game import World


class AdvancedForwardPortTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "advanced.sqlite3")
        await self.db.init()
        attrs = {"body": 4, "agility": 4, "spirit": 5, "insight": 4, "will": 4, "presence": 3}
        for uid, name in ((701, "Azure"), (702, "Crimson"), (703, "Jade")):
            self.assertTrue(await seed_character(self.db, 
                user_id=uid, discord_name=name.lower(), name=name, origin="Greenriver Town",
                path="Beast Binder" if uid == 701 else "Sword Cultivator", spiritual_root="Fire",
                concept="advanced forward port test", location="Greenriver Town", attributes=attrs,
                qi_max=30, vitality_max=30, created_game_minute=0,
            ))
        self.world = World(PROJECT_ROOT / "content" / "world.json")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_01_catalog_matches_advanced_branch_scale(self):
        self.assertEqual(len(self.world.manuals), 148)
        self.assertEqual(len(self.world.techniques), 528)
        evil_manuals = [m for m in self.world.manuals.values() if str(m.get("alignment", "")).lower() == "demonic"]
        evil_techniques = [t for t in self.world.techniques.values() if str((self.world.manuals.get(str(t.get("manual"))) or {}).get("alignment", "")).lower() == "demonic"]
        self.assertEqual(len(evil_manuals), 42)
        self.assertEqual(len(evil_techniques), 160)
        hidden = self.world.sects.get("Heaven-Devouring Demon Sect")
        self.assertTrue(hidden and hidden.get("hidden"))
        self.assertEqual(hidden.get("karma_initiation"), -200)
        self.assertIn("physical_laws", self.world.world_rules)
        self.assertIn("social_laws", self.world.world_rules)

    async def test_02_technique_executability_matrix_uses_39_subtests(self):
        # 39 deterministic samples exercise the generated content contract without
        # turning this into 528 nearly identical test methods.
        samples = sorted(self.world.techniques.items())[:39]
        self.assertEqual(len(samples), 39)
        for technique_id, technique in samples:
            with self.subTest(technique=technique_id):
                self.assertIn(str(technique.get("manual")), self.world.manuals)
                self.assertTrue(str(technique.get("name", "")).strip())
                self.assertGreaterEqual(int(technique.get("qi_cost", 0)), 0)
                self.assertTrue(
                    int(technique.get("damage", 0)) > 0
                    or int(technique.get("heal", 0)) > 0
                    or int(technique.get("suppress_turns", 0)) > 0
                )

    async def test_03_catalog_sync_seeds_sqlite_territories_and_world_era(self):
        await self.db.sync_world_catalog(self.world.data)
        async with self.db._connect() as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM catalog_manuals")
            self.assertEqual((await cur.fetchone())[0], 148)
            cur = await conn.execute("SELECT COUNT(*) FROM catalog_techniques")
            self.assertEqual((await cur.fetchone())[0], 528)
        territories = await self.db.get_territories()
        self.assertEqual(len(territories), len(self.world.locations))
        era = await self.db.get_current_era()
        self.assertEqual(era["name"], "Jade Meridian Awakening Era")







if __name__ == "__main__":
    unittest.main()

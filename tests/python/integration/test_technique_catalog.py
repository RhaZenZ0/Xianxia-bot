"""The manual / technique catalog: content counts, executability, sync to
SQLite. (Was test_advanced_forward_port.py + test_forbidden_arts.py; renamed
and merged in v0.20.3 - the port it was named after finished in v0.18.)"""
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.database import Database
from app.rules.game import World


class TechniqueCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "catalog.sqlite3")
        await self.db.init()
        self.world = World(PROJECT_ROOT / "content" / "world.json")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_catalog_scale_and_the_demonic_branch(self):
        self.assertEqual(len(self.world.manuals), 154)  # 148 generated + 6 authored sect entry manuals (v0.21.4)
        self.assertEqual(len(self.world.techniques), 546)
        evil_manuals = [m for m in self.world.manuals.values() if str(m.get("alignment", "")).lower() == "demonic"]
        evil_techniques = [t for t in self.world.techniques.values() if str((self.world.manuals.get(str(t.get("manual"))) or {}).get("alignment", "")).lower() == "demonic"]
        self.assertEqual(len(evil_manuals), 44)
        self.assertEqual(len(evil_techniques), 166)
        self.assertIn("blood_sea_palm", self.world.techniques)
        self.assertEqual(self.world.sects["Blood River Sect"]["alignment"], "Demonic")
        hidden = self.world.sects.get("Heaven-Devouring Demon Sect")
        self.assertTrue(hidden and hidden.get("hidden"))
        self.assertEqual(hidden.get("karma_initiation"), -200)
        for key in ("physical_laws", "social_laws", "npc_principles"):
            self.assertTrue(self.world.world_rules[key], key)

    async def test_technique_executability_matrix_uses_39_subtests(self):
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

    async def test_catalog_sync_seeds_sqlite_territories_and_world_era(self):
        await self.db.sync_world_catalog(self.world.data)
        async with self.db._connect() as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM catalog_manuals")
            self.assertEqual((await cur.fetchone())[0], 154)
            cur = await conn.execute("SELECT COUNT(*) FROM catalog_techniques")
            self.assertEqual((await cur.fetchone())[0], 546)
        territories = await self.db.get_territories()
        self.assertEqual(len(territories), len(self.world.locations))
        era = await self.db.get_current_era()
        self.assertEqual(era["name"], "Jade Meridian Awakening Era")


if __name__ == "__main__":
    unittest.main()

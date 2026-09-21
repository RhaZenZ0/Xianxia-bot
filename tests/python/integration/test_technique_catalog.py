"""The manual / technique catalog: content counts, executability, sync to
SQLite. (Was test_advanced_forward_port.py + test_forbidden_arts.py; renamed
and merged in v0.20.3 - the port it was named after finished in v0.18.)"""
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, seed_content_tables, PROJECT_ROOT
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
        # 148 generated on the six-path cycle, 12 authored sect entry manuals
        # (v0.21.4; two per higher world since v0.39.0), 23 for the seventh path
        # the cycle never reached and 13 household traditions (both v1.0.3).
        self.assertEqual(len(self.world.manuals), 196)
        self.assertEqual(len(self.world.techniques), 678)
        evil_manuals = [m for m in self.world.manuals.values() if str(m.get("alignment", "")).lower() == "demonic"]
        evil_techniques = [t for t in self.world.techniques.values() if str((self.world.manuals.get(str(t.get("manual"))) or {}).get("alignment", "")).lower() == "demonic"]
        self.assertEqual(len(evil_manuals), 52)  # +6 for the Ghost Cultivator (v1.0.3)
        self.assertEqual(len(evil_techniques), 196)
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

    async def test_the_content_tables_hold_every_manual_and_technique(self):
        # The engine fills content_* in production; pytest has no engine, so
        # the fixture writes the same three columns every reader touches
        # (v1.0.0-rc.40, when the catalog_* mirrors were retired).
        await seed_content_tables(self.db, self.world.data)
        async with self.db._connect() as conn:
            # Against the catalogue rather than against a literal: this test is
            # "the tables hold every manual", and a second copy of the count is
            # free to drift from the one `test_world_catalog_materialised.py`
            # pins (v1.0.3).
            cur = await conn.execute("SELECT COUNT(*) FROM content_manuals")
            self.assertEqual((await cur.fetchone())[0], len(self.world.manuals))
            cur = await conn.execute("SELECT COUNT(*) FROM content_techniques")
            self.assertEqual((await cur.fetchone())[0], len(self.world.techniques))

    async def test_seeding_the_territory_map_gives_every_location_a_node_and_an_era(self):
        await self.db.seed_world_territories(self.world.data)
        territories = await self.db.get_territories()
        self.assertEqual(len(territories), len(self.world.locations))
        era = await self.db.get_current_era()
        self.assertEqual(era["name"], "Jade Meridian Awakening Era")


if __name__ == "__main__":
    unittest.main()

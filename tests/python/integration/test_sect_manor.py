import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, seed_character
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION
from app.sect_manor import (
    SECT_MANOR_ESTABLISHMENT_COST,
    manor_craft_bonus,
    manor_defense_power_bonus,
    manor_qi_multiplier,
    manor_seclusion_multiplier,
    manor_upgrade_cost,
)


ATTRS = {"body": 4, "agility": 3, "spirit": 4, "insight": 4, "will": 4, "presence": 2}


class SectManorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "sect-manor.sqlite3")
        await self.db.init()
        for uid, name in ((1, "Master"), (2, "Elder"), (3, "Disciple")):
            ok = await seed_character(self.db, 
                user_id=uid,
                discord_name=name.lower(),
                name=name,
                origin="Greenriver Town",
                path="Sword Cultivator",
                spiritual_root="Fire",
                concept="sect manor test",
                location="Greenriver Town",
                attributes=ATTRS,
                qi_max=30,
                vitality_max=30,
            )
            self.assertTrue(ok)
        await self.db.set_sect_membership(1, sect_name="Azure Cloud Sect", rank_name="Sect Master", rank_level=70)
        await self.db.set_sect_membership(2, sect_name="Azure Cloud Sect", rank_name="Elder", rank_level=50)
        await self.db.set_sect_membership(3, sect_name="Azure Cloud Sect", rank_name="Inner Disciple", rank_level=20)
        async with self.db._connect() as conn:
            for item_id in ("spirit_iron", "spirit_herb", "beast_core"):
                await conn.execute(
                    "INSERT OR REPLACE INTO sect_treasury(sect_name,item_id,quantity) VALUES(?,?,?)",
                    ("Azure Cloud Sect", item_id, 500),
                )
            await conn.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_schema_migration_includes_manor_tables(self):
        self.assertEqual(SCHEMA_VERSION, 22)
        async with self.db._connect() as conn:
            cur = await conn.execute("SELECT current_version FROM schema_version WHERE singleton=1")
            self.assertEqual(int((await cur.fetchone())[0]), 22)
            cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sect_manors'")
            self.assertIsNotNone(await cur.fetchone())




    async def test_facility_benefit_scaling(self):
        manor = {
            "qi_array_level": 3,
            "alchemy_hall_level": 2,
            "forge_pavilion_level": 4,
            "defense_array_level": 5,
        }
        self.assertAlmostEqual(manor_qi_multiplier(manor), 1.15)
        self.assertAlmostEqual(manor_seclusion_multiplier(manor), 1.24)
        self.assertEqual(manor_craft_bonus(manor, "Alchemy"), 4)
        self.assertEqual(manor_craft_bonus(manor, "Forging"), 8)
        self.assertEqual(manor_defense_power_bonus(manor), 30)


if __name__ == "__main__":
    unittest.main()

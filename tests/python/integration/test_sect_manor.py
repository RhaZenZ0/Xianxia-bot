import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim
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
            ok = await self.db.create_character(
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
        self.assertEqual(SCHEMA_VERSION, 17)
        async with self.db._connect() as conn:
            cur = await conn.execute("SELECT current_version FROM schema_version WHERE singleton=1")
            self.assertEqual(int((await cur.fetchone())[0]), 17)
            cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sect_manors'")
            self.assertIsNotNone(await cur.fetchone())

    async def test_establishment_spends_shared_treasury_and_is_visible_to_members(self):
        before = await self.db.get_sect_treasury("Azure Cloud Sect")
        manor = await self.db.establish_sect_manor(
            1, name="Cloud-Piercing Manor", base_location="Greenriver Town", game_minute=100
        )
        self.assertEqual(manor["name"], "Cloud-Piercing Manor")
        self.assertEqual(manor["base_location"], "Greenriver Town")
        after = await self.db.get_sect_treasury("Azure Cloud Sect")
        for item_id, qty in SECT_MANOR_ESTABLISHMENT_COST.items():
            self.assertEqual(after[item_id], before[item_id] - qty)
        member_view = await self.db.get_member_sect_manor(3)
        self.assertEqual(member_view["sect_name"], "Azure Cloud Sect")
        self.assertEqual(member_view["member_rank_level"], 20)
        projects = await self.db.get_sect_manor_projects("Azure Cloud Sect")
        self.assertEqual(projects[0]["project_type"], "establish")

    async def test_only_sect_master_or_ancestor_can_establish(self):
        with self.assertRaisesRegex(ValueError, "Sect Master or Ancestor"):
            await self.db.establish_sect_manor(
                2, name="Unauthorized Manor", base_location="Greenriver Town", game_minute=100
            )

    async def test_elder_can_upgrade_but_disciple_cannot(self):
        await self.db.establish_sect_manor(
            1, name="Cloud-Piercing Manor", base_location="Greenriver Town", game_minute=100
        )
        cost = manor_upgrade_cost("qi_array", 0)
        before = await self.db.get_sect_treasury("Azure Cloud Sect")
        manor = await self.db.upgrade_sect_manor_facility(2, "qi_array", game_minute=120)
        self.assertEqual(manor["qi_array_level"], 1)
        after = await self.db.get_sect_treasury("Azure Cloud Sect")
        for item_id, qty in cost.items():
            self.assertEqual(after[item_id], before[item_id] - qty)
        with self.assertRaisesRegex(ValueError, "Elder or higher"):
            await self.db.upgrade_sect_manor_facility(3, "alchemy_hall", game_minute=140)

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

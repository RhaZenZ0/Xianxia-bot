import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION

ROOT = PROJECT_ROOT
ATTRS = {"body": 4, "agility": 4, "spirit": 4, "insight": 4, "will": 4, "presence": 4}


class PrivateSceneRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "scenes.sqlite3")
        await self.db.init()
        self.assertTrue(await seed_character(self.db,
            user_id=1801, discord_name="sceneuser", name="Jin Wei",
            origin="Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
            concept="wanderer", location="Greenriver Town", attributes=ATTRS,
            qi_max=20, vitality_max=20, created_game_minute=10,
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_schema_v10_stores_info_expedition_and_scene_threads(self):
        self.assertEqual(SCHEMA_VERSION, 26)
        await self.db.set_server_channels(
            77, announcement_channel_id=1, event_scene_channel_id=2,
            home_scene_channel_id=3, log_channel_id=4, begin_channel_id=5,
            info_channel_id=6, exploration_channel_id=7,
        )
        cfg = await self.db.get_server_config(77)
        self.assertEqual(cfg["info_channel_id"], 6)
        self.assertEqual(cfg["exploration_channel_id"], 7)

        await self.db.set_expedition_thread(
            77, 1801, thread_id=8001, parent_channel_id=7, last_location="Greenriver Town"
        )
        row = await self.db.get_expedition_thread(77, 1801)
        self.assertEqual(row["thread_id"], 8001)
        self.assertEqual((await self.db.get_expedition_thread_by_thread(8001))["user_id"], 1801)
        await self.db.update_expedition_location(77, 1801, "Moonfen Marsh")
        self.assertEqual((await self.db.get_expedition_thread(77, 1801))["last_location"], "Moonfen Marsh")

        async with self.db._connect() as db:
            cur = await db.execute(
                "INSERT INTO birth_families(family_name,surname,archetype,tier,wealth,influence,stability,alignment_bias,location,head_name,head_gender,head_title,head_realm_index,head_phase,treasury_balance,generation,created_game_minute,last_simulated_game_minute,history_json,line_status,clan_structure,bloodline_name,bloodline_affinity,bloodline_trait,bloodline_purity,branch_count,retainer_count,confederacy_name,created_at,updated_at,starter_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("Han Family","Han","martial_household",2,42,48,62,0,"Greenriver Town","Han Wei","male","Patriarch",0,1,168,1,10,10,"[]","active","martial_household","None","None","None",0,1,4,"None",0,0,"starter:mortal_world:martial_household"),
            )
            family_id = int(cur.lastrowid)
            await db.commit()
        await self.db.set_birth_family_household_thread(77, family_id, thread_id=8101, parent_channel_id=3)
        household = await self.db.get_birth_family_household_thread(77, family_id)
        self.assertEqual(household["thread_id"], 8101)
        self.assertEqual((await self.db.get_birth_family_household_thread_by_thread(8101))["family_id"], family_id)

    async def test_sect_abode_is_one_persistent_private_location_per_member(self):
        await self.db.set_sect_membership(1801, sect_name="Azure Cloud Sect", rank_name="Outer Disciple", rank_level=10)
        abode = await self.db.ensure_sect_abode(
            1801, sect_name="Azure Cloud Sect", name="Jin Wei's Disciple Courtyard", base_location="Cloudspine Foothills"
        )
        self.assertEqual(abode["location_key"], "sect_abode:1801")
        await self.db.set_sect_abode_thread(1801, thread_id=9001, thread_channel_id=3)
        self.assertEqual((await self.db.get_sect_abode_by_thread(9001))["sect_name"], "Azure Cloud Sect")
        self.assertEqual((await self.db.get_sect_abode_by_location("sect_abode:1801"))["user_id"], 1801)


if __name__ == "__main__":
    unittest.main()

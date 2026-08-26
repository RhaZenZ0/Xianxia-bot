import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION

ROOT = PROJECT_ROOT
ATTRS = {"body": 4, "agility": 4, "spirit": 4, "insight": 4, "will": 4, "presence": 4}


class PrivateSceneRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "scenes.sqlite3")
        await self.db.init()
        self.assertTrue(await self.db.create_character(
            user_id=1801, discord_name="sceneuser", name="Jin Wei",
            origin="Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
            concept="wanderer", location="Greenriver Town", attributes=ATTRS,
            qi_max=20, vitality_max=20, created_game_minute=10,
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_schema_v10_stores_info_expedition_and_scene_threads(self):
        self.assertEqual(SCHEMA_VERSION, 17)
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

    async def test_sect_abode_is_one_persistent_private_location_per_member(self):
        await self.db.set_sect_membership(1801, sect_name="Azure Cloud Sect", rank_name="Outer Disciple", rank_level=10)
        abode = await self.db.ensure_sect_abode(
            1801, sect_name="Azure Cloud Sect", name="Jin Wei's Disciple Courtyard", base_location="Cloudspine Foothills"
        )
        self.assertEqual(abode["location_key"], "sect_abode:1801")
        await self.db.set_sect_abode_thread(1801, thread_id=9001, thread_channel_id=3)
        self.assertEqual((await self.db.get_sect_abode_by_thread(9001))["sect_name"], "Azure Cloud Sect")
        self.assertEqual((await self.db.get_sect_abode_by_location("sect_abode:1801"))["user_id"], 1801)

    def test_discord_gui_routes_explore_action_and_read_only_info(self):
        source = (ROOT / "app" / "bot" / "main.py").read_text(encoding="utf-8")
        creation_flow = source[
            source.index("class CharacterModal"):
            source.index("def _birth_family_preview_embed")
        ]
        self.assertIn('"xianxia-info": cfg.get("info_channel_id")', source)
        self.assertIn('"expeditions": cfg.get("exploration_channel_id")', source)
        self.assertIn('send_messages=False', source)
        self.assertIn('ensure_expedition_thread(interaction, character)', creation_flow)
        self.assertIn('Your private expedition journal is ready', creation_flow)
        self.assertIn('ensure_expedition_thread(interaction, c)', source)
        self.assertIn('Exploration recorded in your private expedition journal', source)
        self.assertIn('target_thread = await active_private_location_thread(interaction, c)', source)
        self.assertIn('Private Expedition Journal', source)
        self.assertIn('Sect abode assigned', source)
        self.assertIn('@registered_group_command(sect_group, name="abode"', source)
        self.assertIn('bool(private_scene)', source)


if __name__ == "__main__":
    unittest.main()

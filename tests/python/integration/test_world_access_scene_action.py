import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character, bot_package_source
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION

ROOT = PROJECT_ROOT
ATTRS = {"body": 4, "agility": 4, "spirit": 4, "insight": 4, "will": 4, "presence": 4}


class WorldAccessAndSceneActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "access.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.assertTrue(await seed_character(self.db,
            user_id=1601, discord_name="wanderer", name="Lin Yue",
            origin="Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
            concept="discover the world", location="Greenriver Town",
            attributes=ATTRS, qi_max=20, vitality_max=20, created_game_minute=10,
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()


    def test_scene_action_replaces_freeform_act(self):
        source = bot_package_source()
        self.assertIn('@registered_root_command(name="action", description="Open the guided Scene Action panel"', source)
        self.assertNotIn('@registered_root_command(name="act"', source)
        for key in ("observe", "investigate", "influence", "stealth", "physical", "qi", "resolve", "aid"):
            self.assertIn(f'"{key}":', source)
        self.assertIn("fixed_roll=fixed", source)
        self.assertIn("structured_scene_action", source)
        self.assertIn('name="🎲 Result"', source)
        self.assertIn('name="🧠 Attribute"', source)
        self.assertIn('name="🎯 Target"', source)
        self.assertIn('name="📝 Attempt"', source)
        self.assertIn("await interaction.followup.send(embed=embed, ephemeral=False)", source)
        self.assertIn('\"scene.action\"', source)
        self.assertIn('self_action = bool(mechanics.get(\"automatic\", False))', source)
        self.assertIn("Outcome: Automatic success — self-directed action.", source)
        self.assertIn("Hidden canonical information remains concealed.", source)
        self.assertIn("This does not reveal hidden canonical information.", source)

    def test_visibility_guards_cover_world_npcs_and_realm_hubs(self):
        source = bot_package_source()
        self.assertIn("_known_locations", source)
        self.assertIn("_location_is_visible", source)
        self.assertIn("_world_is_unlocked", source)
        self.assertIn("realmhub_world_autocomplete", source)
        self.assertIn("You have no reliable knowledge of that cultivator yet", source)
        self.assertIn("Explore known regions to discover additional routes", source)
        self.assertIn("Higher worlds remain beyond perception", source)


if __name__ == "__main__":
    unittest.main()

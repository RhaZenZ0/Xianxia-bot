import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
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
        self.assertTrue(await self.db.create_character(
            user_id=1601, discord_name="wanderer", name="Lin Yue",
            origin="Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
            concept="discover the world", location="Greenriver Town",
            attributes=ATTRS, qi_max=20, vitality_max=20, created_game_minute=10,
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_schema_v7_tracks_location_discovery(self):
        self.assertEqual(SCHEMA_VERSION, 17)
        rows = await self.db.get_discovered_locations(1601)
        self.assertEqual([row["location"] for row in rows], ["Greenriver Town"])
        self.assertTrue(await self.db.has_discovered_location(1601, "Greenriver Town"))
        self.assertFalse(await self.db.has_discovered_location(1601, "Moonfen Marsh"))
        self.assertTrue(await self.db.discover_location(1601, "Moonfen Marsh", game_minute=55))
        self.assertTrue(await self.db.has_discovered_location(1601, "Moonfen Marsh"))
        self.assertFalse(await self.db.discover_location(1601, "Moonfen Marsh", game_minute=56))

    def test_scene_action_replaces_freeform_act(self):
        source = (ROOT / "app" / "bot" / "main.py").read_text(encoding="utf-8")
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
        self.assertIn('self_action = target == "Self"', source)
        self.assertIn("Outcome: Automatic success — self-directed action.", source)
        self.assertIn("Hidden canonical information remains concealed.", source)
        self.assertIn("This does not reveal hidden canonical information.", source)

    def test_visibility_guards_cover_world_npcs_and_realm_hubs(self):
        source = (ROOT / "app" / "bot" / "main.py").read_text(encoding="utf-8")
        self.assertIn("_known_locations", source)
        self.assertIn("_location_is_visible", source)
        self.assertIn("_world_is_unlocked", source)
        self.assertIn("realmhub_world_autocomplete", source)
        self.assertIn("You have no reliable knowledge of that cultivator yet", source)
        self.assertIn("Explore known regions to discover additional routes", source)
        self.assertIn("Higher worlds remain beyond perception", source)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, seed_character
install_aiosqlite_shim()

from app.database import Database
from app.rules.progression_systems import PROFESSIONS, craft_quality, profession_rank

ATTRS = {"body": 5, "agility": 4, "spirit": 6, "insight": 6, "will": 5, "presence": 5}


class ProfessionCrossLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "crossloops.sqlite3")
        await self.db.init()
        for uid, name in ((7101, "Crafter"), (7102, "Rival")):
            self.assertTrue(await seed_character(self.db,
                user_id=uid, discord_name=name.lower(), name=name,
                origin="Greenriver Town", path="Sword Cultivator", spiritual_root="Fire",
                concept="cross-loop progression", location="Greenriver Town",
                attributes=ATTRS, qi_max=40, vitality_max=40, created_game_minute=0,
            ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_profession_catalog_contains_cross_loop_tracks(self):
        self.assertIn("Foraging", PROFESSIONS)
        self.assertIn("Beast Taming", PROFESSIONS)
        self.assertIn("Artifact Refining", PROFESSIONS)

    def test_generic_craft_quality_rewards_margin_without_multiplying_items(self):
        self.assertEqual(craft_quality(-1, success=False)["label"], "Failed")
        self.assertEqual(craft_quality(0, success=True)["label"], "Ordinary")
        self.assertEqual(craft_quality(4, success=True)["label"], "Fine")
        self.assertEqual(craft_quality(7, success=True)["label"], "Superior")
        masterwork = craft_quality(10, success=True)
        self.assertEqual(masterwork["label"], "Masterwork")
        self.assertGreater(masterwork["xp_bonus"], 0)


if __name__ == "__main__":
    unittest.main()

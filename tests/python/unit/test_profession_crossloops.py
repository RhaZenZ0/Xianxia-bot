import unittest

from app.rules.progression_systems import PROFESSIONS, craft_quality


class ProfessionCrossLoopTests(unittest.TestCase):
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

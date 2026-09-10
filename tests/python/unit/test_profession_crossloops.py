import unittest

from app.rules.progression_systems import PROFESSIONS


class ProfessionCrossLoopTests(unittest.TestCase):
    def test_profession_catalog_contains_cross_loop_tracks(self):
        self.assertIn("Foraging", PROFESSIONS)
        self.assertIn("Beast Taming", PROFESSIONS)
        self.assertIn("Artifact Refining", PROFESSIONS)

if __name__ == "__main__":
    unittest.main()

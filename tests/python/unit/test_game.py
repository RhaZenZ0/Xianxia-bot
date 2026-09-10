from tests.support import PROJECT_ROOT
import unittest
from pathlib import Path

from app.rules.game import World


WORLD = World(PROJECT_ROOT / "content" / "world.json")


class FixedD10Source:
    def __init__(self, *values):
        self.values = iter(values)

    def d10(self):
        return next(self.values)


class GameRulesTests(unittest.TestCase):
    def test_gendered_titles_are_cosmetic(self):
        self.assertEqual(WORLD.realm_name(25, "male"), "Celestial Duke")
        self.assertEqual(WORLD.realm_name(25, "female"), "Celestial Duchess")
        self.assertEqual(WORLD.body_realm_name(25, "male"), "Celestial Body Duke")
        self.assertEqual(WORLD.body_realm_name(25, "female"), "Celestial Body Duchess")

if __name__ == "__main__":
    unittest.main()

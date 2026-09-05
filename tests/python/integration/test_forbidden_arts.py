import unittest

from tests.support import PROJECT_ROOT
from app.rules.game import World


class ForbiddenArtsContentTests(unittest.TestCase):
    def test_content_contains_demonic_manuals_sects_and_world_rules(self):
        world = World(PROJECT_ROOT / "content" / "world.json")
        self.assertGreaterEqual(len(world.manuals), 6)
        self.assertIn("blood_sea_palm", world.techniques)
        self.assertEqual(world.sects["Blood River Sect"]["alignment"], "Demonic")
        self.assertTrue(world.world_rules["npc_principles"])


if __name__ == "__main__":
    unittest.main()

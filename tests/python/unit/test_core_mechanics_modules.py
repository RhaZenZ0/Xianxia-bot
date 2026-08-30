from tests.support import PROJECT_ROOT
import unittest
from pathlib import Path

from app.game import World
from app.sense import concealment_power, sense_precision_check, spiritual_sense_stats


ROOT = PROJECT_ROOT
WORLD = World(ROOT / "content" / "world.json")


def _character(*, conceal=False):
    return {
        "realm_index": 3,
        "phase": 5,
        "attributes": {"body": 2, "agility": 2, "spirit": 5, "insight": 4, "will": 4, "presence": 2},
        "sense_power_bonus": 2,
        "sense_precision_bonus": 3,
        "sense_range_bonus": 10,
        "concealment_bonus": 1,
        "concealment_active": 1 if conceal else 0,
    }


class SenseModuleTests(unittest.TestCase):
    def test_core_sense_rules_are_used_directly(self):
        c = _character(conceal=True)
        self.assertGreater(spiritual_sense_stats(c)["range_m"], 0)
        self.assertGreaterEqual(concealment_power(c), 0)
        direct = sense_precision_check(c, die1=7, die2=8, target_realm_index=4, extra_tn=2)
        self.assertIn("tier", direct)



if __name__ == "__main__":
    unittest.main()

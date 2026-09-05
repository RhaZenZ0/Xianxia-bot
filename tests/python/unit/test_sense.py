from tests.support import PROJECT_ROOT
import unittest
from pathlib import Path

from app.rules.game import World
from app.rules.sense import (
    concealment_power, hidden_npc_names, sense_hidden_npc, sense_precision_check, spiritual_sense_stats,
)


WORLD = World(PROJECT_ROOT / "content" / "world.json")


def character(realm=1, stage=3, *, conceal=False):
    return {
        "realm_index": realm,
        "phase": stage,
        "body_realm_index": realm,
        "body_phase": stage,
        "attributes": {"body": 2, "agility": 2, "spirit": 4, "insight": 4, "will": 3, "presence": 2},
        "sense_power_bonus": 0,
        "sense_precision_bonus": 0,
        "sense_range_bonus": 0,
        "concealment_bonus": 0,
        "concealment_active": 1 if conceal else 0,
        "gender": "neutral",
        "path": "Qi Refiner",
        "spiritual_root": "Lightning",
    }


def character_with_precision_bonus(realm=1, stage=3, bonus=0):
    data = character(realm, stage)
    data["sense_precision_bonus"] = bonus
    return data


class SpiritualSenseTests(unittest.TestCase):
    def test_sense_stats_grow_with_realm(self):
        low = spiritual_sense_stats(character(1, 3))
        high = spiritual_sense_stats(character(5, 3))
        self.assertGreater(high["power"], low["power"])
        self.assertGreater(high["precision"], low["precision"])
        self.assertGreater(high["range_m"], low["range_m"])

    def test_concealment_toggle_matters(self):
        plain = concealment_power(character(conceal=False))
        hidden = concealment_power(character(conceal=True))
        self.assertGreater(hidden, plain)

    def test_fake_hidden_master_can_be_exposed(self):
        result = sense_hidden_npc(character(8, 9), "Old Gou", 100, hidden_masters=WORLD.hidden_masters, realm_name=WORLD.realm_name, realm_world=WORLD.realm_world)
        self.assertEqual(result["kind"], "fake")
        self.assertEqual(result["reveal"], "fake")

    def test_real_hidden_master_resists_junior(self):
        result = sense_hidden_npc(character(1, 3), "Old Beggar Chen", 25, hidden_masters=WORLD.hidden_masters, realm_name=WORLD.realm_name, realm_world=WORLD.realm_world)
        self.assertEqual(result["kind"], "real")
        self.assertEqual(result["reveal"], "none")

    def test_precision_bonus_improves_detail_check(self):
        base = sense_precision_check(
            character_with_precision_bonus(3, 5, 0),
            die1=6,
            die2=7,
            target_realm_index=3,
        )
        improved = sense_precision_check(
            character_with_precision_bonus(3, 5, 12),
            die1=6,
            die2=7,
            target_realm_index=3,
        )
        self.assertEqual(base["tn"], improved["tn"])
        self.assertEqual(improved["total"] - base["total"], 12)
        self.assertGreater(improved["margin"], base["margin"])


if __name__ == "__main__":
    unittest.main()

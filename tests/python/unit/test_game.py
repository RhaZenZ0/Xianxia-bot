from tests.support import PROJECT_ROOT
import unittest
from pathlib import Path

from app.rules.game import World, roll_2d10


WORLD = World(PROJECT_ROOT / "content" / "world.json")


class FixedD10Source:
    def __init__(self, *values):
        self.values = iter(values)

    def d10(self):
        return next(self.values)


class GameRulesTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(WORLD.normalize_path("sword"), "Sword Cultivator")
        self.assertEqual(WORLD.normalize_path("Formation Adept"), "Formation Adept")
        self.assertEqual(WORLD.normalize_root("lightning"), "Lightning")

    def test_qi_realm_progression(self):
        self.assertEqual(len(WORLD.realms), 32)
        self.assertEqual(WORLD.realm_name(0), "Body Tempering")
        self.assertEqual(WORLD.phase_cost(0, 1), 50)
        self.assertEqual(WORLD.next_stage(0, 4), (0, 5))
        self.assertEqual(WORLD.next_stage(0, 9), (1, 1))

    def test_body_realm_progression(self):
        self.assertEqual(len(WORLD.body_realms), len(WORLD.realms))
        self.assertEqual(WORLD.body_realm_name(0), "Skin Tempering")
        self.assertEqual(WORLD.body_next_stage(0, 9), (1, 1))
        self.assertEqual(WORLD.body_perfection_training_cap(), 20)
        self.assertEqual(sum(WORLD.body_perfection["quest_progress"]), 80)
        self.assertEqual(WORLD.body_perfection_quest_count(), 7)
        self.assertGreaterEqual(WORLD.body_perfection_total_preparation(), 30)

    def test_gendered_titles_are_cosmetic(self):
        self.assertEqual(WORLD.realm_name(25, "male"), "Celestial Duke")
        self.assertEqual(WORLD.realm_name(25, "female"), "Celestial Duchess")
        self.assertEqual(WORLD.body_realm_name(25, "male"), "Celestial Body Duke")
        self.assertEqual(WORLD.body_realm_name(25, "female"), "Celestial Body Duchess")

    def test_dual_cultivation_resonance(self):
        c = {"realm_index": 3, "phase": 4, "body_realm_index": 3, "body_phase": 4}
        self.assertTrue(WORLD.dual_resonance_active(c))
        self.assertEqual(WORLD.dual_resonance_check_bonus(c), 1)
        self.assertEqual(WORLD.dual_resonance_training_bonus(c, 20), 2)
        c["body_phase"] = 3
        self.assertFalse(WORLD.dual_resonance_active(c))

    def test_perfect_realm_content(self):
        self.assertEqual(WORLD.perfection_training_cap(), 20)
        self.assertEqual(sum(WORLD.perfection["quest_progress"]), 80)
        self.assertEqual(WORLD.perfection_quest_count(), 7)
        self.assertGreaterEqual(WORLD.perfection_total_preparation(), 30)
        character = {"path": "Sword Cultivator", "spiritual_root": "Lightning"}
        q0 = WORLD.perfection_quest(1, 0, character)
        self.assertGreaterEqual(q0["preparation_required"], 3)
        self.assertEqual(q0["progress_reward"], 8)

    def test_secret_realms_and_inheritances(self):
        self.assertGreaterEqual(len(WORLD.secret_realm_choices()), 3)
        self.assertIn("verdant_immortal_grotto", WORLD.secret_realms)
        self.assertGreaterEqual(WORLD.secret_room("verdant_immortal_grotto", 0)["tn"], 10)
        inheritance_id = WORLD.secret_realms["verdant_immortal_grotto"]["inheritance_id"]
        self.assertIn(inheritance_id, WORLD.inheritances)
        self.assertGreaterEqual(len(WORLD.unexpected_events), 6)

    def test_roll_shape(self):
        r = roll_2d10(3, 14)
        self.assertTrue(1 <= r.die1 <= 10)
        self.assertTrue(1 <= r.die2 <= 10)
        self.assertEqual(r.total, r.die1 + r.die2 + 3)

    def test_roll_source_is_injectable_for_deterministic_checks(self):
        r = roll_2d10(3, 14, source=FixedD10Source(10, 6))
        self.assertEqual((r.die1, r.die2, r.total, r.margin, r.degree), (10, 6, 19, 5, "Strong Success"))


if __name__ == "__main__":
    unittest.main()

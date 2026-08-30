import unittest
from unittest.mock import patch

from app.samsara import choose_samsara_world, reincarnation_scale, soul_legacy_modifiers


class SamsaraAndLifespanTests(unittest.TestCase):
    def test_higher_cultivation_expands_samsara_time_but_wait_is_capped(self):
        low = reincarnation_scale({"realm_index": 1, "phase": 1, "body_realm_index": 0, "body_phase": 1}, base_samsara_years=320, max_wait_seconds=300)
        high = reincarnation_scale({"realm_index": 31, "phase": 9, "body_realm_index": 0, "body_phase": 1}, base_samsara_years=320, max_wait_seconds=300)
        self.assertGreater(high["years"], low["years"])
        self.assertLessEqual(high["wait_seconds"], 300)
        self.assertGreaterEqual(low["wait_seconds"], 45)

    def test_mortal_soul_can_roll_mortal_rebirth(self):
        with patch("app.samsara.secrets.randbelow", return_value=0):
            self.assertEqual(choose_samsara_world(0, 0), "Mortal World")

    def test_peak_celestial_soul_can_roll_celestial_rebirth(self):
        # Peak Celestial base weights sum to 100 and the final 85 points are Celestial.
        with patch("app.samsara.secrets.randbelow", return_value=99):
            self.assertEqual(choose_samsara_world(31, 0), "Celestial World")

    def test_soul_legacy_modifiers_remain_bounded(self):
        mods = soul_legacy_modifiers({"talent_echo": 100, "law_echo": 100, "memory_seed": 100, "special_trait": "Heaven-Defying Fate"})
        self.assertLessEqual(float(mods["cultivation_mult"]), 1.15)
        self.assertLessEqual(int(mods["law_bonus"]), 5)
        self.assertLessEqual(float(mods["insight_mult"]), 1.05)


if __name__ == "__main__":
    unittest.main()

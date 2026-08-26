import unittest
from unittest.mock import patch

from app.lifespan import (
    IMMORTAL_REALM_INDEX,
    realm_lifespan_ceiling,
    realm_lifespan_range,
    status as lifespan_status,
)
from app.samsara import choose_samsara_world, reincarnation_scale, soul_legacy_modifiers


class SamsaraAndLifespanTests(unittest.TestCase):
    def test_lifespan_stage_interpolates_across_realm_range(self):
        self.assertEqual(realm_lifespan_ceiling(2, 1, 75), 200)
        self.assertEqual(realm_lifespan_ceiling(2, 9, 75), 500)
        mid = realm_lifespan_ceiling(2, 5, 75)
        self.assertGreater(mid, 200)
        self.assertLess(mid, 500)

    def test_true_immortal_and_above_are_ageless(self):
        self.assertIsNone(realm_lifespan_range(IMMORTAL_REALM_INDEX))
        self.assertIsNone(realm_lifespan_ceiling(IMMORTAL_REALM_INDEX, 1, 75))
        s = lifespan_status({
            "realm_index": IMMORTAL_REALM_INDEX,
            "phase": 1,
            "body_realm_index": 0,
            "body_phase": 1,
            "natural_lifespan_years": 75,
            "life_extension_years": 0,
            "created_game_minute": 0,
            "age_at_creation_years": 18,
        }, 10_000_000)
        self.assertTrue(s.ageless)
        self.assertIsNone(s.total_years)
        self.assertIsNone(s.remaining_years)

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

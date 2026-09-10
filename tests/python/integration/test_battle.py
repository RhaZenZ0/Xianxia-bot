import unittest

from tests.support import install_aiosqlite_shim

install_aiosqlite_shim()

from app.rules.battle import matchup_label, suppression_label, vitality_band, vitality_bar, vitality_percentage


class BattlePresentationTests(unittest.TestCase):
    def test_vitality_bars_include_color_counts_and_percentages(self):
        self.assertEqual(vitality_percentage(8, 10), 80)
        self.assertIn("🟩", vitality_bar(8, 10))
        self.assertIn("8/10", vitality_bar(8, 10))
        self.assertIn("80%", vitality_bar(8, 10))
        self.assertEqual(vitality_band(5, 10)[0], "🟨")
        self.assertEqual(vitality_band(1, 10)[0], "🟥")

    def test_matchup_and_suppression_labels_cover_battle_context(self):
        self.assertIn("advantage", matchup_label(3, 5, 1, 5).lower())
        self.assertIn("Evenly", matchup_label(2, 4, 2, 4))
        self.assertEqual(suppression_label(0), "None")
        self.assertIn("2 counters", suppression_label(2))


if __name__ == "__main__":
    unittest.main()

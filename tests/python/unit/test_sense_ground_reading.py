"""The line a spiritual-sense sweep prints about the ground it is standing on.

The engine decides how much a sweep learned; this only chooses the words, so
what is pinned here is the ladder - a weak sweep gets the word for the ground,
a better one what is making it so, an overwhelming one the numbers - and that a
sweep which read nothing prints nothing rather than a confident "ordinary".

The numbers themselves are Go's: `placeCultivationMultiplier` has priced the
ground since v1.0.0-rc.4 and the sweep reads that same value, so a player is
never told one thing and paid another. That agreement is held in Go, by
TestSenseGroundReadingTracksTheCultivationMultiplier, not re-derived here.
"""
from __future__ import annotations

import unittest

from app.rules.sense import ground_reading_line


class TheGroundReadingFollowsWhatTheSweepEarned(unittest.TestCase):
    def test_nothing_read_prints_nothing(self):
        for empty in (None, {}):
            self.assertEqual(ground_reading_line(empty), "")

    def test_a_vague_sweep_names_the_quality_and_admits_it_cannot_say_why(self):
        line = ground_reading_line({"quality": "rich", "detail": "vague"})
        self.assertIn("rich", line)
        self.assertIn("cannot tell", line)
        self.assertNotIn("×", line)

    def test_a_named_sweep_says_what_is_gathering_the_qi(self):
        line = ground_reading_line(
            {"quality": "good", "detail": "named", "ground": "the shrine"}
        )
        self.assertIn("good", line)
        self.assertIn("the shrine", line)
        self.assertNotIn("×", line)

    def test_a_named_sweep_on_nameless_ground_still_reads_cleanly(self):
        # Most ground is nothing in particular, and the engine sends "" for it.
        line = ground_reading_line({"quality": "ordinary", "detail": "named", "ground": ""})
        self.assertTrue(line.endswith("."), line)
        self.assertNotIn("gathered by", line)

    def test_an_exact_sweep_gives_the_multiplier_and_the_world(self):
        line = ground_reading_line({
            "quality": "rich", "detail": "exact", "ground": "Cloudrest Cave",
            "multiplier": 1.45, "world_qi": 1.6,
        })
        self.assertIn("×1.45", line)
        self.assertIn("×1.6", line)
        self.assertIn("Cloudrest Cave", line)

    def test_the_multiplier_is_printed_without_trailing_zeroes(self):
        line = ground_reading_line({
            "quality": "ordinary", "detail": "exact", "ground": "", "multiplier": 1.0, "world_qi": 1.0,
        })
        self.assertIn("×1**", line)
        self.assertNotIn("1.0", line)


if __name__ == "__main__":
    unittest.main()

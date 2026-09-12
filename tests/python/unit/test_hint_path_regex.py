"""The hint-path pattern must stay linear.

`_HINT_PATH_RE` scans every reply the bot sends for hub paths it can turn
into buttons (`**/world → City → Look**`). Its first form paired `\\s*` with a
character class that also matches whitespace, so a run of N spaces could be
split N ways and the pattern went quadratic on text that never completes a
match — and the text it scans is player-reachable, so a single message could
stall the event loop. CodeQL called it an inefficient regular expression; it
took 29 seconds on three thousand spaces.
"""
from __future__ import annotations

import os
import re
import time
import unittest
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _pattern():
    with patch.dict(os.environ, ENV):
        import importlib

        return importlib.import_module("app.bot.hubs")._HINT_PATH_RE


class TheHintPatternIsUnambiguous(unittest.TestCase):
    def test_it_still_reads_every_shape_of_hint(self):
        pattern = _pattern()
        for text, expected in (
            ("**/world → City → Look**", [("world", " → City → Look")]),
            ("**/cultivation → Qi Body → Refine**", [("cultivation", " → Qi Body → Refine")]),
            ("**/abode → Upgrade**", [("abode", " → Upgrade")]),
            ("**/menu**", [("menu", "")]),
            ("a line with **/world → City → Look** inside", [("world", " → City → Look")]),
            ("two **/menu** and **/sheet**", [("menu", ""), ("sheet", "")]),
        ):
            with self.subTest(text=text):
                self.assertEqual([(m.group(1), m.group(2)) for m in pattern.finditer(text)], expected)

    def test_the_step_list_is_one_flat_class_with_nothing_to_backtrack_over(self):
        """The structural property rather than the timing: no repeated group
        at all, so there is no pair of quantifiers to split text between."""
        source = _pattern().pattern
        self.assertNotIn("(?:", source, "a repeated group is what made this backtrack")
        self.assertIn("[^*]*", source, "the step list is one class that cannot cross the closing **")

    def test_neither_attack_shape_takes_any_time(self):
        pattern = _pattern()
        for name, evil in (
            # Splitting spaces between adjacent repetitions: exponential.
            ("arrow repetitions", "**/a" + "\u2192" + ") \u2192" * 26),
            # Splitting one run of spaces N ways: quadratic.
            ("run of spaces", "**/world \u2192 " + " " * 4000),
        ):
            with self.subTest(attack=name):
                started = time.perf_counter()
                self.assertIsNone(pattern.search(evil))
                self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()

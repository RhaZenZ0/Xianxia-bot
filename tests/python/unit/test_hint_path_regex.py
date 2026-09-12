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

    def test_no_repetition_can_split_a_run_of_spaces_two_ways(self):
        """The structural property, not the timing: every repetition of the
        step group must be anchored by a literal arrow, which neither `\\s`
        nor the class can match."""
        source = _pattern().pattern
        self.assertIn("→", source)
        self.assertNotIn(r"→\s*[^*→]+", source, "the arrow's trailing \\s* reintroduces the ambiguity")

    def test_text_that_never_completes_a_match_returns_promptly(self):
        pattern = _pattern()
        evil = "**/world → " + " " * 4000
        started = time.perf_counter()
        self.assertIsNone(pattern.search(evil))
        # The old pattern took ~29s on 3000 spaces and grows quadratically.
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()

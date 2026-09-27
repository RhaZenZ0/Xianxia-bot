"""Good deeds are worth karma, and the reply says so (v1.9.1).

The engine pays it and caps it (`karma_deeds.go`, held by
`karma_deeds_test.go`); this holds the line every reply prints and that each
of the four places a deed happens prints it.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import pytest

from app.rules.advanced_runtime import deed_karma_line

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SURFACES = {
    "app/bot/ui/event_scene.py": 2,    # a world-event action and an engage
    "app/bot/commands/battle.py": 1,   # an event battle that takes a site's last beast
    "app/bot/commands/scene.py": 1,    # the Resolve scene action
    "app/bot/commands/exploration.py": 1,  # a personal event helped to its end
}


class TheLine(unittest.TestCase):
    def test_a_paid_deed_says_what_and_why(self):
        line = deed_karma_line({"deed": "event_site_cleared", "karma_delta": 3, "karma_score": 12})
        self.assertEqual(line, "☯️ Karma **+3** for clearing the last of the site → **+12**")

    def test_a_deed_that_paid_nothing_prints_nothing(self):
        self.assertEqual(deed_karma_line(None), "")
        self.assertEqual(deed_karma_line({"deed": "scene_resolve", "karma_delta": 0}), "")

    def test_every_deed_the_engine_names_has_words(self):
        go = (ROOT / "go_core" / "internal" / "game").glob("*.go")
        named = set()
        for path in go:
            if path.name.endswith("_test.go"):
                continue
            text = path.read_text(encoding="utf-8")
            for deed in ("world_event_good_deed", "scene_resolve", "personal_event_helped", "event_site_cleared"):
                if f'"{deed}"' in text:
                    named.add(deed)
        self.assertEqual(len(named), 4, f"the engine names {sorted(named)}; the reader is broken, not the tree")
        for deed in named:
            self.assertNotIn("a good deed", deed_karma_line({"deed": deed, "karma_delta": 1}),
                             f"{deed} has no words of its own")


class EveryDeedIsPrinted(unittest.TestCase):
    def test_each_surface_prints_the_line(self):
        for rel, want in SURFACES.items():
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and getattr(n.func, "id", "") == "deed_karma_line"]
            self.assertGreaterEqual(len(calls), want, f"{rel} does not print the karma a good deed paid")


if __name__ == "__main__":
    unittest.main()

"""Cultivating by a method practises it, and the button says what it is (v1.11.0).

Asked as *"How can we practice a manual"*, and the answer had two faults in it.
The leaf called **Practise** (`/manual practise`) chose which manual a
cultivator gathers by and added no practice at all; and gathering by a manual
never raised its mastery, so weeks of sessions left a method exactly as
mastered as the day it was read. Mastery rose only by studying the manual again
or by fighting with its techniques.

The engine half - a session that gathers adds a point to the manual it was
cultivated by, and a full stage adds none - is held in Go by
`TestCultivatingByAMethodPractisesIt`.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import pytest

from app.rules.advanced_runtime import manual_practice_line

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
LAW = ROOT / "app" / "bot" / "commands" / "law.py"
CULTIVATION = ROOT / "app" / "bot" / "commands" / "cultivation.py"
GO_TRAIN = ROOT / "go_core" / "internal" / "game" / "cultivation_actions.go"
LEVELS = ("Learned", "Practiced", "Proficient", "Mastered", "Perfected")


class TheSessionSaysWhatItPractised(unittest.TestCase):
    def test_a_practised_session_prints_the_new_total(self):
        line = manual_practice_line({"manual_name": "Iron Canon", "gain": 1, "practice": 5, "mastery": 1}, LEVELS)
        self.assertEqual(line, "📖 Practice in **Iron Canon** +1 → 5")

    def test_a_level_reached_is_named(self):
        line = manual_practice_line({"manual_name": "Iron Canon", "gain": 1, "practice": 8, "mastery": 2,
                                     "mastery_rose": True}, LEVELS)
        self.assertTrue(line.endswith("• it reaches **Proficient**"), line)

    def test_nothing_practised_prints_nothing(self):
        self.assertEqual(manual_practice_line(None, LEVELS), "")
        self.assertEqual(manual_practice_line({}, LEVELS), "")

    def test_the_cultivate_reply_reads_it(self):
        tree = ast.parse(CULTIVATION.read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "manual_practice_line"]
        self.assertTrue(calls, "the /cultivate reply no longer prints the practice a session added")
        self.assertIn("manual_practice", ast.unparse(calls[0].args[0]))

    def test_the_engine_still_reports_it(self):
        go = GO_TRAIN.read_text(encoding="utf-8")
        self.assertIn('payload["manual_practice"] = practised', go)


class TheButtonSaysWhatItDoes(unittest.TestCase):
    def test_the_leaf_is_cultivate_by_and_practise_is_gone(self):
        tree = ast.parse(LAW.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "registered_group_command":
                if node.args and ast.unparse(node.args[0]) == "manual_group":
                    names |= {k.value.value for k in node.keywords if k.arg == "name" and isinstance(k.value, ast.Constant)}
        self.assertIn("study", names, "the reader found no manual commands; it is broken, not the tree")
        self.assertIn("cultivate_by", names)
        self.assertNotIn("practise", names, "a button called Practise that practises nothing is back")


if __name__ == "__main__":
    unittest.main()

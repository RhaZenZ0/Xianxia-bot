"""An ordinary explore finds the missing (v1.22.1).

The engine searches the ground around an explore - the explorer's whole city and the road
sites and wilds around it, never the next city - and reports who it found
(`found_npcs`) and which graves it reached (`found_graves`); the Go tests hold
that half (`explore_search_test.go`). This holds the reply: it prints both,
and it prints them where the discovery block cannot overwrite them - that
block assigns `discovery_text` rather than appending to it.
"""
from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "123456789012345678",
    "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
    "DATABASE_PATH": "data/test.sqlite3",
}
SOURCE = PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py"


class TheReplySaysWhoWasFound(unittest.TestCase):
    def _lines(self, outcome: dict) -> str:
        with patch.dict(os.environ, ENV):
            return importlib.import_module("app.bot.commands.exploration").search_lines(outcome)

    def test_a_found_person_is_named(self):
        text = self._lines({"found_npcs": [
            {"npc_name": "Lost Herbalist Mei", "days_missing": 12, "home_location": "Moonfen City",
             "location": "Shrine of the Patient Ox"}]})
        self.assertIn("Lost Herbalist Mei", text)
        self.assertIn("Shrine of the Patient Ox", text, "a search reaches beyond where you stand, so say where")
        self.assertIn("12", text)
        self.assertIn("Moonfen City", text)

    def test_a_grave_names_what_was_carried(self):
        text = self._lines({"found_graves": [
            {"npc_name": "Buried Lu", "days_missing": 64, "home_location": "Moonfen City",
             "location": "Sunken Bell Ruin", "keepsake_item": "spirit_herb", "keepsake_stones": 7}]})
        self.assertIn("Buried Lu", text)
        self.assertIn("Sunken Bell Ruin", text)
        self.assertIn("7 spirit stones", text)

    def test_nothing_found_says_nothing(self):
        self.assertEqual(self._lines({}), "")
        self.assertEqual(self._lines({"found_npcs": None, "found_graves": None}), "")


class TheLinesAreNotOverwritten(unittest.TestCase):
    def test_the_lines_are_added_after_the_last_assignment(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        explore = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "explore")
        assigned, added = [], []
        for node in ast.walk(explore):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "discovery_text" for t in node.targets):
                assigned.append(node.lineno)
            if (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
                    and node.target.id == "discovery_text" and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == "search_lines"):
                added.append(node.lineno)
        self.assertTrue(assigned, "found no assignment to discovery_text; the reader is broken, not the tree")
        self.assertEqual(len(added), 1, "the explore reply does not print what the search found")
        self.assertGreater(added[0], max(assigned), (
            "the search's lines are added before a later `discovery_text = ...`, which drops them "
            "whenever the explore also charts a route"))


if __name__ == "__main__":
    unittest.main()

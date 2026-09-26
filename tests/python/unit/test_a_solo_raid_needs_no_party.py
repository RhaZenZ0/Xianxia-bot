"""A lone cultivator raids without making a party first (v1.7.8).

The engine makes a party of one for the raid when the caller has none, marks it
``raid_only`` and closes it when the raid ends (``boss_solo_test.go`` holds all
of that). This holds the bot's half: the start reply tells the player it
happened, from the engine's own ``solo_party`` flag rather than a guess of its
own, and the flag is one the engine really sends.
"""

from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

BOSS = PROJECT_ROOT / "app" / "bot" / "commands" / "boss.py"
GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "group_combat_actions.go"


def _boss_start_reads() -> set[str]:
    tree = ast.parse(BOSS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "boss_start":
            keys = set()
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and getattr(call.func, "attr", "") == "get"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                ):
                    keys.add(str(call.args[0].value))
            return keys
    return set()


class ASoloRaidNeedsNoParty(unittest.TestCase):
    def test_the_reader_finds_the_reply(self):
        self.assertIn("encounter_id", _boss_start_reads(), "boss_start could not be read; the gate is broken, not the tree")

    def test_the_reply_says_a_party_was_formed(self):
        self.assertIn("solo_party", _boss_start_reads(), "the start reply never tells a lone cultivator a party was made for them")

    def test_the_engine_sends_the_flag_it_reads(self):
        self.assertIn('"solo_party": soloParty', GO.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

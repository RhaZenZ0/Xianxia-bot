"""A dead keeper's stall card comes down at every door a death comes through (v1.37.0).

The engine closes the stall in the same transaction as the death (closeStallTx
through the leaving door, go_core/internal/game/death_escrow.go), and the card
that names it stays in the market channel until the bot takes it down. The bot
has three doors a death comes through - old age in `require_character`, a defeat
in the two battle handlers - and each ends in `_record_true_death_history`, which
refreshes the card with the guild it is given. This gate reads the class: any
function in the bot that handles a death result must end there, and every call
must carry the guild, so the next door added cannot leave a card standing.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
RUNTIME = (BOT / "runtime.py").read_text(encoding="utf-8")
DEATH_KEYS = {"true_death", "lifecycle.true_death"}
WRITER = "_record_true_death_history"


def _functions(source: str):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _calls(fn: ast.AST, name: str) -> list[ast.Call]:
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute) and target.attr == name:
                out.append(node)
            elif isinstance(target, ast.Name) and target.id == name:
                out.append(node)
    return out


def _names_a_death(fn: ast.AST) -> bool:
    return any(isinstance(n, ast.Constant) and n.value in DEATH_KEYS for n in ast.walk(fn))


class TheWriterTakesTheCardDown(unittest.TestCase):
    def test_the_death_writer_takes_the_guild_and_refreshes_the_card(self):
        writer = next(fn for fn in _functions(RUNTIME) if fn.name == WRITER)
        self.assertIn("guild", [a.arg for a in writer.args.args],
                      f"{WRITER} no longer takes the guild, so it cannot find the market channel")
        self.assertTrue(_calls(writer, "refresh_stall"),
                        f"{WRITER} never refreshes the stall card: a dead keeper's card stands until the next tick's sync, "
                        "if a tick comes")


class EveryDeathDoorEndsAtTheWriter(unittest.TestCase):
    def _doors(self):
        for path in sorted(BOT.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for fn in _functions(source):
                if fn.name == WRITER or not _names_a_death(fn):
                    continue
                yield path.relative_to(PROJECT_ROOT), fn

    def test_the_reader_finds_the_three_doors(self):
        doors = {(str(p), fn.name) for p, fn in self._doors()}
        self.assertGreaterEqual(len(doors), 3, f"the door reader found {sorted(doors)}; the gate is broken, not the tree")
        self.assertIn(("app/bot/runtime.py", "require_character"), doors)

    def test_each_door_records_the_death_with_its_guild(self):
        for path, fn in self._doors():
            with self.subTest(door=f"{path}:{fn.name}"):
                calls = _calls(fn, WRITER)
                self.assertTrue(calls, f"{fn.name} handles a death and never calls {WRITER}: the history and the card are both missed")
                for call in calls:
                    passed = len(call.args) + len(call.keywords)
                    self.assertEqual(passed, 4, f"{fn.name} calls {WRITER} with {passed} argument(s); the guild is the fourth, "
                                                "and without it the stall card is never found")


if __name__ == "__main__":
    unittest.main()

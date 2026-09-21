"""`roll_line` is handed a roll, never a result that has none (v1.0.3).

`/craft` raised `AttributeError: 'types.SimpleNamespace' object has no attribute
'die1'` on every craft that got past the materials check. `_run_crafting` built
`result = SimpleNamespace(**resolved)` - the engine result, which carried the
*flattened* `d1`/`d2` and no `degree` - and passed that to `roll_line`, which
reads `die1`, `die2` and `degree`.

The raise landed *after* `applyAuthoritative` had committed, so the materials
were spent, the pill was granted and the profession XP credited, and the player
was shown a wiring failure and told nothing had happened. Reported from live
play as "it doesn't let you craft but also takes your items" - by a cultivator
whose bag held six of the pills they had been told they never made.

v1.0.1 found and fixed exactly this for the forage reply and did not carry it to
the craft forty lines above. Neither harness could see it: the engine half
asserts on the result map and never renders a reply, and `scripts/playtest_discord.py`
names `craft` nowhere, so its generic leaf sweep only ever reached the designed
"missing materials" refusal.

**There are two right ways to ship a roll and this file only guards the seam.**
An action may merge the roll map into its result (`for k, v := range roll`, which
`beastTameAction` and `pvpActAction` do) or carry it nested under `"roll"`
(`forageResolveAction`, and now the craft). Craft did neither. The general rule -
a result that reports dice reports the whole roll - is stated once, in Go, by
`TestAResultThatReportsARollReportsTheWholeRoll`.

A first version of this file swept every `roll_line` call site instead and
reported three findings that were not findings, all of them actions that merge
the roll. Two statements of one rule are free to disagree and the weaker one
produces the false findings, which is the call v1.0.1 made about its own
table-level sweep; so the sweep is gone and what is left is the regression and
the reader it depends on.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
CRAFTING = APP / "bot" / "commands" / "exploration.py"


def _function(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """By AST, because a multi-line `def` defeats an indentation slice (rc.59)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone; this gate is guarding a function that moved")


class ACraftPrintsItsOwnRollTests(unittest.TestCase):
    def setUp(self) -> None:
        self.crafting = _function(CRAFTING.read_text(encoding="utf-8"), "_run_crafting")

    def test_the_craft_reply_builds_its_roll_from_the_roll_map(self):
        built = [
            target.id
            for node in ast.walk(self.crafting)
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            for target in [node.targets[0]]
            if isinstance(target, ast.Name)
            and isinstance(node.value, ast.Call)
            # ast.unparse normalises string quotes to single ones.
            and "'roll'" in ast.unparse(node.value)
        ]
        self.assertTrue(
            built,
            "_run_crafting no longer reads the engine's `roll` map. Passing the whole result "
            "raises AttributeError on `die1` *after* the craft has committed: the materials are "
            "spent, the output granted, and the player is told it failed.",
        )
        printed = [
            node.args[0].id
            for node in ast.walk(self.crafting)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "roll_line" and node.args and isinstance(node.args[0], ast.Name)
        ]
        self.assertTrue(printed, "the sweep found no roll_line call in _run_crafting; it is broken, not the tree")
        for name in printed:
            self.assertIn(
                name, built,
                f"_run_crafting prints roll_line({name}), and {name} is not built from the engine's `roll` map",
            )

    def test_roll_line_still_reads_what_this_gate_thinks_it_does(self):
        """If `roll_line` stopped reading `die1`/`degree` the rule above would be
        guarding nothing, and would keep passing while it did."""
        source = (APP / "bot" / "formatting.py").read_text(encoding="utf-8")
        body = ast.unparse(_function(source, "roll_line"))
        for attribute in ("die1", "die2", "degree"):
            self.assertIn(
                f"result.{attribute}", body,
                f"roll_line no longer reads {attribute}; this gate is guarding a rule that moved",
            )


if __name__ == "__main__":
    unittest.main()

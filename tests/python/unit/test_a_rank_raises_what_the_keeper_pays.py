"""A rank in a trade raises what a keeper pays for its goods (v1.0.17).

The engine owns the price: `tradeSellQuoteTx` in `go_core/internal/game/
trade_rank_price.go` adds two of the shop's coin per rank above Novice and
never reaches the cheapest shelf price, and `trade_rank_price_test.go` holds
that. This holds the presentation half: the Browse board and the Sell reply
say which rank raised the price, and say nothing when none did.
"""

from __future__ import annotations

import ast
import pathlib
import unittest
from typing import Any

import pytest

from app.rules.progression_systems import profession_rank

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[3]
ECONOMY = ROOT / "app" / "bot" / "commands" / "economy.py"


def _functions() -> dict[str, ast.AST]:
    tree = ast.parse(ECONOMY.read_text(encoding="utf-8"))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _rank_lift():
    """The helper compiled from its own source, without booting the bot."""
    node = _functions().get("_rank_lift")
    if node is None:
        return None
    namespace: dict[str, Any] = {"Any": Any, "profession_rank": profession_rank}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ECONOMY), "exec"), namespace)
    return namespace["_rank_lift"]


class TheKeeperSaysWhyItPaidMore(unittest.TestCase):
    def test_the_reader_finds_the_helper(self) -> None:
        self.assertIsNotNone(_rank_lift(), "_rank_lift is gone from economy.py; the gate is broken, not the tree")

    def test_a_rank_is_named_with_what_anybody_gets(self) -> None:
        lift = _rank_lift()
        row = {"trade": "Alchemy", "trade_rank": 2, "base_price": 4, "price": 8}
        board = lift(row)
        self.assertIn("Grade 2", board)
        self.assertIn("Alchemy", board)
        self.assertIn("4", board)
        sale = lift(row, sentence=True)
        self.assertIn("Grade 2", sale)
        self.assertIn("lifts it from 4", sale)

    def test_nothing_is_said_when_no_rank_raised_it(self) -> None:
        lift = _rank_lift()
        self.assertEqual(lift({"trade": "", "trade_rank": 0, "base_price": 1}), "")
        self.assertEqual(lift({}), "")

    def test_the_board_and_the_sale_both_say_it(self) -> None:
        functions = _functions()
        for name in ("shop_browse", "shop_sell"):
            calls = {
                getattr(node.func, "id", "")
                for node in ast.walk(functions[name])
                if isinstance(node, ast.Call)
            }
            self.assertIn("_rank_lift", calls, f"{name} no longer says which rank raised the keeper's price")


if __name__ == "__main__":
    unittest.main()

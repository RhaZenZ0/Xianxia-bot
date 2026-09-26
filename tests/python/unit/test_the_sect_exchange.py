"""What a sect issues its members, and how they climb (v1.8.0).

`sect_system.exchange` is content the engine acts on: the stock every sect
issues, each sect's own item, the price multiple, the earning ratios and the
promotion ladder. The engine refuses an id it does not know and a rank it
cannot name only at the moment somebody presses Redeem, so a typo here is a
button that never works rather than an error anywhere - the class `/learn` and
the peach were. This holds the shape; the rules are Go's, and their tests are
in `go_core/internal/game/sect_exchange_test.go`.
"""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
EXCHANGE = WORLD["sect_system"].get("exchange") or {}
RANKS = {int(r["level"]): r["name"] for r in WORLD["sect_system"]["ranks"]}


class TheSectExchangeIsWellFormed(unittest.TestCase):
    def test_the_reader_found_an_exchange(self):
        self.assertTrue(EXCHANGE.get("stock"), "content carries no sect exchange stock; the gate is broken, not the tree")
        self.assertGreater(int(EXCHANGE.get("points_per_sect_value") or 0), 1,
                           "an issued item must cost more points than donating it earns")

    def test_every_lot_names_a_real_item_and_a_real_rank(self):
        lots = list(EXCHANGE["stock"]) + [lot for own in EXCHANGE.get("sect_stock", {}).values() for lot in own]
        for lot in lots:
            self.assertIn(lot["item_id"], WORLD["items"], f"{lot['item_id']} is issued and does not exist")
            self.assertIn(int(lot["min_rank_level"]), RANKS, f"{lot['item_id']} waits for rank {lot['min_rank_level']}, which is no rung")
            self.assertGreater(int(WORLD["items"][lot["item_id"]].get("sect_value") or 0), 0,
                               f"{lot['item_id']} has no sect value to be priced from")

    def test_each_sects_own_item_is_its_own_alone(self):
        seen: dict[str, str] = {}
        for sect, lots in EXCHANGE.get("sect_stock", {}).items():
            self.assertIn(sect, WORLD["sects"], f"{sect} issues stock and is not a sect")
            self.assertTrue(WORLD["sects"][sect].get("recruitment"), f"{sect} is the hidden sect; its members are not on the exchange")
            for lot in lots:
                item = WORLD["items"][lot["item_id"]]
                self.assertTrue(item.get("market_excluded"), f"{lot['item_id']} is {sect}'s own and could be bought on a market")
                self.assertNotIn(lot["item_id"], seen, f"{lot['item_id']} is issued by both {seen.get(lot['item_id'])} and {sect}")
                seen[lot["item_id"]] = sect
        common = {lot["item_id"] for lot in EXCHANGE["stock"]}
        self.assertFalse(common & set(seen), "a sect's own item is also in the common stock")
        recruiting = {name for name, sect in WORLD["sects"].items() if sect.get("recruitment")}
        self.assertEqual(recruiting, set(EXCHANGE.get("sect_stock", {})), "every recruiting sect keeps one item of its own")

    def test_the_promotion_ladder_climbs(self):
        ladder = EXCHANGE.get("promotion") or []
        self.assertTrue(ladder, "no promotion ladder; a member's rank would never move")
        last_rank, last_earned = 10, 0  # a member joins as Outer Disciple with nothing earned
        for rung in ladder:
            self.assertIn(int(rung["rank_level"]), RANKS)
            self.assertGreater(int(rung["rank_level"]), last_rank)
            self.assertGreater(int(rung["earned"]), last_earned)
            last_rank, last_earned = int(rung["rank_level"]), int(rung["earned"])
        self.assertLess(last_rank, max(RANKS), "the ladder reaches the top; the sect's own offices are not earned by donating")

    def test_every_rank_that_locks_stock_can_be_earned(self):
        reachable = {10} | {int(r["rank_level"]) for r in EXCHANGE.get("promotion") or []}
        lots = list(EXCHANGE["stock"]) + [lot for own in EXCHANGE.get("sect_stock", {}).values() for lot in own]
        for lot in lots:
            self.assertIn(int(lot["min_rank_level"]), reachable,
                          f"{lot['item_id']} waits for {RANKS[int(lot['min_rank_level'])]}, which no player can earn")


class TheBotRestatesNoPrice(unittest.TestCase):
    """The treasury page and the redeem picker print the engine's numbers;
    the old page carried its own copy of the scarcity multiplier."""

    def test_no_multiplier_is_restated(self):
        tree = ast.parse((PROJECT_ROOT / "app" / "bot" / "commands" / "sect.py").read_text(encoding="utf-8"))
        floats = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, float)}
        self.assertFalse(floats & {1.60, 1.35, 1.20}, "sect.py restates the engine's redemption multiplier")
        called = {node.args[0].value for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "action"
                  and node.args and isinstance(node.args[0], ast.Constant)}
        self.assertIn("sect.exchange", called, "the treasury page does not ask the engine for the exchange")


if __name__ == "__main__":
    unittest.main()

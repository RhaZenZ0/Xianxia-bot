"""`/auction sell` lists in the house's own coin unless told otherwise (v1.2.3).

The command's default currency was the Mortal stone in every world, so a lot
listed on a Spiritual, Immortal or Celestial floor asked bidders for a coin
nobody in that world is paid in (rc.44). The default reads the house's
`default_currency`, which every one of the 48 houses in the content declares.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from tests.support import bot_function_source, code_only

ROOT = Path(__file__).resolve().parents[3]


class ALotIsListedInTheHousesCoin(unittest.TestCase):
    def test_the_default_is_not_a_currency_id(self):
        tree = ast.parse(bot_function_source("auction_sell"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "auction_sell")
        names = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
        defaults = dict(zip(names[-len(fn.args.defaults):], fn.args.defaults)) if fn.args.defaults else {}
        default = defaults.get("currency")
        self.assertIsNotNone(default, "the currency parameter could not be read; the reader is broken, not the tree")
        self.assertEqual(default.value, "", f"the default currency is {default.value!r}, a coin that is wrong in three worlds of four")
        body = code_only(ast.unparse(fn))
        self.assertIn("default_currency", body, "the handler never reads the house's default_currency")

    def test_every_house_declares_its_coin(self):
        world = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
        houses = world["auction_houses"]
        houses = houses if isinstance(houses, list) else list(houses.values())
        self.assertGreater(len(houses), 40)
        missing = [h["name"] for h in houses if not str(h.get("default_currency") or "").strip()]
        self.assertEqual(missing, [], "a house with no default_currency would list in nothing")


if __name__ == "__main__":
    unittest.main()

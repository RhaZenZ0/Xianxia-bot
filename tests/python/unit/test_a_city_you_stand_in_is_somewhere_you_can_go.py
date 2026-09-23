"""Zero hops is a distance, not a missing value (v1.0.13).

**Found by playing**: inside an apothecary, `/travel` refused with

    Travel failed: the shop door opens onto Azure Crown Imperial City

naming the one destination the engine allows from inside a shop - and the
picker did not offer it.

The engine was right and `knownLocationsTx` had the city all along: standing in
a shop, *"the city is known, its roads, and every gate and district of it"*.
What dropped it was the picker's ordering:

    rows.append((name, "🌀", …, 20 + (n or 50)))

`n` is the hop count, the city a player stands inside is **0** hops away, and
`0 or 50` is 50 - so the one place they could legally go sorted at 70, behind
every road city, and fell off the end of a 25-option Discord select.

**The rule was known and broken in the same expression.** One operand to the
left, the *label* asks `if n is not None`, which is the correct question; the
order asked whether the number was truthy. That is v1.0.1's own lesson -
*"ask whether the field is absent, never whether it is falsy"* - which that
release fixed in `playtest_engine.py` and recorded as a rule about assertions.
Here it was in production, deciding what a player is shown.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest

from tests.support import PROJECT_ROOT

LOCATIONS = PROJECT_ROOT / "app" / "bot" / "locations.py"


def _picker():
    os.environ.setdefault("DISCORD_TOKEN", "gate")
    os.environ.setdefault("GUILD_ID", "123456789012345678")
    os.environ.setdefault("ENGINE_AUTH_TOKEN", "gate-token-1234567890")
    os.environ.setdefault("DATABASE_PATH", "data/gate.sqlite3")
    os.environ.setdefault("HEALTH_PORT", "18094")
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from app.bot.locations import REALM_HUBS, destination_groups
    from app.bot.runtime import WORLD

    return destination_groups, WORLD, REALM_HUBS


class ACityYouStandInIsSomewhereYouCanGo(unittest.TestCase):
    def setUp(self):
        self.groups, self.world, self.hubs = _picker()
        # A real shop in a real capital, because a fixture city invented here
        # is exactly the shape that cannot fail the way production failed.
        self.city = "Azure Crown Imperial City"
        self.shop = next(n for n, d in self.world.locations.items()
                         if d.get("shop") and str(d.get("outside_location")) == self.city)
        self.assertTrue(self.world.locations[self.city].get("realm_hub"),
                        f"{self.city} is no longer a capital; the gate is broken, not the tree")

    def _rows(self):
        known = {self.shop, self.city}
        for name, data in self.world.locations.items():
            if data.get("district") and str(data.get("outside_location")) == self.city:
                known.add(name)
        for road in (self.world.locations[self.city].get("roads") or []):
            known.add(str(road))
        for hub in self.hubs.values():
            known.add(str(hub["location"]))
        return self.groups(self.shop, known, 0)

    def test_the_street_the_door_opens_onto_is_offered_first(self):
        rows = self._rows()
        names = [r[0] for r in rows]
        self.assertIn(self.city, names, (
            f"from inside {self.shop!r} the picker does not offer {self.city!r} at all - the one "
            "destination the engine allows from inside a shop"))
        index = names.index(self.city)
        self.assertLess(index, 25, (
            f"{self.city!r} sits at position {index} of the picker, past Discord's 25-option "
            "select, so the only legal way out of the shop is not on the list"))
        order = rows[index][3]
        elsewhere = min((r[3] for r in rows if r[0] != self.city and r[3] >= 20), default=99)
        self.assertLessEqual(order, elsewhere, (
            f"{self.city!r} is 0 road hops away and sorts at {order}, behind places {elsewhere} "
            "away. `20 + (n or 50)` read a zero hop count as a missing one"))

    def test_no_hop_count_is_read_for_truth(self):
        """The rule, not the one expression that broke it."""
        source = LOCATIONS.read_text(encoding="utf-8")
        tree = ast.parse(source)
        func = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "destination_groups")
        # Statements only, never the docstring or the comment explaining the
        # fix - both name the expression they exist to forbid (rc.52).
        body = func.body[1:] if (func.body and isinstance(func.body[0], ast.Expr)
                                 and isinstance(func.body[0].value, ast.Constant)) else func.body
        # The *operand*, not a substring of the line: the first version looked
        # for "hops" anywhere in the expression and flagged
        # `WORLD.shops.get(...) or {}`, because "shops" contains it. A needle
        # is not a reader.
        def _is_hop_count(node: ast.AST) -> bool:
            if isinstance(node, ast.Name) and node.id == "n":
                return True
            return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "hops")

        offenders = []
        for statement in body:
            for node in ast.walk(statement):
                if (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
                        and node.values and _is_hop_count(node.values[0])):
                    offenders.append(ast.unparse(node))
        self.assertFalse(offenders, (
            "a hop count is read with `or`, so 0 hops - the city a player is standing inside - is "
            f"replaced by the missing-value number and sorts to the back: {offenders}"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Two doors a player could not find (v1.11.1).

*"Are the black market discoverable?"* - the rumours refused anybody the
brokers did not already trust, and Underworld Contacts had one source, a trade
at a post, which needed that trust. So the reputation door was shut for good,
and the Hidden-Weapon family's "discreet underworld contacts" were prose. The
engine half (a broker buys from a stranger; five households send a child out
known) is held in Go by `underworld_contacts_test.go`.

*"And where is the perfect stage"* - the engine lets a Perfection begin at
stage 9 of any realm, and the panel hid the page until Soul Formation.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

import pytest

from app.rules.birthfamily import family_connections_line
from app.rules.black_market import BLACK_MARKET_ACCESS_REPUTATION, underworld_trust_line

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
ECONOMY = ROOT / "app" / "bot" / "commands" / "economy.py"
SURFACE = ROOT / "app" / "bot" / "surface.py"
GO_TRADE = ROOT / "go_core" / "internal" / "game" / "economy_actions.go"
WORLD = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _function(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found; the reader is broken, not the tree")


class TheUnderworldCanBeReached(unittest.TestCase):
    def test_the_threshold_is_the_engine_s(self):
        self.assertIn(f"blackMarketTrustReputation = int64({BLACK_MARKET_ACCESS_REPUTATION})",
                      GO_TRADE.read_text(encoding="utf-8"))

    def test_the_rumours_refuse_nobody(self):
        tree = ast.parse(ECONOMY.read_text(encoding="utf-8"))
        rumours = _function(tree, "blackmarket_rumors")
        texts = [n.value for n in ast.walk(rumours) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertTrue(any("will buy" in t for t in texts), "the rumours no longer tell a stranger the way in")
        self.assertFalse(any("does not trust you yet" in t for t in texts),
                         "the rumours refuse a stranger again, so a post can never be found")

    def test_the_sell_picker_needs_no_trust(self):
        tree = ast.parse(ECONOMY.read_text(encoding="utf-8"))
        sell = _function(tree, "blackmarket_sell_autocomplete")
        self.assertIn("selling=True", ast.unparse(sell))

    def test_a_fence_says_how_far_trust_is(self):
        line = underworld_trust_line({"access": "fencing as a stranger", "underworld_reputation": 4, "trust_reputation": 15})
        self.assertIn("11 more", line)
        trusted = underworld_trust_line({"access": "fencing as a stranger", "underworld_reputation": 15, "trust_reputation": 15})
        self.assertIn("will sell to you", trusted)
        self.assertEqual(underworld_trust_line({"access": "dark karma", "underworld_reputation": 3}), "")

    def test_the_hidden_weapon_family_is_known(self):
        rep = WORLD["birth_family_sendoff"]["hidden_weapon_family"].get("reputation") or {}
        self.assertGreaterEqual(int(rep.get("Underworld Contacts") or 0), BLACK_MARKET_ACCESS_REPUTATION)
        line = family_connections_line({"reputation": {"Underworld Contacts": 15}})
        self.assertIn("Underworld Contacts +15", line)
        self.assertEqual(family_connections_line({}), "")


class PerfectionIsOnThePanelAtStageNine(unittest.TestCase):
    def test_the_page_opens_from_realm_zero(self):
        unlocks = WORLD["feature_unlocks"]
        flat = json.dumps(unlocks)
        self.assertIn('"cultivation / Arts"', flat, "the roster read is broken, not the tree")
        self.assertNotIn('"ascend / Perfection"', flat, "the Perfection page waits for a realm again")
        self.assertNotIn('"perfect start"', flat)

    def test_start_is_drawn_at_stage_nine_and_the_rest_while_a_path_runs(self):
        tree = ast.parse(SURFACE.read_text(encoding="utf-8"))
        body = ast.unparse(_function(tree, "_progression_hidden_actions"))
        self.assertIn("body_phase != 9", body, "Start ignores the body path's stage 9 again")
        self.assertIn("shut['perfection_path']", body)
        gates = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                     and ast.unparse(n.target) == "PROGRESSION_GATES")
        self.assertIn("'perfect trial'", ast.unparse(gates.value))


if __name__ == "__main__":
    unittest.main()

"""v1.0.0-rc.2: the GM's view of v0.39, and the content it left thin.

The realm rotation rides on secret_realm.status and shows on the dashboard's
events page, in /realm status and in the inn's rumours; the trades between
cultivators are on the economy page with one audited void; exploring a
road-side site reads from lines of its own; the forty-three local auction
floors each have prose of their own.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
SERVER = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
CONTRACT = (PROJECT_ROOT / "app" / "dashboard" / "contract.py").read_text(encoding="utf-8")
APP_JS = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


def _code(source: str, name: str) -> str:
    """`_body` without the docstring - what the function *does*.

    A docstring that explains what a function no longer does names the very
    things a "this must not appear" assertion is looking for, and would fail
    the test for saying so.
    """
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    statements = node.body
    if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant) and isinstance(statements[0].value.value, str):
        statements = statements[1:]
    return "\n".join(ast.get_source_segment(source, statement) for statement in statements)


class TheRotationIsVisible(unittest.TestCase):
    def test_the_engine_puts_it_on_the_status_query(self):
        status = (GO / "secret_realm_actions.go").read_text(encoding="utf-8")
        self.assertIn('out["rotation"] = rotation', status)
        self.assertIn("SecretRealmRotationView(conn, catalog, gm)", status)

    def test_the_engine_answers_the_rotation_as_a_query_of_its_own(self):
        """v1.0.0: `secret_realm.rotation`, so a caller that is not a
        cultivator can ask the world for its schedule."""
        authoritative = (GO / "authoritative.go").read_text(encoding="utf-8")
        self.assertIn('"secret_realm.rotation":     true,', authoritative)
        self.assertIn('case "secret_realm.rotation":', authoritative)
        rotation = (GO / "secret_realm_rotation.go").read_text(encoding="utf-8")
        for field in ("last_realm_name", "last_location", "next_realm_name", "next_location", "order"):
            self.assertIn(field, rotation, f"the view must carry {field} so no caller has to look it up")

    def test_the_dashboard_asks_rather_than_working_it_out(self):
        """The rotation is the engine's rule, and the dashboard keeps no copy.

        It used to read world_state, parse the whole content pack on every
        request to turn ids into names, and repeat the interval, the catalogue
        ordering and the "which is next" arithmetic in Python. The copies had
        already drifted: on a world that had never rotated the engine answered
        "the next tick" and the dashboard "the first interval after minute
        zero". This is what stops a second copy growing back.
        """
        self.assertIn('"secret_realm_rotation": await self._secret_realm_rotation()', _body(SERVER, "events"))
        code = _code(SERVER, "_secret_realm_rotation")
        self.assertIn('self._engine.action("secret_realm.rotation", 0, {})', code)
        for copied in ("world_state", "world.json", "sorted(", "24 * 60", "% len("):
            self.assertNotIn(copied, code,
                             f"the dashboard is working the rotation out again ({copied!r})")
        self.assertIn("Secret Realm Rotation", APP_JS)
        realm = (BOT / "commands" / "secretrealm.py").read_text(encoding="utf-8")
        self.assertIn('result.get("rotation")', realm)
        self.assertIn("Next on the rotation", realm)
        rumours = _body((BOT / "commands" / "exploration.py").read_text(encoding="utf-8"), "city_rumours")
        self.assertIn('str(row.get("event_type")) != "secret_realm"', rumours)
        self.assertIn("is next to open", rumours)


class TradesOnTheDashboard(unittest.TestCase):
    def test_the_economy_page_lists_trades_and_voids_with_an_audit(self):
        self.assertIn('db, "trade_offers"', _body(SERVER, "economy"))
        self.assertIn('"trade.void": "admin.trade.void"', SERVER)
        self.assertIn('"trade_offers",', CONTRACT)
        self.assertIn("adminPost('trade.void'", APP_JS)
        self.assertIn("Trades Between Cultivators", APP_JS)
        actions = (GO / "actions.go").read_text(encoding="utf-8")
        self.assertIn('auditAdmin(conn, adminUserID, "admin.trade.void"', actions)
        self.assertIn("UPDATE trade_offers SET status='voided'", actions)


class NarrationForTheRoad(unittest.TestCase):
    def test_a_site_reads_from_its_own_lines(self):
        from app.rules.narration_pool import SCENE_KINDS, exploration_kind
        for kind in ("exploration_waystation", "exploration_hunting_ground", "exploration_ruin", "exploration_shrine"):
            self.assertIn(kind, SCENE_KINDS)
        self.assertEqual(exploration_kind(WORLD, "Shrine of the Patient Ox"), "exploration_shrine")
        self.assertEqual(exploration_kind(WORLD, "Greenriver Town"), "exploration")
        self.assertEqual(exploration_kind(WORLD, "abode:1"), "exploration")
        narrator = (PROJECT_ROOT / "app" / "ai" / "narrator.py").read_text(encoding="utf-8")
        self.assertIn('exploration_kind(self._world_data, character.get("location"))', narrator)


class TheLocalFloorsHaveTheirOwnProse(unittest.TestCase):
    def test_forty_three_floors_forty_three_paragraphs_each_naming_its_broker(self):
        houses = {k: v for k, v in WORLD["auction_houses"].items() if v.get("size") == "local"}
        self.assertEqual(len(houses), 43)
        descriptions = []
        for key, house in houses.items():
            with self.subTest(floor=key):
                interior = WORLD["locations"][house["location"]]
                text = str(interior.get("description") or "")
                self.assertGreater(len(text), 200)
                broker = next((n for n, npc in WORLD["npcs"].items() if npc.get("location") == house["location"]), "")
                self.assertTrue(broker, "a floor has a broker")
                self.assertIn(broker, text, "the floor's prose names its broker")
                self.assertIn("protection", str(house.get("description") or ""))
                descriptions.append(text)
        self.assertEqual(len(set(descriptions)), 43, "no two floors share a paragraph")
        # And no two share their opening sentence either - the archetype spine is gone.
        openings = {d.split(". ")[0] for d in descriptions}
        self.assertEqual(len(openings), 43)


if __name__ == "__main__":
    unittest.main()

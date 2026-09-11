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


class TheRotationIsVisible(unittest.TestCase):
    def test_the_engine_puts_it_on_the_status_query(self):
        status = (GO / "secret_realm_actions.go").read_text(encoding="utf-8")
        self.assertIn('out["rotation"] = rotation', status)
        self.assertIn("SecretRealmRotationView(conn, catalog, gm)", status)

    def test_the_dashboard_events_page_and_the_discord_replies_show_it(self):
        self.assertIn('"secret_realm_rotation": rotation', _body(SERVER, "events"))
        self.assertIn("SELECT value_json FROM world_state WHERE key='secret_realm_rotation'", _body(SERVER, "_secret_realm_rotation"))
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

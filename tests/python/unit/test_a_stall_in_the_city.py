"""A cultivator's own market stall in a city's street (v1.5.0).

The engine holds the rules (stall_actions.go, npc_stalls.go and their tests);
this file holds the Python half of the wiring, which is where a finished
mechanic in this tree has most often had one wire missing:

- the page reaches a player: seven leaves on one economy page, under the cap;
- the reads stay open: neither the board nor the status is in any hide, and
  both open at realm 0 on the curriculum, as does buying (the owner's "Shop");
- the trade objective is recorded by a purchase before the reply answers;
- the realm floor the panel quotes is the content's, not a copy;
- and the two erasure decisions a new person-column needs were taken.
"""
from __future__ import annotations

import ast
import json
import os
import re
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
SURFACE = PROJECT_ROOT / "app" / "bot" / "surface.py"
ECONOMY = PROJECT_ROOT / "app" / "bot" / "commands" / "economy.py"
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
STALL_LEAVES = {"stall board", "stall status", "stall open", "stall list", "stall withdraw", "stall buy", "stall close"}
READS = {"stall board", "stall status"}


def _literal(source: str, name: str):
    tree = ast.parse(source)
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(getattr(t, "id", "") == name for t in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a literal assignment")


def _hub_pages():
    with patch.dict(os.environ, ENV):
        import app.bot.surface  # noqa: F401  (registers the hubs)
        from app.bot.hubs import REGISTERED_HUBS, _leaf_actions
    return {
        (definition.name, page.label): [action.path.lstrip("/") for action in _leaf_actions(page)]
        for definition in REGISTERED_HUBS for page in definition.pages
    }


class ThePageReachesAPlayer(unittest.TestCase):
    def test_the_seven_leaves_sit_on_one_economy_page_under_the_cap(self):
        pages = _hub_pages()
        self.assertIn(("economy", "Market Stalls"), pages, sorted(k for k in pages if k[0] == "economy"))
        leaves = pages[("economy", "Market Stalls")]
        self.assertEqual(set(leaves), STALL_LEAVES, leaves)
        self.assertLessEqual(len(leaves), 8, "a page past eight rows needs a LONG_PAGES entry and a Next button")


class TheReadsStayOpen(unittest.TestCase):
    def setUp(self):
        self.source = SURFACE.read_text(encoding="utf-8")

    def test_no_gate_hides_the_board_or_the_status(self):
        hidden = set()
        for name in ("PROGRESSION_GATES", "LOCATION_GATES"):
            for leaves in _literal(self.source, name).values():
                hidden.update(leaves)
        self.assertEqual(sorted(hidden & READS), [], "a read is never hidden: a road nobody can see is a road nobody learns exists")
        self.assertTrue({"stall list", "stall withdraw", "stall close", "stall open"} <= hidden, sorted(hidden & STALL_LEAVES))

    def test_the_curriculum_opens_buying_and_the_reads_at_realm_zero(self):
        roster = dict(CONTENT.get("feature_unlocks") or {})
        leaves = dict(roster.get("leaves") or {})
        self.assertIn("stall open", leaves, "the roster read found no stall entry; the gate is broken, not the tree")
        self.assertEqual(int(dict(roster.get("pages") or {}).get("economy / Market Stalls") or 0), 2)
        for leaf in ("stall board", "stall status", "stall buy"):
            self.assertNotIn(leaf, leaves, f"{leaf!r} waits for realm {leaves.get(leaf)}; buying is the owner's Shop and a read is never held back")
        for leaf in ("stall open", "stall list", "stall withdraw", "stall close"):
            self.assertEqual(int(leaves.get(leaf) or 0), 2, leaf)

    def test_the_realm_floor_the_panel_quotes_is_the_contents(self):
        content_floor = int(dict(CONTENT.get("stall_system") or {}).get("min_realm_index") or 0)
        self.assertGreater(content_floor, 0, "stall_system.min_realm_index is missing from the content file")
        match = re.search(r'STALL_MIN_REALM_INDEX = int\(\(WORLD\.data\.get\("stall_system"\) or \{\}\)\.get\("min_realm_index"\)', self.source)
        self.assertIsNotNone(match, "surface.py no longer reads the stall's realm floor off the content roster")


class APurchaseIsATrade(unittest.TestCase):
    def test_stall_buy_records_the_trade_before_it_answers(self):
        tree = ast.parse(ECONOMY.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "stall_buy")
        recorded = answered = None
        for index, stmt in enumerate(fn.body):
            text = ast.unparse(stmt)
            if "_report_trade(" in text and recorded is None:
                recorded = index
            if "followup.send(" in text and "return" not in text and answered is None:
                answered = index
        self.assertIsNotNone(recorded, "stall_buy reports no trade; a quest asking for one cannot be advanced at a stall")
        self.assertIsNotNone(answered)
        self.assertLess(recorded, answered, "the record comes after the reply, so a reply that raises loses the quest (v1.0.5)")


class TheEngineDoorsExist(unittest.TestCase):
    def test_every_operation_is_allowlisted_and_dispatched(self):
        auth = (PROJECT_ROOT / "go_core" / "internal" / "game" / "authoritative.go").read_text(encoding="utf-8")
        registry = (PROJECT_ROOT / "go_core" / "internal" / "game" / "late_migration_registry.go").read_text(encoding="utf-8")
        for op in ("stall.open", "stall.list", "stall.withdraw", "stall.buy", "stall.close"):
            self.assertIn(f'"{op}":', auth, op)
            self.assertIn(f'case "{op}":', registry, op)
        for op in ("stall.board", "stall.status"):
            self.assertIn(f'"{op}":', auth, op)

    def test_the_new_person_column_has_both_decisions(self):
        privacy = (PROJECT_ROOT / "go_core" / "internal" / "game" / "privacy_actions.go").read_text(encoding="utf-8")
        reset = (PROJECT_ROOT / "go_core" / "internal" / "game" / "character_reset.go").read_text(encoding="utf-8")
        self.assertIn('"buyer_user_id": true', privacy, "buyer_user_id is not a subject column; an erasure would miss it")
        self.assertIn('"stall_sales.buyer_user_id"', privacy, "the ledger's buyer is not anonymised, so an erasure deletes the seller's own record")
        self.assertIn('"stall_sales.buyer_user_id"', reset, "a reset would refuse over a purchase at somebody else's stall")


if __name__ == "__main__":
    unittest.main()


class APickerOffersOnlyWhatTheListingTakes(unittest.TestCase):
    """v1.7.1: a trade's goods go on a stall only for somebody holding its
    certificate. The engine says which carried items those are; the `/stall
    list` picker must offer exactly those (rc.46) and restate no rule."""

    def _picker(self, status, inventory):
        import asyncio
        from types import SimpleNamespace
        with patch.dict(os.environ, ENV):
            from app.bot.commands import economy

        async def fake_status(_uid):
            if isinstance(status, Exception):
                raise status
            return status

        async def fake_inventory(_uid):
            return dict(inventory)

        interaction = SimpleNamespace(user=SimpleNamespace(id=43))
        with patch.object(economy, "_stall_status", fake_status), patch.object(economy.DB, "get_inventory", fake_inventory):
            choices = asyncio.run(economy.stall_item_autocomplete(interaction, ""))
        return [c.value for c in choices]

    def test_it_offers_the_engines_list_and_nothing_the_engine_would_refuse(self):
        bag = {"recovery_pill": 3, "spirit_herb": 5}
        self.assertEqual(self._picker({"sellable_items": ["spirit_herb"], "uncertified_items": ["recovery_pill"]}, bag), ["spirit_herb"])
        self.assertEqual(sorted(self._picker({"sellable_items": ["recovery_pill", "spirit_herb"]}, bag)), ["recovery_pill", "spirit_herb"])

    def test_an_unreachable_engine_offers_the_bag_rather_than_nothing(self):
        bag = {"recovery_pill": 3, "spirit_herb": 5}
        self.assertEqual(sorted(self._picker(RuntimeError("engine down"), bag)), ["recovery_pill", "spirit_herb"])

    def test_the_list_command_uses_it(self):
        source = ECONOMY.read_text(encoding="utf-8")
        self.assertIn("@app_commands.autocomplete(item=stall_item_autocomplete)", source)


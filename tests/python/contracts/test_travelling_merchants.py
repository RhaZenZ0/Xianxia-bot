"""v0.34.1: travelling merchants.

Go owns the merchants (their walk, the purchase of an unsold lot, the
roadside sale); this side asserts the Python boundary: the slash group is
there and guided, the travel reply names who is on the road, the auction
card calls a merchant's purchase a sale, and the automation switch is
listed everywhere a switch is listed.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
ECONOMY = (BOT / "commands" / "economy.py").read_text(encoding="utf-8")
EXPLORATION = (BOT / "commands" / "exploration.py").read_text(encoding="utf-8")
FEED = (BOT / "auction_feed.py").read_text(encoding="utf-8")
SURFACE = (BOT / "surface.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
INSPECT = (BOT / "admin" / "inspect_sim.py").read_text(encoding="utf-8")
DASHBOARD = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineOwnsTheMerchants(unittest.TestCase):
    def test_the_two_operations_are_registered_and_the_tick_walks_them(self):
        authoritative = (GO / "game" / "authoritative.go").read_text(encoding="utf-8")
        self.assertIn('"merchant.buy":                    true,', authoritative)
        self.assertIn('"merchant.status":           true,', authoritative)
        self.assertIn('case "merchant.status":', authoritative)
        # The one action a traveller may take mid-journey.
        self.assertIn('if req.Operation != "merchant.buy" {', authoritative)
        registry = (GO / "game" / "late_migration_registry.go").read_text(encoding="utf-8")
        self.assertIn('case "merchant.buy":', registry)
        tick = (GO / "simulation" / "advanced_maintenance.go").read_text(encoding="utf-8")
        self.assertIn('game.AdvanceMerchants(conn, r.World, gm)', tick)
        self.assertIn('game.MerchantBuysUnsoldLot(conn, r.World, a, gm)', tick)
        self.assertIn('automation["merchants"]', tick)

    def test_the_schema_carries_the_two_tables_and_the_buyer_column(self):
        migration = CORE.split('"playtest_board_and_merchants"')[1].split("),")[0]
        for needle in ("CREATE TABLE IF NOT EXISTS merchant_state", "CREATE TABLE IF NOT EXISTS merchant_stock", "ALTER TABLE auctions ADD COLUMN merchant_buyer"):
            self.assertIn(needle, migration, needle)

    def test_python_never_writes_merchant_tables(self):
        for source in (ECONOMY, EXPLORATION, FEED, CORE):
            self.assertNotIn("INSERT INTO merchant_", source)
            self.assertNotIn("UPDATE merchant_", source)


class TheSlashSurface(unittest.TestCase):
    def test_the_group_has_status_and_buy_and_both_pickers_are_guided(self):
        self.assertIn('merchant_group=app_commands.Group(name="merchant"', ECONOMY)
        self.assertIn('@registered_group_command(merchant_group, name="status"', ECONOMY)
        self.assertIn('@registered_group_command(merchant_group, name="buy"', ECONOMY)
        self.assertIn('@merchant_buy.autocomplete("merchant")', ECONOMY)
        self.assertIn('@merchant_buy.autocomplete("item")', ECONOMY)
        self.assertIn("@serialized_user_action\nasync def merchant_buy(", ECONOMY)
        buy = _body(ECONOMY, "merchant_buy")
        self.assertIn('ENGINE.authoritative_action("merchant.buy"', buy)
        self.assertIn('action_id=f"discord:{interaction.id}:merchant.buy"', buy)
        status = _body(ECONOMY, "merchant_status")
        self.assertIn('ENGINE.action("merchant.status"', _body(ECONOMY, "_merchant_status"))
        self.assertIn("/economy → Merchants → Buy", status)

    def test_the_shop_and_the_floor_finds_are_told_apart(self):
        # v0.34.2: the engine marks every stock line "wares" or "auction";
        # the status, the buy reply and the picker all say which.
        go = (GO / "game" / "merchant_actions.go").read_text(encoding="utf-8")
        self.assertIn("func restockMerchantWaresTx(", go)
        self.assertIn("if state.Location == m.Home {", go)
        self.assertIn('source = "wares"', go)
        lines = _body(ECONOMY, "_merchant_stock_lines")
        self.assertIn('"🛒" if str(line.get("source"))=="wares" else "🏮"', lines)
        self.assertIn('" from the shop" if str(result.get("source"))=="wares"', _body(ECONOMY, "merchant_buy"))
        self.assertIn('"shop" if str(line.get("source"))=="wares" else "floor find"', _body(ECONOMY, "merchant_buy_item_autocomplete"))

    def test_the_item_picker_follows_the_chosen_merchant(self):
        picker = _body(ECONOMY, "merchant_buy_item_autocomplete")
        self.assertIn('getattr(interaction.namespace,"merchant","")', picker)

    def test_the_economy_hub_has_the_page(self):
        self.assertIn('_hub_page("merchant", "Merchants"', SURFACE)
        self.assertIn('"merchant": merchant_group,', SURFACE)
        self.assertIn('"merchant", "blackmarket",', SURFACE)


class TheRoadAndTheFloor(unittest.TestCase):
    def test_the_travel_reply_names_merchants_on_the_road(self):
        travel = EXPLORATION.split("merchant_encounters")[1]
        self.assertIn('str(row.get("met"))=="road"', travel)
        self.assertIn("trade by the roadside", travel)
        self.assertIn("{road}{merchants}{safe}{meeting}", EXPLORATION)

    def test_the_card_calls_a_merchant_purchase_a_sale(self):
        embed = _body(FEED, "lot_embed")
        self.assertIn('lot.get("merchant_buyer")', embed)
        self.assertIn("(travelling merchant)", embed)
        self.assertIn("WORLD.merchants.get(merchant_key)", embed)
        settle = _body(FEED, "settle_lots")
        self.assertIn('lot.get("merchant_buyer")', settle)


class TheAutomationSwitch(unittest.TestCase):
    def test_the_switch_is_listed_everywhere_a_switch_is_listed(self):
        self.assertIn('"merchants": True,', CORE)
        self.assertIn('"merchants": True,', DASHBOARD)
        self.assertIn('value="merchants"', INSPECT)
        actions = (GO / "game" / "actions.go").read_text(encoding="utf-8")
        self.assertIn('"merchants":               true,', actions)

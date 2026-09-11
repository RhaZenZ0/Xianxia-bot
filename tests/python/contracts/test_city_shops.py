"""v0.35.0: city shops.

Go owns the shops (finding one, the door, the shelf, the refill); this side
asserts the Python boundary: the slash group is there and guided, explore
and travel replies tell the player what they found and where the door
goes, the Quest Forge leaves shopfronts off its lists, and the schema
carries the shelf.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
ECONOMY = (BOT / "commands" / "economy.py").read_text(encoding="utf-8")
EXPLORATION = (BOT / "commands" / "exploration.py").read_text(encoding="utf-8")
SURFACE = (BOT / "surface.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
FORGE = (PROJECT_ROOT / "app" / "ai" / "quest_forge.py").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal"


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineOwnsTheShops(unittest.TestCase):
    def test_the_operations_are_registered_and_explore_and_travel_know_shops(self):
        authoritative = (GO / "game" / "authoritative.go").read_text(encoding="utf-8")
        for op in ('"shop.buy":                        true,', '"shop.sell":                       true,', '"shop.here":                 true,', '"shop.browse":               true,', 'case "shop.here", "shop.browse":'):
            self.assertIn(op, authoritative, op)
        registry = (GO / "game" / "late_migration_registry.go").read_text(encoding="utf-8")
        self.assertIn('case "shop.buy":', registry)
        self.assertIn('case "shop.sell":', registry)
        exploration = (GO / "game" / "exploration_actions.go").read_text(encoding="utf-8")
        self.assertIn("discoverCityShopTx(conn, catalog, userID, c, p.GameMinute, now)", exploration)
        self.assertIn('"discovered_shop": discoveredShop', exploration)
        self.assertIn("travel there first", exploration)
        self.assertIn("the shop door opens onto", exploration)

    def test_the_schema_carries_the_shelf(self):
        migration = CORE.split('"city_shops"')[1].split("),")[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS shop_state", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS shop_stock", migration)
        for source in (ECONOMY, EXPLORATION, CORE):
            self.assertNotIn("INSERT INTO shop_", source)
            self.assertNotIn("UPDATE shop_", source)


class TheSlashSurface(unittest.TestCase):
    def test_the_group_has_four_leaves_and_guided_items(self):
        self.assertIn('shop_group=app_commands.Group(name="shop"', ECONOMY)
        for leaf in ("here", "browse", "buy", "sell"):
            self.assertIn(f'@registered_group_command(shop_group, name="{leaf}"', ECONOMY)
        self.assertIn('@shop_buy.autocomplete("item")', ECONOMY)
        self.assertIn('@shop_sell.autocomplete("item")', ECONOMY)
        self.assertIn("@serialized_user_action\nasync def shop_buy(", ECONOMY)
        self.assertIn("@serialized_user_action\nasync def shop_sell(", ECONOMY)
        self.assertIn('ENGINE.authoritative_action("shop.buy"', _body(ECONOMY, "shop_buy"))
        self.assertIn('ENGINE.authoritative_action("shop.sell"', _body(ECONOMY, "shop_sell"))
        self.assertIn('ENGINE.action("shop.here"', _body(ECONOMY, "shop_here"))
        self.assertIn('ENGINE.action("shop.browse"', _body(ECONOMY, "_shop_browse"))
        # Selling offers only what the keeper wants and the player carries.
        sell_picker = _body(ECONOMY, "shop_sell_item_autocomplete")
        self.assertIn('shop.get("buys")', sell_picker)
        self.assertIn("DB.get_inventory(interaction.user.id)", sell_picker)

    def test_the_economy_hub_has_the_page(self):
        self.assertIn('_hub_page("shop", "City Shops"', SURFACE)
        self.assertIn('"shop": shop_group,', SURFACE)
        self.assertIn('"merchant", "shop", "blackmarket",', SURFACE)


class FindingAndEntering(unittest.TestCase):
    def test_the_explore_reply_names_the_shop_found(self):
        self.assertIn('outcome.get("discovered_shop")', EXPLORATION)
        self.assertIn("You find {discovered_shop.get('name')}", EXPLORATION)
        self.assertIn("/economy → City Shops → Browse", EXPLORATION)

    def test_the_travel_reply_says_you_stepped_inside(self):
        self.assertIn('.get("shop") or "")', EXPLORATION)
        self.assertIn("looks up from the counter", EXPLORATION)
        self.assertIn("{desc}{gate_line}{shop_line}{road}", EXPLORATION)

    def test_the_forge_leaves_shopfronts_off_its_lists(self):
        self.assertIn('loc.get("auction_house") or loc.get("shop") or loc.get("district")', FORGE)

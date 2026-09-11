"""v0.38.0: city life.

An inn in every city, a board of commissions per city (the capitals' quest
pavilion, the gate notice elsewhere), a sect envoys' hall in the capitals'
temple quarter, rumours told by the people who hear everything first, and
prosperity that trade moves and the shelves show. Go owns the prosperity
and the merchant's seat at the inn; this side asserts the Python boundary.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
EXPLORATION = (BOT / "commands" / "exploration.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal"


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineOwnsProsperity(unittest.TestCase):
    def test_trade_moves_prosperity_and_the_shelf_shows_it(self):
        shops = (GO / "game" / "shop_actions.go").read_text(encoding="utf-8")
        for needle in ("func nudgeCityProsperityTx(", "func cityProsperityTx(", "func prosperityShelfBonus(", "quantity := max64(1, line.Quantity+bonus)", "MAX(10,MIN(95,prosperity+?))"):
            self.assertIn(needle, shops, needle)
        self.assertEqual(shops.count("nudgeCityProsperityTx(conn, catalog, shop.City, 1)"), 2, "buy and sell each nudge")
        merchants = (GO / "game" / "merchant_actions.go").read_text(encoding="utf-8")
        self.assertIn("func AuctionStruckProsperityTx(", merchants)
        self.assertIn("nudgeCityProsperityTx(conn, catalog, state.Location, 1)", merchants)
        self.assertIn("func cityInn(", merchants)
        self.assertIn("game.AuctionStruckProsperityTx(conn, r.World, a)", (GO / "simulation" / "advanced_maintenance.go").read_text(encoding="utf-8"))
        for source in (EXPLORATION, CORE):
            self.assertNotIn("UPDATE civilization_regions", source)


class TheBoard(unittest.TestCase):
    def test_the_board_lists_the_citys_givers_and_accept_stays_in_the_city(self):
        board = _body(EXPLORATION, "_city_board")
        self.assertIn('_city_of(where) == city', board)
        self.assertIn("The Quest Pavilion of", _body(EXPLORATION, "city_board"))
        self.assertIn("DB.list_active_bounties(limit=8)", _body(EXPLORATION, "city_board"))
        accept = _body(EXPLORATION, "city_accept")
        self.assertIn("if quest not in board:", accept)
        self.assertIn('ENGINE.authoritative_action("commission.accept"', accept)
        self.assertIn("@serialized_user_action\nasync def city_accept(", EXPLORATION)
        self.assertIn('@city_accept.autocomplete("quest")', EXPLORATION)
        self.assertIn("FROM bounties b JOIN characters c", _body(CORE, "list_active_bounties"))


class TheHallTheRumoursAndTheInn(unittest.TestCase):
    def test_the_envoys_hall_is_in_the_capitals_temple_quarter_and_puts_routes_on_the_map(self):
        envoys = _body(EXPLORATION, "city_envoys")
        self.assertIn('data.get("district") != "temple"', envoys)
        self.assertIn('"discovery_kind": "envoys_hall"', envoys)
        self.assertIn("The sect envoys keep their hall here", EXPLORATION)

    def test_rumours_come_through_the_one_viewpoint_gate(self):
        rumours = _body(EXPLORATION, "city_rumours")
        self.assertIn("DB.get_structured_world_history(location=place, user_id=interaction.user.id", rumours)
        self.assertNotIn("list_world_history(", rumours, "the unfiltered reader must not feed rumours")

    def test_the_inn_names_who_is_in_town_and_opens_the_common_room(self):
        inn = _body(EXPLORATION, "city_inn")
        self.assertIn("DB.get_characters_at_location(place, exclude_user_id=interaction.user.id)", inn)
        self.assertIn('ENGINE.action("merchant.status"', inn)
        thread = _body(EXPLORATION, "_inn_thread")
        self.assertIn("type=discord.ChannelType.public_thread", thread)
        self.assertIn("DB.get_realm_hub_channels(guild.id)", thread)

    def test_the_gate_and_the_look_show_the_citys_mood(self):
        self.assertIn("The gate queue is long today", EXPLORATION)
        self.assertIn("SIM.civilization_status(city)", _body(EXPLORATION, "city_look"))

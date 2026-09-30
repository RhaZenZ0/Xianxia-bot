"""`/talk` offers a commission only where Accept would succeed (v1.12.3).

v1.3.1 made the engine refuse a commission taken anywhere but the city its
giver posts work in (`commissionAcceptAction` asks `cityOf(home)` against
`cityOf(where you stand)`). `/talk` ran the offer ladder for any giver the
player was talking to, and the simulation walks people out of their home city,
so a player could be offered work on a card whose Accept button could only be
refused - a surface offering what the engine refuses (rc.46).

`offer_where_it_can_be_accepted` is the one place the rule is asked of an offer.
It is held against the rule computed a third time off the raw content file over
every giver and every location, which is how a pair of wrong halves cannot
agree with each other and pass (v1.0.9).
"""
from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
SCENE = PROJECT_ROOT / "app" / "bot" / "commands" / "scene.py"
ACCEPT_GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "commission_actions.go"


def _modules():
    with patch.dict(os.environ, ENV):
        scene = importlib.import_module("app.bot.commands.scene")
        exploration = importlib.import_module("app.bot.commands.exploration")
        rules = importlib.import_module("app.rules.commissions")
        runtime = importlib.import_module("app.bot.runtime")
    return scene, exploration, rules, runtime.WORLD


def _raw_city_of(locations: dict, name: str) -> str:
    data = locations.get(name) or {}
    if data.get("outside_location") and (data.get("district") or data.get("shop") or data.get("auction_house")):
        return str(data["outside_location"])
    return name


def _raw_home(world, giver: str) -> str:
    givers = world.data.get("commission_givers") or {}
    npc = (world.data.get("npcs") or {}).get(giver) or {}
    return str((givers.get(giver) or {}).get("location") or npc.get("location") or "").strip()


class AnOfferIsMadeOnlyInTheGiversCity(unittest.TestCase):
    def setUp(self):
        self.scene, self.exploration, self.rules, self.world = _modules()
        self.givers = sorted(name for name in (self.world.data.get("commission_givers") or {}) if _raw_home(self.world, name))
        self.assertTrue(self.givers, "the content read found no giver with a home; the gate is broken, not the tree")

    def _offer(self, giver):
        return self.rules.Offer(kind="offer", giver=giver, definition={"quest_key": "k", "title": "T"}, standing="neutral")

    def test_every_giver_at_every_location_matches_the_engines_rule(self):
        locations = self.world.locations
        wrong = []
        for giver in self.givers:
            home_city = _raw_city_of(locations, _raw_home(self.world, giver))
            for here in locations:
                answer = self.scene.offer_where_it_can_be_accepted(self._offer(giver), here)
                should_offer = home_city == _raw_city_of(locations, here)
                if (answer.kind == "offer") != should_offer:
                    wrong.append((giver, here, answer.kind))
        self.assertEqual(wrong[:5], [], f"{len(wrong)} (giver, place) pairs disagree with cityOf(home) == cityOf(here)")

    def test_a_gate_or_district_of_the_givers_city_is_the_city(self):
        locations = self.world.locations
        for giver in self.givers:
            home_city = _raw_city_of(locations, _raw_home(self.world, giver))
            parts = [n for n, p in locations.items() if str(p.get("outside_location") or "") == home_city and (p.get("district") or p.get("shop"))]
            if parts:
                answer = self.scene.offer_where_it_can_be_accepted(self._offer(giver), sorted(parts)[0])
                self.assertEqual(answer.kind, "offer", f"{giver} met in a part of {home_city} lost their offer")
                return
        self.fail("no giver's city has a district; the gate is broken, not the tree")

    def test_away_from_home_the_narrator_is_told_why_and_no_card_is_drawn(self):
        locations = self.world.locations
        giver = self.givers[0]
        home_city = _raw_city_of(locations, _raw_home(self.world, giver))
        elsewhere = next(n for n in locations if _raw_city_of(locations, n) != home_city)
        answer = self.scene.offer_where_it_can_be_accepted(self._offer(giver), elsewhere)
        self.assertEqual(answer.kind, "unavailable")
        self.assertIn(home_city, answer.reason, "the narrator is not told where the work is posted")
        self.assertIsNone(answer.definition, "a card would be drawn for work that cannot be accepted here")

    def test_the_ladders_other_branches_need_no_place(self):
        giver = self.givers[0]
        for kind in ("progress", "cooldown", "nothing", "unavailable"):
            offer = self.rules.Offer(kind=kind, giver=giver)
            self.assertIs(self.scene.offer_where_it_can_be_accepted(offer, "Nowhere In Particular"), offer)

    def test_a_giver_with_no_home_imposes_no_place(self):
        offer = self.rules.Offer(kind="offer", giver="Nobody Listed At All", definition={"quest_key": "k"})
        self.assertIs(self.scene.offer_where_it_can_be_accepted(offer, "Greenriver Town"), offer)

    def test_the_board_and_the_offer_read_one_giver_home(self):
        # `_city_board` was the only Python statement of where work is posted;
        # it asks the helper now, so the board and the offer cannot part.
        source = (PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        board = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_city_board")
        self.assertIn("_commission_giver_home", {ast.unparse(c.func) for c in ast.walk(board) if isinstance(c, ast.Call)})


class TalkAsksBeforeItOffers(unittest.TestCase):
    def test_talk_runs_the_offer_through_the_rule_before_it_builds_the_card_or_the_narrator_block(self):
        tree = ast.parse(SCENE.read_text(encoding="utf-8"))
        talk = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "talk")
        lines = {}
        for node in ast.walk(talk):
            if isinstance(node, ast.Call):
                lines.setdefault(ast.unparse(node.func), []).append(node.lineno)
        self.assertIn("offer_where_it_can_be_accepted", lines, "/talk offers work without asking where it can be accepted")
        gate = min(lines["offer_where_it_can_be_accepted"])
        self.assertLess(min(lines["commission_offer_for"]), gate)
        self.assertLess(gate, min(lines["commission_rules.commission_context"]),
                        "the narrator is handed the offer before the rule is asked")

    def test_the_engine_still_refuses_outside_the_givers_city(self):
        go = ACCEPT_GO.read_text(encoding="utf-8")
        self.assertIn("commissionGiverHome(catalog, def.GiverNPC)", go, "the engine rule this twins is gone or moved")
        self.assertIn("cityOf(catalog, home) != cityOf(catalog, c.Location)", go)


if __name__ == "__main__":
    unittest.main()

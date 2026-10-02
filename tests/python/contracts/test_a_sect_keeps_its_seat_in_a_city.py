"""A sect keeps its seat in a city (v1.19.0).

Asked for as *"more capital cities with rivals, maybe move the sects that fit
with that"*, and taken as Option A: one capital per world stays, and every
public sect's gate becomes a ``sect_gate`` district of a **seat** city of its
world, so the sect politics the engine already runs - claims, wars, relations -
are a city's politics, legible on the city's own card.

**No new sect field.** The seat is `cityOf(gate)`, read off the catalogue
through the one rule the engine already uses for a gate, a district and a
shop (`cityOf`; `city_of_place` is its Python twin), so the seat and the gate
cannot disagree. A wilderness gate (a sect nobody seated) is its own home, and
a hidden sect has no seat at all.

What is held here is the content mapping, the Python twin against a third
computation off the raw file (v1.0.9: two wrong halves agreeing is exactly
what a test against the twin alone would pass), and that every surface that
names a city or its gate now names the seat: the panel header, the city page,
the Enter picker, and the two replies that put a gate on a player's map.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import unittest
from unittest.mock import patch

import pytest

from tests.support import PROJECT_ROOT as ROOT

pytestmark = pytest.mark.contract

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
CONTENT = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LOCATIONS: dict[str, dict] = CONTENT["locations"]
SECTS: dict[str, dict] = CONTENT["sects"]
CITIES = {h["entrance_location"] for h in CONTENT["auction_houses"].values()}
EXPLORATION = ROOT / "app" / "bot" / "commands" / "exploration.py"
SECT_PY = ROOT / "app" / "bot" / "commands" / "sect.py"
EXPLORATION_GO = ROOT / "go_core" / "internal" / "game" / "exploration_actions.go"


def _gate(sect: str) -> str:
    return str((SECTS[sect].get("recruitment") or {}).get("location") or "").strip()


def _public_sects() -> list[str]:
    return sorted(s for s, d in SECTS.items() if not d.get("hidden") and _gate(s))


def _city_of_by_content(location: str) -> str:
    """`cityOf`'s rule, applied to the raw content - a third statement,
    deliberately, so the twin is held to the content and not to itself."""
    entry = LOCATIONS.get(location) or {}
    outside = str(entry.get("outside_location") or "").strip()
    if outside and (entry.get("district") or entry.get("shop") or entry.get("auction_house")):
        return outside
    return location


def _seat_by_content(sect: str) -> str:
    gate = _gate(sect)
    city = _city_of_by_content(gate)
    return city if gate and city != gate else ""


def _function(path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}; the reader is broken, not the tree")


def _body_without_docstring(node) -> str:
    body = node.body[1:] if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant) else node.body
    return "\n".join(ast.unparse(stmt) for stmt in body)


class EveryPublicSectIsSeatedInACityOfItsWorld(unittest.TestCase):
    def test_the_reader_finds_the_sects(self):
        """Asserted before it is trusted (rc.57)."""
        self.assertGreaterEqual(len(_public_sects()), 12, _public_sects())
        self.assertGreater(len(CITIES), 40, "the city reader is broken, not the tree")

    def test_every_public_gate_is_a_sect_gate_district_of_a_walled_city_of_its_world(self):
        for sect in _public_sects():
            gate = _gate(sect)
            seat = _seat_by_content(sect)
            with self.subTest(sect=sect, gate=gate):
                self.assertTrue(seat, f"{gate} is a wilderness gate; every public sect keeps a seat")
                self.assertEqual(LOCATIONS[gate].get("district"), "sect_gate")
                self.assertIn(seat, CITIES, f"{seat} is not a walled city")
                self.assertEqual(LOCATIONS[gate].get("world"), LOCATIONS[seat].get("world"), "a seat is in the sect's own world")
                self.assertFalse(LOCATIONS[seat].get("realm_hub"), "a capital seats no sect: one capital per world stays")
                self.assertNotEqual(seat, "Greenriver Town", "the starting town is nobody's seat")

    def test_no_two_sects_share_a_seat(self):
        seats = [_seat_by_content(s) for s in _public_sects()]
        self.assertEqual(len(seats), len(set(seats)), f"two sects share a seat: {sorted(seats)}")

    def test_a_private_gate_stays_private(self):
        """The two demonic sects' gates are `private` districts: a sponsor reveals
        them and the street does not show them."""
        private = [s for s in _public_sects() if LOCATIONS[_gate(s)].get("private")]
        self.assertGreaterEqual(len(private), 2, "the private gates were made public")
        for sect in private:
            self.assertFalse((SECTS[sect].get("recruitment") or {}).get("public_route", True), f"{sect} has a private gate on a public route")

    def test_the_gates_people_live_at_the_gate(self):
        for sect in _public_sects():
            gate = _gate(sect)
            people = [n for n, npc in CONTENT["npcs"].items() if npc.get("location") == gate]
            with self.subTest(gate=gate):
                self.assertTrue(people, f"{gate} is empty")
                for name in people:
                    self.assertEqual(CONTENT["npcs"][name].get("district"), gate)


class ThePythonTwinAgreesWithTheContent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, ENV):
            cls.ex = importlib.import_module("app.bot.commands.exploration")
            cls.loc = importlib.import_module("app.bot.locations")
        cls.WORLD = cls.ex.WORLD

    def test_sect_seat_is_the_content_rule_for_every_sect(self):
        for sect in sorted(SECTS):
            with self.subTest(sect=sect):
                self.assertEqual(self.WORLD.sect_seat(sect), _seat_by_content(sect) if not SECTS[sect].get("hidden") else "")

    def test_seated_sect_is_the_inverse_and_a_city_seats_at_most_one(self):
        for city in sorted(CITIES):
            seated = [s for s in _public_sects() if _seat_by_content(s) == city]
            self.assertLessEqual(len(seated), 1)
            self.assertEqual(self.WORLD.seated_sect(city), seated[0] if seated else "")

    def test_the_panel_header_names_the_seat_on_the_city_and_on_the_gate(self):
        sect = "Azure Cloud Sect"
        seat, gate = _seat_by_content(sect), _gate(sect)
        self.assertIn(f"seat of the {sect}", self.loc.here_summary(seat))
        gate_line = self.loc.here_summary(gate)
        self.assertIn(f"gate of the {sect}", gate_line)
        self.assertIn(seat, gate_line)

    def test_enter_offers_a_public_gate_from_its_seat_and_never_a_private_part(self):
        for sect in _public_sects():
            seat, gate = _seat_by_content(sect), _gate(sect)
            offered = [name for name, _e, _w in self.ex._places_to_enter(seat)]
            with self.subTest(sect=sect):
                if LOCATIONS[gate].get("private"):
                    self.assertNotIn(gate, offered, "a private gate is a sponsor's to reveal, not the street's to show")
                else:
                    self.assertIn(gate, offered)
                    self.assertIn(f"the gate of the {sect}", [w for n, _e, w in self.ex._places_to_enter(seat) if n == gate][0])
                for name in offered:
                    self.assertFalse(LOCATIONS[name].get("private"), f"{seat} offers the private {name}")

    def test_the_seat_lines_never_raise(self):
        async def boom(*_a, **_k):
            raise RuntimeError("engine away")
        with patch.object(self.ex.DB, "get_territory", boom):
            lines = asyncio.run(self.ex._seat_lines("Cloudblade City"))
        self.assertEqual(lines, [])

    def test_the_seat_lines_read_the_seat_and_the_banner(self):
        async def territory(_key):
            return {"controller_type": "sect", "controller_key": "Crimson Furnace Sect"}
        async def relations(_sect):
            return [{"sect_a": "Azure Cloud Sect", "sect_b": "Jade Meridian Sect", "relation_score": 15},
                    {"sect_a": "Black Serpent Clan", "sect_b": "Azure Cloud Sect", "relation_score": -5}]
        async def wars(**_k):
            return [{"attacker_key": "Crimson Furnace Sect", "defender_key": "Azure Cloud Sect", "territory_key": "Cloudblade City"}]
        with patch.object(self.ex.DB, "get_territory", territory), patch.object(self.ex.DB, "get_sect_relations", relations), \
                patch.object(self.ex.DB, "get_territory_wars", wars):
            lines = asyncio.run(self.ex._seat_lines("Cloudblade City"))
        text = "\n".join(lines)
        self.assertIn("**Seat:** the **Azure Cloud Sect**", text)
        self.assertIn("**Banner:** the **Crimson Furnace Sect** holds this city", text)
        self.assertIn("allied with Jade Meridian Sect", text)
        self.assertIn("at odds with Black Serpent Clan", text)
        self.assertIn("at war over Cloudblade City", text)


class EverySurfaceNamesTheSeat(unittest.TestCase):
    def test_the_city_page_draws_the_seat_lines(self):
        body = _body_without_docstring(_function(EXPLORATION, "city_look"))
        self.assertIn("_seat_lines(city)", body)
        seat = _body_without_docstring(_function(EXPLORATION, "_seat_lines"))
        self.assertIn("WORLD.seated_sect(city)", seat)
        self.assertIn("DB.get_territory(city)", seat)
        self.assertIn("DB.get_sect_relations(seated)", seat)

    def test_the_two_replies_that_map_a_gate_name_its_seat(self):
        for path, name in ((SECT_PY, "sect_recruitment_recommendation"), (EXPLORATION, "city_envoys")):
            body = _body_without_docstring(_function(path, name))
            with self.subTest(reply=name):
                self.assertIn("'seat'", body, f"{name} no longer reads the seat the engine returned")
                self.assertIn("/world → City → Enter", body, f"{name} no longer says how a gate is entered")

    def test_the_engine_keeps_a_private_part_off_the_map(self):
        source = EXPLORATION_GO.read_text(encoding="utf-8")
        start = source.index("func knownLocationsTx(")
        body = source[start: source.index("\n}\n", start)]
        self.assertIn("cityPartsOf(catalog, city)", body)
        self.assertIn(".Private", body, "knownLocationsTx marks a private gate known from the street")


if __name__ == "__main__":
    unittest.main()

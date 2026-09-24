"""v1.3.1: the bot's half of six engine wires.

Three panel hides v1.1.0 left out for their cost (the ghost road's two
grounds, the black-market post, the array), the ghost-ground twin of the
engine's rule, and the sect reconcile that no longer names a sect.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.rules.death_qi import GHOST_APPEASE_GROUND_CEILING, GHOST_HARVEST_GROUND_FLOOR, death_qi_ground_multiplier
from tests.support import PROJECT_ROOT, code_only

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
GO = PROJECT_ROOT / "go_core" / "internal" / "game"


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


def _first(pred) -> str:
    for name, place in sorted(CONTENT["locations"].items()):
        if pred(place):
            return name
    raise AssertionError("no such place in the content; the reader is broken, not the tree")


class TheGhostGroundTwin(unittest.TestCase):
    def test_the_floors_are_the_engines(self):
        go = (GO / "death_qi.go").read_text(encoding="utf-8")
        floor = float(re.search(r"ghostHarvestGroundFloor\s*=\s*([0-9.]+)", go).group(1))
        ceiling = float(re.search(r"ghostAppeaseGroundCeiling\s*=\s*([0-9.]+)", go).group(1))
        self.assertEqual((GHOST_HARVEST_GROUND_FLOOR, GHOST_APPEASE_GROUND_CEILING), (floor, ceiling))

    def test_the_twin_reads_the_content_in_the_engines_order(self):
        system, places = CONTENT["death_qi_system"], CONTENT["locations"]
        ground = system["ground"]
        ruin = _first(lambda p: p.get("road_site") == "ruin")
        shrine = _first(lambda p: p.get("road_site") == "shrine")
        temple = _first(lambda p: p.get("district") == "temple" and not p.get("road_site"))
        city = _first(lambda p: p.get("settlement_type") and not p.get("district") and not p.get("road_site"))
        self.assertEqual(death_qi_ground_multiplier(system, places, ruin), ground["road_sites"]["ruin"])
        self.assertEqual(death_qi_ground_multiplier(system, places, shrine), ground["road_sites"]["shrine"])
        self.assertEqual(death_qi_ground_multiplier(system, places, temple), ground["districts"]["temple"])
        self.assertEqual(death_qi_ground_multiplier(system, places, city), ground["city_penalty"])
        self.assertEqual(death_qi_ground_multiplier(system, places, "birth_family:7"), ground["default"])
        # The shipped content has ground on both sides of both floors, or
        # the hides could never draw.
        self.assertGreaterEqual(ground["road_sites"]["ruin"], GHOST_HARVEST_GROUND_FLOOR)
        self.assertLessEqual(ground["road_sites"]["shrine"], GHOST_APPEASE_GROUND_CEILING)


def hides(location: str, *, post=None, crossings=(), fail=False) -> dict[str, str]:
    surface = _surface()

    async def member_manor(_uid):
        return None

    async def market(_here, _minute):
        if fail:
            raise RuntimeError("engine down")
        return post

    async def crossing_rows(_here):
        if fail:
            raise RuntimeError("engine down")
        return list(crossings)

    async def clock():
        return SimpleNamespace(total_minutes=1000, period="Morning")

    interaction = SimpleNamespace(user=SimpleNamespace(id=42))
    with patch.object(surface.DB, "get_member_sect_manor", member_manor), \
         patch.object(surface.DB, "get_active_black_market", market), \
         patch.object(surface.DB, "list_world_crossings", crossing_rows), \
         patch.object(surface, "current_world_time", clock):
        return asyncio.run(surface._location_hidden_actions(interaction, {"location": location}))


class TheThreeHidesDrawWhereTheEngineWouldNotRefuse(unittest.TestCase):
    def test_the_ghost_road_hides_the_harvest_on_a_shrine_and_the_rites_on_a_ruin(self):
        ruin = _first(lambda p: p.get("road_site") == "ruin")
        shrine = _first(lambda p: p.get("road_site") == "shrine")
        self.assertNotIn("/ghost harvest", hides(ruin))
        self.assertIn("/ghost appease", hides(ruin))
        self.assertIn("/ghost harvest", hides(shrine))
        self.assertNotIn("/ghost appease", hides(shrine))

    def test_the_black_market_is_hidden_without_a_post_and_drawn_with_one(self):
        town = "Greenriver Town"
        self.assertIn("/blackmarket buy", hides(town))
        self.assertNotIn("/blackmarket buy", hides(town, post={"location": town}))

    def test_the_array_is_drawn_where_one_departs_or_a_crossing_stands(self):
        departs = str(next(iter(CONTENT["teleport_arrays"].values()))["from"])
        nowhere = _first(lambda p: p.get("road_site") == "ruin")
        self.assertNotIn("/array use", hides(departs))
        self.assertIn("/array use", hides(nowhere))
        self.assertNotIn("/array use", hides(nowhere, crossings=[{"location_key": nowhere}]))

    def test_a_read_that_fails_hides_nothing(self):
        town = "Greenriver Town"
        shut = hides(town, fail=True)
        self.assertNotIn("/blackmarket buy", shut, "one unavailable read would hide a door that works")
        nowhere = _first(lambda p: p.get("road_site") == "ruin")
        self.assertNotIn("/array use", hides(nowhere, fail=True))


class TheReconcileNamesNoSect(unittest.TestCase):
    def test_the_payload_carries_no_list(self):
        source = (PROJECT_ROOT / "app" / "bot" / "commands" / "sect.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_sync_sect_discoveries")
        body = "\n".join(ast.unparse(stmt) for stmt in fn.body[1:])
        self.assertIn("sect.discover", body)
        self.assertNotIn("'sects'", body, "the client names the sects again; the engine derives them (v1.3.1)")
        self.assertNotIn("recruitment_definition", body)

    def test_the_engine_derives_the_list(self):
        go = code_only((GO / "identity_discovery_actions.go").read_text(encoding="utf-8"))
        self.assertIn("knownLocationsTx(", go)
        self.assertIn("sectGate(", go)

    def test_the_sponsor_and_the_board_and_the_claim_are_the_engines(self):
        doors = code_only((GO / "sect_doors.go").read_text(encoding="utf-8"))
        self.assertIn("npcWhereaboutsTx(", doors)
        self.assertIn("commissionGiverHome(", code_only((GO / "commission_actions.go").read_text(encoding="utf-8")))
        self.assertIn("is claimed from", code_only((GO / "territory_actions.go").read_text(encoding="utf-8")))

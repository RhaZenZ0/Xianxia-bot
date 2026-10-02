"""The way up into an allied sect (v1.18.0).

A sect is for life: nothing lets a member leave one, and the recommendation and
the trial both refuse anybody already in a public sect. So a cultivator who
took the sect road at realm 1 - which the beginner path hands everybody -
carried a Mortal sect through three worlds and could never join the six sects
above, which are half the sects in the game. The world-flow study found it;
`sect.ascend` is the second door: each public sect names in content the allied
sect one world above (`ascends_to`), and a member standing at that sect's gate,
at the world's own floor, is taken in as an Outer Disciple on their elders'
letter. No roll, no payload - the gate and the floor are the catalogue's.

This file holds the Python half: the content climbs one world at a time and
the promotion ladder reaches the rank the manor's construction asks; the
command sends nothing the engine could be told; the panel anticipates each of
the engine's four refusals with its reason and draws the door where the engine
would open it; and the engine playtest drives the climb. The engine half is
`sect_ascend_test.go`.
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

from tests.support import PROJECT_ROOT

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
SECTS = CONTENT["sects"]
LOCATIONS = CONTENT["locations"]
RANKS = {int(r["level"]): str(r["name"]) for r in CONTENT["sect_system"]["ranks"]}
PROMOTION = {int(r["rank_level"]) for r in CONTENT["sect_system"]["exchange"]["promotion"]}
WORLD_ORDER = []
for realm in CONTENT["realms"]:
    if realm["world"] not in WORLD_ORDER:
        WORLD_ORDER.append(str(realm["world"]))
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _gate(sect: str) -> str:
    """`sectGate`'s rule: a public sect's recruitment location, if the
    catalogue carries it."""
    definition = SECTS.get(sect) or {}
    if definition.get("hidden"):
        return ""
    gate = str((definition.get("recruitment") or {}).get("location") or "").strip()
    return gate if gate in LOCATIONS else ""


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


class _Db:
    def __init__(self, sect: str | None):
        self._sect = sect

    async def get_sect_membership(self, _uid):
        if self._sect is None:
            return None
        return {"sect_name": self._sect, "rank_level": 30, "rank_name": RANKS[30]}

    def __getattr__(self, name):
        async def nothing(*_a, **_k):
            return None
        return nothing


def _hidden(surface, *, sect: str | None, here: str, realm: int) -> dict[str, str]:
    character = {"location": here, "realm_index": realm, "phase": 1, "body_realm_index": 0, "body_phase": 1}
    interaction = SimpleNamespace(user=SimpleNamespace(id=7))
    with patch.object(surface, "DB", _Db(sect)):
        return asyncio.run(surface._progression_hidden_actions(interaction, character))


class TheContentClimbsOneWorldAtATime(unittest.TestCase):
    def test_the_content_was_read(self):
        self.assertGreaterEqual(len(WORLD_ORDER), 4, "the realms name fewer than four worlds; the gate is broken, not the tree")
        self.assertGreaterEqual(sum(1 for s in SECTS.values() if s.get("ascends_to")), 10,
                                "fewer than ten sects name a sect above them; the content read is broken, not the tree")

    def test_every_public_sect_below_the_top_names_a_gate_one_world_up(self):
        top = WORLD_ORDER[-1]
        for name, definition in SECTS.items():
            with self.subTest(sect=name):
                gate = _gate(name)
                above = str(definition.get("ascends_to") or "")
                if not gate or LOCATIONS[gate]["world"] == top:
                    self.assertEqual(above, "", f"{name} has no way up and names {above!r} above it")
                    continue
                self.assertTrue(above, f"{name} stands in the {LOCATIONS[gate]['world']} and names no sect above it: a member there can never climb")
                above_gate = _gate(above)
                self.assertTrue(above_gate, f"{name} names {above}, which keeps no public gate")
                self.assertEqual(WORLD_ORDER.index(LOCATIONS[above_gate]["world"]), WORLD_ORDER.index(LOCATIONS[gate]["world"]) + 1,
                                 f"{name} ({LOCATIONS[gate]['world']}) names {above} ({LOCATIONS[above_gate]['world']}), which is not the world above")

    def test_the_ladder_reaches_the_rank_the_manor_asks(self):
        """The homestead's rank was put on the ladder in v1.17.1 (Deacon at
        9,000); the manor's construction rank, Elder, was still a GM's lever
        only. The way up resets standing, so the ladder has to be climbable in
        one sect for the manor to be anybody's."""
        construction = int(CONTENT["sect_abode_system"]["manor_construction_rank_level"])
        self.assertIn(construction, PROMOTION,
                      f"manor_construction_rank_level is {construction} ({RANKS.get(construction)}) and no rung of the promotion ladder reaches it")
        rungs = sorted((int(r["rank_level"]), int(r["earned"])) for r in CONTENT["sect_system"]["exchange"]["promotion"])
        self.assertEqual([level for level, _ in rungs], sorted(level for level, _ in rungs))
        self.assertEqual([earned for _, earned in rungs], sorted(earned for _, earned in rungs), "a higher rank is earned for less than a lower one")


class TheCommandSendsNothingTheEngineCouldBeTold(unittest.TestCase):
    def test_the_payload_is_empty(self):
        source = (PROJECT_ROOT / "app" / "bot" / "commands" / "sect.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "sect.ascend":
                calls.append(node)
        self.assertEqual(len(calls), 1, "sect.ascend is called from no command, or from more than one")
        payload = calls[0].args[2]
        self.assertIsInstance(payload, ast.Dict, "the ascent's payload is not a literal dict")
        self.assertEqual(payload.keys, [], f"the ascent sends {ast.unparse(payload)}; which sect, which gate and which floor are the catalogue's (v1.1.0)")

    def test_the_engine_allows_it_and_dispatches_it(self):
        allow = (GO / "authoritative.go").read_text(encoding="utf-8")
        self.assertRegex(allow, r'"sect\.ascend":\s*true')
        registry = (GO / "late_migration_registry.go").read_text(encoding="utf-8")
        self.assertIn('case "sect.ascend":', registry)
        self.assertIn("sectAscendActionGo(conn, catalog, userID, raw)", registry)

    def test_the_engine_playtest_climbs_and_reads_the_membership_back(self):
        playtest = (PROJECT_ROOT / "scripts" / "playtest_engine.py").read_text(encoding="utf-8")
        self.assertIn('act("sect.ascend", PLAYER, {})', playtest, "the engine playtest never drives the way up")
        after = playtest[playtest.index('climbed = await step(report, "sect.ascend"'):]
        self.assertIn("db.get_sect_membership(PLAYER)", after[:1500], "the playtest trusts the reply rather than reading the membership back")


class ThePanelAnticipatesTheEnginesRefusals(unittest.TestCase):
    def setUp(self):
        self.surface = _surface()
        self.below, self.above = "Azure Cloud Sect", str(SECTS["Azure Cloud Sect"]["ascends_to"])
        self.gate = _gate(self.above)
        self.floor = int(LOCATIONS[self.gate].get("min_realm_index") or 0)
        self.assertTrue(self.gate and self.floor > 0, "the shipped content no longer puts a gate with a floor above Azure Cloud; the fixture is wrong, not the rule")

    def test_a_member_at_the_allied_gate_is_shown_the_door(self):
        hidden = _hidden(self.surface, sect=self.below, here=self.gate, realm=self.floor)
        self.assertNotIn("/sect recruitment ascend", hidden, hidden.get("/sect recruitment ascend"))

    def test_a_member_in_the_street_is_told_where_the_way_up_is_taken(self):
        hidden = _hidden(self.surface, sect=self.below, here="Greenriver Town", realm=self.floor)
        self.assertIn("/sect recruitment ascend", hidden)
        self.assertIn(self.gate, hidden["/sect recruitment ascend"])
        self.assertIn(self.above, hidden["/sect recruitment ascend"])

    def test_a_member_below_the_floor_is_told_the_realm(self):
        hidden = _hidden(self.surface, sect=self.below, here=self.gate, realm=self.floor - 1)
        self.assertIn("/sect recruitment ascend", hidden)
        self.assertIn(self.surface.WORLD.realm_name(self.floor), hidden["/sect recruitment ascend"])

    def test_a_member_of_a_top_sect_is_told_nothing_stands_above_it(self):
        top = next(name for name, d in SECTS.items() if _gate(name) and not d.get("ascends_to"))
        hidden = _hidden(self.surface, sect=top, here=self.gate, realm=self.floor)
        self.assertIn("/sect recruitment ascend", hidden)
        self.assertIn("names no sect above it", hidden["/sect recruitment ascend"])

    def test_somebody_in_no_sect_is_told_to_join_one(self):
        hidden = _hidden(self.surface, sect=None, here=self.gate, realm=self.floor)
        self.assertIn("/sect recruitment ascend", hidden)
        self.assertIn("join a sect", hidden["/sect recruitment ascend"])

    def test_the_panel_reads_the_gate_off_the_content_not_a_name(self):
        source = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        body = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_sect_ascent_refusal")
        statements = body.body[1:] if isinstance(body.body[0], ast.Expr) else body.body
        # String constants, not `ast.unparse`'s spelling of them (v1.0.16: a
        # gate that reads quoting passes on the quoting it did not expect).
        strings = {n.value for s in statements for n in ast.walk(s) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertIn("ascends_to", strings)
        self.assertIn("recruitment", strings)
        self.assertFalse([s for s in strings if re.search(r"Jade Meridian|Azure Cloud|Stone Gate", s)], "the panel names a sect or a gate of its own")
        for s in statements:
            for node in ast.walk(s):
                if isinstance(node, ast.Compare):
                    self.assertFalse(any(isinstance(c, ast.Constant) and isinstance(c.value, int) for c in node.comparators),
                                     f"the panel compares against a literal: {ast.unparse(node)}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

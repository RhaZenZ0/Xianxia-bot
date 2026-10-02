"""Two doors on the panel opened for nobody (v1.17.1).

The world-flow study found them: the homestead asked sect rank 40 and
promotion by earned contribution stopped at 30, so only a GM's lever could
open it; the sect manor asked rank 70, a Go literal beside a promotion ladder
that is content; and the Personal World page opened at realm 5 for a world the
engine refuses below realm 30 and Space Law 100. Each is a page a player
reaches and finds locked for good, with nothing saying why.

Three rules now, and this file holds the Python side of each:

- **The homestead's rank is a rung the ladder reaches.** Deacon (40) is earned
  at 9,000 contribution; `test_the_sect_exchange.py` already holds the ladder's
  shape and `TestTheHomesteadsRankIsOnThePromotionLadder` holds the reach.
- **Every floor is content, read by the engine and the panel alike.**
  `sect_abode_system.manor_founding_rank_level` and
  `manor_construction_rank_level`, and `personal_world_system`, are what the
  Go reads (`two_doors_test.go` drives them with the floor moved); the panel
  reads the same keys and no literal of its own.
- **A door below its floor is hidden with a line naming the floor**, the
  rc.32 shape: the engine would refuse it outright, so the panel says what it
  asks rather than drawing a button that can only refuse. The Personal World
  page opens at the realm the content names, read off it by the authoring
  script rather than written there.
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

from tests.support import PROJECT_ROOT, code_only

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
RANKS = {int(r["level"]): str(r["name"]) for r in CONTENT["sect_system"]["ranks"]}
PROMOTION = {int(r["rank_level"]) for r in CONTENT["sect_system"]["exchange"]["promotion"]}
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


def _function_source(path, name: str) -> str:
    """One function's statements, without its docstring (rc.52)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                                     and isinstance(getattr(node.body[0], "value", None), ast.Constant)) else node.body
            return "\n".join(ast.unparse(stmt) for stmt in body)
    raise AssertionError(f"{name} not found in {path.name}; the reader is broken, not the tree")


def _go_function(filename: str, name: str) -> str:
    source = (GO / filename).read_text(encoding="utf-8")
    start = source.find(f"func {name}(")
    if start < 0:
        raise AssertionError(f"{name} not found in {filename}; the reader is broken, not the tree")
    end = source.find("\nfunc ", start + 1)
    body = source[start:end if end > 0 else None]
    return re.sub(r"//[^\n]*", "", body)


class _Db:
    """A member at `rank_level` in a sect, with a realm the test gives, who owns
    nothing and holds Space Law at `space`; every other read answers nothing."""

    def __init__(self, rank_level=None, space=0):
        self._rank = rank_level
        self._space = space

    async def get_sect_membership(self, _uid):
        if self._rank is None:
            return None
        return {"sect_name": "Azure Cloud Sect", "rank_level": self._rank, "rank_name": RANKS.get(self._rank, str(self._rank))}

    async def get_law_progress(self, _uid, law_id=None):
        return [{"law_id": "space", "comprehension": self._space}] if self._space else []

    def __getattr__(self, name):
        async def nothing(*_a, **_k):
            return None
        return nothing


def _hidden(surface, *, realm, rank_level, space=0):
    character = {"location": "Greenriver Town", "realm_index": realm, "phase": 1, "body_realm_index": 0, "body_phase": 1}
    interaction = SimpleNamespace(user=SimpleNamespace(id=7))
    with patch.object(surface, "DB", _Db(rank_level, space)):
        return asyncio.run(surface._progression_hidden_actions(interaction, character))


class TheContentReaches(unittest.TestCase):
    def test_the_homesteads_rank_is_a_rung_the_ladder_reaches(self):
        floor = int(CONTENT["abode_system"]["founding_rank_level"])
        self.assertIn(floor, PROMOTION, f"abode_system.founding_rank_level is {floor} ({RANKS.get(floor)}) and no rung of the promotion ladder reaches it: Establish is drawn for a door nobody can earn")

    def test_the_manors_ranks_and_the_worlds_floor_are_content(self):
        sect_abode = CONTENT["sect_abode_system"]
        self.assertIn(int(sect_abode["manor_founding_rank_level"]), RANKS)
        self.assertIn(int(sect_abode["manor_construction_rank_level"]), RANKS)
        self.assertLess(int(sect_abode["manor_construction_rank_level"]), int(sect_abode["manor_founding_rank_level"]),
                        "directing construction should ask less than founding the manor")
        world = CONTENT["personal_world_system"]
        self.assertGreater(int(world["min_realm_index"]), 0)
        self.assertLess(int(world["min_realm_index"]), len(CONTENT["realms"]))
        self.assertEqual(int(world["space_law_comprehension"]), 100, "the whole of Space Law is what folding a world has always asked")

    def test_the_personal_world_page_opens_where_the_engine_does(self):
        pages = CONTENT["feature_unlocks"]["pages"]
        self.assertEqual(int(pages.get("innerworld / Personal World") or 0), int(CONTENT["personal_world_system"]["min_realm_index"]),
                         "the Personal World page opens at a realm the engine refuses the world at")
        script = code_only((PROJECT_ROOT / "scripts" / "author_feature_unlocks.py").read_text(encoding="utf-8"))
        self.assertIn('"innerworld / Personal World": PERSONAL_WORLD_FLOOR', script, "the authoring script writes the page's floor by hand again")
        self.assertIn("personal_world_system", script, "the authoring script no longer reads the floor off the content")


class TheEngineReadsTheContent(unittest.TestCase):
    """The behavioural half is `two_doors_test.go`; this holds that the Go still
    asks the content rather than a number of its own."""

    def test_the_manor_asks_the_content(self):
        body = _go_function("sect_actions.go", "sectManorActionGo")
        self.assertIn("manorFoundingRankGo(catalog)", body)
        self.assertIn("manorConstructionRankGo(catalog)", body)
        for literal in ('< 70', '< 50'):
            self.assertNotIn(literal, body, f"the manor's rank is a literal again ({literal})")

    def test_the_world_asks_the_content(self):
        body = _go_function("property_storage_actions.go", "personalWorldCreateActionGo")
        self.assertIn("personalWorldFloorGo(catalog)", body)
        self.assertNotIn("< 30", body, "the world's realm is a literal again")
        self.assertNotIn("< 100", body, "the world's Space Law is a literal again")


class TheDoorsAreHiddenBelowTheirFloors(unittest.TestCase):
    def setUp(self):
        self.surface = _surface()

    def test_a_core_disciple_is_told_what_a_homestead_asks(self):
        hidden = _hidden(self.surface, realm=3, rank_level=30)
        self.assertIn("/abode establish", hidden)
        self.assertIn(RANKS[int(CONTENT["abode_system"]["founding_rank_level"])], hidden["/abode establish"])
        self.assertIn(RANKS[30], hidden["/abode establish"], "the line does not say what the player holds")

    def test_somebody_in_no_sect_is_told_the_same(self):
        hidden = _hidden(self.surface, realm=3, rank_level=None)
        self.assertIn("/abode establish", hidden)
        self.assertIn("no sect", hidden["/abode establish"])

    def test_a_deacon_is_shown_establish(self):
        hidden = _hidden(self.surface, realm=3, rank_level=40)
        self.assertNotIn("/abode establish", hidden, hidden.get("/abode establish"))

    def test_an_elder_is_told_what_the_manor_asks_and_may_direct_construction(self):
        hidden = _hidden(self.surface, realm=10, rank_level=50)
        self.assertIn("/sect manor establish", hidden)
        self.assertIn(RANKS[int(CONTENT["sect_abode_system"]["manor_founding_rank_level"])], hidden["/sect manor establish"])
        self.assertNotIn("/sect manor upgrade", hidden, "an Elder may direct construction and the door is hidden")

    def test_a_deacon_is_told_what_construction_asks(self):
        hidden = _hidden(self.surface, realm=10, rank_level=40)
        self.assertIn("/sect manor upgrade", hidden)
        self.assertIn(RANKS[int(CONTENT["sect_abode_system"]["manor_construction_rank_level"])], hidden["/sect manor upgrade"])

    def test_a_sect_master_is_shown_the_manor(self):
        hidden = _hidden(self.surface, realm=10, rank_level=70)
        self.assertNotIn("/sect manor establish", hidden, hidden.get("/sect manor establish"))

    def test_the_world_names_both_of_its_floors(self):
        hidden = _hidden(self.surface, realm=5, rank_level=30, space=40)
        self.assertIn("/innerworld create", hidden)
        line = hidden["/innerworld create"]
        self.assertIn(self.surface.WORLD.realm_name(int(CONTENT["personal_world_system"]["min_realm_index"])), line)
        self.assertIn("100%", line)
        self.assertIn("40%", line, "the line does not say where the player's Space Law stands")

    def test_a_dao_saint_with_the_whole_law_is_shown_create(self):
        hidden = _hidden(self.surface, realm=30, rank_level=30, space=100)
        self.assertNotIn("/innerworld create", hidden, hidden.get("/innerworld create"))

    def test_the_floors_are_read_off_the_content_not_written(self):
        source = _function_source(PROJECT_ROOT / "app" / "bot" / "surface.py", "_progression_hidden_actions")
        for literal in (" 40", " 70", " 50", " 30"):
            self.assertNotRegex(source, rf"[<>=]\s*{literal.strip()}\b", f"the panel compares against a literal {literal.strip()}")
        module = code_only((PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8"))
        for key in ("founding_rank_level", "manor_founding_rank_level", "manor_construction_rank_level", "personal_world_system"):
            self.assertIn(key, module, f"surface.py no longer reads {key} off the content")

    def test_the_manor_page_names_the_contents_rank(self):
        source = _function_source(PROJECT_ROOT / "app" / "bot" / "commands" / "sect.py", "sect_manor_status")
        self.assertIn("manor_founding_rank_level", source, "the manor page restates who may establish one")
        self.assertNotIn("Sect Master or Ancestor", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

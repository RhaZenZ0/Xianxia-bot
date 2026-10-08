"""A sect's doors follow its ranks (v1.25.0).

v1.19.4 opened every leaf of the `sect` hub to any member, and the owner's call
is that this was wrong: a member sees what their sect rank lets them do. The
realm curriculum still steps aside for a member (`_curriculum_opened`); what is
new is a padlock per rank floor, and the engine refusing the same act.

- **One table.** `sect_system.rank_floors` is keyed by engine operation, read by
  `requireSectRankTx` in Go and by `SECT_RANK_FLOORS` here. The panel names no
  rank of its own.
- **Every floor hides something.** `RANK_FLOOR_LEAVES` maps each operation to the
  leaves it hides and the phrase the engine's refusal opens with; a floor the
  table does not map would be a door the engine refuses and the panel draws.
- **The padlock names the rank and the rank held**, the way the manor's do.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
RANKS = {int(r["level"]): str(r["name"]) for r in CONTENT["sect_system"]["ranks"]}
FLOORS = {str(k): int(v) for k, v in CONTENT["sect_system"]["rank_floors"].items()}
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


class _Db:
    def __init__(self, rank_level=None):
        self._rank = rank_level

    async def get_sect_membership(self, _uid):
        if self._rank is None:
            return None
        return {"sect_name": "Azure Cloud Sect", "rank_level": self._rank, "rank_name": RANKS.get(self._rank, str(self._rank))}

    def __getattr__(self, name):
        async def nothing(*_a, **_k):
            return None
        return nothing


def _hidden(surface, rank_level):
    character = {"location": "Cloudblade City", "realm_index": 3, "phase": 1, "body_realm_index": 0, "body_phase": 1}
    interaction = SimpleNamespace(user=SimpleNamespace(id=7))
    with patch.object(surface, "DB", _Db(rank_level)):
        return asyncio.run(surface._progression_hidden_actions(interaction, character))


class TheFloorsAreOneTable(unittest.TestCase):
    def test_the_panel_reads_the_contents_floors(self):
        surface = _surface()
        self.assertTrue(FLOORS, "sect_system.rank_floors is empty; the gate is broken, not the tree")
        self.assertEqual(surface.SECT_RANK_FLOORS, FLOORS)

    def test_every_floor_is_a_rank_and_hides_something(self):
        surface = _surface()
        for op, floor in FLOORS.items():
            self.assertIn(floor, RANKS, f"{op} asks rank {floor}, which is not on sect_system.ranks")
            self.assertIn(op, surface.RANK_FLOOR_LEAVES, f"{op} has a floor and the panel hides nothing for it")

    def test_the_padlock_opens_with_the_engines_phrase(self):
        surface = _surface()
        go = "\n".join(p.read_text(encoding="utf-8") for p in GO.glob("*.go") if not p.name.endswith("_test.go"))
        for op, (what, _leaves) in surface.RANK_FLOOR_LEAVES.items():
            self.assertIn(f'requireSectRankTx(catalog, mem, "{op}", "{what}")', go,
                          f"the engine's refusal for {op} does not open with the panel's phrase {what!r}")


class TheDoorsFollowTheRank(unittest.TestCase):
    def test_an_outer_disciple_sees_none_of_the_higher_doors(self):
        surface = _surface()
        hidden = _hidden(surface, 10)
        for op, (what, leaves) in surface.RANK_FLOOR_LEAVES.items():
            if FLOORS.get(op, 0) <= 10:
                continue
            for leaf in leaves:
                self.assertIn("/" + leaf, hidden, f"an Outer Disciple is shown {leaf}, which asks {RANKS[FLOORS[op]]}")
                self.assertIn(RANKS[FLOORS[op]], hidden["/" + leaf])
                self.assertIn("you hold Outer Disciple", hidden["/" + leaf])
                self.assertTrue(hidden["/" + leaf].startswith(what))

    def test_each_rank_opens_exactly_its_doors(self):
        surface = _surface()
        for level in sorted(RANKS):
            hidden = _hidden(surface, level)
            for op, (_what, leaves) in surface.RANK_FLOOR_LEAVES.items():
                for leaf in leaves:
                    shut = "/" + leaf in hidden and "asks for" in hidden["/" + leaf]
                    self.assertEqual(shut, level < FLOORS.get(op, 0),
                                     f"a {RANKS[level]} {'is' if shut else 'is not'} padlocked out of {leaf} (floor {FLOORS.get(op)})")

    def test_somebody_in_no_sect_is_told_they_are_in_none(self):
        surface = _surface()
        hidden = _hidden(surface, None)
        self.assertIn("in no sect", hidden["/territory claim"])


class PeaceIsMadeByRank(unittest.TestCase):
    """v1.24.0's `war.peace` refuses below `war_system.peace_min_rank_level`;
    the panel padlocks it from the same key, and names the rank."""

    def test_the_panel_reads_the_engines_key(self):
        surface = _surface()
        self.assertEqual(surface.WAR_PEACE_RANK, int(CONTENT["war_system"]["peace_min_rank_level"]))

    def test_a_member_below_it_is_padlocked_and_one_at_it_is_not(self):
        surface = _surface()
        floor = surface.WAR_PEACE_RANK
        below = max(level for level in RANKS if level < floor)
        self.assertIn(RANKS[floor], _hidden(surface, below).get("/war peace", ""))
        self.assertNotIn("/war peace", _hidden(surface, floor))

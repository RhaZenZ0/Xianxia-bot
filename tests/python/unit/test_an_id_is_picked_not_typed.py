"""An id is picked, not typed (v1.11.0).

Reported from play: *"Tame ask for id when taming and each time you feed
them etcetera."* Every beast leaf took a bare `int` - the encounter id from
`/beast encounters`, the beast id from `/beast status` - so a player read a
number off one page and typed it into another. Each is a picker now, on the
panel (a hub option provider) and on the slash command (autocomplete over the
same rows).

Nineteen other parameters had the same shape. The beast five are fixed here; the rest are
named in `STILL_TYPED`, so the list can only shrink and a new command that asks
for a bare id fails the day it is written.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
COMMANDS = ROOT / "app" / "bot"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

# Commands that still ask for a typed id, each a picker nobody has built yet.
STILL_TYPED = {
    ("boss_claim", "encounter_id"), ("hunter_act", "pursuit_id"), ("crime_atone", "crime_id"),
    ("formation_assign", "formation_id"), ("formation_activate", "formation_id"),
    ("sect_discipleship_accept", "request_id"), ("sect_discipleship_reject", "request_id"),
    ("birth_family_investigate", "history_id"), ("birth_family_legacy", "history_id"),
    ("birth_family_quest", "history_id"), ("birth_family_quest", "quest_id"),
    ("birth_family_claim", "history_id"), ("birth_family_conflict", "claim_id"),
    ("caravan_events", "caravan_id"),
    ("auction_bid", "auction_id"), ("auction_appraise", "auction_id"),
    ("duel_respond", "challenge_id"), ("bond_respond", "partnership_id"),
}


def _beast():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.beast")


def _typed_ids() -> set[tuple[str, str]]:
    """Every registered command whose parameter is an int named `*_id`."""
    found: set[tuple[str, str]] = set()
    for path in COMMANDS.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not any("registered_group_command" in ast.unparse(d) or "registered_root_command" in ast.unparse(d)
                       for d in node.decorator_list):
                continue
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.arg.endswith("_id") and arg.annotation is not None and ast.unparse(arg.annotation) == "int":
                    found.add((node.name, arg.arg))
    return found


def _pickered() -> set[tuple[str, str]]:
    """Every (function, parameter) that registers a hub option provider."""
    found: set[tuple[str, str]] = set()
    for path in COMMANDS.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        loops: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Tuple):
                loops[node.target.id] = [ast.unparse(e) for e in node.iter.elts]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "register_hub_option_provider" and len(node.args) >= 2:
                target, param = ast.unparse(node.args[0]), node.args[1]
                if isinstance(param, ast.Constant):
                    for name in loops.get(target, [target]):
                        found.add((name, param.value))
    return found


class EveryIdIsAPicker(unittest.TestCase):
    def test_the_reader_finds_the_ids(self):
        typed = _typed_ids()
        self.assertIn(("equipment_equip", "equipment_id"), typed, "the reader is broken, not the tree")
        self.assertIn(("equipment_equip", "equipment_id"), _pickered(), "the provider reader is broken, not the tree")

    def test_no_command_asks_for_a_typed_id_unlisted(self):
        missing = sorted(_typed_ids() - _pickered() - STILL_TYPED)
        self.assertEqual(missing, [], "a command asks the player to type an id; give it a picker")

    def test_the_list_only_shrinks(self):
        stale = sorted(STILL_TYPED & _pickered())
        self.assertEqual(stale, [], "these have a picker now; take them off STILL_TYPED")
        gone = sorted(STILL_TYPED - _typed_ids())
        self.assertEqual(gone, [], "these no longer ask for an id; take them off STILL_TYPED")

    def test_every_beast_leaf_is_picked(self):
        for pair in [("beast_tame", "encounter_id"), ("beast_feed", "beast_id"), ("beast_train", "beast_id"),
                     ("beast_evolve", "beast_id"), ("beast_active", "beast_id")]:
            self.assertIn(pair, _pickered(), f"{pair} is typed again")
            self.assertNotIn(pair, STILL_TYPED)


class TheBeastPickerReadsTheBeasts(unittest.TestCase):
    ROWS = [
        {"beast_id": 7, "name": "Ash", "species": "Iron-Horn Boar", "rank": 4, "loyalty": 60,
         "evolution_stage": 1, "active": 0},
        {"beast_id": 3, "name": "Mist", "species": "Moon Serpent", "rank": 2, "loyalty": 30,
         "evolution_stage": 0, "active": 1},
    ]

    def _run(self, fn):
        beast = _beast()

        async def get_spirit_beasts(uid):
            return self.ROWS

        interaction = SimpleNamespace(user=SimpleNamespace(id=1))
        with patch.object(beast.DB, "get_spirit_beasts", get_spirit_beasts, create=True):
            return asyncio.run(getattr(beast, fn)(interaction, ""))

    def test_the_active_beast_comes_first_and_is_marked(self):
        options = self._run("beast_hub_options")
        self.assertEqual([o.value for o in options], [3, 7])
        self.assertEqual(options[0].emoji, "⭐")
        self.assertIn("Moon Serpent", options[0].label)

    def test_active_offers_only_a_beast_that_is_not(self):
        self.assertEqual([o.value for o in self._run("beast_inactive_hub_options")], [7])

    def test_the_slash_command_gets_the_same_rows(self):
        choices = self._run("beast_autocomplete")
        self.assertEqual([c.value for c in choices], [3, 7])


if __name__ == "__main__":
    unittest.main()

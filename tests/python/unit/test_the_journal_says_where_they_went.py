"""A quest's person says where they are now (v1.8.3).

Reported from play: a commission asked for "Have a word with Fishmonger Le
Wan", and the docks - where the commission and the content file both put him -
had nobody on them. The civilization tick walks ordinary townsfolk to the next
district and home again (`npc_movement.go`), and since v1.0.8 the `/talk` and
`/npcinfo` pickers offer only who is in the room, so nothing told a player
where to look. The journal names it now, under `/npcinfo`'s rule: a place the
player has not discovered is not named, and a missing person is never placed.
"""

from __future__ import annotations

import asyncio
import ast
import importlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
PLAYER = {"user_id": 1, "location": "Moonfen Mist Docks"}
TALK = {"id": "word", "type": "talk", "target": "Fishmonger Le Wan", "count": 1, "label": "Have a word with Fishmonger Le Wan"}


def _locations():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.locations")


class _Sim:
    def __init__(self, state):
        self.state = state

    async def npc_status(self, name):
        return self.state


def _suffix(state, where, visible=True, objective=TALK, done=False):
    locations = _locations()

    async def resolved(name, period=None):
        return where

    async def is_visible(user_id, character, location):
        return visible

    with patch.object(locations, "SIM", _Sim(state)), \
         patch.object(locations, "current_npc_location", resolved), \
         patch.object(locations, "_location_is_visible", is_visible):
        return asyncio.run(locations.objective_line_suffix(1, PLAYER, objective, done))


class TheJournalSaysWhereTheyWent(unittest.TestCase):
    def test_somebody_who_walked_off_is_named_where_they_went(self):
        line = _suffix({"status": "alive"}, "Moonfen City")
        self.assertEqual(line, " · *Fishmonger Le Wan is now at **Moonfen City***")

    def test_somebody_in_the_room_is_here(self):
        self.assertIn("is here with you", _suffix({"status": "alive"}, "Moonfen Mist Docks"))

    def test_a_place_the_player_has_not_found_is_not_named(self):
        line = _suffix({"status": "alive"}, "Ashen Hollow", visible=False)
        self.assertIn("somewhere you have not been", line)
        self.assertNotIn("Ashen Hollow", line)

    def test_a_missing_person_is_never_placed(self):
        line = _suffix({"status": "missing"}, "Ashen Hollow")
        self.assertIn("missing", line)
        self.assertNotIn("Ashen Hollow", line)

    def test_a_missing_person_the_searcher_stands_beside_is_here(self):
        """v1.20.2: the rule every reply asks now. A searcher who arrived has
        found them, and saying "nobody knows where" there would contradict the
        `/talk` that reports the find."""
        self.assertIn("is here with you", _suffix({"status": "missing"}, "Moonfen Mist Docks"))

    def test_the_dead_are_said_to_be(self):
        locations = _locations()
        self.assertIn("has died", _suffix({"status": "dead"}, locations.DEAD))

    def test_only_an_unfinished_talk_objective_carries_a_note(self):
        self.assertEqual(_suffix({"status": "alive"}, "Moonfen City", done=True), "")
        self.assertEqual(_suffix({"status": "alive"}, "Moonfen City", objective={**TALK, "type": "explore"}), "")

    def test_a_lookup_that_fails_costs_the_note_and_not_the_journal(self):
        locations = _locations()

        class Broken:
            async def npc_status(self, name):
                raise RuntimeError("engine down")

        with patch.object(locations, "SIM", Broken()):
            self.assertEqual(asyncio.run(locations.objective_line_suffix(1, PLAYER, TALK, False)), "")

    def test_both_journals_draw_it(self):
        tree = ast.parse((ROOT / "app" / "bot" / "commands" / "character.py").read_text(encoding="utf-8"))
        callers = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "objective_line_suffix"
        }
        self.assertEqual(callers, {"quests_command", "_player_dashboard_card"})


if __name__ == "__main__":
    unittest.main()

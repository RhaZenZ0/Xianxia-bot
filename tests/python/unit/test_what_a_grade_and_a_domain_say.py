"""What a grade does is printed, and a Domain is not the opponent's (v1.12.3).

Three presentation halves of engine fixes, each held where only Python can see:

- **The recovery picker printed base figures.** ``_battle_available_options`` built
  the line under a pill from the content's own ``instant`` numbers, while the
  engine heals at the carried grade. That half is held by
  ``test_the_recovery_picker_reads_the_grade.py``, which tested the same twin.
- **The opponent card printed the engine's own bookkeeping.** The battle row's
  ``opponent_modifiers_json`` records which effects have landed as
  ``effect:<id>`` beside the stats (a control effect lands once a battle), and
  ``opponent_debuff_label`` is the one thing that prints it.
- **The capped-grade reply named Tier 7.** A trade stops rising at rank 6, so the
  reply says the road the engine reports: a rank *and* an opener.
"""

from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

import pytest

from app.rules.battle import opponent_debuff_label
from app.rules.game import World
from app.rules.item_grades import grade_cap_note
from app.rules.progression_systems import profession_rank
from tests.support import PROJECT_ROOT

pytestmark = pytest.mark.unit

WORLD = World(PROJECT_ROOT / "content" / "world.json")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _battle():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.battle")


class TheOpponentCardSkipsTheEnginesRecord(unittest.TestCase):
    def test_the_mark_of_a_landed_effect_is_not_a_stat(self):
        raw = {"agility": -3, "escape_bonus": -5, "effect:spatial_lockdown": 1}
        self.assertEqual(opponent_debuff_label(raw), "agility -3 · escape -5")
        self.assertEqual(opponent_debuff_label({"effect:world_collapse": 1}), "")


class TheCappedGradeReplySaysTheRealRoad(unittest.TestCase):
    def test_the_note_names_the_rank_and_the_opener(self):
        rank = profession_rank(6, "Forging")
        note = grade_cap_note("Transcendent", "Mid", rank, "a fully refined flame")
        self.assertIn(f"Transcendent needs **{rank}** and **a fully refined flame**.", note)
        self.assertNotIn("Tier 7", note, "the note names a rank no trade reaches")

    def test_a_rung_with_no_opener_names_the_rank_alone(self):
        note = grade_cap_note("High", "Mid", profession_rank(3, "Alchemy"), "")
        self.assertTrue(note.endswith("**."), note)
        self.assertNotIn(" and **", note)

    def test_the_reply_hands_the_engines_answer_to_the_note(self):
        # The wire: the craft reply reads both fields the engine reports and
        # builds its note through the one helper.
        source = (PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "grade_cap_note"]
        self.assertEqual(len(calls), 1, "the capped-grade reply no longer builds its note through grade_cap_note")
        read = {n.value for n in ast.walk(calls[0]) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertIn("grade_reached_opener", read, "the reply does not hand the engine's opener to the note")


if __name__ == "__main__":
    unittest.main()

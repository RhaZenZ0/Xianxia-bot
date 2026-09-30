"""What a grade does is printed, and a Domain is not the opponent's (v1.12.3).

Three presentation halves of engine fixes, each held where only Python can see:

- **The recovery picker printed base figures.** ``_battle_available_options`` built
  the line under a pill from the content's own ``instant`` numbers, while the
  engine heals at the carried grade (``itemEffectMult``), so a High pill read as
  a Low one. ``app.rules.item_grades.effect_mult`` / ``graded_amount`` are the
  display twins of the engine's ``itemEffectMult`` / ``gradedAmount``.
- **The opponent card printed the engine's own bookkeeping.** The battle row's
  ``opponent_modifiers_json`` records which effects have landed as
  ``effect:<id>`` beside the stats (a control effect lands once a battle), and
  ``opponent_debuff_label`` is the one thing that prints it.
- **The capped-grade reply named Tier 7.** A trade stops rising at rank 6, so the
  reply says the road the engine reports: a rank *and* an opener.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import os
import unittest
from unittest.mock import patch

import pytest

from app.rules.battle import opponent_debuff_label
from app.rules.game import World
from app.rules.item_grades import effect_mult, grade_cap_note, graded_amount
from app.rules.progression_systems import profession_rank
from tests.support import PROJECT_ROOT

pytestmark = pytest.mark.unit

WORLD = World(PROJECT_ROOT / "content" / "world.json")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _battle():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.battle")


class TheGradeTwinIsTheEngines(unittest.TestCase):
    def test_a_grade_multiplies_by_the_ladders_own_number(self):
        ladder = WORLD.item_grades
        self.assertEqual(effect_mult(ladder, "recovery_pill"), 1.0)
        self.assertEqual(effect_mult(ladder, "recovery_pill@mid"), 1.25)
        self.assertEqual(effect_mult(ladder, "recovery_pill@high"), 1.5)
        self.assertEqual(effect_mult(ladder, "recovery_pill@transcendent"), 3.0)

    def test_an_unknown_grade_is_worth_one_not_the_bottom_rung(self):
        # The engine's itemEffectMult answers 1 for an id itemDef refuses; a
        # fallback that looked like a value would hand it some rung's number.
        self.assertEqual(effect_mult(WORLD.item_grades, "recovery_pill@legendary"), 1.0)

    def test_a_quantity_rounds_half_away_from_zero_and_never_shrinks(self):
        # Go's math.Round: 2 x 1.25 = 2.5 -> 3. Python's round() gives 2.
        self.assertEqual(graded_amount(2, 1.25), 3)
        self.assertEqual(graded_amount(12, 1.5), 18)
        self.assertEqual(graded_amount(8, 1.0), 8)
        self.assertEqual(graded_amount(3, 0.5), 3, "a grade only ever adds")
        self.assertEqual(graded_amount(0, 3.0), 0)


class TheRecoveryPickerPrintsWhatTheGradeHeals(unittest.TestCase):
    def test_a_high_pill_reads_higher_than_a_low_one(self):
        battle = _battle()
        base = WORLD.item_definition("recovery_pill")["use"]["instant"]
        vitality = int(base["vitality_restore"])
        self.assertGreater(vitality, 0, "the reader found no vitality restore; the gate is broken, not the tree")

        class _DB:
            async def get_law_progress(self, _user_id):
                return []

            async def get_manuals(self, _user_id):
                return []

            async def get_inventory(self, _user_id):
                return {"recovery_pill": 1, "recovery_pill@high": 1}

        with patch.object(battle, "DB", _DB()):
            _, usable = asyncio.run(battle._battle_available_options(1, {"realm_index": 0}))
        lines = {item_id: description for item_id, _label, description in usable}
        self.assertEqual(lines["recovery_pill"], f"Vitality +{vitality}" + (f" • Qi +{int(base['qi_restore'])}" if int(base.get("qi_restore", 0)) else ""))
        self.assertIn(f"Vitality +{graded_amount(vitality, 1.5)}", lines["recovery_pill@high"],
                      "the picker printed the base figure for a High pill, which heals at x1.5")


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

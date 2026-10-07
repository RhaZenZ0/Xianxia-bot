"""The battle's recovery picker prints a pill at its grade (v1.12.3).

`_battle_available_options` described a recovery item from
`WORLD.item_definition(iid)`, which is the *base* entry's use with its name and
price graded - so a Mid Recovery Pill read "Vitality +8" in the picker while
the engine restored 10 (`gradedAmount`, the grade's `effect_mult`). The number
a player chooses by was the Low pill's.

`effect_mult` and `graded_amount` in `app/rules/item_grades.py` are the display
twins of the engine's `itemEffectMult` and `gradedAmount`. The twins are held to
the ladder in the content file and to the engine's rounding (half away from
zero, never below the base), and the picker is driven with a carried graded pill
rather than read, because the source reads correctly in both versions.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from unittest.mock import patch

from app.rules.item_grades import effect_mult, graded_amount
from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "item_grade.go"


def _battle():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.battle")


class TheTwinsAgreeWithTheEngine(unittest.TestCase):
    def setUp(self):
        self.battle = _battle()
        self.ladder = self.battle.WORLD.item_grades
        self.assertTrue(self.ladder.get("grades"), "the ladder read found no grades; the gate is broken, not the tree")

    def test_every_rung_of_the_ladder_is_its_effect_mult(self):
        for index, rung in enumerate(self.ladder["grades"]):
            item_id = "recovery_pill" if index == 0 else f"recovery_pill@{rung['key']}"
            with self.subTest(rung=rung["key"]):
                self.assertEqual(effect_mult(self.ladder, item_id), float(rung.get("effect_mult") or 1))

    def test_a_bare_unknown_or_unusable_rung_is_one(self):
        self.assertEqual(effect_mult(self.ladder, "recovery_pill"), 1.0)
        self.assertEqual(effect_mult(self.ladder, "recovery_pill@not_a_rung"), 1.0)
        self.assertEqual(effect_mult({}, "recovery_pill@high"), 1.0)
        self.assertEqual(effect_mult({"grades": [{"key": "high", "effect_mult": 0}]}, "x@high"), 1.0)

    def test_rounding_is_the_engines_half_away_from_zero_and_never_below_the_base(self):
        # 15 x 1.5 = 22.5: the engine's math.Round gives 23, Python's round() gives 22.
        self.assertEqual(graded_amount(15, 1.5), 23)
        self.assertEqual(graded_amount(5, 1.5), 8)
        self.assertEqual(graded_amount(8, 1.25), 10)
        self.assertEqual(graded_amount(8, 1.0), 8)
        self.assertEqual(graded_amount(8, 0.5), 8, "a grade only ever adds")
        self.assertEqual(graded_amount(0, 3.0), 0)
        self.assertEqual(graded_amount(-4, 3.0), 0)

class TheBattlePickerPrintsTheGrade(unittest.TestCase):
    def setUp(self):
        self.battle = _battle()
        self.world = self.battle.WORLD
        self.vit = int(self.world.items["recovery_pill"]["use"]["instant"]["vitality_restore"])
        self.qi = int(self.world.items["qi_replenishment_pill"]["use"]["instant"]["qi_restore"])
        self.ladder = {r["key"]: float(r.get("effect_mult") or 1) for r in self.world.item_grades["grades"]}

    def _options(self, inventory):
        async def nothing(*_a, **_k):
            return []

        async def get_inventory(_uid):
            return inventory

        with patch.object(self.battle.DB, "get_law_progress", nothing), \
                patch.object(self.battle.DB, "get_manuals", nothing), \
                patch.object(self.battle.DB, "get_inventory", get_inventory):
            _techniques, usable = asyncio.run(self.battle._battle_available_options(7, {"realm_index": 0}))
        return {item_id: description for item_id, _name, description in usable}

    def test_a_graded_pill_is_described_at_its_grade(self):
        inventory = {"recovery_pill": 1, "recovery_pill@mid": 1, "recovery_pill@high": 1, "qi_replenishment_pill@high": 1}
        shown = self._options(inventory)
        self.assertEqual(set(shown), set(inventory), "a carried pill is missing from the picker; the drive is broken, not the tree")
        self.assertEqual(shown["recovery_pill"], f"Vitality +{self.vit}")
        self.assertEqual(shown["recovery_pill@mid"], f"Vitality +{graded_amount(self.vit, self.ladder['mid'])}")
        self.assertEqual(shown["recovery_pill@high"], f"Vitality +{graded_amount(self.vit, self.ladder['high'])}")
        self.assertEqual(shown["qi_replenishment_pill@high"], f"Qi +{graded_amount(self.qi, self.ladder['high'])}")
        self.assertNotEqual(shown["recovery_pill@mid"], shown["recovery_pill"],
                            "a Mid pill is described as the Low pill: its grade changes the number")


if __name__ == "__main__":
    unittest.main()

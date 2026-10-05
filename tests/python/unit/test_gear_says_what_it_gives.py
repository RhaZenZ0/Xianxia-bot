"""What a weapon, armour or beast gives, said where a player looks (v1.7.5).

Asked for as "a description to each weapon or beast, or armour, how much and
what chance they give". The numbers were already in the tree and printed
nowhere useful: the equipment card showed a raw ATK/DEF/Spirit/Agility list,
shops and merchants showed a name and a price, and a beast's card showed rank
and loyalty without saying what they add to a fight.

Three things are held here.

* **The display twins agree with the engine.** Go owns every formula; the
  Python helpers in ``app/rules/advanced_runtime.py`` are read off the same
  expressions, and this file reads those expressions out of the Go source so a
  retune of either half fails here rather than printing a wrong promise.
* **A graded piece is described at its grade.** The equipment card looked a
  carried id up in ``EQUIPMENT_DEFINITIONS`` with a bare ``.get``, and a graded
  id (``spirit_iron_sword@high``) is not a key there, so every High sword said
  "No stat modifiers" - the grade existed and the card could not see it.
* **Spirit is never shown.** Every definition carries it and no rule reads it,
  so a line advertising it would be a promise the engine does not keep.
  Agility is the one real percentage: each point is one point of boss-raid hit
  chance. Everything else is a modifier on a 2d10 roll and is shown as a number.
"""

from __future__ import annotations

import ast
import json
import re
import unittest

from tests.support import PROJECT_ROOT

from app.rules.advanced_runtime import (
    EQUIPMENT_DEFINITIONS,
    _go_round,
    companion_bonus,
    describe_equipment,
    equipment_effective,
    equipment_quality_mult,
    grade_equipment_quality,
)

GO_GAME = PROJECT_ROOT / "go_core" / "internal" / "game"
COMMANDS = PROJECT_ROOT / "app" / "bot" / "commands"
LADDER = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))["item_grade_system"]


def _go(name: str) -> str:
    return (GO_GAME / name).read_text(encoding="utf-8")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


class TheTwinsReadTheEnginesExpressions(unittest.TestCase):
    """Each Python twin is held to the Go expression it restates."""

    def test_quality_scales_gear_the_way_the_engine_does(self):
        source = _squash(_go("item_grade.go"))
        self.assertIn("returnmath.Max(0.5,1+(float64(quality)-100)/200)", source)
        self.assertIn("returnint64(math.Round(100+200*(mult-1)))", source)
        for quality, mult in ((100, 1.0), (150, 1.25), (200, 1.5), (0, 0.5), (-400, 0.5)):
            self.assertAlmostEqual(equipment_quality_mult(quality), mult)
        for rung in LADDER["grades"]:
            mult = float(rung["effect_mult"])
            self.assertAlmostEqual(equipment_quality_mult(grade_equipment_quality(mult)), mult)

    def test_a_one_on_one_fight_scales_by_condition_and_rounds_per_stat(self):
        source = _squash(_go("combat_actions.go"))
        self.assertIn("ifcondition<0.25{condition=0.25}ifcondition>1{condition=1}", source)
        self.assertIn("attack+=int64(math.Round(float64(d[0])*condition*quality))", source)

    def test_a_beast_adds_half_its_rank_its_stage_a_point_per_forty_loyalty_and_its_milestones(self):
        source = _squash(_go("combat_actions.go"))
        self.assertIn("SELECTrank,evolution_stage,loyalty,intelligenceFROMspirit_beasts", source)
        self.assertIn("bonus+=i64(r.Rows[0][0])/2+i64(r.Rows[0][1])+i64(r.Rows[0][2])/40+beastMilestoneBonus(i64(r.Rows[0][0]))+beastIntelligenceBonus(i64(r.Rows[0][3]))", source)
        self.assertIn("funcbeastIntelligenceBonus(intelligenceint64)int64{returnminI64(beastIntelligenceCap,maxI64(0,intelligence)/25)}", source)
        self.assertIn("constbeastIntelligenceCap=int64(4)", source)
        self.assertEqual(companion_bonus(0, 0, 0, 80), 3)
        self.assertEqual(companion_bonus(0, 0, 0, 1000), 4)
        milestone = _squash(_go("beast_artifact_actions.go"))
        self.assertIn("funcbeastMilestoneBonus(rankint64)int64{ifrank<10{return0}return2*(rank/10)}", milestone)
        self.assertEqual(companion_bonus(5, 2, 79), 2 + 2 + 1)
        self.assertEqual(companion_bonus(1, 0, 39), 0)
        self.assertEqual(companion_bonus(8, 3, 100), 4 + 3 + 2)
        # Every tenth rank adds two (the milestones merged from main in v1.7.7).
        self.assertEqual(companion_bonus(10, 0, 0), 5 + 2)
        self.assertEqual(companion_bonus(25, 1, 40), 12 + 1 + 1 + 4)

    def test_each_point_of_agility_is_one_point_of_raid_hit_chance(self):
        source = _squash(_go("group_combat_actions.go"))
        self.assertIn('accuracy:=65+agi*2+equip["agility"]-phase.Defense*2', source)

    def test_the_taming_line_names_the_terms_the_roll_adds(self):
        source = _squash(_go("beast_artifact_actions.go"))
        self.assertIn(
            'modifier:=character.Attributes["spirit"]+character.Attributes["presence"]+character.Attributes["will"]/2+pathBonus+bondExperience+tamingLevel',
            source,
        )
        self.assertIn("pathBonus=4", source)
        self.assertIn("bondExperience:=minI64(count,4)", source)

    def test_go_rounds_half_away_from_zero(self):
        self.assertEqual([_go_round(x) for x in (2.5, 3.5, -2.5, 0.49)], [3, 4, -3, 0])


class APieceIsDescribedAtItsGrade(unittest.TestCase):
    def test_a_graded_sword_is_worth_its_grade(self):
        self.assertEqual(equipment_effective("spirit_iron_sword", ladder=LADDER)["attack"], 4)
        self.assertEqual(equipment_effective("spirit_iron_sword@high", ladder=LADDER)["attack"], 6)
        self.assertEqual(equipment_effective("spirit_iron_sword@transcendent", ladder=LADDER)["attack"], 12)
        self.assertIn("+6 attack", describe_equipment("spirit_iron_sword@high", ladder=LADDER))

    def test_an_equipped_piece_uses_its_own_quality_and_condition(self):
        # Quarter durability is the floor of the condition scale.
        self.assertEqual(equipment_effective("spirit_iron_sword", quality=100, durability=1, max_durability=120)["attack"], 1)
        self.assertEqual(equipment_effective("spirit_iron_sword", quality=200, durability=120, max_durability=120)["attack"], 6)

    def test_spirit_is_never_shown_and_a_penalty_is(self):
        for item_id in EQUIPMENT_DEFINITIONS:
            self.assertNotIn("spirit", describe_equipment(item_id).lower(), item_id)
        self.assertIn("-1 agility (-1% raid hit)", describe_equipment("spirit_iron_armor"))

    def test_something_that_is_not_gear_says_nothing(self):
        self.assertEqual(describe_equipment("qi_pill"), "")
        self.assertEqual(describe_equipment("qi_pill@high", ladder=LADDER), "")


def _calls(path, function: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            names = set()
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    target = inner.func
                    names.add(target.id if isinstance(target, ast.Name) else getattr(target, "attr", ""))
            return names
    raise AssertionError(f"{function} is not defined in {path.name}; the gate is broken, not the tree")


class EverySurfaceSaysIt(unittest.TestCase):
    SITES = (
        # v1.9.1: the card also prints the item's description and its passive.
        ("equipment.py", "equipment_status", "describe_equipment_in_full"),
        ("equipment.py", "_equipment_option", "describe_equipment"),
        ("equipment.py", "equipment_bind_hub_options", "describe_equipment"),
        ("character.py", "inventory", "describe_equipment"),
        ("economy.py", "shop_browse", "_gear_line"),
        ("economy.py", "_merchant_stock_lines", "_gear_line"),
        ("economy.py", "_stall_listing_line", "_gear_line"),
        ("economy.py", "market_prices_command", "_gear_line"),
        ("beast.py", "beast_status", "_companion_line"),
        ("beast.py", "beast_active", "companion_bonus"),
    )

    def test_each_place_gear_or_a_beast_is_shown_describes_it(self):
        missing = [f"{fn} ({name})" for name, fn, helper in self.SITES if helper not in _calls(COMMANDS / name, fn)]
        self.assertEqual(missing, [], "these surfaces show gear or a beast without saying what it gives")

    def test_no_command_looks_gear_up_by_its_raw_id(self):
        offenders = [
            path.name
            for path in sorted(COMMANDS.glob("*.py"))
            if "EQUIPMENT_DEFINITIONS.get(" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], "a bare EQUIPMENT_DEFINITIONS.get misses every graded id; use equipment_definition")


if __name__ == "__main__":
    unittest.main()

"""What a spiritual root is worth (v1.0.0-rc.55).

`spiritual_root_system.grades` is a six-rung ladder and every rung carries two
mechanical numbers: a `cultivation_mult` (Mortal 0.88 through Immortal 1.34)
and a `breakthrough_bonus` (-1 through +3). Both were parsed into
`worlddata.RootGrade` and **read by nothing**. Of that struct's eight fields,
the six that decide how a root is *made* - the roll band, the element chances,
the mutation chance, the realm a grade may evolve at - were all read, and the
only two that decide what having it is *worth* were the two that were not. The
grade on a cultivator's sheet decided everything about how they were made and
nothing about what they were.

Nor was that only a creation roll: `aptitude.evolve` lets a player climb the
ladder a rung at a time, paying stability for a failure and risking a forced
mutation, and the whole payoff of that climb was these two numbers.

The grade did reach cultivation by one flatter route - `grade_bonus_per_rank`
in `elemental_qi_system`, 0.02 a rung - which was a second statement of a rule
the root system already authored, living in a system named for the five phases,
and folded into the *element* multiplier, which the bot declines to print when
the relation is indifferent. So the authored 1.52x spread was live as 1.10x,
under another name, unseen.

This is the content half of the gate. What the numbers *do* is behavioural and
belongs to Go - `spiritual_root_worth_test.go` drives a real session and a real
breakthrough - because a grep cannot see a condition somebody disabled. What is
checked here is that the content is whole, that the rule is stated once, and
that Python's display twin agrees with the formula the engine reads.
"""
from __future__ import annotations

import json
import unittest

from app.rules.aptitudes import root_cultivation_mult
from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ROOT_SYSTEM = WORLD["spiritual_root_system"]
GRADES = list(ROOT_SYSTEM["grades"])

SEARCH_ROOTS = ("app", "go_core", "scripts", "dashboard")
SEARCH_SUFFIXES = (".py", ".go", ".js")

# A file that states what a grade is worth anyway, with the reason it may.
# Empty, and empty the day it was written: `rootWorthMultiplier` is the one
# reader in the engine and `root_cultivation_mult` the one in presentation,
# and both read the content rather than restating it.
RESTATES_THE_LADDER: dict[str, str] = {}


class TheLadderIsWhole(unittest.TestCase):
    def test_every_rung_carries_both_numbers(self):
        self.assertGreaterEqual(len(GRADES), 2)
        for grade in GRADES:
            name = str(grade.get("name", ""))
            self.assertTrue(name, f"a rung with no name: {grade}")
            self.assertIn("cultivation_mult", grade, name)
            self.assertIn("breakthrough_bonus", grade, name)
            self.assertGreater(float(grade["cultivation_mult"]), 0, name)

    def test_both_ladders_climb_with_the_roll_band(self):
        rolls = [int(g["min_roll"]) for g in GRADES]
        self.assertEqual(rolls, sorted(rolls), "the rungs are not in roll order")
        mults = [float(g["cultivation_mult"]) for g in GRADES]
        self.assertEqual(mults, sorted(mults), f"cultivation does not climb: {mults}")
        bonuses = [int(g["breakthrough_bonus"]) for g in GRADES]
        self.assertEqual(bonuses, sorted(bonuses), f"breakthroughs do not climb: {bonuses}")
        # A rarer root must actually be better, or the ladder is decoration.
        self.assertGreater(mults[-1], mults[0])
        self.assertGreater(bonuses[-1], bonuses[0])

    def test_the_common_rung_is_the_baseline_everything_else_is_priced_against(self):
        common = next(g for g in GRADES if str(g["name"]) == "Common")
        self.assertEqual(float(common["cultivation_mult"]), 1.0)
        self.assertEqual(int(common["breakthrough_bonus"]), 0)

    def test_purity_belongs_to_the_root_and_not_to_the_cycle(self):
        # It lived under `elemental_qi_system` until rc.55, which is a system
        # about the five phases and what a method's qi is to a root. Purity is
        # neither, and Python has no accessor for that system at all - which is
        # exactly why app/rules/aptitudes.py used to invent its own factor.
        self.assertIn("purity_bonus_at_full", ROOT_SYSTEM)
        self.assertGreater(float(ROOT_SYSTEM["purity_bonus_at_full"]), 0)
        self.assertNotIn("purity_bonus_at_full", WORLD["elemental_qi_system"])
        self.assertNotIn("grade_bonus_per_rank", WORLD["elemental_qi_system"])


class TheRuleIsStatedOnce(unittest.TestCase):
    def test_no_production_file_restates_the_grade_ladder(self):
        offenders = {}
        for root in SEARCH_ROOTS:
            base = PROJECT_ROOT / root
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if path.suffix not in SEARCH_SUFFIXES or path.name.endswith("_test.go"):
                    continue
                rel = str(path.relative_to(PROJECT_ROOT))
                if "grade_bonus_per_rank" in path.read_text(encoding="utf-8", errors="ignore"):
                    offenders[rel] = "names grade_bonus_per_rank"
        self.assertEqual(
            {k: v for k, v in offenders.items() if k not in RESTATES_THE_LADDER}, {},
            "the grade's worth is stated in spiritual_root_system.grades and read from there",
        )

    def test_the_engine_half_still_exists(self):
        # The only thing Python can honestly check about Go from here is that
        # the reader is present and called where it matters; whether the
        # condition inside it is live is what the Go test drives.
        go = PROJECT_ROOT / "go_core" / "internal" / "game"
        worth = (go / "root_worth.go").read_text(encoding="utf-8")
        self.assertIn("func rootWorthMultiplier(", worth)
        self.assertIn("func rootGradeBreakthroughBonus(", worth)
        actions = (go / "cultivation_actions.go").read_text(encoding="utf-8")
        self.assertIn("rootMult := rootWorthMultiplier(catalog, bundle.Root)", actions)
        self.assertIn("* elementMult * rootMult))", actions)
        aptitudes = (go / "aptitude_actions.go").read_text(encoding="utf-8")
        self.assertIn("rootGradeBreakthroughBonus(catalog, bundle.Root)", aptitudes)


class PresentationAgreesWithTheEngine(unittest.TestCase):
    """One authored number, two readers that agree.

    Python cannot call Go, so `app/rules/aptitudes.py` keeps a copy of the
    formula for the effects list. The honest bar is not "stated once" but that
    both readers read the same authored numbers - which is the thing that was
    false before rc.55, when Python multiplied the grade by invented purity,
    mixed-element, compatibility and stability factors and the engine read
    none of it.
    """

    def test_the_display_twin_is_the_engines_formula(self):
        bonus = float(ROOT_SYSTEM["purity_bonus_at_full"])
        for grade in GRADES:
            for purity in (0, 37, 50, 100):
                want = round(float(grade["cultivation_mult"]) * (1 + bonus * purity / 100.0), 4)
                got = root_cultivation_mult(ROOT_SYSTEM, str(grade["name"]), purity)
                self.assertAlmostEqual(got, want, places=4, msg=f"{grade['name']} at purity {purity}")

    def test_a_grade_off_the_ladder_is_worth_one_not_the_bottom_rung(self):
        # `admin.player.set_spiritual_root` writes the column with no check
        # against the ladder, and the engine's `gradeIndex` answers 0 for a
        # name it does not know - so the careless fallback hands an unknown
        # grade Mortal's 0.88 and -1. A fallback that looks like a value is
        # not a sentinel. 'Heavenly' is the real case: it sat in the Go test
        # fixtures for releases.
        for unknown in ("Heavenly", "", "Divine", "mortal-ish"):
            self.assertEqual(root_cultivation_mult(ROOT_SYSTEM, unknown, 100), 1.0, unknown)

    def test_the_effect_carries_the_authored_numbers_and_invents_nothing(self):
        from app.rules.aptitudes import aptitude_effects

        bundle = {"root": {"grade": "Heaven", "purity": 80, "elements": ["Fire", "Metal"],
                           "mutation": "", "stability": 100}}
        effects = aptitude_effects(
            bundle, root_system=ROOT_SYSTEM,
            bloodline_definitions=WORLD["bloodlines"], physique_definitions=WORLD["physiques"],
        )
        root_effect = next(e for e in effects if e["effect_key"] == "innate_spiritual_root")
        mods = {m["stat"]: m["value"] for m in root_effect["modifiers"]}
        heaven = next(g for g in GRADES if str(g["name"]) == "Heaven")
        self.assertEqual(mods["breakthrough_bonus"], int(heaven["breakthrough_bonus"]))
        self.assertAlmostEqual(mods["cultivation_gain"],
                               root_cultivation_mult(ROOT_SYSTEM, "Heaven", 80), places=4)
        # Two elements and a middling purity used to drag the number down by
        # factors the engine never applied; nothing but grade and purity now.
        self.assertAlmostEqual(
            mods["cultivation_gain"],
            round(float(heaven["cultivation_mult"]) * (1 + float(ROOT_SYSTEM["purity_bonus_at_full"]) * 0.8), 4),
            places=4,
        )


if __name__ == "__main__":
    unittest.main()

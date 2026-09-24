"""The last lesson (v1.0.0-rc.34): the Python-owned boundary.

The head of the house teaches and tests the new cultivator at home, once per
life (`household_lesson.go`): one demonstration roll, a day's wait on a
failure, and on a pass level 0 in all four trades, the house's manual studied
once, its story into the chronicle and a keepsake. Python authors the words the
head speaks and the stage that asks for it, asks the engine, and prints. What
is held here is the shape of that content and the wiring the engine relies on.
"""

from __future__ import annotations

import json
import unittest

from app.rules.quests import OBJECTIVE_TYPES, validate_quest_definition
from tests.python.contracts.test_beginner_path import BUDGET, WORLD
from tests.python.unit.test_quest_objective_reporters import reported_objective_types
from tests.support import PROJECT_ROOT

GO = PROJECT_ROOT / "go_core" / "internal" / "game"
BOT = PROJECT_ROOT / "app" / "bot"
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LESSONS = dict(CONTENT["birth_family_lesson"])
SENDOFF = dict(CONTENT["birth_family_sendoff"])
MANUALS = dict(CONTENT["technique_system"]["manuals"])
STAGES = list(CONTENT["beginner_path"])


class EveryHouseHasALessonToGive(unittest.TestCase):
    def test_every_archetype_the_sendoff_names_has_one(self):
        """Every starter household - the thirteen a character is created into.

        v1.3.0 gave the thirty-three upper-world houses a samsara rebirth can
        land in send-offs of their own, and none of them a lesson: the lesson
        is the beginner path's last stage, and the path is handed over at
        creation only (`grantBeginnerPathTx` has one production caller), so
        an upper-house rebirth never holds the stage and the Hearth's Lesson
        refuses it honestly. Their lessons are content still to author
        (`docs/TODO.md`). So the sendoff's Mortal subset is what must have
        one, and every lesson must be a sendoff's.
        """
        from app.rules.birthfamily import FAMILY_ARCHETYPES

        mortal = {str(a["id"]) for a in FAMILY_ARCHETYPES}
        self.assertEqual(len(mortal), 13, "the starter households changed shape")
        self.assertEqual(sorted(LESSONS), sorted(a for a in SENDOFF if a in mortal))
        self.assertTrue(set(LESSONS) <= set(SENDOFF), "a lesson for a house with no send-off")

    def test_the_head_speaks_five_times_and_hands_over_two_things(self):
        for archetype, lesson in LESSONS.items():
            with self.subTest(archetype=archetype):
                for line in ("lesson", "test", "pass", "fail", "story"):
                    self.assertGreaterEqual(len(str(lesson.get(line) or "").strip()), 40, f"{line} is too short to be the head speaking")
                self.assertIn(lesson["keepsake"], CONTENT["items"], "the keepsake must be an item of this world")
                self.assertTrue(CONTENT["items"][lesson["keepsake"]].get("market_excluded"), "a keepsake was never for sale")

    def test_the_manual_is_realm_zero_and_never_forbidden(self):
        """A child's first lesson costs no karma: `manualForbidden` retires a
        Demonic manual on first study, so none may be a house's own."""
        for archetype, lesson in LESSONS.items():
            with self.subTest(archetype=archetype):
                manual = MANUALS[lesson["manual"]]
                self.assertEqual(int(manual.get("min_realm_index", 0)), 0, "a new cultivator must be able to study it")
                self.assertNotEqual(str(manual.get("alignment")), "Demonic")
                self.assertFalse({"forbidden", "demonic", "evil"} & {str(t).lower() for t in manual.get("tags") or []})
                self.assertIn(manual["item_id"], CONTENT["items"])
                self.assertTrue(any(int(CONTENT["technique_system"]["techniques"][t].get("min_mastery", 0)) == 0
                                    for t in manual.get("techniques") or []),
                                "the manual must carry a technique usable at mastery 0, or the lesson teaches nothing at once")

    def test_the_four_trades_are_the_ones_the_engine_qualifies(self):
        trades = sorted({str(v.get("trade")) for v in SENDOFF.values()})
        source = (GO / "household_lesson.go").read_text(encoding="utf-8")
        self.assertIn('var householdLessonTrades = []string{"Forging", "Inscription", "Formation", "Alchemy"}', source)
        self.assertEqual(trades, ["Alchemy", "Forging", "Formation", "Inscription"])
        for trade in trades:
            self.assertIn(f'"{trade}":', source, f"{trade} has no attribute the head asks for")


class TheStageEndsThePath(unittest.TestCase):
    def test_the_path_ends_at_the_lesson_after_home(self):
        """The lesson is the last stage, and where it points is the first hour's
        last word.

        It pointed at nothing until v1.0.0-rc.45, which was true of the path and
        wrong about the world: `road_to_a_sect` sat seeded and reachable by
        nobody while the only chain that could have handed it over ended
        deliberately one stage short. The lesson is still the last *stage* - what
        it may not be is the last thing that ever happens to a new cultivator.
        """
        # v1.2.0: the lesson is no longer the last stage - the trade ("Iron
        # from the Seam") and the first gate follow it, and the last stage is
        # what points at the sect. Held as relationships, not positions.
        by_key = {s["quest_key"]: s for s in STAGES}
        self.assertEqual(by_key["beginner_home"]["follow_on"], "beginner_lesson")
        self.assertEqual(by_key["beginner_lesson"]["follow_on"], "beginner_iron")
        self.assertEqual(by_key["beginner_iron"]["follow_on"], "beginner_gate")
        self.assertEqual(STAGES[-1]["quest_key"], "beginner_gate")
        self.assertEqual(STAGES[-1]["follow_on"], "road_to_a_sect",
                         "the first hour ends by pointing at a sect, where the trial's odds are "
                         "finally worth taking")
        self.assertNotIn("road_to_a_sect", [str(stage["quest_key"]) for stage in STAGES],
                         "the sect road is a static quest, not a sixth stage")

    def test_the_stage_is_a_quest_the_forge_would_accept(self):
        lesson = next(s for s in STAGES if s["quest_key"] == "beginner_lesson")
        definition, errors = validate_quest_definition(lesson, WORLD, BUDGET)
        self.assertEqual(errors, [])
        self.assertEqual([o["type"] for o in definition["objectives"]], ["family_lesson"])
        self.assertIn("**/family → Hearth → Lesson**", lesson["objectives"][0]["label"])

    def test_the_objective_is_in_the_vocabulary_and_reported_only_on_a_pass(self):
        """A failed lesson returns before the record is reached, so it cannot
        advance the stage.

        The record used to sit after the reply and this test held that too.
        v1.0.5 moved it ahead of the reply and
        `test_a_quest_is_recorded_before_it_is_told.py` owns the ordering for
        every site at once; what stays here is the half that is this stage's -
        *only on a pass* - which is a `return` above the record, not a position
        relative to the answer.
        """
        self.assertIn("family_lesson", OBJECTIVE_TYPES)
        self.assertIsNone(OBJECTIVE_TYPES["family_lesson"]["target"])
        self.assertEqual(reported_objective_types().get("family_lesson"), {"family.py"})
        family = (BOT / "commands" / "family.py").read_text(encoding="utf-8")
        body = family.split("async def birth_family_lesson(", 1)[1].split("@registered_group_command", 1)[0]
        fail_return = body.index('if result.get("outcome")!="pass":')
        report = body.index('record_quest_progress(interaction.user.id, "family_lesson"')
        self.assertLess(fail_return, report, "a failed test must return before the stage is recorded")

class TheDoorIsWhereItWorks(unittest.TestCase):
    def test_the_engine_asks_for_it_at_home_once_per_life(self):
        source = (GO / "household_lesson.go").read_text(encoding="utf-8")
        self.assertIn('requireAtHomeTx(conn, userID, "the head\'s lesson")', source)
        self.assertIn("soul_legacy", source)
        self.assertIn("taught you all this lesson holds", source)
        self.assertIn("the head will hear you again in %d in-world minutes", source)

    def test_the_panel_hides_the_lesson_outside_and_the_hearth_holds_it(self):
        surface = (BOT / "surface.py").read_text(encoding="utf-8")
        self.assertIn('"family lesson"', surface.split("HOUSEHOLD_INDOOR_ACTIONS = ", 1)[1].split("\n", 1)[0])
        hearth = surface.split('key="family_hearth"', 1)[1].split(")", 1)[0]
        self.assertIn('"family lesson"', hearth)

    def test_the_wait_has_words_on_the_cooldown_card_and_in_the_refusal(self):
        cooldowns = (BOT / "commands" / "cooldowns.py").read_text(encoding="utf-8")
        self.assertIn('"family_lesson_retry": (', cooldowns)
        self.assertIn("**/family → Hearth → Lesson**", cooldowns)
        status = (GO / "cooldown_status.go").read_text(encoding="utf-8")
        self.assertIn('fromGameMinute("family_lesson_retry", "family_lesson_retry", "", ends)', status)
        runtime = (BOT / "runtime.py").read_text(encoding="utf-8")
        self.assertIn("the head will hear you again in (?P<minutes>", runtime)

    def test_a_graduate_is_handed_the_stage_at_the_door(self):
        """Somebody who finished the road home before the lesson existed was
        never handed it by the chain; the door catches the path up."""
        source = (GO / "household_lesson.go").read_text(encoding="utf-8")
        self.assertIn("catchUpBeginnerPathTx(conn, catalog, userID, p.GameMinute)", source)
        # The catch-up follows the chain the stages are seeded with (v1.3.1)
        # and hands over through the one ordinary grant; the spelling of its
        # variables is not the rule (v1.0.8).
        body = source[source.index("func catchUpBeginnerPathTx("):]
        body = body[:body.index("\nfunc ")]
        self.assertIn("questFollowOnTx(", body)
        self.assertIn("grantOrdinaryQuestTx(", body)

    def test_the_sendoff_still_teaches_through_the_same_helper(self):
        sendoff = (GO / "birth_family_actions.go").read_text(encoding="utf-8")
        self.assertIn('teachTradeMethodsTx(conn, catalog, userID, trade, householdWorldTx(conn, catalog, familyID), "birth_family", gameMinute, now)', sendoff)

"""v1.0.0-rc.6: the array you raise, the method you practise, the tilted climb.

A player's own property can hold a spirit-gathering array, raised through
the `formation` facility, which multiplies what every session gathers at
home and in closed-door seclusion; the manual a cultivator practises
multiplies it too, by its grade and their mastery, with the best method
they have learned as the default; and a stage takes more sessions at each
realm, with the qi of a new world as the relief.
"""
from __future__ import annotations

import json
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
LAW_SOURCE = (BOT / "commands" / "law.py").read_text(encoding="utf-8")
CULTIVATION_SOURCE = (BOT / "commands" / "cultivation.py").read_text(encoding="utf-8")
CARDS_SOURCE = (BOT / "status_cards.py").read_text(encoding="utf-8")
SERVICES_SOURCE = (BOT / "services.py").read_text(encoding="utf-8")
GO_MANUAL = (GO / "cultivation_manual.go").read_text(encoding="utf-8")
GO_PLACE = (GO / "cultivation_place.go").read_text(encoding="utf-8")
GO_PACE = (GO / "cultivation_pace.go").read_text(encoding="utf-8")
GO_ACTIONS = (GO / "cultivation_actions.go").read_text(encoding="utf-8")
GO_SECLUSION = (GO / "seclusion_environment.go").read_text(encoding="utf-8")
GO_AUTHORITATIVE = (GO / "authoritative.go").read_text(encoding="utf-8")
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


class TheGatheringArray(unittest.TestCase):
    def test_the_formation_facility_is_the_array_and_it_lifts_the_ground(self):
        self.assertIn("func abodeArrayMultiplier(", GO_PLACE)
        self.assertIn("abodeArrayPerLevel = 0.06", GO_PLACE)
        self.assertIn("SELECT name,cultivation_level,formation_level FROM cave_abodes", GO_PLACE)
        self.assertIn("SELECT name,cultivation_level,formation_level FROM sect_abodes", GO_PLACE)
        self.assertIn("gathering array", GO_PLACE)
        self.assertIn('"formation": "Spirit-Gathering Array"', SERVICES_SOURCE)

    def test_seclusion_reads_the_same_array(self):
        self.assertIn("abodeArray = abodeArrayMultiplier(abode)", GO_SECLUSION)
        self.assertIn("abodeArray = abodeArrayMultiplier(sectAbode)", GO_SECLUSION)
        self.assertIn("mult := base * abodeArray", GO_SECLUSION)
        self.assertIn('env["abode_array_mult"] = abodeArray', GO_SECLUSION)

    def test_the_facility_is_one_a_player_can_actually_raise(self):
        self.assertIn("formation", WORLD["abode_system"]["facilities"])
        self.assertIn("formation", WORLD["sect_abode_system"]["facilities"])


class TheMethodYouPractise(unittest.TestCase):
    def test_every_grade_in_the_content_has_a_multiplier(self):
        grades = {str(m.get("grade")) for m in WORLD["technique_system"]["manuals"].values()}
        for grade in grades:
            self.assertIn(f'"{grade}":', GO_MANUAL, grade)
        self.assertIn("func manualCultivationMultiplier(", GO_MANUAL)
        self.assertIn("masteryGatheringShare = 0.03", GO_MANUAL)

    def test_the_grades_rank_in_order_and_a_dao_method_beats_a_mortal_one(self):
        order = ["Mortal", "Earth", "Spirit", "Heaven", "Immortal", "Dao"]
        values = []
        for grade in order:
            line = next(line for line in GO_MANUAL.splitlines() if line.strip().startswith(f'"{grade}":'))
            values.append(float(line.split(":")[1].strip().rstrip(",")))
        self.assertEqual(values, sorted(values))
        self.assertGreater(values[-1], values[0] * 1.3)

    def test_the_engine_keeps_the_choice_and_defaults_to_the_best_learned(self):
        self.assertIn('"cultivation.manual"', GO_AUTHORITATIVE)
        self.assertIn("func practisedManual(", GO_MANUAL)
        self.assertIn("func cultivationManualAction(", GO_MANUAL)
        self.assertIn("has not been learned; study it first", GO_MANUAL)
        self.assertIn("manualMult", GO_ACTIONS)

    def test_the_player_chooses_it_from_what_they_have_learned(self):
        self.assertIn('@registered_group_command(manual_group, name="practise"', LAW_SOURCE)
        self.assertIn("@app_commands.autocomplete(manual=learned_manual_autocomplete)", LAW_SOURCE)
        self.assertIn('"cultivation.manual",interaction.user.id', LAW_SOURCE)
        self.assertIn("manual_grade", CULTIVATION_SOURCE)
        self.assertIn("manual_name", CARDS_SOURCE)


class TheClimbTightens(unittest.TestCase):
    def test_a_stage_costs_more_sessions_at_each_realm(self):
        self.assertIn("func sessionsForStage(realmIndex int64) int64", GO_PACE)
        self.assertIn("cultivationSessionsPerRealmNumerator   = 5", GO_PACE)

        def sessions_for(index: int) -> int:
            return 8 + index * 5 // 4

        self.assertEqual(sessions_for(0), 8)
        self.assertEqual(sessions_for(8), 18)
        self.assertLess(sessions_for(0), sessions_for(31))

    def test_a_new_world_is_the_relief_that_makes_the_next_ladder_climbable(self):
        realms = WORLD["realms"]
        density = WORLD["world_qi_density"]

        def realm_sessions(index: int) -> int:
            realm = realms[index]
            quality = 1 + (3 + index) / 20
            total = 0
            for cost in realm["phase_costs"]:
                pace = max(8, cost // (8 + index * 5 // 4))
                total += -(-cost // max(1, round(pace * quality * density[realm["world"]])))
            return total

        mortal = [realm_sessions(index) for index in range(8)]
        self.assertEqual(mortal, sorted(mortal), mortal)
        self.assertLess(realm_sessions(8), mortal[-1])


if __name__ == "__main__":
    unittest.main()

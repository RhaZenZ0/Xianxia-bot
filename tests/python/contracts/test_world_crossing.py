"""The crossing a tribulation leaves behind (v1.0.0-rc.44).

Clearing a world-crossing tribulation wrote `tribulation_state.cleared`, paid
a reputation point and a fate point, and stopped. Nothing said what the
clearance was *for*, and the only anchored way into the world above was one
authored array standing in one capital - so a cultivator who survived the
heavens at a waystation in the hills was told, implicitly, to walk.

`ascension.gate` anchors the seam where the lightning fell, and the quest that
names it is content: `world_crossing_system.quests`, keyed by the world the
crossing leads out of. This file holds the Python half of that - the content is
whole, the seeder makes ordinary giver-less quests out of it, and the bot
actually seeds them. The engine half (the fare, the terminus, the refusals, the
conversion) is `world_crossing_test.go`, where the rules live.
"""
from __future__ import annotations

import json
import unittest

from app.rules.game import World
from app.rules.quests import OBJECTIVE_TYPES, ascension_quest_seed_rows
from tests.support import PROJECT_ROOT

WORLD = World(PROJECT_ROOT / "content" / "world.json")
CROSSING = dict(WORLD.data.get("world_crossing_system") or {})
# The three world-crossing tribulations, by the world each one departs. Kept
# here rather than imported so a gate quietly disappearing from the engine
# fails this rather than silently shrinking the expectation.
DEPARTING_WORLDS = ("Mortal World", "Spiritual World", "Immortal World")


class TheCrossingContentIsWhole(unittest.TestCase):
    def test_every_world_a_tribulation_leads_out_of_has_a_quest(self):
        quests = dict(CROSSING.get("quests") or {})
        self.assertEqual(sorted(quests), sorted(DEPARTING_WORLDS))
        keys = [str(q.get("quest_key") or "") for q in quests.values()]
        self.assertEqual(len(set(keys)), len(keys), f"two crossings share a quest key: {keys}")
        for key in keys:
            self.assertTrue(key, "a crossing quest with no key is a quest nothing can hand over")

    def test_the_objectives_are_the_two_halves_of_an_ascension(self):
        for departing, quest in dict(CROSSING.get("quests") or {}).items():
            with self.subTest(world=departing):
                objectives = list(quest.get("objectives") or [])
                self.assertEqual([str(o.get("type")) for o in objectives], ["ascension_gate", "world_cross"],
                                 "anchor the seam, then step through it - in that order")
                for objective in objectives:
                    self.assertIn(str(objective.get("type")), OBJECTIVE_TYPES)
                    # A label that names no door is the thing rc.26 built
                    # `next_objective_label` to stop: it confirms something
                    # counted and leaves the player where they were.
                    self.assertIn("**/", str(objective.get("label") or ""),
                                  "an objective label names the hub path that advances it")

    def test_the_knobs_are_present_and_sane(self):
        self.assertGreaterEqual(int(CROSSING.get("raise_cost_multiplier") or 0), 1,
                                "anchoring a seam costs more than a transit through it")
        self.assertIn("{character}", str(CROSSING.get("name_template") or ""))
        chance = int(CROSSING.get("npc_crossing_chance_percent") or 0)
        self.assertTrue(0 < chance <= 100, f"the NPC crossing chance is {chance}")
        # The raising is significant enough for the Quest Forge (whose floor is
        # 80) and one traveller afterwards deliberately is not: the crossing is
        # the event, not everybody who uses it.
        self.assertGreaterEqual(int(CROSSING.get("history_significance") or 0), 80)
        self.assertLess(int(CROSSING.get("npc_history_significance") or 0), 80)


class TheSeederMakesOrdinaryQuests(unittest.TestCase):
    def test_a_crossing_quest_is_a_giverless_quest_definition_row(self):
        rows = ascension_quest_seed_rows(WORLD)
        self.assertEqual(len(rows), len(DEPARTING_WORLDS))
        for row in rows:
            with self.subTest(quest=row["quest_key"]):
                # A giver would make it a commission: the one-at-a-time slot, a
                # deadline, offered in person. `grantOrdinaryQuestTx` refuses
                # one by design, so a giver here costs the player the quest.
                self.assertEqual(row["giver_npc"], "")
                self.assertEqual(row["deadline_game_minutes"], 0)
                self.assertEqual(row["source_type"], "system")
                self.assertTrue(row["source_key"].startswith("world_crossing:"))
                self.assertTrue(row["title"] and row["description"])
                self.assertIn(row["seed"]["from_world"], DEPARTING_WORLDS)

    def test_the_bot_and_the_engine_playtest_both_seed_them(self):
        # The same assertion `test_household_errands.py` makes, and for the
        # same reason: a seeder nothing calls is a quest nobody can be given,
        # which is precisely the fault this release exists to stop repeating.
        bot = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("ascension_quest_seed_rows(WORLD)", bot)
        playtest = (PROJECT_ROOT / "scripts" / "playtest_engine.py").read_text(encoding="utf-8")
        self.assertIn("ascension_quest_seed_rows(content)", playtest)


class TheTableIsTheEnginesToWrite(unittest.TestCase):
    def test_python_reads_world_crossings_and_never_writes_one(self):
        """A gate is authoritative state, so `ascension.gate` is its only writer.

        Presentation draws raised gates beside the authored arrays, which needs
        a read; what it must never do is write one, because a crossing that
        Python could create would be a road the engine did not price, gate on a
        realm, or charge for.
        """
        offenders = []
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "world_crossings" not in text:
                continue
            for statement in ("INSERT INTO world_crossings", "UPDATE world_crossings", "DELETE FROM world_crossings"):
                if statement in text and path.name != "core.py":
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {statement}")
            if path.name == "core.py":
                # The migration and the DDL live there; a write does not.
                for statement in ("INSERT INTO world_crossings", "UPDATE world_crossings", "DELETE FROM world_crossings"):
                    if statement in text:
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {statement}")
        self.assertEqual(offenders, [], f"Python writes a crossing: {offenders}")

    def test_the_readiness_probe_knows_the_table(self):
        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        self.assertIn('"world_crossings",', core)
        self.assertIn("SCHEMA_VERSION = 54", core)

    def test_the_content_block_parses_as_the_engine_reads_it(self):
        raw = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
        self.assertIn("world_crossing_system", raw)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

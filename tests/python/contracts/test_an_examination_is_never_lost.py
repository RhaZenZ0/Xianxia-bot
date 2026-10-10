"""An examination is never lost to the rank that climbed past it (v1.33.0).

A rank rises on crafting alone, and a Craft All is up to fifty crafts in one
press, so one press can carry a fresh crafter across all three ranks the content
examines. The hall used to sit the rank held *now*: the three quests were handed
over, could never be finished, and the trade stayed uncertified for the life.
The engine now sits the lowest rank this life has reached and not passed
(`professionExamsOpenTx`, held in Go by `profession_exam_test.go`), hands every
open examination over from the craft and from the counter, and reports a pass
under the examination's own quest key.

That last part is the half this file holds. Untargeted, one Tier 1 pass
completes - and pays - every examination quest a crafter holds, so the engine
half alone would have made the fault worse. Each examination's objective names
its own quest key; the validator knows the target kind; the bot reports under
the key; migration 80 carries the target onto a running world, where the
seeding is insert-only.
"""
from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import PROJECT_ROOT

from app.rules.game import World
from app.rules.progression_systems import examination_below_held_line, examinations_offered_line
from app.rules.quests import OBJECTIVE_PATHS, OBJECTIVE_TYPES, validate_quest_definition


CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
WORLD = World(PROJECT_ROOT / "content" / "world.json")
BUDGET = {"max_xp": 1000, "max_stones": 1000, "max_items": 5}
EXAMS = [(str(trade), dict(exam)) for trade, ladder in dict(CONTENT.get("profession_exams") or {}).items() for exam in ladder]
LAW_PY = PROJECT_ROOT / "app" / "bot" / "commands" / "law.py"
EXPLORATION_PY = PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py"


def _core():
    from tests.support import install_aiosqlite_shim

    install_aiosqlite_shim()
    from app.database import core

    return core


def _function(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path.name} carries no {name}; the reader is broken, not the tree")


class EveryExaminationNamesItsOwnQuest(unittest.TestCase):
    def test_the_content_has_the_examinations_the_gate_reads(self):
        self.assertEqual(len(EXAMS), 12, "four trades of three ranks; the reader is broken, not the tree")

    def test_each_objective_targets_its_own_quest_key(self):
        for trade, exam in EXAMS:
            with self.subTest(quest=exam["quest_key"]):
                objectives = list(exam["objectives"])
                self.assertEqual(len(objectives), 1)
                self.assertEqual(objectives[0]["type"], "profession_exam")
                self.assertEqual(
                    objectives[0].get("target"), exam["quest_key"],
                    f"{trade} rank {exam['rank']}'s objective takes any pass, so one Tier 1 pass completes it "
                    "and pays it - and every other examination quest the crafter holds")

    def test_the_validator_accepts_every_examination_and_keeps_its_target(self):
        # Errors and target only: a label is cut at 90 characters and these
        # carry the path to the door after the sentence.
        for trade, exam in EXAMS:
            with self.subTest(quest=exam["quest_key"]):
                definition, errors = validate_quest_definition(exam, WORLD, BUDGET)
                self.assertEqual(errors, [])
                self.assertIsNotNone(definition)
                self.assertEqual(definition["objectives"][0]["target"], exam["quest_key"])

    def test_the_validator_refuses_a_key_that_is_no_examination(self):
        trade, exam = EXAMS[0]
        draft = dict(exam, objectives=[{"id": "sit", "type": "profession_exam", "count": 1, "target": "exam_nobody_at_all"}])
        definition, errors = validate_quest_definition(draft, WORLD, BUDGET)
        self.assertIsNone(definition)
        self.assertTrue(any("unknown examination" in e for e in errors), errors)

    def test_the_validator_labels_a_bare_target_with_the_engines_rank_name(self):
        trade, exam = EXAMS[0]
        draft = dict(exam, objectives=[{"id": "sit", "type": "profession_exam", "count": 1, "target": exam["quest_key"].upper()}])
        definition, errors = validate_quest_definition(draft, WORLD, BUDGET)
        self.assertEqual(errors, [])
        self.assertEqual(definition["objectives"][0]["target"], exam["quest_key"])
        self.assertIn(exam["rank_name"], definition["objectives"][0]["label"])
        self.assertIn(trade, definition["objectives"][0]["label"])

    def test_an_objective_that_names_no_key_is_still_the_vocabulary(self):
        # A quest that merely asks for a pass (`realm_road_21`) stays valid.
        self.assertEqual(OBJECTIVE_TYPES["profession_exam"]["target"], "profession_exam")
        draft = dict(EXAMS[0][1], objectives=[{"id": "sit", "type": "profession_exam", "count": 1}])
        definition, errors = validate_quest_definition(draft, WORLD, BUDGET)
        self.assertEqual(errors, [])
        self.assertNotIn("target", definition["objectives"][0])


class TheWorkbenchCanStillSaveAnExamination(unittest.TestCase):
    def test_a_hand_edit_of_every_examination_passes_the_dashboards_own_world(self):
        """A hand edit goes through the same validator against the dashboard's
        own slimmer world. Before it carried the content pack, every target
        kind added since v1.16.0 was "unknown" there, so the workbench refused
        to save a realm-road stage - and an examination, once its objective
        named its key, would have joined them."""
        from app.dashboard.server import _QuestWorld

        world = _QuestWorld(CONTENT)
        stages = list(CONTENT.get("realm_road") or [])
        self.assertGreater(len(stages), 20, "the realm road came back short; the reader is broken, not the tree")
        for trade, exam in EXAMS:
            with self.subTest(quest=exam["quest_key"]):
                _definition, errors = validate_quest_definition(exam, world, BUDGET)
                self.assertEqual(errors, [])
        for stage in stages:
            with self.subTest(quest=stage["quest_key"]):
                _definition, errors = validate_quest_definition(stage, world, BUDGET)
                self.assertEqual(errors, [])


class ThePassIsReportedUnderTheExaminationSat(unittest.TestCase):
    def test_the_command_reports_the_engines_quest_key(self):
        """Read by AST, not by spelling: the call must pass `target=` and the
        value must be read from the result's `quest_key`."""
        handler = _function(LAW_PY, "profession_exam")
        reports = [
            node for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) == "record_quest_progress"
            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "profession_exam"
        ]
        self.assertEqual(len(reports), 1, "the exam command reports its pass exactly once; the reader is broken, not the tree")
        target = {kw.arg: kw.value for kw in reports[0].keywords}.get("target")
        self.assertIsNotNone(target, "a pass reported without a target completes every examination quest the crafter holds")
        read = [n.value for n in ast.walk(target) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertIn("quest_key", read, "the target must come from the engine's `quest_key`, not be invented here")

    def test_the_craft_reply_names_what_the_engine_handed_over(self):
        craft = _function(EXPLORATION_PY, "_run_crafting")
        calls = {getattr(n.func, "id", getattr(n.func, "attr", "")) for n in ast.walk(craft) if isinstance(n, ast.Call)}
        self.assertIn("examinations_offered_line", calls, "the craft reply no longer says which examinations it handed over")
        strings = {n.value for n in ast.walk(craft) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertIn("exams_offered", strings)
        self.assertNotIn("exam_offered", strings,
                         "the reply reads the first examination only, which is the rank held when a Craft All crossed several")


class TheLinesTheRepliesPrint(unittest.TestCase):
    OFFERED = [
        {"quest_key": "exam_alchemy_apprentice", "rank": 1, "rank_name": "Tier 1 Pill Apprentice", "hall": "apothecary"},
        {"quest_key": "exam_alchemy_journeyman", "rank": 2, "rank_name": "Tier 2 Pill Adept", "hall": "apothecary"},
        {"quest_key": "exam_alchemy_expert", "rank": 3, "rank_name": "Tier 3 Pill Artisan", "hall": "apothecary"},
    ]

    def test_nothing_offered_says_nothing(self):
        self.assertEqual(examinations_offered_line("Alchemy", []), "")

    def test_one_examination_is_named_without_an_order(self):
        line = examinations_offered_line("Alchemy", self.OFFERED[:1])
        self.assertIn("Tier 1 Pill Apprentice", line)
        self.assertNotIn("lowest first", line)

    def test_several_are_named_in_the_engines_words_and_ordered(self):
        line = examinations_offered_line("Alchemy", self.OFFERED)
        for exam in self.OFFERED:
            self.assertIn(exam["rank_name"], line)
        self.assertIn("lowest first", line)
        self.assertIn("3 examinations", line)
        self.assertIn(OBJECTIVE_PATHS["profession_exam"], line, "the door is the one table every quest label reads")

    def test_a_rank_held_is_only_remarked_on_when_the_examination_is_below_it(self):
        self.assertEqual(examination_below_held_line("Alchemy", 2, 2, 0, True), "")
        self.assertEqual(examination_below_held_line("Alchemy", 3, 2, 0, True), "")
        owed = examination_below_held_line("Alchemy", 1, 3, 2, True)
        self.assertIn("Tier 3 Pill Artisan", owed)
        self.assertIn("2 more examinations", owed)
        self.assertIn("certified", examination_below_held_line("Alchemy", 3, 4, 0, True))
        self.assertIn("lowest rank you have not passed", examination_below_held_line("Alchemy", 1, 3, 2, False))


class MigrationEightyCarriesTheTargetOntoARunningWorld(unittest.IsolatedAsyncioTestCase):
    GO_TERMS = ('{"objectives":[{"count":1,"id":"sit","label":"x","type":"profession_exam"}],'
                '"rewards":{"insight_xp":30},"pinned_at_game_minute":5}')

    async def _world_at_79(self) -> Path:
        core = _core()
        from app.database import Database

        path = Path(tempfile.mkdtemp()) / "exams.sqlite3"
        migrations = tuple(m for m in core.SCHEMA_MIGRATIONS if int(m[0]) <= 79)
        with patch.object(core, "SCHEMA_VERSION", 79), patch.object(core, "SCHEMA_MIGRATIONS", migrations):
            await Database(path).init()
        untargeted = json.dumps([{"id": "sit", "type": "profession_exam", "count": 1, "label": "Sit the examination"}])
        gm_targeted = json.dumps([{"id": "sit", "type": "profession_exam", "count": 1, "label": "x", "target": "exam_my_own_key"}])
        with sqlite3.connect(path) as conn:
            def define(key: str, source: str, objectives: str) -> None:
                conn.execute(
                    "INSERT INTO quest_definitions(quest_key,title,description,source_type,source_key,objectives_json,"
                    "rewards_json,status,origin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,0,0)",
                    (key, key, "", "system", source, objectives, "{}", "approved", "content"))

            define("exam_alchemy_apprentice", "profession_exam:Alchemy", untargeted)
            define("exam_alchemy_journeyman", "profession_exam:Alchemy", untargeted)
            define("exam_forging_apprentice", "profession_exam:Forging", gm_targeted)
            define("realm_road_21", "realm_road", untargeted)

            def hold(user: int, key: str, status: str, terms: str) -> None:
                conn.execute(
                    "INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,created_at,updated_at,terms_json)"
                    " VALUES(?,?,?,'{}',0,0,0,?)", (user, key, status, terms))

            hold(1, "exam_alchemy_apprentice", "active", self.GO_TERMS)
            hold(1, "exam_alchemy_journeyman", "active", "")  # never touched: the definition is its terms
            hold(1, "realm_road_21", "active", self.GO_TERMS)
            hold(2, "exam_alchemy_apprentice", "completed", self.GO_TERMS)
            hold(2, "exam_forging_apprentice", "active", self.GO_TERMS.replace(
                '"type":"profession_exam"', '"target":"exam_my_own_key","type":"profession_exam"'))
            conn.commit()
        await Database(path).init()
        return path

    def _objective(self, path: Path, table: str, column: str, key: str, user: int | None = None) -> dict:
        with sqlite3.connect(path) as conn:
            version = conn.execute("SELECT current_version FROM schema_version WHERE singleton=1").fetchone()[0]
            where, params = ("quest_key=?", (key,)) if user is None else ("quest_key=? AND user_id=?", (key, user))
            raw = conn.execute(f"SELECT {column} FROM {table} WHERE {where}", params).fetchone()[0]
        self.assertGreaterEqual(version, 80, "the migration did not run; the reader is broken, not the tree")
        parsed = json.loads(raw)
        return dict((parsed["objectives"] if isinstance(parsed, dict) else parsed)[0])

    async def test_a_definition_gets_its_own_key_as_its_target(self):
        path = await self._world_at_79()
        for key in ("exam_alchemy_apprentice", "exam_alchemy_journeyman"):
            self.assertEqual(
                self._objective(path, "quest_definitions", "objectives_json", key).get("target"), key,
                f"{key} still takes any pass on a world seeded before v1.33.0")

    async def test_a_quest_already_pinned_gets_it_too(self):
        path = await self._world_at_79()
        pinned = self._objective(path, "character_quests", "terms_json", "exam_alchemy_apprentice", user=1)
        self.assertEqual(pinned.get("target"), "exam_alchemy_apprentice",
                         "a crafter already holding the quest still completes it with any pass")
        # The terms keep everything else they carried.
        with sqlite3.connect(path) as conn:
            terms = json.loads(conn.execute(
                "SELECT terms_json FROM character_quests WHERE user_id=1 AND quest_key='exam_alchemy_apprentice'").fetchone()[0])
        self.assertEqual(terms["rewards"], {"insight_xp": 30})
        self.assertEqual(terms["pinned_at_game_minute"], 5)

    async def test_a_gms_target_a_completed_quest_and_an_unpinned_row_are_left_alone(self):
        path = await self._world_at_79()
        self.assertEqual(self._objective(path, "quest_definitions", "objectives_json", "exam_forging_apprentice").get("target"),
                         "exam_my_own_key", "a GM's own target was overwritten")
        self.assertEqual(self._objective(path, "character_quests", "terms_json", "exam_forging_apprentice", user=2).get("target"),
                         "exam_my_own_key")
        self.assertNotIn("target", self._objective(path, "character_quests", "terms_json", "exam_alchemy_apprentice", user=2),
                         "a completed quest was rewritten after it paid")
        with sqlite3.connect(path) as conn:
            unpinned = conn.execute(
                "SELECT terms_json FROM character_quests WHERE user_id=1 AND quest_key='exam_alchemy_journeyman'").fetchone()[0]
        self.assertEqual(unpinned, "")

    async def test_a_quest_that_only_asks_for_a_pass_stays_untargeted(self):
        path = await self._world_at_79()
        self.assertNotIn("target", self._objective(path, "quest_definitions", "objectives_json", "realm_road_21"))
        self.assertNotIn("target", self._objective(path, "character_quests", "terms_json", "realm_road_21", user=1))

    async def test_running_it_twice_changes_nothing(self):
        path = await self._world_at_79()
        core = _core()
        statements = dict((int(v), s) for v, _n, s in core.SCHEMA_MIGRATIONS)[80]
        with sqlite3.connect(path) as conn:
            before = conn.execute("SELECT objectives_json FROM quest_definitions ORDER BY quest_key").fetchall()
            before_terms = conn.execute("SELECT terms_json FROM character_quests ORDER BY user_id,quest_key").fetchall()
            for statement in statements:
                conn.execute(statement)
            self.assertEqual(before, conn.execute("SELECT objectives_json FROM quest_definitions ORDER BY quest_key").fetchall())
            self.assertEqual(before_terms, conn.execute("SELECT terms_json FROM character_quests ORDER BY user_id,quest_key").fetchall())

    async def test_a_fresh_database_applies_it_to_nothing(self):
        core = _core()
        from app.database import Database

        path = Path(tempfile.mkdtemp()) / "fresh.sqlite3"
        await Database(path).init()
        with sqlite3.connect(path) as conn:
            version = conn.execute("SELECT current_version FROM schema_version WHERE singleton=1").fetchone()[0]
            names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations WHERE version=80")]
        self.assertEqual(version, core.SCHEMA_VERSION)
        self.assertEqual(names, ["an_examination_names_its_quest"])


if __name__ == "__main__":
    unittest.main()

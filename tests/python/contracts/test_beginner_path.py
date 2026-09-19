"""The path a new cultivator is put on (v1.0.0-rc.26).

`first_steps` - "First Steps Beneath Heaven" - has been in this repo since
before the Quest Forge, with exactly the right three objectives, and no player
has ever held it. It is seeded into `quest_definitions` on every boot and it
is listed in `/quests`; what never existed is a path that hands it to anybody.
The only two statements in the engine that write a `character_quests` row are
both in `commission_actions.go` and both want a giver, which the static quests
deliberately do not have.

These hold the replacement to the two things that would make it worthless: a
stage a player cannot finish where they are standing, and a chain that does
not lead anywhere.
"""

from __future__ import annotations

import json
import re
import unittest

from app.rules.game import World
from app.rules.quests import (
    MAX_OBJECTIVES,
    OBJECTIVE_TYPES,
    QUEST_DEFINITIONS,
    beginner_path_seed_rows,
    next_objective_label,
    validate_quest_definition,
)
from tests.support import PROJECT_ROOT

WORLD = World(PROJECT_ROOT / "content" / "world.json")
STAGES = list(json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))["beginner_path"])
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
BUDGET = {"max_xp": 100, "max_stones": 100, "max_items": 3}

# A new character is created at `birth_family:<id>` - a private residence - and
# the engine refuses several things there outright. This maps the objective
# types a stage can ask for onto the Go function that would have to succeed,
# so the test below can read the refusals out of the engine rather than
# trusting a list somebody typed here.
ACTION_FOR_OBJECTIVE = {
    "cultivate": ("cultivation_actions.go", "cultivationTrain"),
    "explore": ("exploration_actions.go", "explorationExploreAction"),
    "travel": ("exploration_actions.go", "explorationTravelAction"),
    "combat_win": ("exploration_actions.go", "explorationHuntAction"),
}


def go_function(filename: str, name: str) -> str:
    source = (GO / filename).read_text(encoding="utf-8")
    start = source.index(f"func {name}(")
    end = source.find("\nfunc ", start + 1)
    return source[start:end if end != -1 else len(source)]


def refuses_indoors(filename: str, name: str) -> bool:
    """Whether that engine action refuses inside a birth-family household."""
    return 'birth_family:' in go_function(filename, name)


class TheContentIsAQuestTheForgeWouldAccept(unittest.TestCase):
    def test_every_stage_passes_the_validator_the_forge_is_held_to(self):
        for stage in STAGES:
            with self.subTest(stage=stage["quest_key"]):
                definition, errors = validate_quest_definition(stage, WORLD, BUDGET)
                self.assertEqual(errors, [])
                self.assertLessEqual(len(definition["objectives"]), MAX_OBJECTIVES)

    def test_every_objective_type_used_is_one_the_bot_reports(self):
        for stage in STAGES:
            for objective in stage["objectives"]:
                with self.subTest(stage=stage["quest_key"], objective=objective["id"]):
                    self.assertIn(str(objective["type"]), OBJECTIVE_TYPES)

    def test_no_stage_carries_a_giver(self):
        """A giver makes it a commission - one at a time, with a deadline, and
        offered in person. The engine refuses to hand one of those over, so a
        giver here would silently cost a new player their whole path."""
        for row in beginner_path_seed_rows(WORLD):
            with self.subTest(stage=row["quest_key"]):
                self.assertEqual(row["giver_npc"], "")
                self.assertEqual(row["deadline_game_minutes"], 0)


class TheChainLeadsSomewhere(unittest.TestCase):
    def test_every_follow_on_names_something_that_can_be_handed_over(self):
        """A chain pointing at nothing strands the player where it stops.

        Until v1.0.0-rc.45 this also held that the last stage chained to
        nothing, which was true of the path and false about the fault it was
        built to fix: `road_to_a_sect` sat seeded and unreachable for nineteen
        releases while the path it should have followed ended deliberately.
        The last stage may leave the path now - what it may not do is name
        something nobody could be given, so an onward chain has to name a
        giver-less definition the bot actually seeds.
        """
        keys = [str(s["quest_key"]) for s in STAGES]
        self.assertEqual(len(set(keys)), len(keys), "two stages share a quest key")
        onward = {key for key in QUEST_DEFINITIONS if not QUEST_DEFINITIONS[key].get("giver_npc")}
        for index, stage in enumerate(STAGES):
            follow_on = str(stage.get("follow_on") or "")
            with self.subTest(stage=stage["quest_key"]):
                self.assertNotEqual(follow_on, stage["quest_key"], "a stage chained to itself")
                if index == len(STAGES) - 1:
                    if follow_on:
                        self.assertIn(follow_on, onward,
                                      "the path leaves itself for a quest nothing seeds, or one with a "
                                      "giver - and `grantOrdinaryQuestTx` refuses a giver by design")
                else:
                    self.assertIn(follow_on, keys, "a chain pointing at nothing strands the player there")

    def test_following_the_chain_reaches_every_stage_exactly_once(self):
        by_key = {str(s["quest_key"]): s for s in STAGES}
        seen: list[str] = []
        key = str(STAGES[0]["quest_key"])
        # The walk stops where the path does: a chain that leaves for a static
        # quest is followed as far as the path's own stages and no further,
        # which is what makes "every stage exactly once" still mean that.
        while key in by_key:
            self.assertNotIn(key, seen, "the beginner path loops")
            seen.append(key)
            key = str(by_key[key].get("follow_on") or "")
        self.assertEqual(seen, list(by_key), "a stage is unreachable by following the chain")

    def test_the_chain_travels_as_seed_json(self):
        """The engine reads the chain off `quest_definitions.seed_json`, not
        off this file, so a GM who re-points it in the workbench is obeyed."""
        rows = {r["quest_key"]: r for r in beginner_path_seed_rows(WORLD)}
        for stage in STAGES:
            with self.subTest(stage=stage["quest_key"]):
                self.assertEqual(rows[stage["quest_key"]]["seed"]["follow_on"], str(stage.get("follow_on") or ""))


class TheFirstStageIsPossibleWhereTheyAreStanding(unittest.TestCase):
    """The one that decides whether any of this works.

    A new character is made at `birth_family:<id>`, indoors, and the engine
    refuses exploring, travelling and hunting there outright. A first stage
    asking for any of those is unfinishable until the player works out that
    `/family → Leave` exists - which is exactly the problem the path is for.
    """

    def test_the_refusals_are_read_out_of_the_engine_and_not_assumed(self):
        # A floor: if these stop refusing, the test below is vacuous and this
        # is what says so.
        for objective in ("explore", "travel", "combat_win"):
            filename, name = ACTION_FOR_OBJECTIVE[objective]
            with self.subTest(objective=objective):
                self.assertTrue(refuses_indoors(filename, name),
                                f"{name} no longer refuses inside a private residence")
        self.assertFalse(refuses_indoors(*ACTION_FOR_OBJECTIVE["cultivate"]),
                         "cultivation now refuses indoors; the first stage has to change")

    def test_nothing_the_first_stage_asks_for_is_refused_indoors(self):
        first = STAGES[0]
        for objective in first["objectives"]:
            kind = str(objective["type"])
            with self.subTest(objective=objective["id"], type=kind):
                mapped = ACTION_FOR_OBJECTIVE.get(kind)
                if mapped is None:
                    # Not gated on location at all (scene.action is refused
                    # only for a muted player), so it is reachable indoors.
                    continue
                self.assertFalse(
                    refuses_indoors(*mapped),
                    f"the first stage asks for {kind!r}, which the engine refuses inside a birth-family "
                    "household - a new character cannot finish it where they are put",
                )

    def test_leaving_is_named_before_anything_outdoors_is_asked_for(self):
        """The second stage is the first to ask for the open world, so it is
        the one that has to say how to get out of the house."""
        outdoors = next(
            (s for s in STAGES
             if any(refuses_indoors(*ACTION_FOR_OBJECTIVE[str(o["type"])])
                    for o in s["objectives"] if str(o["type"]) in ACTION_FOR_OBJECTIVE)),
            None,
        )
        self.assertIsNotNone(outdoors, "no stage asks for the open world")
        self.assertIn("/family", str(outdoors.get("description") or "") + str(outdoors.get("opening") or ""),
                      "the first stage that needs the open world never says how to leave the house")


class TheEngineHandsItOver(unittest.TestCase):
    def test_creation_grants_the_first_stage_in_the_same_transaction(self):
        source = (GO / "authoritative.go").read_text(encoding="utf-8")
        self.assertIn("grantBeginnerPathTx(conn, catalog, userID, p.GameMinute)", source)
        self.assertIn('result["beginner_quest"] = beginnerQuest', source)

    def test_completion_hands_over_the_follow_on(self):
        source = (GO / "actions.go").read_text(encoding="utf-8")
        self.assertIn("questFollowOnTx(conn, p.QuestKey)", source)
        self.assertIn("grantOrdinaryQuestTx(conn, userID, followOn, gameMinute)", source)
        # Only an ordinary quest chains: finishing a commission must never put
        # another one in the player's hands by itself.
        chained = source[source.index("} else if complete {"):]
        self.assertNotIn("resolveCommissionTx", chained[:chained.index("questFollowOnTx")])

    def test_nothing_python_side_writes_the_quest_row(self):
        for path in ("app/database/core.py", "app/ops/core_services.py", "app/bot/ui/creation.py"):
            source = (PROJECT_ROOT / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotIn("INSERT INTO character_quests", source)


class TheReplyNamesTheNextDoor(unittest.TestCase):
    def test_the_next_step_is_the_first_objective_still_short(self):
        objectives = STAGES[0]["objectives"]
        first = next_objective_label(objectives, {})
        self.assertEqual(first, str(objectives[0]["label"]))
        done_first = next_objective_label(objectives, {str(objectives[0]["id"]): 1})
        self.assertEqual(done_first, str(objectives[1]["label"]))
        everything = {str(o["id"]): int(o.get("count", 1)) for o in objectives}
        self.assertEqual(next_objective_label(objectives, everything), "")

    def test_every_objective_label_names_a_command(self):
        """The labels are the whole of "which door, now": a player is told what
        is outstanding and the label names the command that does it. Inside a
        hub panel `suggested_actions` turns that into a button; test_hint_paths
        holds each one to resolving."""
        for stage in STAGES:
            for objective in stage["objectives"]:
                with self.subTest(stage=stage["quest_key"], objective=objective["id"]):
                    self.assertRegex(str(objective["label"]), r"\*\*/[a-z]+",
                                     "an objective that names no command leaves the player where they were")

    def test_progress_announces_what_is_left(self):
        source = (PROJECT_ROOT / "app" / "bot" / "character_state.py").read_text(encoding="utf-8")
        announce = source[source.index("async def announce_quest_progress"):]
        announce = announce[:announce.index("\nasync def ") if "\nasync def " in announce[1:] else len(announce)]
        self.assertIn("next_objective_label", announce)
        self.assertIn("follow_on", announce)


if __name__ == "__main__":
    unittest.main()

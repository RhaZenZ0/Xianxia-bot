"""A house worth coming back to (v1.0.0-rc.32): the Python-owned boundary.

The engine gates the household's four gifts on standing inside it, opens the
door only from the family's town, and hands the errands over one at a time
(`household_return.go`); Python authors the errands as content, seeds them
the way the beginner path is seeded, reports `return_home`, and hides the
doors on the panel where they would refuse. What is held here is the shape
of that content and the wiring the engine relies on.
"""

from __future__ import annotations

import json
import unittest

from app.rules.quests import (
    HOUSEHOLD_ERRAND_PREFIX,
    HOUSEHOLD_STANDING_REWARD_KEY,
    MAX_OBJECTIVES,
    OBJECTIVE_TYPES,
    household_errand_seed_rows,
    validate_quest_definition,
)
from tests.python.contracts.test_beginner_path import BUDGET, WORLD
from tests.python.unit.test_quest_objective_reporters import reported_objective_types
from tests.support import PROJECT_ROOT

GO = PROJECT_ROOT / "go_core" / "internal" / "game"
BOT = PROJECT_ROOT / "app" / "bot"
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ERRANDS = dict(CONTENT["household_errands"])
TRADES = sorted({str(v.get("trade") or "") for v in CONTENT["birth_family_sendoff"].values()})


class TheErrandsAreQuestsTheForgeWouldAccept(unittest.TestCase):
    def test_every_trade_a_household_teaches_has_errands(self):
        self.assertEqual(sorted(ERRANDS), TRADES)
        for trade, pool in ERRANDS.items():
            with self.subTest(trade=trade):
                self.assertGreaterEqual(len(pool), 3)

    def test_every_errand_passes_the_validator_and_ends_at_home(self):
        keys = set()
        for trade, pool in ERRANDS.items():
            for errand in pool:
                with self.subTest(errand=errand["quest_key"]):
                    definition, errors = validate_quest_definition(errand, WORLD, BUDGET)
                    self.assertEqual(errors, [])
                    self.assertLessEqual(len(definition["objectives"]), MAX_OBJECTIVES)
                    self.assertEqual(definition["objectives"][-1]["type"], "return_home",
                                     "an errand is brought home: its last objective is the door")
                    self.assertTrue(errand["quest_key"].startswith(HOUSEHOLD_ERRAND_PREFIX))
                    self.assertGreater(int(definition["rewards"].get(HOUSEHOLD_STANDING_REWARD_KEY, 0)), 0)
                    self.assertNotIn(errand["quest_key"], keys)
                    keys.add(errand["quest_key"])

    def test_every_objective_type_used_is_one_the_bot_reports(self):
        reported = reported_objective_types()
        for pool in ERRANDS.values():
            for errand in pool:
                for objective in errand["objectives"]:
                    self.assertIn(objective["type"], reported, objective)

    def test_only_an_errand_may_pay_standing(self):
        errand = dict(ERRANDS["Forging"][0])
        forged = {**errand, "quest_key": "forge_something_else"}
        _, errors = validate_quest_definition(forged, WORLD, BUDGET)
        self.assertTrue(any(HOUSEHOLD_STANDING_REWARD_KEY in e for e in errors), errors)
        source = (GO / "household_return.go").read_text(encoding="utf-8")
        self.assertIn('strings.HasPrefix(questKey, householdErrandPrefix)', source)

    def test_the_seed_rows_carry_no_giver_and_the_opening(self):
        rows = household_errand_seed_rows(WORLD)
        self.assertEqual(len(rows), sum(len(pool) for pool in ERRANDS.values()))
        for row in rows:
            self.assertEqual(row["giver_npc"], "")
            self.assertEqual(row["deadline_game_minutes"], 0)
            self.assertTrue(row["seed"]["trade"] in ERRANDS)
        bot = (BOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("household_errand_seed_rows(WORLD)", bot)


class TheDoorsAreWhereTheyWork(unittest.TestCase):
    def test_return_home_is_in_the_vocabulary_and_reported_after_the_reply(self):
        self.assertIn("return_home", OBJECTIVE_TYPES)
        self.assertIsNone(OBJECTIVE_TYPES["return_home"]["target"])
        family = (BOT / "commands" / "family.py").read_text(encoding="utf-8")
        reply = family.index('f"🏠 Entered **{result.get(\'family_name\')')
        report = family.index('QUESTS.progress(interaction.user.id, "return_home"')
        self.assertLess(reply, report, "the reporter must speak after /family enter has answered")
        economy = (BOT / "commands" / "economy.py").read_text(encoding="utf-8")
        self.assertIn('QUESTS.progress(interaction.user.id, "return_home"', economy)

    def test_the_engine_gates_the_four_gifts_and_the_door(self):
        source = (GO / "household_return.go").read_text(encoding="utf-8")
        for what in ("a contribution to the household", "the household's teaching", "an errand"):
            self.assertIn(f'requireAtHomeTx(conn, userID, "{what}")', source)
        support = (GO / "family_dao_actions.go").read_text(encoding="utf-8")
        self.assertIn('requireAtHomeTx(conn, userID, "the household\'s support")', support)
        door = (GO / "family_household_actions.go").read_text(encoding="utf-8")
        self.assertIn("travel there first", door)

    def test_the_panel_hides_a_door_where_it_would_refuse(self):
        surface = (BOT / "surface.py").read_text(encoding="utf-8")
        self.assertIn("register_hidden_actions(_hidden_actions)", surface)
        self.assertIn("_household_hidden_actions", surface)
        for name in ("family leave", "family support", "family contribute", "family tutor", "family errand"):
            self.assertIn(f'"{name}"', surface)
        hubs = (BOT / "hubs.py").read_text(encoding="utf-8")
        self.assertEqual(hubs.count("self.page_actions(page)"), 3, "every panel draws through the filter")

    def test_the_beginner_path_now_ends_at_home(self):
        stages = list(CONTENT["beginner_path"])
        self.assertEqual(stages[-1]["quest_key"], "beginner_home")
        self.assertEqual(stages[-2]["follow_on"], "beginner_home")
        self.assertIn("return_home", [o["type"] for o in stages[-1]["objectives"]])


class TheTalismansMakeTheRoundTrip(unittest.TestCase):
    def test_both_talismans_are_content_and_an_inscription_apprentice_can_make_them(self):
        items = CONTENT["items"]
        self.assertTrue(items["hearth_return_talisman"]["use"].get("homeward"))
        self.assertTrue(items["waymark_talisman"]["use"].get("waymark"))
        recipes = CONTENT["recipes"]
        for name in ("Hearth-Return Talisman", "Waymark Talisman"):
            with self.subTest(recipe=name):
                self.assertEqual(recipes[name]["profession"], "Inscription")
                self.assertEqual(int(recipes[name].get("min_level", 0)), 1, "an Apprentice's method, learned like any other - not an entry method the grandfathering migration would have to sweep in")

    def test_the_send_off_folds_one_into_every_childs_hands(self):
        source = (GO / "birth_family_actions.go").read_text(encoding="utf-8")
        self.assertIn("hearthReturnTalismanItem", source)
        heirloom = source.index("source_type='birth_family_sendoff'")
        talisman = source.index("hearthReturnTalismanItem")
        self.assertLess(heirloom, talisman, "the talisman sits inside the heirloom's once-guard")

    def test_item_use_moves_the_discord_side_too(self):
        economy = (BOT / "commands" / "economy.py").read_text(encoding="utf-8")
        self.assertIn("ensure_birth_family_household_thread(interaction, fam)", economy)
        self.assertIn("open_expedition_thread_after_exit(interaction)", economy)

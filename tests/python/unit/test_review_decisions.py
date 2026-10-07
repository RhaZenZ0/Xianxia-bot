"""Decisions the owner took after a review, held on the bot's side.

Merged from:

test_the_last_todos_of_v134.py — The last five TODO decisions (v1.3.4), on the bot's side.

Two era keys deleted from the vocabulary, a path's skill on the sheet, the
ghost inheritance re-pointed at the Ghost Cultivator with its scripture the
path's high manual, and `preferred_paths` read by the inheritance grant. The
engine halves are held in Go against the shipped catalogue; these hold the
content and what the bot prints.


test_the_rule_decisions_of_v133.py — The three rule decisions of v1.3.3, on the bot's side.

The owner read the research on eight open design entries and chose three
changes: the auction-door ambush is said to happen on the house's own doorstep
(where the content says its protection ends), a clan relation that runs out
ends and a broken treaty leaves a rivalry, and a Law control effect's
modifiers reach the battle's opponent. The engine halves are held in Go; these
hold what the bot prints and tells the narrator, and that no surface restates
the engine's numbers.


test_the_second_tier.py — The review's second tier of claims, each read and held on the Python side (v1.2.3).

Every gate here reads the function it is about by AST with the docstrings and
comments blanked (rc.52), because each fix's own comment names the thing it
forbids.

"""
from __future__ import annotations

import ast
import importlib
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import PROJECT_ROOT, bot_function_source, code_only


# --- from test_the_last_todos_of_v134.py ---

APP = PROJECT_ROOT / "app"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _function_source(path, name: str) -> str:
    source = code_only(path.read_text(encoding="utf-8"))
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(source, node) or ""


class TheSkillIsOnTheSheet(unittest.TestCase):
    def test_every_path_names_a_skill_and_the_sheet_prints_it(self):
        for name, path in WORLD["paths"].items():
            with self.subTest(path=name):
                self.assertTrue(str(path.get("skill") or "").strip(), "a path with no skill would print a blank line")
        source = _function_source(APP / "bot" / "commands" / "character.py", "sheet")
        self.assertIn('.get("skill")', source, "/sheet does not read the path's skill")
        self.assertIn('name="Path"', source)


class TheGhostInheritanceIsTheGhostCultivators(unittest.TestCase):
    def test_the_legacy_prefers_the_ghost_cultivator_and_its_scripture_is_a_manual(self):
        legacy = WORLD["inheritances"]["stygian_keeper_legacy"]
        self.assertEqual(legacy["preferred_paths"], ["Ghost Cultivator"])
        item = WORLD["items"][legacy["item"]]
        self.assertEqual(item.get("type"), "manual")
        manual = WORLD["technique_system"]["manuals"][item["manual_id"]]
        self.assertEqual(manual["path"], "Ghost Cultivator")
        self.assertEqual(manual["item_id"], legacy["item"])
        self.assertGreaterEqual(len(manual["techniques"]), 3)
        for tid in manual["techniques"]:
            self.assertEqual(WORLD["technique_system"]["techniques"][tid]["manual"], item["manual_id"])
        # The tomb's rooms still favour the soul path beside the ghost path: it
        # was theirs for twelve releases and the roll is the only thing a room
        # reads a path for.
        for room in WORLD["secret_realms"]["stygian_lantern_tomb"]["rooms"]:
            self.assertIn("Ghost Cultivator", room["preferred_paths"])

    def test_the_grant_reads_preferred_paths_and_the_reply_says_which(self):
        go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "secret_realm_actions.go").read_text(encoding="utf-8")
        body = go[go.index("func grantInheritanceTx("):]
        body = body[: body.index("\n}\n")]
        self.assertIn("stringInList(inheritance.PreferredPaths, path)", body, "the grant does not read preferred_paths")
        self.assertIn("manualForItem(catalog, inheritance.Item)", body)
        reply = code_only((APP / "bot" / "commands" / "secretrealm.py").read_text(encoding="utf-8"))
        self.assertIn('inheritance.get("studied")', reply, "the reply does not say the scripture was studied")


class TheEraVocabularyLostTwoKeys(unittest.TestCase):
    def test_no_era_and_no_script_authors_the_deleted_keys(self):
        cycles = WORLD["world_era_cycles"]
        for world, eras in cycles.items():
            for era in eras:
                for key in ("secret_realm_frequency", "market_volatility"):
                    self.assertNotIn(key, era.get("modifiers") or {}, f"{world}/{era.get('name')} authors {key}")
        gate = (PROJECT_ROOT / "go_core" / "internal" / "worlddata" / "era_vocabulary_test.go").read_text(encoding="utf-8")
        allow = gate[gate.index("var unreadEraModifiers = map[string]string{"):]
        allow = allow[: allow.index("}") + 1]
        self.assertEqual(allow, "var unreadEraModifiers = map[string]string{}", "the era allowlist carries an entry again")


# --- from test_the_rule_decisions_of_v133.py ---

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


class TheAmbushIsAtTheDoors(unittest.TestCase):
    def test_the_leave_reply_reads_where_off_the_engine(self):
        """The doorstep is the engine's one statement (`auctionDoorstep`); the
        reply prints `incident.where` and spells no place of its own."""
        source = _function_source(APP / "bot" / "commands" / "economy.py", "auction_leave")
        self.assertIn("incident.get('where')", source)
        self.assertNotIn("doorstep", source, "the reply carries its own copy of where the ambush stands")

    def test_the_engine_names_the_doorstep_once(self):
        go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "economy_actions.go").read_text(encoding="utf-8")
        self.assertEqual(go.count("func auctionDoorstep("), 1)
        self.assertIn('"where": auctionDoorstep(house.Name)', go)

    def test_the_narrator_is_told_the_fight_is_on_the_doorstep(self):
        """The context line above it says violence cannot begin in a safe zone,
        and the ambush stands in one for 47 of 48 houses; the narrator has to
        be told which the battle is, read off the battle's own `source`."""
        source = _function_source(APP / "ai" / "narrator_context.py", "build")
        self.assertIn('startswith("auction:")', source)
        self.assertIn("doorstep", source)


class TheDebuffIsTheEnginesSum(unittest.TestCase):
    def _rules(self):
        with patch.dict(os.environ, ENV):
            return importlib.import_module("app.rules.battle")

    def test_the_label_reads_the_engine_sum_and_invents_nothing(self):
        rules = self._rules()
        self.assertEqual(rules.opponent_debuff_label('{"agility": -3, "escape_bonus": -5}'), "agility -3 · escape -5")
        self.assertEqual(rules.opponent_debuff_label({"body": -2.0}), "body -2")
        for empty in (None, "", "{}", {}, "not json", {"agility": 0}):
            with self.subTest(value=empty):
                self.assertEqual(rules.opponent_debuff_label(empty), "")

    def test_the_card_and_the_reply_name_the_debuff_from_the_row(self):
        battle = APP / "bot" / "commands" / "battle.py"
        card = _function_source(battle, "_battle_panel") if "_battle_panel" in battle.read_text(encoding="utf-8") else ""
        text = code_only(battle.read_text(encoding="utf-8"))
        self.assertIn("opponent_debuff_label(b.get('opponent_modifiers_json'))", text, "the battle card does not read the row's debuff")
        self.assertIn('opponent_debuff_label(result.get("opponent_modifiers"))', text, "the technique reply does not say what landed on the opponent")
        self.assertNotIn("-3", card, "the card spells a number the content owns")

    def test_the_column_is_the_migrations_alone(self):
        """rc.57: a column a migration adds is not in the base DDL, and every
        engine read and write guards on it, because in the compose stack the
        engine is healthy before db-init migrates."""
        core = (APP / "database" / "core.py").read_text(encoding="utf-8")
        self.assertEqual(core.count("opponent_modifiers_json"), 1, "the column is declared more than once")
        go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "combat_actions.go").read_text(encoding="utf-8")
        self.assertIn("func battleHasOpponentMods(", go)
        for site in ("func writeOpponentMods(", "func loadBattle("):
            body = go[go.index(site):]
            body = body[: body.index("\n}\n")]
            self.assertIn("battleHasOpponentMods(conn)", body, f"{site} does not guard on the column")


# --- from test_the_second_tier.py ---

ROOT = Path(__file__).resolve().parents[3]


def _function(path: Path, name: str, cls: str | None = None) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if cls is not None and isinstance(node, ast.ClassDef) and node.name == cls:
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner.name == name:
                    return inner
        elif cls is None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path.name}: {cls or ''}.{name} not found; the reader is broken, not the tree")


def _calls(node: ast.AST) -> list[str]:
    return [ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)]


class TheSecondTier(unittest.TestCase):
    def test_a_duel_turn_is_the_engines_to_refuse(self):
        # The engine's breach check runs before its turn check (v0.23.1) so the
        # living player can end a duel whose turn holder has died; a Python
        # refusal in front of it undid that.
        body = code_only(bot_function_source("duel_act"))
        self.assertNotIn("turn_user_id", body, "duel_act refuses on whose turn it is before the engine can end a breached duel")

    def test_narrate_it_meets_the_two_gates(self):
        fn = _function(APP / "bot" / "typed_play.py", "callback", cls="_NarrateButton")
        calls = _calls(fn)
        self.assertIn("maintenance.refuse", calls, "Narrate it skips the maintenance gate a picker click meets")
        self.assertIn("seclusion.refuse", calls, "Narrate it skips the seclusion gate a picker click meets")

    def test_a_refused_creation_is_told_in_the_engines_words(self):
        fn = _function(APP / "bot" / "ui" / "creation.py", "on_submit", cls="CharacterModal")
        handlers = {ast.unparse(h.type) for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers if h.type is not None}
        self.assertIn("GameEngineError", handlers, "character.create's refusal is printed as a wiring failure")

    def test_nothing_tells_sect_discover_the_time(self):
        offenders = []
        for path in APP.rglob("*.py"):
            text = code_only(path.read_text(encoding="utf-8"))
            for m in re.finditer(r'"sect\.discover"', text):
                window = text[m.start(): m.start() + 600]
                if "game_minute" in window.split("})")[0]:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "a sect.discover payload still carries game_minute (rc.48)")
        self.assertTrue(any('"sect.discover"' in p.read_text(encoding="utf-8") for p in APP.rglob("*.py")), "no caller found; the reader is broken, not the tree")

    def test_the_shared_budget_is_asked_before_a_routes_own_slot(self):
        fn = _function(APP / "ai" / "ai_router.py", "generate")
        src = code_only(ast.unparse(fn))
        shared = src.find("self.limiter.try_acquire()")
        route = src.find("self._route_limiter(model).try_acquire()")
        self.assertTrue(shared > 0 and route > 0, "the two limiter calls were not both found; the reader is broken, not the tree")
        self.assertLess(shared, route, "a route's slot is spent before the shared budget can end the walk")

    def test_a_control_body_that_never_arrives_is_answered(self):
        text = code_only((APP / "ops" / "health.py").read_text(encoding="utf-8"))
        self.assertIn("IncompleteReadError", text, "a short control body escapes to the catch-all and the socket closes with no status")


if __name__ == "__main__":
    unittest.main()

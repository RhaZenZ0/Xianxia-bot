"""The three rule decisions of v1.3.3, on the bot's side.

The owner read the research on eight open design entries and chose three
changes: the auction-door ambush is said to happen on the house's own doorstep
(where the content says its protection ends), a clan relation that runs out
ends and a broken treaty leaves a rivalry, and a Law control effect's
modifiers reach the battle's opponent. The engine halves are held in Go; these
hold what the bot prints and tells the narrator, and that no surface restates
the engine's numbers.
"""
from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT, code_only

APP = PROJECT_ROOT / "app"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _function_source(path, name: str) -> str:
    source = code_only(path.read_text(encoding="utf-8"))
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(source, node) or ""


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


if __name__ == "__main__":
    unittest.main()

"""A home's status card says what is there (v1.30.0).

Asked as "an update on player (sect) abode - show what is there". `/abode status`
and the sect residence's status printed each facility's level and nothing else,
while what a level is worth lived in rules the card could not see. The engine's
`property.overview` answers each room with the numbers the rules use (held in
Go by `TestTheOverviewSaysWhatEachRoomDoes` and
`TestEveryHomeRuleAsksTheOverviewsHelpers`); this file holds the bot's half:
the card says those numbers and no number of its own, and both status cards
read it.
"""
from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

HOME = {
    "facilities": [
        {"key": "cultivation", "level": 1, "max_level": 9, "does": {"cultivation_mult": 1.1},
         "next": {"cultivation_mult": 1.15}, "next_cost": 400, "next_currency": "low_spirit_stone"},
        {"key": "alchemy", "level": 0, "max_level": 9, "does": {},
         "next": {"craft_bonus": 2, "trades": ["Alchemy"], "focus_effect": "Alchemy Inspiration"},
         "next_cost": 100, "next_currency": "low_spirit_stone"},
        {"key": "storage", "level": 9, "max_level": 9, "does": {"storage_slots": 90}},
        {"key": "herb_garden", "level": 1, "max_level": 9, "does": {"forage_bonus": 2},
         "next": {"forage_bonus": 4}, "next_cost": 160, "next_currency": "contribution",
         "next_needs": "Qi Refining"},
    ]
}


def _formatting():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.formatting")


class TheCardSaysTheEnginesNumbers(unittest.TestCase):
    def test_each_room_says_what_it_does_and_what_the_next_level_adds(self) -> None:
        lines = _formatting().property_overview_lines(HOME, currency_name=lambda cid: "Low Spirit Stones")
        text = "\n".join(lines)
        self.assertIn("**Cultivation Chamber** Lv.1/9 — cultivation here ×1.10", text)
        self.assertIn("Lv.2: cultivation here ×1.15 for 400 Low Spirit Stones", text)
        self.assertIn("**Alchemy Furnace** — not built", text)
        self.assertIn("build: +2 to Alchemy rolls here; Focus grants Alchemy Inspiration for 100 Low Spirit Stones", text)
        self.assertIn("+90 stacks of spatial storage · at its highest level", text)
        self.assertIn("for 160 contribution points (needs Qi Refining)", text)

    def test_a_number_the_engine_does_not_send_is_never_said(self) -> None:
        formatting = _formatting()
        self.assertEqual(formatting.facility_does_text({}), "")
        self.assertEqual(formatting.facility_does_text({"something_new": 3}), "")
        # An unbuilt room says what building gives, never what it does now.
        self.assertNotIn("—", formatting.property_overview_lines(
            {"facilities": [{"key": "forge", "level": 0, "max_level": 9, "does": {"craft_bonus": 2}}]})[0].split("not built")[1])


def _calls(source: str, function: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == function:
            return {
                (call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", ""))
                for call in ast.walk(node) if isinstance(call, ast.Call)
            }
    raise AssertionError(f"{function} is not defined; the gate is broken, not the tree")


class BothStatusCardsReadTheOverview(unittest.TestCase):
    def test_the_homestead_and_the_residence_status_read_it(self) -> None:
        for module, function in (("abode.py", "abode_status"), ("sect.py", "sect_abode")):
            calls = _calls((BOT / "commands" / module).read_text(encoding="utf-8"), function)
            self.assertIn("home_overview", calls, f"{function} no longer reads property.overview")
            self.assertIn("property_overview_lines", calls, f"{function} no longer says what each room does")

    def test_the_read_never_raises(self) -> None:
        source = (BOT / "commands" / "abode.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "home_overview")
        self.assertTrue(any(isinstance(n, ast.Try) for n in ast.walk(fn)),
                        "home_overview can raise; a status card that cannot say what a room does must still answer")
        self.assertIn('"property.overview"', ast.get_source_segment(source, fn) or "")


if __name__ == "__main__":
    unittest.main()

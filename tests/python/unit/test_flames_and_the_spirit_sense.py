"""Flames and the spirit sense (v1.10.0): the presentation half.

The rules are the engine's (`flames.go`, `spirit_sense.go`, held in Go); this
holds that the bot prints the engine's numbers, that each practice's reply
says what it built, and that the pages exist where a player looks.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from app.rules.advanced_runtime import spirit_gain_line

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
WORLD = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _law():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.law")


class TheRosters(unittest.TestCase):
    def test_flames_serve_fire_and_the_sense_serves_spirit(self):
        self.assertEqual(sorted(WORLD["flame_system"]["trades"]), ["Alchemy", "Forging"])
        self.assertEqual(sorted(WORLD["spirit_sense_system"]["trades"]), ["Formation", "Inscription"])

    def test_the_top_rung_is_opened_rather_than_ranked(self):
        top = WORLD["item_grade_system"]["grades"][-1]
        self.assertEqual(top["key"], "transcendent")
        self.assertLess(int(top["opened_min_rank"]), int(top["min_rank"]),
                        "a flame or a built sense must open the top rung below the rank no trade reaches")

    def test_the_sense_grows_from_all_three_practices(self):
        self.assertEqual(set(WORLD["spirit_sense_system"]["gains"]), {"craft", "meditation", "scene"})


class TheLines(unittest.TestCase):
    def test_a_gain_says_how_far_and_when_it_is_ready(self):
        self.assertEqual(spirit_gain_line({"gain": 14, "progress": 40, "need": 40, "ready": True}),
                         "🌀 Spirit sense **+14** → 40/40 • ready to settle (**/spirit settle**)")
        self.assertEqual(spirit_gain_line(None), "")

    def test_each_practice_prints_what_it_built(self):
        for rel in ("app/bot/commands/exploration.py", "app/bot/commands/cultivation.py", "app/bot/commands/scene.py"):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "spirit_gain_line"]
            self.assertTrue(calls, f"{rel} does not print the spirit sense a practice built")


class TheCards(unittest.TestCase):
    def test_the_flame_card_names_where_an_unheld_flame_burns(self):
        law = _law()
        status = {"trades": ["Alchemy", "Forging"], "max_refinement": 9, "flames": [
            {"flame_id": "earth_heart_fire", "name": "Earth-Heart Fire", "held": True, "bound": True, "refinement": 3,
             "bonus": 1, "next_refine_items": {"beast_core": 4}, "next_refine_qi": 40},
            {"flame_id": "nine_heavens_sun_flame", "name": "Nine-Heavens Sun Flame", "held": False,
             "location": "Solar Furnace Forge Terraces", "world": "Immortal World", "min_realm_index": 16,
             "base_bonus": 2, "max_bonus": 4, "opens_top_grade": True}]}
        card = law._flame_card({"name": "Arceus"}, status)
        text = "\n".join(value for _, value, _ in card.fields)
        self.assertIn("Solar Furnace Forge Terraces", text)
        self.assertIn("opens Transcendent when fully refined", text)
        self.assertTrue(card.fields[0][0].startswith("✅ Earth-Heart Fire"))

    def test_the_spirit_card_says_when_a_stage_is_ready(self):
        law = _law()
        status = {"trades": ["Formation", "Inscription"], "stage": 2, "max_stage": 9, "progress": 120, "need": 120,
                  "bonus": 1, "next_bonus": 2, "settle_qi": 45, "gains": {"craft": 12, "meditation": 4, "scene": 3},
                  "scene_gains_per_day": 5, "spirit_divisor": 4}
        card = law._spirit_card({"name": "Arceus"}, status)
        text = "\n".join(value for _, value, _ in card.fields)
        self.assertIn("/spirit settle", text)
        self.assertIn("meditation", text)

    def test_both_are_slash_commands_and_craft_pages(self):
        with patch.dict(os.environ, ENV):
            surface = importlib.import_module("app.bot.surface")
        for name in ("flame", "spirit"):
            self.assertIn(name, surface.TREE_COMMANDS)
            self.assertIn(name, surface._GROUP_ACTION_ROOTS)


if __name__ == "__main__":
    unittest.main()

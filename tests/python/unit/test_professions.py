"""Professions: the craft echo, the profession panel, cross-loops, flames and the spirit sense.

Merged from:

test_craft_echo.py — The craft echo (v1.0.0-rc.32): the Python half.

Samsara clears `profession_progress` before the new household tutors the new
life, so a past life's crafting used to leave no trace. The engine now records
the trades a life practised into its past-life entry ahead of the wipe and
lends a new life's rolls an echo of the best of them, as far as the soul's
memory has woken (`craftEchoTx`). Python only says so. What is held here is
that both rolls the echo can ride read it, that the record is written before
the wipe that would erase it, that the surfaces say it, and that the
reporters those commands fire did not move to make room.

test_the_profession_panel.py — The trades have a panel (v1.9.1).

Asked for in play as "a panel to check the status of your profession":
`/craft → Profession → Status` answered in a wall of text. It is a card now,
one section per trade with a bar toward the next rank, and `/profession` is a
slash command of its own.

test_profession_crossloops.py — (no docstring)

test_flames_and_the_spirit_sense.py — Flames and the spirit sense (v1.10.0): the presentation half.

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

from tests.support import PROJECT_ROOT

from app.rules.advanced_runtime import spirit_gain_line
from app.rules.progression_systems import PROFESSIONS

pytestmark = pytest.mark.unit

# --- from test_craft_echo.py ---


GO = PROJECT_ROOT / "go_core" / "internal" / "game"


BOT = PROJECT_ROOT / "app" / "bot" / "commands"


class TheEngineReadsTheEchoOnBothRolls(unittest.TestCase):
    def test_craft_and_forage_both_ask_a_past_life(self) -> None:
        crafting = (GO / "crafting_actions.go").read_text(encoding="utf-8")
        self.assertIn("craftEchoTx(conn, userID, profession)", crafting)
        self.assertIn('craftEchoTx(conn, userID, "Foraging")', crafting)
        self.assertEqual(crafting.count('"craft_echo":'), 2, "both result maps carry the echo")

    def test_the_record_is_written_before_the_wipe(self) -> None:
        lifecycle = (GO / "lifecycle_actions.go").read_text(encoding="utf-8")
        recorded = lifecycle.index("pastLifeProfessionsTx(conn, userID)")
        # The wipe is one call now (v1.12.3: `clearIncarnationStateTx`, the one
        # door for the list of tables, which the flame and the spirit sense were
        # missing from). The rule is the order of the two calls inside the
        # rebirth, and that the trades are still among what the wipe clears.
        wiped = lifecycle.index("clearIncarnationStateTx(conn, userID)")
        self.assertLess(recorded, wiped, "the trades must be read into the past-life record before samsara clears the rows")
        self.assertIn('"profession_progress", "faction_reputation"', lifecycle, "samsara no longer clears the trades it just recorded")
        self.assertIn('"professions": professions', lifecycle)
        self.assertIn('"past_life_trades": professions', lifecycle)

    def test_the_echo_is_capped(self) -> None:
        echo = (GO / "craft_echo.go").read_text(encoding="utf-8")
        self.assertIn("const craftEchoCap = 3", echo)
        self.assertIn("minI64(craftEchoCap, best*awakened/seed)", echo)


class TheSurfacesSayIt(unittest.TestCase):
    def test_craft_and_forage_name_the_echo(self) -> None:
        exploration = (BOT / "exploration.py").read_text(encoding="utf-8")
        self.assertIn("Past-life {profession} memory", exploration)
        self.assertIn("A past life’s hands", exploration)

    def test_the_soul_record_and_the_rebirth_name_the_hands(self) -> None:
        character = (BOT / "character.py").read_text(encoding="utf-8")
        self.assertIn('life.get("professions")', character)
        self.assertIn('result.get("past_life_trades")', character)

# --- from test_the_profession_panel.py ---


ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _law():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.law")


class TheProfessionPanel(unittest.TestCase):
    def test_each_trade_is_a_section_with_a_bar(self):
        law = _law()
        rows = [{"profession": "Alchemy", "level": 1, "xp": 50, "successes": 3, "failures": 1, "quality_points": 4}]
        methods = {"Alchemy": ["  ✅ **Qi Nourishing Pill** — Spirit Herb ×2 (have 5) • TN 11"],
                   "Forging": ["  ❌ **Spirit-Iron Sword** — Spirit Iron ×3 (have 0) • TN 12"]}
        card = law._profession_card({"name": "Arceus"}, rows, methods, True)
        names = [name for name, _, _ in card.fields]
        self.assertTrue(names[0].startswith("Alchemy — "), names)
        self.assertTrue(any(name.startswith("Forging — ") for name in names),
                        "a trade known only by its methods lost its section")
        alchemy = card.fields[0][1]
        self.assertIn("🟧", alchemy)
        self.assertIn("XP**", alchemy)
        self.assertIn("Qi Nourishing Pill", alchemy)
        self.assertIn("Reading this", names)

    def test_the_bar_never_overflows(self):
        law = _law()
        self.assertEqual(law._xp_bar(500, 100).count("🟧"), 10)
        self.assertEqual(law._xp_bar(0, 100).count("🟧"), 0)

    def test_profession_is_a_slash_command(self):
        with patch.dict(os.environ, ENV):
            surface = importlib.import_module("app.bot.surface")
        self.assertIn("profession", surface.TREE_COMMANDS)
        self.assertIn("body", surface.TREE_COMMANDS)

# --- from test_profession_crossloops.py ---


class ProfessionCrossLoopTests(unittest.TestCase):
    def test_profession_catalog_contains_cross_loop_tracks(self):
        self.assertIn("Foraging", PROFESSIONS)
        self.assertIn("Beast Taming", PROFESSIONS)
        self.assertIn("Artifact Refining", PROFESSIONS)

# --- from test_flames_and_the_spirit_sense.py ---


ROOT = Path(__file__).resolve().parents[3]


WORLD = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))


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
        full = spirit_gain_line({"gain": 0, "progress": 40, "need": 40, "ready": True, "full": True})
        self.assertIn("/spirit settle", full, "a full stage must say so rather than print nothing")

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

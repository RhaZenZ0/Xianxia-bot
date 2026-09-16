"""The craft echo (v1.0.0-rc.32): the Python half.

Samsara clears `profession_progress` before the new household tutors the new
life, so a past life's crafting used to leave no trace. The engine now records
the trades a life practised into its past-life entry ahead of the wipe and
lends a new life's rolls an echo of the best of them, as far as the soul's
memory has woken (`craftEchoTx`). Python only says so. What is held here is
that both rolls the echo can ride read it, that the record is written before
the wipe that would erase it, that the surfaces say it, and that the
reporters those commands fire did not move to make room.
"""

from __future__ import annotations

import unittest

from tests.support import PROJECT_ROOT

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
        wiped = lifecycle.index('"profession_progress", "faction_reputation"')
        self.assertLess(recorded, wiped, "the trades must be read into the past-life record before samsara clears the rows")
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

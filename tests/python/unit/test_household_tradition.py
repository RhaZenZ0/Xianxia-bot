"""Tradition and Tutoring (v1.0.0-rc.31): the Python half.

The engine decides the roll (`householdTradeBonusTx`) and writes the head
start (`tutorHouseholdTradeTx`); Python's rule is for display and the
send-off prose only says what the engine did. The prose lives in `rules`
beside the rule it prints, because both the creation embed (`bot/ui`) and
`/family` (`bot/commands`) say it and `ui` sits below `commands`. What is held here is that the
Python rule reads the same roster the engine reads, that every household's
trade is one a character can actually practise, and that no surface still
assumes the bonus is Alchemy's.
"""

from __future__ import annotations

import json
import unittest

from app.rules.birthfamily import FAMILY_ARCHETYPES, FAMILY_TRADE_BONUS, family_profession_bonus, family_trade, family_tutoring_line
from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
SENDOFF = dict(WORLD["birth_family_sendoff"])
# FAMILY_ARCHETYPES is a list of archetype dicts, each carrying its id.
ARCHETYPE_IDS = (
    [str(a.get("id") or a.get("archetype")) for a in FAMILY_ARCHETYPES]
    if isinstance(FAMILY_ARCHETYPES, (list, tuple)) else list(FAMILY_ARCHETYPES)
)


class EveryHouseholdTeachesARealTrade(unittest.TestCase):
    def test_every_archetype_names_a_trade_with_an_entry_method(self):
        recipes = WORLD["recipes"]
        for archetype in ARCHETYPE_IDS:
            with self.subTest(archetype=archetype):
                trade = family_trade({"archetype": archetype}, SENDOFF)
                self.assertTrue(trade, f"{archetype} sends its children out with no trade")
                entry = [n for n, r in recipes.items() if r.get("profession") == trade and int(r.get("min_level", 0)) <= 0]
                self.assertTrue(entry, f"{archetype} teaches {trade}, which has no level-0 recipe to teach")

    def test_the_bonus_follows_the_trade_not_the_archetype_name(self):
        for archetype in ARCHETYPE_IDS:
            trade = family_trade({"archetype": archetype}, SENDOFF)
            with self.subTest(archetype=archetype, trade=trade):
                self.assertEqual(family_profession_bonus({"archetype": archetype}, trade, SENDOFF), FAMILY_TRADE_BONUS)
                other = "Alchemy" if trade != "Alchemy" else "Forging"
                self.assertEqual(family_profession_bonus({"archetype": archetype}, other, SENDOFF), 0)

    def test_the_body_tempering_family_finally_counts(self):
        """It taught Alchemy from rc.20 and, keyed on the string
        "alchemy_family", earned nothing for it."""
        self.assertEqual(family_trade({"archetype": "body_tempering_family"}, SENDOFF), "Alchemy")
        self.assertEqual(family_profession_bonus({"archetype": "body_tempering_family"}, "Alchemy", SENDOFF), FAMILY_TRADE_BONUS)

    def test_no_household_and_no_roster_are_both_nothing(self):
        self.assertEqual(family_profession_bonus(None, "Alchemy", SENDOFF), 0)
        self.assertEqual(family_profession_bonus({"archetype": "alchemy_family"}, "Alchemy", None), 0)
        self.assertEqual(family_trade({"archetype": "not_a_house"}, SENDOFF), "")


class NoSurfaceStillAssumesAlchemy(unittest.TestCase):
    def test_the_result_labels_name_the_trade(self):
        source = (PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py").read_text(encoding="utf-8")
        self.assertNotIn("Birth-family Alchemy tradition", source)
        self.assertNotIn("Alchemy-family herb lore", source)
        self.assertIn("family_trade", source)

    def test_the_engine_keys_on_the_trade_too(self):
        go = (PROJECT_ROOT / "go_core" / "internal" / "game")
        crafting = (go / "crafting_actions.go").read_text(encoding="utf-8")
        self.assertNotIn('== "alchemy_family"', crafting, "the archetype-string special case is gone from the roll")
        self.assertIn("householdTradeBonusTx(conn, catalog, userID, profession)", crafting)
        self.assertIn("householdForageBonusTx(conn, catalog, userID)", crafting)
        sendoff = (go / "birth_family_actions.go").read_text(encoding="utf-8")
        self.assertLess(sendoff.index("teachHouseholdMethodsTx(conn, catalog, userID, familyID, archetype, gameMinute, now)"),
                        sendoff.index("tutorHouseholdTradeTx(conn, catalog, userID, familyID, archetype, now)"),
                        "the methods come first, then how well they were taught")


class TheSendoffSaysWhoTaughtYou(unittest.TestCase):
    def _line(self, sendoff: dict) -> str:
        return family_tutoring_line(sendoff)

    def test_each_band_reads_differently(self):
        base = {"trade": "Forging"}
        master = self._line({**base, "tutoring": {"profession": "Forging", "tutor": "a master retained", "level": 1, "xp": 0}})
        tutor = self._line({**base, "tutoring": {"profession": "Forging", "tutor": "a hired tutor", "level": 0, "xp": 55}})
        basics = self._line({**base, "tutoring": {"profession": "Forging", "tutor": "shown the basics", "level": 0, "xp": 0}})
        self.assertIn("**Apprentice** of Forging", master)
        self.assertIn("**55 XP** toward Apprentice Forging", tutor)
        self.assertIn("knowing the basics of Forging", basics)
        for line in (master, tutor, basics):
            self.assertIn(f"+{FAMILY_TRADE_BONUS} Forging tradition", line)

    def test_no_trade_says_nothing(self):
        self.assertEqual(self._line({}), "")
        self.assertEqual(self._line({"tutoring": {}}), "")


if __name__ == "__main__":
    unittest.main()

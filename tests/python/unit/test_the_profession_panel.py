"""The trades have a panel (v1.9.1).

Asked for in play as "a panel to check the status of your profession":
`/craft → Profession → Status` answered in a wall of text. It is a card now,
one section per trade with a bar toward the next rank, and `/profession` is a
slash command of its own.
"""

from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

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


if __name__ == "__main__":
    unittest.main()

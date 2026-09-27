"""The raid is drawn as one card (v1.8.3).

Start, Status and Act answered in loose text lines - the boss's HP, a phase
name, and each raider as a bare line - and the engine names a struck raider by
user id, so a player read "Last Roar hits 1456074443989188610 for 9". The card
draws the boss's health as a bar, the phase and round, and every raider's
vitality and whether they have acted this round, off the row the engine wrote.
"""

from __future__ import annotations

import ast
import importlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

BOSS = Path(__file__).resolve().parents[3] / "app" / "bot" / "commands" / "boss.py"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
RAIDER, OTHER = 1456074443989188610, 1456074443989188611


def _encounter(**overrides):
    row = {
        "encounter_id": 7, "template_key": "iron_tusk_boar_king", "boss_name": "Iron-Tusk Boar King",
        "location": "Greenriver Town", "boss_hp": 90, "boss_hp_max": 180, "phase_index": 1,
        "round_index": 3, "status": "active",
        "participants": [
            {"user_id": RAIDER, "vitality": 30, "vitality_max": 40, "acted_round": 3, "guard": 0,
             "total_damage": 55, "status": "active"},
            {"user_id": OTHER, "vitality": 0, "vitality_max": 40, "acted_round": 2, "guard": 0,
             "total_damage": 12, "status": "knocked_out"},
        ],
    }
    row.update(overrides)
    return row


def raid_card(*args, **kwargs):
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.boss").raid_card(*args, **kwargs)


def _fields(card):
    return {name: value for name, value, _inline in card.fields}


class TheRaidHasACard(unittest.TestCase):
    def test_the_card_draws_the_boss_the_phase_and_every_raider(self):
        embed = raid_card(_encounter())
        fields = _fields(embed)
        self.assertIn("Raid #7", embed.title)
        self.assertIn("**90/180**", fields["Boss"])
        self.assertIn("Blood Frenzy", fields["Phase"])
        self.assertIn("2/3", fields["Phase"])
        self.assertIn("33%", fields["Phase"], "the phase does not say when it shifts, off the template's threshold")
        self.assertEqual(fields["Round"], "**3**")
        party = fields["Raid Party (2)"]
        self.assertIn(f"<@{RAIDER}> • ✅ acted", party)
        self.assertIn(f"<@{OTHER}> • 💀 down", party)
        self.assertIn("**30/40**", party)

    def test_a_raider_who_has_not_acted_is_waited_on(self):
        row = _encounter()
        row["participants"][0]["acted_round"] = 2
        self.assertIn("⏳ to act", _fields(raid_card(row))["Raid Party (2)"])

    def test_the_engines_user_ids_become_mentions(self):
        embed = raid_card(_encounter(), events=[f"Blood Frenzy hits {RAIDER} for 9 raid vitality."])
        self.assertIn(f"hits <@{RAIDER}> for 9", embed.description)
        self.assertNotIn(f" {RAIDER} ", embed.description)

    def test_a_won_raid_says_how_to_claim(self):
        embed = raid_card(_encounter(status="victory", boss_hp=0))
        self.assertIn("🏆", embed.title)
        self.assertIn("/boss claim encounter_id:7", embed.footer)
        self.assertNotIn("shifts", _fields(embed)["Phase"], "a finished raid does not promise a phase shift")

    def test_a_large_party_stays_inside_one_field(self):
        row = _encounter()
        row["participants"] = [
            {"user_id": RAIDER + i, "vitality": 10, "vitality_max": 40, "acted_round": 0, "guard": 0,
             "total_damage": 0, "status": "active"} for i in range(30)
        ]
        party = _fields(raid_card(row))["Raid Party (30)"]
        self.assertLessEqual(len(party), 1024)
        self.assertIn("more raiders", party)

    def test_start_status_and_act_all_answer_with_the_card(self):
        tree = ast.parse(BOSS.read_text(encoding="utf-8"))
        callers = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "raid_card"
        }
        self.assertTrue({"boss_start", "boss_status", "boss_act"} <= callers, callers)


if __name__ == "__main__":
    unittest.main()

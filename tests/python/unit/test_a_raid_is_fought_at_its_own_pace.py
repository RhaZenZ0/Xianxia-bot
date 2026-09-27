"""A raid is fought at its own pace, with every technique a raider has (v1.9.1).

Reported from play, in three messages about one solo raid:

- *"I get the cooldown thing for the actions."* Every press of the raid card
  spent a token from the per-player action meter (four, then one every ten
  seconds). The meter guards the AI narration allowance; a raid press calls no
  model, and the engine already holds a raider to one action a round. Solo,
  every press is a round, so the meter was the pace of the whole fight.
- *"I can't use my manual techniques. The techniques here are for laws."* The
  Technique button offered Law techniques only.
- *"Can you say what each technique does specifically?"* A manual technique's
  picker line was its flavour text, which for the generated catalogue reads
  "A mortal-tier sword cultivator technique preserved in ...".

The engine half (a manual technique in `boss.act`, and a miss that says it
missed) is held in Go by `boss_solo_test.go`.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from app.rules.advanced_runtime import describe_manual_technique, law_raid_strike_bonus

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
BOSS = ROOT / "app" / "bot" / "commands" / "boss.py"
RUNTIME = ROOT / "app" / "bot" / "runtime.py"
GO_RAID = ROOT / "go_core" / "internal" / "game" / "group_combat_actions.go"
GO_MANUAL = ROOT / "go_core" / "internal" / "game" / "manual_forbidden_actions.go"
WORLD = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
LOW_FLAME = "one_in_ten_furnace_record_low_flame"


def _boss():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.boss")


def _function(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found; the reader is broken, not the tree")


def _calls(node: ast.AST) -> set[str]:
    return {ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}


class ARaidPressSpendsNoMeter(unittest.TestCase):
    def test_the_card_spends_no_token(self):
        tree = ast.parse(BOSS.read_text(encoding="utf-8"))
        dispatch = _function(tree, "dispatch")
        self.assertIn("_user_action_lock", _calls(dispatch), "the reader found the wrong dispatch")
        self.assertNotIn("budget_refusal_line", _calls(dispatch),
                         "a raid card press spends the action meter again, so a solo raid is paced by it")

    def test_the_slash_commands_spend_no_token(self):
        tree = ast.parse(BOSS.read_text(encoding="utf-8"))
        for name in ("boss_act", "boss_claim"):
            decorators = [ast.unparse(d) for d in _function(tree, name).decorator_list]
            self.assertIn("serialized_user_action(metered=False)", decorators,
                          f"/boss {name[5:]} spends the action meter again")

    def test_an_unmetered_action_keeps_the_lock_and_spends_nothing(self):
        with patch.dict(os.environ, ENV):
            runtime = importlib.import_module("app.bot.runtime")
        ran: list[str] = []

        class _Response:
            def is_done(self):
                return False

            async def send_message(self, text, **_):
                ran.append(f"refused: {text}")

        class _User:
            id = 424242

        class _Interaction:
            user = _User()
            response = _Response()

        async def act(interaction):
            ran.append("acted")

        unmetered = runtime.serialized_user_action(metered=False)(act)
        metered = runtime.serialized_user_action(act)
        with patch.object(runtime, "budget_refusal_line", lambda uid, door: "⏳ slow down"):
            asyncio.run(unmetered(_Interaction()))
            asyncio.run(metered(_Interaction()))
        self.assertEqual(ran, ["acted", "refused: ⏳ slow down"],
                         "metered=False must skip the meter, and the default must still spend it")


class AManualTechniqueIsOfferedInARaid(unittest.TestCase):
    def test_a_studied_manual_s_technique_is_on_the_picker(self):
        boss = _boss()
        options = boss._raid_manual_techniques([{"manual_id": "one_in_ten_furnace_record", "mastery": 2}])
        values = {value: text for value, _, text in options}
        self.assertIn(f"manual:{LOW_FLAME}", values)
        self.assertIn("+6 to the strike", values[f"manual:{LOW_FLAME}"])

    def test_an_unstudied_manual_offers_nothing(self):
        self.assertEqual(_boss()._raid_manual_techniques([]), [])

    def test_a_manual_technique_rides_its_own_field(self):
        boss = _boss()
        self.assertEqual(boss._technique_payload(f"manual:{LOW_FLAME}"),
                         {"technique": "", "manual_technique": LOW_FLAME})
        self.assertEqual(boss._technique_payload("folded_step"), {"technique": "folded_step"})
        go = GO_RAID.read_text(encoding="utf-8")
        self.assertIn('ManualTechnique string `json:"manual_technique"`', go)


class ATechniqueSaysWhatItDoes(unittest.TestCase):
    """The numbers are the engine's, so the Go expressions they twin are held."""

    def test_the_twinned_expressions_are_still_the_engine_s(self):
        manual = GO_MANUAL.read_text(encoding="utf-8")
        raid = GO_RAID.read_text(encoding="utf-8")
        self.assertIn("damage := max64(0, t.Damage+mastery)", manual)
        self.assertIn("heal := max64(0, t.Heal+mastery)", manual)
        self.assertIn("bonus = max64(1, mt.Damage+mastery)", raid)
        self.assertIn("bonus = 2 + comp/20", raid)

    def test_a_battle_line_names_damage_heal_and_cost(self):
        technique = WORLD["technique_system"]["techniques"][LOW_FLAME]
        line = describe_manual_technique(technique, mastery=2)
        self.assertEqual(line, "💥 6 damage · 🩸 +2 vitality · ⚡ 2 Qi")

    def test_a_forbidden_art_says_so(self):
        techniques = WORLD["technique_system"]["techniques"]
        technique = techniques["life_burning_demon_flame"]
        manual = WORLD["technique_system"]["manuals"][technique["manual"]]
        line = describe_manual_technique(technique, manual=manual)
        self.assertIn("☯️ forbidden", line)
        self.assertIn("Vit", line)

    def test_every_line_fits_a_select_option(self):
        manuals = WORLD["technique_system"]["manuals"]
        for tid, technique in WORLD["technique_system"]["techniques"].items():
            for raid in (False, True):
                line = describe_manual_technique(technique, mastery=10, manual=manuals.get(technique.get("manual")), raid=raid)
                self.assertLessEqual(len(line), 100, f"{tid}: Discord caps an option's description at 100")

    def test_the_law_bonus_is_the_engine_s(self):
        self.assertEqual([law_raid_strike_bonus(c) for c in (0, 19, 20, 100)], [2, 2, 3, 7])

    def test_the_battle_picker_reads_what_a_technique_does(self):
        battle = (ROOT / "app" / "bot" / "commands" / "battle.py").read_text(encoding="utf-8")
        options = _function(ast.parse(battle), "_battle_available_options")
        self.assertIn("describe_manual_technique", _calls(options),
                      "the battle picker prints a manual technique's flavour text again")


if __name__ == "__main__":
    unittest.main()

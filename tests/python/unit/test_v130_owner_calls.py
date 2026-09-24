"""The ten decisions the owner took in one message (v1.3.0), the bot's half.

Each test is one rule, and each was drilled against the tree before the fix:
the Qi Body card printing nothing but where the page opens, a confirm step
naming no count, a boss lair the panel could never draw, a craft reply that
said the ingredients were consumed when half had come back, an examination
reply that named nothing of what it withheld, a homestead offered inside a
shared household, and a talisman hall paying more than its inputs cost.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT, code_only

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
BOT = PROJECT_ROOT / "app" / "bot"


def _module(name: str):
    with patch.dict(os.environ, ENV):
        return importlib.import_module(name)


def _function_source(path, name: str) -> str:
    """One function's statements, without its docstring (rc.52)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                                     and isinstance(getattr(node.body[0], "value", None), ast.Constant)) else node.body
            return "\n".join(ast.unparse(stmt) for stmt in body)
    raise AssertionError(f"{name} not found in {path.name}; the reader is broken, not the tree")


class TheQiBodyCardShowsThePool(unittest.TestCase):
    STATUS = {"qi": 30, "qi_max": 120, "purity": 40, "purity_ceiling": 60, "skill_cost_mult": 1.1,
              "meridians_open": 20, "meridian_ceiling": 108, "meridians_damaged": 0}

    def test_the_pool_and_the_purity_are_shown_below_the_floor(self):
        cards = _module("app.bot.status_cards")
        roster = cards.WORLD.data.get("feature_unlocks") or {}
        self.assertTrue(roster, "no curriculum; the reader is broken, not the tree")
        line = cards._qi_body_field(self.STATUS, 0)
        self.assertIn("**30 / 120** qi", line, "the pool is hidden at Body Tempering")
        self.assertIn("purity **40%**", line, "the purity is hidden at Body Tempering")
        self.assertIn("channels open at", line, "the card no longer says where the channels open")
        self.assertNotIn("meridians", line, "the card advertises the lever the curriculum hides")

    def test_the_channels_come_back_when_the_page_opens(self):
        cards = _module("app.bot.status_cards")
        line = cards._qi_body_field(self.STATUS, 31)
        self.assertIn("**20/108** meridians", line)
        self.assertNotIn("channels open at", line)


class TheResetConfirmNamesTheCount(unittest.TestCase):
    def _note(self, answer):
        character = _module("app.bot.commands.character")

        async def action(_op, _uid, _payload):
            if isinstance(answer, Exception):
                raise answer
            return answer

        interaction = SimpleNamespace(user=SimpleNamespace(id=42))
        with patch.object(character.ENGINE, "action", action):
            return asyncio.run(character._reset_confirm_note(interaction))

    def test_the_count_is_the_engines(self):
        # Five is a number this tree does not contain (v1.0.13's rule), so a
        # panel printing "of 3" on its own would fail here.
        note = self._note({"resets_used": 3, "resets_remaining": 2, "reset_allowance": 5})
        self.assertIn("**2 of 5**", note)

    def test_an_absent_field_is_unknown_never_zero(self):
        self.assertIn("unknown", self._note({"resets_used": 1}))
        self.assertIn("unknown", self._note(RuntimeError("engine down")))

    def test_the_confirm_step_asks_for_the_note(self):
        hubs = _module("app.bot.hubs")
        self.assertIn("_confirm_note(", _function_source(BOT / "hubs.py", "_start_hub_action"))
        self.assertIn("/reset", hubs._CONFIRM_NOTES, "the reset leaf registered no note")

    def test_a_raising_provider_costs_only_its_line(self):
        hubs = _module("app.bot.hubs")

        async def boom(_interaction):
            raise RuntimeError("no")

        hubs._CONFIRM_NOTES["/drill"] = boom
        try:
            note = asyncio.run(hubs._confirm_note(SimpleNamespace(), SimpleNamespace(path="/drill")))
        finally:
            hubs._CONFIRM_NOTES.pop("/drill", None)
        self.assertEqual(note, "", "one failed lookup would cost the whole question rather than one line of it")


class TheNineEchoFloorIsDrawnWhereItIsFought(unittest.TestCase):
    def test_the_twin_answers_what_the_engine_answers(self):
        runtime = importlib.import_module("app.rules.advanced_runtime")
        template = runtime.BOSS_TEMPLATES["nine_echo_sword_wraith"]
        lair, realm_id = runtime.boss_lair(template, CONTENT["secret_realms"])
        # The rule a third time, off the raw content (v1.0.9): a realm named
        # by the lair is fought at that realm's entrance.
        realm = next((rid for rid, r in CONTENT["secret_realms"].items() if r["name"] == template["location"]), "")
        self.assertTrue(realm, "the wraith's lair no longer names a secret realm; re-read the decision")
        self.assertEqual((lair, realm_id), (CONTENT["secret_realms"][realm]["location"], realm))
        self.assertIn(lair, CONTENT["locations"], "the entrance is not a catalogue place")
        for key, boss in runtime.BOSS_TEMPLATES.items():
            if key != "nine_echo_sword_wraith":
                self.assertEqual(runtime.boss_lair(boss, CONTENT["secret_realms"]), (boss["location"], ""))

    def test_the_panel_draws_start_at_the_entrance(self):
        surface = _module("app.bot.surface")
        runtime = importlib.import_module("app.rules.advanced_runtime")
        lair, _ = runtime.boss_lair(runtime.BOSS_TEMPLATES["nine_echo_sword_wraith"], CONTENT["secret_realms"])

        async def member_manor(_uid):
            return None

        interaction = SimpleNamespace(user=SimpleNamespace(id=42))
        with patch.object(surface.DB, "get_member_sect_manor", member_manor):
            at_entrance = asyncio.run(surface._location_hidden_actions(interaction, {"location": lair}))
            lairs = {runtime.boss_lair(b, CONTENT["secret_realms"])[0] for b in runtime.BOSS_TEMPLATES.values()}
            nowhere = next(name for name in sorted(CONTENT["locations"]) if name not in lairs)
            elsewhere = asyncio.run(surface._location_hidden_actions(interaction, {"location": nowhere}))
        self.assertNotIn("/boss start", at_entrance, "Start is hidden at the one place the raid can begin")
        self.assertIn("/boss start", elsewhere)


class TheCraftReplySaysWhatCameBack(unittest.TestCase):
    def test_the_reply_reads_the_engines_refund(self):
        source = _function_source(BOT / "commands" / "exploration.py", "_run_crafting")
        self.assertIn("resolved.get('returned')", source, "the reply no longer reads what the engine handed back")
        self.assertIn("salvage", source)


class TheExaminationReplyNamesWhatItWithheld(unittest.TestCase):
    def test_the_reply_reads_the_engines_withheld_list(self):
        source = (BOT / "commands" / "law.py").read_text(encoding="utf-8")
        self.assertIn("recipes_withheld", code_only(source))
        self.assertIn("hall_world", code_only(source))


class AHomesteadIsNotOfferedInsideTheHousehold(unittest.TestCase):
    def test_establish_is_hidden_inside_a_birth_household(self):
        surface = _module("app.bot.surface")

        async def member_manor(_uid):
            return None

        interaction = SimpleNamespace(user=SimpleNamespace(id=42))
        with patch.object(surface.DB, "get_member_sect_manor", member_manor):
            shut = asyncio.run(surface._location_hidden_actions(interaction, {"location": "birth_family:7"}))
        self.assertIn("/abode establish", shut)
        self.assertIn("household you were born into", shut["/abode establish"])

    def test_the_engine_refuses_it_too(self):
        go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "property_storage_actions.go").read_text(encoding="utf-8")
        self.assertIn('strings.HasPrefix(loc, "birth_family:")', go, "the bound lives only in the client (rc.48)")


class NoTalismanHallPaysMoreThanTheMakingsCost(unittest.TestCase):
    def test_the_starfall_talisman_is_no_longer_a_profit_at_novice(self):
        """v1.0.17 measured the Celestial Mandate hall paying 120 against inputs
        of 102; on the owner's call the buy line is under the makings."""
        recipe = CONTENT["recipes"]["Starfall Talisman"]
        checked = 0
        for shop in CONTENT["shops"].values():
            buy = (shop.get("buys") or {}).get("starfall_talisman")
            if buy is None:
                continue
            world = shop["world"]
            cost = 0
            for item, qty in recipe["cost"].items():
                prices = [int(line["price"]) for s in CONTENT["shops"].values() if s["world"] == world
                          for line in s.get("sells") or [] if line["item_id"] == item]
                self.assertTrue(prices, f"{world} shelves no {item}; the reader is broken, not the tree")
                cost += min(prices) * int(qty)
            checked += 1
            with self.subTest(hall=shop["name"]):
                self.assertLess(int(buy), cost, f"{shop['name']} pays {buy} for a talisman whose makings cost {cost} on its world's shelves")
        self.assertGreaterEqual(checked, 5, "no hall buys the talisman; the reader is broken, not the tree")

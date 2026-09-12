"""v1.0.0-rc.7: the qi body — three dantian, a hundred and eight meridians.

A cultivator now has somewhere for qi to live: a lower dantian whose size is
the realm they stand in, the channels they have opened and the method they
practise; a middle dantian whose purity prices every technique; and an upper
dantian that opens at Nascent Soul. Every qi number written in the content is
read as a share of that pool rather than as a flat count, so the prices still
bite at the fifth realm.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
CORE_SOURCE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
CULTIVATION_SOURCE = (BOT / "commands" / "cultivation.py").read_text(encoding="utf-8")
CARDS_SOURCE = (BOT / "status_cards.py").read_text(encoding="utf-8")
GO_QI = (GO / "qi_body.go").read_text(encoding="utf-8")
GO_AUTHORITATIVE = (GO / "authoritative.go").read_text(encoding="utf-8")
GO_ACTIONS = (GO / "cultivation_actions.go").read_text(encoding="utf-8")
GO_STANCE = (GO / "cultivation_stance.go").read_text(encoding="utf-8")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _modules():
    with patch.dict(os.environ, ENV):
        return (importlib.import_module("app.bot.surface"),
                importlib.import_module("app.bot.hubs"),
                importlib.import_module("app.bot.status_cards"))


class TheTableAndTheEngine(unittest.TestCase):
    def test_schema_forty_is_the_qi_body(self):
        from app.database import SCHEMA_VERSION
        # The qi body is migration 40; later releases move the current schema on.
        self.assertGreaterEqual(SCHEMA_VERSION, 40)
        self.assertIn('        40,\n        "qi_body",', CORE_SOURCE)
        self.assertIn("CREATE TABLE IF NOT EXISTS character_qi_body", CORE_SOURCE)
        for column in ("purity", "meridians_open", "meridians_damaged", "dantian_state", "settled_game_minute"):
            self.assertIn(column, CORE_SOURCE, column)

    def test_the_three_dantian_and_the_hundred_and_eight_channels(self):
        self.assertIn("meridianCeiling     = 108", GO_QI)
        self.assertIn("meridianStartOpen   = 12", GO_QI)
        self.assertIn("func qiCapacityFor(", GO_QI)
        self.assertIn("func qiRegenPerGameMinute(", GO_QI)
        self.assertIn("func purityCeilingFor(", GO_QI)
        self.assertIn("func upperDantianOpen(realm int64) bool { return realm >= 4 }", GO_QI)
        self.assertIn("func spiritualSenseReach(", GO_QI)
        self.assertIn("qiRefillGameMinutes = 240", GO_QI)

    def test_every_qi_number_is_a_share_of_the_pool(self):
        self.assertIn("func referenceQiPool(spirit int64) float64", GO_QI)
        self.assertIn("func scaledQiCost(base, capacity int64, reference float64, body qiBody) int64", GO_QI)
        self.assertIn("func scaledQiRestore(", GO_QI)
        self.assertIn("qiCostShareCeiling = 0.5", GO_QI)
        self.assertIn("func (s qiState) Cost(base int64) int64", GO_QI)
        self.assertIn("func (s qiState) Restore(base int64) int64", GO_QI)
        # Every caller that used to read a flat qi number now goes through it.
        for path in ("manual_forbidden_actions.go", "alchemy_actions.go", "item_use_actions.go", "combat_actions.go"):
            source = (GO / path).read_text(encoding="utf-8")
            self.assertTrue("state.Cost(" in source or "state.Restore(" in source, path)

    def test_the_engine_registers_the_three_actions_and_the_read(self):
        for operation in ('"meridian.open"', '"meridian.heal"', '"qi.refine"'):
            self.assertIn(operation, GO_AUTHORITATIVE, operation)
        self.assertIn('"qi.status":                 true', GO_AUTHORITATIVE)
        self.assertIn("func meridianOpenAction(", GO_QI)
        self.assertIn("func meridianHealAction(", GO_QI)
        self.assertIn("func refineQiAction(", GO_QI)
        self.assertIn("func qiBodyStatusQuery(", GO_QI)

    def test_cultivation_pays_in_qi_and_in_purity(self):
        # A breakthrough spends a quarter of the dantian before the roll.
        self.assertIn("breakthroughQiShare = 4", GO_QI)
        self.assertIn("qi_spent", GO_ACTIONS)
        # Every stage crossed opens a channel; forcing costs purity; a severe
        # deviation tears one.
        self.assertIn("openMeridianOnStage(", GO_ACTIONS)
        self.assertIn("losePurity(", GO_ACTIONS)
        self.assertIn("damageMeridian(", GO_ACTIONS)
        # The sheet needs one call, so the cultivation status carries it all.
        for key in ('"qi"', '"qi_max"', '"qi_regen"', '"purity"', '"purity_ceiling"',
                    '"skill_cost_mult"', '"meridians_open"', '"meridian_ceiling"',
                    '"dantian_state"', '"breakthrough_qi_cost"'):
            self.assertIn(key, GO_STANCE, key)


class TheQiBodyOnTheSurface(unittest.TestCase):
    def test_the_cultivation_hub_has_a_qi_body_page(self):
        surface, hubs, _ = _modules()
        pages = {page.label: page for page in surface._HUB_BY_NAME["cultivation"].pages}
        self.assertIn("Qi Body", pages)
        paths = {action.path for action in hubs._leaf_actions(pages["Qi Body"])}
        for expected in ("/dantian status", "/dantian refine", "/meridian status",
                         "/meridian open", "/meridian heal"):
            self.assertIn(expected, paths, expected)

    def test_the_commands_speak_of_the_dantian_and_the_channels(self):
        self.assertIn('"meridian.open", interaction.user.id', CULTIVATION_SOURCE)
        self.assertIn('"meridian.heal", interaction.user.id', CULTIVATION_SOURCE)
        self.assertIn('"qi.refine"', CULTIVATION_SOURCE)
        self.assertIn("Lower dantian", CULTIVATION_SOURCE)
        self.assertIn("Middle dantian", CULTIVATION_SOURCE)
        self.assertIn("Upper dantian", CULTIVATION_SOURCE)

    def test_the_sheet_carries_the_pool_the_purity_and_the_channels(self):
        surface, _, cards = _modules()
        status = {
            "realm_index": 4, "stage": 2, "cultivation": 400, "cost": 7885, "insight_xp": 4,
            "odds": {"tn": 18, "modifier": 13, "probability": 72, "movers": []},
            "stance": "circulate", "stance_label": "Circulate", "stance_mult": 1.0, "cooldown_remaining": 0,
            "qi": 6000, "qi_max": 12000, "qi_regen": 52.5, "purity": 61, "purity_ceiling": 63,
            "skill_cost_mult": 1.39, "meridians_open": 26, "meridians_damaged": 1,
            "meridian_ceiling": 108, "dantian_state": "cracked",
            "period": "Dawn", "season": "Spring", "time_mult": 1.1,
        }
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(cards.DB, "get_character", AsyncMock(return_value={"gender": "female"})), \
             patch.object(cards.ENGINE, "action", AsyncMock(return_value=status)):
            fields = asyncio.run(surface._cultivation_hub_status(interaction))
        names = [field.name for field in fields]
        self.assertIn("🫀 Qi Body", names)
        text = "\n".join(f"{f.name}\n{f.value}" for f in fields)
        self.assertIn("**6,000 / 12,000** qi", text)
        self.assertIn("+52.5/game minute", text)
        self.assertIn("purity **61%** of **63%**", text)
        self.assertIn("techniques **x1.39**", text)
        self.assertIn("**26/108** meridians", text)
        self.assertIn("**1 ruptured**", text)
        self.assertIn("cracked", text)
        self.assertLess(len(text), 1024)


if __name__ == "__main__":
    unittest.main()

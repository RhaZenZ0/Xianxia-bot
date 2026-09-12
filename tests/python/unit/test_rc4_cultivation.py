"""v1.0.0-rc.4: the place matters, Insight XP does something, four pages.

Where a cultivator sits changes what a session gathers, and the Here line,
the result and the sheet all say so; Insight XP buys a seized moment after
a failed breakthrough and a bonus on a Law comprehension; the cultivation
hub is four pages named after what you are doing; and every 2d10 roll
prints the chance it had.
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
SURFACE_SOURCE = (BOT / "surface.py").read_text(encoding="utf-8")
HUBS_SOURCE = (BOT / "hubs.py").read_text(encoding="utf-8")
CULTIVATION_SOURCE = (BOT / "commands" / "cultivation.py").read_text(encoding="utf-8")
LAW_SOURCE = (BOT / "commands" / "law.py").read_text(encoding="utf-8")
GO_ACTIONS = (GO / "cultivation_actions.go").read_text(encoding="utf-8")
GO_PLACE = (GO / "cultivation_place.go").read_text(encoding="utf-8")
GO_STANCE = (GO / "cultivation_stance.go").read_text(encoding="utf-8")
GO_LAW = (GO / "law_actions.go").read_text(encoding="utf-8")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _modules():
    with patch.dict(os.environ, ENV):
        return (importlib.import_module("app.bot.surface"),
                importlib.import_module("app.bot.hubs"),
                importlib.import_module("app.bot.locations"),
                importlib.import_module("app.bot.formatting"),
                importlib.import_module("app.bot.status_cards"))


class FourPagesNamedAfterTheWork(unittest.TestCase):
    def test_the_hub_is_four_pages_and_every_action_is_still_on_one(self):
        surface, hubs, _, _, _ = _modules()
        hub = surface._HUB_BY_NAME["cultivation"]
        # v1.0.0-rc.7 adds the Qi Body page between them.
        self.assertEqual([page.label for page in hub.pages], ["Cultivate", "Body", "Qi Body", "Path", "Arts"])
        paths = {action.path for page in hub.pages for action in hubs._leaf_actions(page)}
        for expected in ("/cultivate", "/stance", "/insight", "/breakthrough", "/seclusion end",
                         "/body cultivate", "/aptitude temper", "/law comprehend",
                         "/manual study", "/profession status", "/conceal",
                         "/dantian refine", "/meridian open", "/meridian heal"):
            self.assertIn(expected, paths, expected)

    def test_a_page_that_gathers_several_roots_names_its_rows_in_full(self):
        surface, hubs, _, _, _ = _modules()
        pages = {page.label: page for page in surface._HUB_BY_NAME["cultivation"].pages}
        labels = {action.label for action in hubs._leaf_actions(pages["Path"])}
        self.assertIn("Law Status", labels)
        self.assertIn("Aptitude Status", labels)
        # A page with one command keeps the short labels it always had.
        self.assertEqual({a.label for a in hubs._leaf_actions(pages["Body"])}, {"Sheet", "Cultivate", "Breakthrough"})

    def test_every_hint_path_in_the_bot_still_resolves_to_an_action(self):
        _, hubs, _, _, _ = _modules()
        for steps, expected in (
            (["Cultivate", "End"], "/seclusion end"),
            (["Cultivate", "Insight"], "/insight"),
            (["Cultivate", "Stance"], "/stance"),
            (["Body", "Breakthrough"], "/body breakthrough"),
            (["Path", "Comprehend"], "/law comprehend"),
            (["Path", "Harmonize"], "/aptitude harmonize"),
            (["Arts", "Study"], "/manual study"),
        ):
            action = hubs._hint_action("cultivation", steps)
            self.assertIsNotNone(action, steps)
            self.assertEqual(action.path, expected, steps)


class ARollSaysItsChance(unittest.TestCase):
    def test_roll_line_prints_the_probability_the_engine_sent(self):
        _, _, _, formatting, _ = _modules()
        roll = SimpleNamespace(die1=4, die2=6, modifier=3, total=13, tn=12, degree="Success", probability=85)
        line = formatting.roll_line(roll)
        self.assertIn("vs TN **12**", line)
        self.assertIn("**85%** chance", line)
        # A roll from before this release, with no probability, still renders.
        self.assertNotIn("chance", formatting.roll_line(SimpleNamespace(die1=1, die2=1, modifier=0, total=2, tn=12, degree="Severe Failure")))

    def test_the_engine_puts_the_chance_on_every_2d10_map(self):
        for path in ("aptitude_actions.go", "check_scene_actions.go"):
            text = (GO / path).read_text(encoding="utf-8")
            self.assertIn('"probability": rollOdds(modifier, tn)', text, path)
        self.assertIn("func rollOdds(modifier, tn int64) int64", GO_STANCE)


class ThePlaceMatters(unittest.TestCase):
    def test_the_engine_prices_the_ground_and_the_result_names_it(self):
        self.assertIn("func placeCultivationMultiplier(", GO_PLACE)
        self.assertIn("placeCultivationMultiplier(conn, catalog, userID, c.Location, p.GameMinute)", GO_ACTIONS)
        self.assertIn('"place_name": placeName, "place_mult": placeMult, "place_quality": placeQuality(placeMult)', GO_ACTIONS)
        self.assertIn("place_quality", CULTIVATION_SOURCE)

    def test_the_here_line_says_when_the_ground_is_worth_sitting_in(self):
        _, _, locations, _, _ = _modules()
        shrine = next((name for name, data in locations.WORLD.locations.items() if data.get("road_site") == "shrine"), "")
        self.assertTrue(shrine)
        self.assertIn("rich qi", locations.here_summary(shrine))
        plain = next((name for name, data in locations.WORLD.locations.items()
                      if not data.get("road_site") and not data.get("district") and data.get("roads")), "")
        self.assertNotIn("qi", locations.here_summary(plain))

    def test_the_sheet_carries_the_ground_and_the_moment_to_seize(self):
        surface, _, _, _, cards = _modules()
        status = {
            "realm_index": 0, "stage": 4, "cultivation": 10, "cost": 100, "insight_xp": 9,
            "odds": {"tn": 12, "modifier": 1, "probability": 55, "movers": []},
            "stance": "circulate", "stance_label": "Circulate", "stance_mult": 1.0, "cooldown_remaining": 0,
            "place_name": "the shrine", "place_mult": 1.15, "place_quality": "good",
            "reroll_available": True, "reroll_cost": 3,
            "period": "Dawn", "season": "Spring", "time_mult": 1.1,
        }
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(cards.DB, "get_character", AsyncMock(return_value={"gender": "female"})), \
             patch.object(cards.ENGINE, "action", AsyncMock(return_value=status)):
            fields = asyncio.run(surface._cultivation_hub_status(interaction))
        text = "\n".join(f"{f.name}\n{f.value}" for f in fields)
        self.assertIn("the shrine — good ground **x1.15**", text)
        self.assertIn("one more roll for **3 Insight XP**", text)
        self.assertLess(len(text), 900)


class InsightXPIsSpent(unittest.TestCase):
    def test_a_failed_breakthrough_offers_the_seized_moment(self):
        self.assertIn("async def breakthrough(interaction: discord.Interaction, confirm: bool = False, reroll: bool = False)", CULTIVATION_SOURCE)
        self.assertIn('{"confirm": bool(confirm), "reroll": bool(reroll)}', CULTIVATION_SOURCE)
        self.assertIn("The moment has not passed", CULTIVATION_SOURCE)
        self.assertIn("You seized the moment", CULTIVATION_SOURCE)

    def test_the_engine_charges_the_moment_once_a_stage_and_waives_the_essence(self):
        self.assertIn("reroll := p.Reroll && !body", GO_ACTIONS)
        self.assertIn("rerollState(conn, userID, realm, phase)", GO_ACTIONS)
        self.assertIn("no moment to seize", GO_ACTIONS)
        self.assertIn("} else if current < cost {", GO_ACTIONS)
        self.assertIn("func insightRerollCost(realmIndex int64) int64", GO_STANCE)

    def test_a_law_comprehension_can_take_insight_xp(self):
        self.assertIn("async def law_comprehend(interaction:discord.Interaction,law:str,spend_insight:bool=False)", LAW_SOURCE)
        self.assertIn('{"law":law,"spend_insight":bool(spend_insight)}', LAW_SOURCE)
        self.assertIn("lawInsightSpendCost", GO_LAW)
        self.assertIn("insight+spirit+affinity+legacyBonus+insightBonus", GO_LAW)


if __name__ == "__main__":
    unittest.main()

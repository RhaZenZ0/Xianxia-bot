"""v1.0.0-rc.5: the pacing fix, and what the sheet and the replies say.

A session is a share of the stage it fills rather than a flat fourteen
essence, so a realm takes about the same work at Divine Transformation as
at Body Tempering; crossing a realm raises the cultivator's attributes,
which nothing but reincarnation ever did; the higher worlds are thick with
qi; an untreated qi deviation deepens; and a session at a full stage banks
nothing and risks nothing.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
CULTIVATION_SOURCE = (BOT / "commands" / "cultivation.py").read_text(encoding="utf-8")
GO_PACE = (GO / "cultivation_pace.go").read_text(encoding="utf-8")
GO_ACTIONS = (GO / "cultivation_actions.go").read_text(encoding="utf-8")
GO_STANCE = (GO / "cultivation_stance.go").read_text(encoding="utf-8")
GO_SECLUSION = (GO / "seclusion_environment.go").read_text(encoding="utf-8")
GO_PLACE = (GO / "cultivation_place.go").read_text(encoding="utf-8")
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _cards():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.status_cards")


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


class TheStageSetsThePace(unittest.TestCase):
    def test_a_session_is_a_share_of_the_stage_and_the_engine_says_which(self):
        self.assertIn("func stagePace(cost int64) int64", GO_PACE)
        self.assertIn("cultivationSessionsPerStage = 12", GO_PACE)
        self.assertIn("pace := stagePace(cost)", GO_ACTIONS)
        self.assertIn("base := maxI64(1, int64(math.Round(float64(pace)*quality*variance)))", GO_ACTIONS)
        # The old flat session is gone.
        self.assertNotIn("base := int64(8+rv) + mods.value(c.Attributes[attr], attr)", GO_ACTIONS)

    def test_the_curve_it_produces_is_flat_across_the_realms(self):
        realms = WORLD["realms"]
        density = WORLD["world_qi_density"]

        def sessions(realm: dict, will: int) -> int:
            quality = 1 + will / 20
            total = 0
            for cost in realm["phase_costs"]:
                pace = max(8, cost // 12)
                gain = max(1, round(pace * quality * density[realm["world"]]))
                total += -(-cost // gain)
            return total

        # Will grows by one a realm, which is what a crossing now grants.
        counts = [sessions(realms[index], 3 + index) for index in range(8)]
        self.assertLess(max(counts), 130, counts)
        self.assertGreater(min(counts), 60, counts)

    def test_seclusion_is_paced_by_the_same_stage(self):
        self.assertIn("pace, worldMult := characterStagePace(catalog, character, mode)", GO_SECLUSION)
        self.assertIn("seclusionSessionsPerDay", GO_PACE)
        self.assertNotIn("base = 8 + i64(attrs[\"will\"])", GO_SECLUSION)


class CultivatorsGrow(unittest.TestCase):
    def test_crossing_a_realm_writes_the_attributes(self):
        self.assertIn("func growAttributesOnRealmCrossing(", GO_PACE)
        self.assertIn("UPDATE characters SET attributes_json=?", GO_PACE)
        self.assertIn("attributeGains, err = growAttributesOnRealmCrossing(conn, catalog, userID, c, body, now)", GO_ACTIONS)
        self.assertIn("func pathPrimaryAttribute(", GO_PACE)
        self.assertIn("attribute_gains", CULTIVATION_SOURCE)

    def test_the_higher_worlds_have_their_own_qi_density(self):
        density = WORLD["world_qi_density"]
        worlds = []
        for realm in WORLD["realms"]:
            if realm["world"] not in worlds:
                worlds.append(realm["world"])
        self.assertEqual(sorted(density), sorted(worlds))
        self.assertEqual(density[worlds[0]], 1.0)
        for lower, higher in zip(worlds, worlds[1:]):
            self.assertGreater(density[higher], density[lower], (lower, higher))
        self.assertIn("func worldQiMultiplier(", GO_PACE)
        self.assertIn("worldMult := worldQiMultiplier(catalog, worldName)", GO_ACTIONS)
        self.assertIn("The qi of the **", CULTIVATION_SOURCE)


class TheFourBalanceFixes(unittest.TestCase):
    def test_an_untreated_deviation_deepens(self):
        self.assertIn("currentConditionSeverity(conn, userID, \"qi_deviation\")", GO_STANCE)
        self.assertIn("minI64(5, held+1)", GO_STANCE)

    def test_a_full_stage_banks_nothing_and_risks_nothing(self):
        self.assertIn("if gain > 0 {\n\t\tinsightGain, deviation, err = applyStanceToTraining(", GO_ACTIONS)
        self.assertIn('"stage_full": room == 0', GO_ACTIONS)
        self.assertIn("This stage is already full", CULTIVATION_SOURCE)

    def test_one_reading_of_a_deployed_array(self):
        self.assertIn("func deployedArrayMultiplier(", GO_PACE)
        self.assertIn("deployedArrayMultiplier(conn, location, gameMinute)", GO_PLACE)
        self.assertIn("deployedArrayMultiplier(conn, location, gameMinute)", GO_SECLUSION)
        for source in (GO_PLACE, GO_SECLUSION):
            self.assertNotIn("FROM deployed_location_arrays", source)

    def test_the_body_path_gathers_by_the_same_ground(self):
        head = GO_ACTIONS[GO_ACTIONS.index("func cultivationTrain("):GO_ACTIONS.index("func nextStage(")]
        place = head.index("placeCultivationMultiplier(")
        body_only = head.index("if !body {", place)
        self.assertLess(place, body_only, "the ground must be priced for both paths")


class TheSheetAndTheReplies(unittest.TestCase):
    def test_the_sheet_shows_the_pace_the_world_and_a_deviation(self):
        surface, cards = _surface(), _cards()
        status = {
            "realm_index": 8, "stage": 3, "cultivation": 400, "cost": 7885, "insight_xp": 4,
            "odds": {"tn": 18, "modifier": 13, "probability": 72, "movers": []},
            "stance": "force", "stance_label": "Force", "stance_mult": 1.3, "cooldown_remaining": 0,
            "pace": 657, "sessions_per_stage": 12, "world_mult": 1.6, "deviation_severity": 3,
            "period": "Dawn", "season": "Spring", "time_mult": 1.1,
        }
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(cards.DB, "get_character", AsyncMock(return_value={"gender": "female"})), \
             patch.object(cards.ENGINE, "action", AsyncMock(return_value=status)):
            fields = asyncio.run(surface._cultivation_hub_status(interaction))
        text = "\n".join(f"{f.name}\n{f.value}" for f in fields)
        self.assertIn("about **12** sessions a stage at **657** each", text)
        self.assertIn("world qi **x1.60**", text)
        self.assertIn("Qi deviation **3/5**", text)
        self.assertLess(len(text), 900)

    def test_the_breakthrough_reply_names_what_the_crossing_made_of_you(self):
        self.assertIn("Crossing into a new realm remade your foundation", CULTIVATION_SOURCE)
        self.assertIn("Every session from here gathers more", CULTIVATION_SOURCE)


if __name__ == "__main__":
    unittest.main()

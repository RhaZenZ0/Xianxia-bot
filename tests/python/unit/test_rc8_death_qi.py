"""v1.0.0-rc.8: the ghost road — death qi, and the two households born to it.

A cultivator born into the Nether-Market Household or the Tomb-Watch Clan may
take a seventh cultivation path that nobody else can. It fills the same three
dantian with death qi instead of spirit qi: rich where the living are gone and
at night, thin under the sun and in a temple quarter. Every session leaves a
residue that eats how clean the qi can ever be, tears channels once it is deep,
and step by step remakes the body into something the living avoid.
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
CORE_SOURCE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
CULTIVATION_SOURCE = (BOT / "commands" / "cultivation.py").read_text(encoding="utf-8")
CARDS_SOURCE = (BOT / "status_cards.py").read_text(encoding="utf-8")
GO_DEATH = (GO / "death_qi.go").read_text(encoding="utf-8")
GO_ACTIONS = (GO / "cultivation_actions.go").read_text(encoding="utf-8")
GO_QI = (GO / "qi_body.go").read_text(encoding="utf-8")
GO_AUTHORITATIVE = (GO / "authoritative.go").read_text(encoding="utf-8")
GO_FAMILIES = (GO / "birth_family_actions.go").read_text(encoding="utf-8")
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
GHOSTS = ("nether_market_house", "tomb_watch_clan")


def _modules():
    with patch.dict(os.environ, ENV):
        return (importlib.import_module("app.bot.surface"),
                importlib.import_module("app.bot.hubs"),
                importlib.import_module("app.bot.status_cards"))


class TheTwoHouseholdsBornToIt(unittest.TestCase):
    def test_thirteen_archetypes_and_the_last_two_are_the_ghost_born(self):
        from app.rules.birthfamily import FAMILY_ARCHETYPES, FAMILY_HOMELANDS, STARTER_BIRTH_FAMILY_SURNAMES

        by_id = {family["id"]: family for family in FAMILY_ARCHETYPES}
        self.assertEqual(len(FAMILY_ARCHETYPES), 13)
        for ghost in GHOSTS:
            self.assertIn(ghost, by_id, ghost)
            self.assertEqual(by_id[ghost]["category"], "ghost")
            self.assertIn(ghost, FAMILY_HOMELANDS, ghost)
            self.assertIn(ghost, STARTER_BIRTH_FAMILY_SURNAMES, ghost)
            # A ghost household is not a respectable one.
            self.assertLess(by_id[ghost]["alignment_bias"], 0, ghost)

    def test_the_engine_and_the_rules_agree_on_the_thirteen(self):
        from app.rules.birthfamily import FAMILY_ARCHETYPES

        for family in FAMILY_ARCHETYPES:
            self.assertIn(f'{{"{family["id"]}", "', GO_FAMILIES, family["id"])
        self.assertIn('"nether_market_house": {Theme:', GO_FAMILIES)
        self.assertIn('"tomb_watch_clan": {Theme:', GO_FAMILIES)

    def test_the_content_names_the_path_and_its_two_households(self):
        system = WORLD["death_qi_system"]
        self.assertEqual(system["path"], "Ghost Cultivator")
        self.assertEqual(list(system["families"]), list(GHOSTS))
        self.assertIn("Ghost Cultivator", WORLD["paths"])
        self.assertIn("Ghost Cultivator", WORLD["spiritual_root_system"]["path_affinities"])
        self.assertIn("Yin", WORLD["spiritual_root_system"]["path_affinities"]["Ghost Cultivator"])

    def test_the_engine_refuses_the_path_to_every_other_household(self):
        self.assertIn("func ghostBornFamily(", GO_DEATH)
        self.assertIn("func isGhostPath(", GO_DEATH)
        self.assertIn("born among the dead", GO_AUTHORITATIVE)
        self.assertIn("isGhostPath(catalog, path) && !ghostBornFamily(", GO_AUTHORITATIVE)


class TheGroundTheHoursAndTheResidue(unittest.TestCase):
    def test_the_ground_and_the_hours_run_the_other_way(self):
        system = WORLD["death_qi_system"]
        ground, hours = system["ground"], system["hours"]
        # A ruin is rich; a shrine, which is the living cultivator's best road
        # ground, is the ghost road's worst.
        self.assertGreater(ground["road_sites"]["ruin"], 1.0)
        self.assertLess(ground["road_sites"]["shrine"], 1.0)
        self.assertLess(ground["districts"]["temple"], 1.0)
        self.assertLess(ground["city_penalty"], 1.0)
        self.assertGreater(hours["Night"], 1.0)
        self.assertLess(hours["Afternoon"], 1.0)
        self.assertGreater(hours["Night"], hours["Afternoon"])
        self.assertIn("func deathQiGroundMultiplier(", GO_DEATH)
        self.assertIn("func deathQiHourMultiplier(", GO_DEATH)
        self.assertIn("placeName, placeMult = deathQiGroundMultiplier(catalog, c.Location)", GO_ACTIONS)

    def test_the_ghost_form_ladder_climbs_and_costs(self):
        forms = WORLD["death_qi_system"]["ghost_forms"]
        self.assertGreaterEqual(len(forms), 5)
        self.assertEqual(forms[0]["corruption"], 0)
        self.assertEqual(forms[0]["capacity_mult"], 1.0)
        self.assertEqual(forms[0]["daylight_penalty"], 0.0)
        for lower, higher in zip(forms, forms[1:]):
            self.assertGreater(higher["corruption"], lower["corruption"])
            self.assertGreater(higher["capacity_mult"], lower["capacity_mult"])
            self.assertGreater(higher["daylight_penalty"], lower["daylight_penalty"])
            self.assertGreaterEqual(higher["min_realm_index"], lower["min_realm_index"])
            self.assertTrue(higher["note"])
        self.assertIn("func ghostFormIndex(", GO_DEATH)
        self.assertIn("func ghostFormCapacityMultiplier(", GO_DEATH)
        self.assertIn("ghostFormCapacityMultiplier(catalog, body)", GO_QI)

    def test_the_residue_is_paid_for_and_can_be_shed(self):
        corruption = WORLD["death_qi_system"]["corruption"]
        for key in ("per_session", "per_harvest", "appease_relief", "appease_stone_cost",
                    "rupture_threshold", "rupture_chance_percent", "purity_ceiling_penalty_per_ten"):
            self.assertGreater(int(corruption[key]), 0, key)
        # A harvest stains more than a session, and the rites lift more again.
        self.assertGreater(corruption["per_harvest"], corruption["per_session"])
        self.assertGreater(corruption["appease_relief"], corruption["per_harvest"])
        self.assertIn("func addCorruption(", GO_DEATH)
        self.assertIn("func corruptionRupture(", GO_DEATH)
        self.assertIn("func corruptionPurityPenalty(", GO_DEATH)
        self.assertIn("ceiling -= corruptionPurityPenalty(catalog, body)", GO_QI)

    def test_schema_forty_one_carries_the_road_on_the_qi_body(self):
        from app.database import SCHEMA_VERSION

        self.assertEqual(SCHEMA_VERSION, 41)
        self.assertIn('        41,\n        "death_qi",', CORE_SOURCE)
        for column in ("qi_type", "corruption", "ghost_form"):
            self.assertIn(f"ALTER TABLE character_qi_body ADD COLUMN {column}", CORE_SOURCE)
        # The path is the single source of truth for which qi is held.
        self.assertIn("func normaliseQiType(", GO_DEATH)
        self.assertIn("normaliseQiType(catalog, body, fmt.Sprint(row[\"path\"]))", GO_QI)
        self.assertIn("normaliseQiType(catalog, ghostBody, c.Path)", GO_ACTIONS)

    def test_the_engine_registers_the_two_rites_and_the_read(self):
        for operation in ('"ghost.harvest"', '"ghost.appease"', '"ghost.status"'):
            self.assertIn(operation, GO_AUTHORITATIVE, operation)
        self.assertIn("func ghostHarvestAction(", GO_DEATH)
        self.assertIn("func ghostAppeaseAction(", GO_DEATH)
        self.assertIn("func ghostStatusQuery(", GO_DEATH)
        self.assertIn("func requireGhostCultivator(", GO_DEATH)


class TheRoadOnTheSurface(unittest.TestCase):
    def test_the_cultivation_hub_has_a_ghost_page(self):
        surface, hubs, _ = _modules()
        pages = {page.label: page for page in surface._HUB_BY_NAME["cultivation"].pages}
        self.assertIn("Ghost", pages)
        paths = {action.path for action in hubs._leaf_actions(pages["Ghost"])}
        self.assertEqual(paths, {"/ghost status", "/ghost harvest", "/ghost appease"})

    def test_the_picker_never_offers_a_path_the_engine_would_refuse(self):
        from app.rules.creation_ui import BIRTH_GATED_STYLES, GHOST_BORN_FAMILIES

        self.assertEqual(set(GHOST_BORN_FAMILIES), set(GHOSTS))
        self.assertEqual(BIRTH_GATED_STYLES["Ghost Cultivator"], GHOST_BORN_FAMILIES)
        self.assertIn("selectable_cultivation_styles(WORLD.paths, family)", (BOT / "ui" / "creation.py").read_text(encoding="utf-8"))

    def test_the_commands_speak_of_the_road(self):
        self.assertIn('"ghost.harvest", interaction.user.id', CULTIVATION_SOURCE)
        self.assertIn('"ghost.appease", interaction.user.id', CULTIVATION_SOURCE)
        self.assertIn('"ghost.status"', CULTIVATION_SOURCE)
        self.assertIn("chosen at birth, and never after", CULTIVATION_SOURCE)
        self.assertIn("does not lift with it", CULTIVATION_SOURCE)

    def test_the_sheet_names_death_qi_and_its_residue(self):
        surface, _, cards = _modules()
        status = {
            "realm_index": 3, "stage": 5, "cultivation": 400, "cost": 3000, "insight_xp": 2,
            "odds": {"tn": 16, "modifier": 9, "probability": 60, "movers": []},
            "stance": "circulate", "stance_label": "Circulate", "stance_mult": 1.0, "cooldown_remaining": 0,
            "qi": 3000, "qi_max": 9000, "qi_regen": 41.2, "purity": 44, "purity_ceiling": 49,
            "skill_cost_mult": 1.56, "meridians_open": 21, "meridians_damaged": 0,
            "meridian_ceiling": 108, "dantian_state": "intact",
            "qi_type": "death", "corruption": 47, "ghost_form_name": "Corpse-Hardened",
            "period": "Night", "season": "Winter", "time_mult": 1.35,
        }
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(cards.DB, "get_character", AsyncMock(return_value={"gender": "female"})), \
             patch.object(cards.ENGINE, "action", AsyncMock(return_value=status)):
            fields = asyncio.run(surface._cultivation_hub_status(interaction))
        text = "\n".join(f"{f.name}\n{f.value}" for f in fields)
        self.assertIn("**3,000 / 9,000** death qi", text)
        self.assertIn("**Corpse-Hardened**", text)
        self.assertIn("corruption **47/100**", text)
        self.assertLess(len(text), 1024)
        # A living cultivator's sheet says none of it.
        living = dict(status, qi_type="spirit")
        with patch.object(cards.DB, "get_character", AsyncMock(return_value={"gender": "female"})), \
             patch.object(cards.ENGINE, "action", AsyncMock(return_value=living)):
            fields = asyncio.run(surface._cultivation_hub_status(interaction))
        plain = "\n".join(f"{f.name}\n{f.value}" for f in fields)
        self.assertNotIn("corruption", plain)
        self.assertIn("**3,000 / 9,000** qi", plain)


if __name__ == "__main__":
    unittest.main()

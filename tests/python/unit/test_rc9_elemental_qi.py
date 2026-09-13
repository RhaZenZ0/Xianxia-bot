"""v1.0.0-rc.9: elemental qi — what a method draws, and what a root can take.

Every cultivation manual in the catalogue draws one kind of qi, and a
cultivator's spiritual root decides how much of that kind actually goes in. The
five phases generate and overcome one another in the old cycle: a resonant root
gathers a quarter more, a clashing one a quarter less and risks the qi turning
on the way in. Void and Chaos stand outside the cycle and answer to nobody.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
CULTIVATION_SOURCE = (BOT / "commands" / "cultivation.py").read_text(encoding="utf-8")
LAW_SOURCE = (BOT / "commands" / "law.py").read_text(encoding="utf-8")
CARDS_SOURCE = (BOT / "status_cards.py").read_text(encoding="utf-8")
GO_ELEMENTS = (GO / "elemental_qi.go").read_text(encoding="utf-8")
GO_ACTIONS = (GO / "cultivation_actions.go").read_text(encoding="utf-8")
GO_MANUAL = (GO / "cultivation_manual.go").read_text(encoding="utf-8")
GO_STANCE = (GO / "cultivation_stance.go").read_text(encoding="utf-8")
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ELEMENTS = ("Fire", "Water", "Wood", "Metal", "Earth", "Lightning", "Wind", "Ice", "Yin", "Yang", "Void", "Chaos")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _modules():
    with patch.dict(os.environ, ENV):
        return (importlib.import_module("app.bot.surface"),
                importlib.import_module("app.bot.status_cards"))


class TheCatalogueCarriesTheElements(unittest.TestCase):
    def test_every_manual_has_a_known_element(self):
        manuals = WORLD["technique_system"]["manuals"]
        counts = Counter()
        for manual_id, manual in manuals.items():
            with self.subTest(manual=manual_id):
                self.assertIn(manual.get("element"), ELEMENTS, manual_id)
            counts[manual["element"]] += 1
        # Every element has methods a cultivator of that root can go and find.
        for element in ELEMENTS:
            self.assertGreaterEqual(counts[element], 4, f"{element}: {counts[element]}")
        self.assertEqual(sum(counts.values()), len(manuals))

    def test_the_generator_would_reproduce_exactly_these_elements(self):
        """A regenerated catalogue must not silently reshuffle every method."""
        from app.rules.advanced_catalog import manual_element

        for manual_id, manual in WORLD["technique_system"]["manuals"].items():
            with self.subTest(manual=manual_id):
                self.assertEqual(manual["element"], manual_element(manual_id, manual["name"]))
        self.assertIn('"element": manual_element(manual_id, name)',
                      (PROJECT_ROOT / "app" / "rules" / "advanced_catalog.py").read_text(encoding="utf-8"))

    def test_the_cycle_is_the_old_one_and_the_relations_are_ordered(self):
        system = WORLD["elemental_qi_system"]
        self.assertEqual(system["phases"], ["Wood", "Fire", "Earth", "Metal", "Water"])
        self.assertEqual(system["generates"], {"Wood": "Fire", "Fire": "Earth", "Earth": "Metal",
                                               "Metal": "Water", "Water": "Wood"})
        self.assertEqual(system["overcomes"], {"Wood": "Earth", "Earth": "Water", "Water": "Fire",
                                               "Fire": "Metal", "Metal": "Wood"})
        # Every phase generates and overcomes exactly one other, and is
        # generated and overcome by exactly one.
        for table in ("generates", "overcomes"):
            self.assertEqual(sorted(system[table]), sorted(system["phases"]))
            self.assertEqual(sorted(system[table].values()), sorted(system["phases"]))
        # Void and Chaos stand with no phase.
        for outsider in ("Void", "Chaos"):
            self.assertEqual(system["phase_of"][outsider], "")
        for standing in ("Lightning", "Wind", "Ice", "Yin", "Yang"):
            self.assertIn(system["phase_of"][standing], system["phases"], standing)
        relations = system["relations"]
        order = [relations[key]["mult"] for key in ("clashing", "drained", "neutral", "generative", "resonant")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(relations["neutral"]["mult"], 1.0)
        # Only a clash risks anything.
        self.assertGreater(relations["clashing"]["deviation_surcharge_percent"], 0)
        for key in ("resonant", "generative", "neutral", "drained"):
            self.assertEqual(relations[key].get("deviation_surcharge_percent", 0), 0, key)
        self.assertGreater(system["grade_bonus_per_rank"], 0)
        self.assertGreater(system["purity_bonus_at_full"], 0)


class TheEngineReadsThem(unittest.TestCase):
    def test_the_relation_and_the_absorption_live_in_the_engine(self):
        # manualElementOf was on this list and was never called by anything:
        # the method's element is read by manualCultivationMultiplier, as part
        # of the bundle it needs anyway, and that reader is pinned by
        # test_the_method_hands_back_its_element_and_both_sheets_carry_it
        # below. A name a test keeps alive is not the same as a rule the
        # engine owns, which is the thing this is here to assert.
        for symbol in ("func elementPhase(", "func elementRelation(",
                       "func bestElementRelation(", "func rootAbsorptionBonus(", "func absorptionFor("):
            self.assertIn(symbol, GO_ELEMENTS, symbol)
        self.assertIn("relationResonant   = \"resonant\"", GO_ELEMENTS)
        self.assertIn("relationClashing   = \"clashing\"", GO_ELEMENTS)

    def test_the_session_is_worked_by_it_and_the_body_path_is_not(self):
        self.assertIn("absorption := absorptionFor(catalog, bundle.Root, manualElement)", GO_ACTIONS)
        self.assertIn("elementMult := absorption.Mult", GO_ACTIONS)
        self.assertIn("if body {\n\t\telementMult = 1\n\t}", GO_ACTIONS)
        self.assertIn("* manualMult * elementMult))", GO_ACTIONS)
        # A clash can turn on its own, whatever the stance.
        self.assertIn('"cultivation", "element_clash", p.GameMinute', GO_ACTIONS)
        self.assertIn("absorption.Surcharge > 0", GO_ACTIONS)

    def test_the_method_hands_back_its_element_and_both_sheets_carry_it(self):
        self.assertIn("element string, mult float64, chosen bool, err error", GO_MANUAL)
        self.assertIn("strings.TrimSpace(definition.Element)", GO_MANUAL)
        self.assertIn("absorption := absorptionFor(catalog, bundle.Root, manualElement)", GO_STANCE)
        for key in ('"element"', '"element_relation"', '"element_label"', '"element_mult"'):
            self.assertIn(key, GO_STANCE, key)
            self.assertIn(key, GO_ACTIONS, key)


class TheElementOnTheSurface(unittest.TestCase):
    def test_the_commands_name_the_kind_of_qi(self):
        self.assertIn("_ELEMENT_MARKS", CARDS_SOURCE)
        self.assertIn("element_clash", CULTIVATION_SOURCE)
        self.assertIn("qi turned going in", CULTIVATION_SOURCE)
        self.assertIn("It draws **{element} qi**", LAW_SOURCE)
        self.assertIn("_ELEMENT_MARKS.get(element", LAW_SOURCE)

    def test_the_sheet_names_the_element_beside_the_method(self):
        surface, cards = _modules()
        status = {
            "realm_index": 2, "stage": 4, "cultivation": 300, "cost": 1200, "insight_xp": 3,
            "odds": {"tn": 14, "modifier": 7, "probability": 65, "movers": []},
            "stance": "circulate", "stance_label": "Circulate", "stance_mult": 1.0, "cooldown_remaining": 0,
            "manual_name": "Vermilion Crane Canon", "manual_grade": "Spirit", "manual_mult": 1.12,
            "element": "Fire", "element_relation": "resonant", "element_label": "resonant", "element_mult": 1.38,
            "period": "Dawn", "season": "Summer", "time_mult": 1.1,
        }
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(cards.DB, "get_character", AsyncMock(return_value={"gender": "female"})), \
             patch.object(cards.ENGINE, "action", AsyncMock(return_value=status)):
            fields = asyncio.run(surface._cultivation_hub_status(interaction))
        text = "\n".join(f"{f.name}\n{f.value}" for f in fields)
        self.assertIn("Vermilion Crane Canon", text)
        self.assertIn("🔥 **Fire** resonant **x1.38**", text)
        self.assertLess(len(text), 1024)
        # A cultivator practising nothing gets no element line at all.
        with patch.object(cards.DB, "get_character", AsyncMock(return_value={"gender": "female"})), \
             patch.object(cards.ENGINE, "action", AsyncMock(return_value={k: v for k, v in status.items() if not k.startswith(("manual", "element"))})):
            fields = asyncio.run(surface._cultivation_hub_status(interaction))
        plain = "\n".join(f"{f.name}\n{f.value}" for f in fields)
        self.assertNotIn("resonant", plain)


if __name__ == "__main__":
    unittest.main()

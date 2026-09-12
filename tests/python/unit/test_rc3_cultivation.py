"""v1.0.0-rc.3: a better cultivation system and menu.

The menu is four rows of four (You, World, Doing, Home) under a header of
live facts, with Begin when there is no character and Back to the hub you
left; the cultivation hub's first page is the sheet the engine computes
(`cultivation.status`); the stance is engine state applied to every
session; the odds of a breakthrough are shown from the same modifier the
roll uses; and stage 9 into a new realm is a gate that a banked insight or
a completed Realm Perfection opens.
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
GO_ACTIONS = (GO / "cultivation_actions.go").read_text(encoding="utf-8")
GO_STANCE = (GO / "cultivation_stance.go").read_text(encoding="utf-8")
GO_AUTHORITATIVE = (GO / "authoritative.go").read_text(encoding="utf-8")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _modules():
    with patch.dict(os.environ, ENV):
        surface = importlib.import_module("app.bot.surface")
        hubs = importlib.import_module("app.bot.hubs")
    return surface, hubs


def _cards():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.status_cards")


def _buttons(view) -> list:
    out = []

    def walk(item):
        if item.__class__.__name__.endswith("Button"):
            out.append(item)
        for child in getattr(item, "children", []) or []:
            walk(child)
    for child in view.children:
        walk(child)
    return out


class TheMenuIsFourRowsOfFour(unittest.TestCase):
    def test_the_groups_cover_every_hub_once_with_the_right_labels(self):
        surface, _ = _modules()
        grouped = [name for _, _, names in surface._MENU_GROUPS for name in names]
        self.assertEqual(sorted(grouped), sorted(d.name for d in surface._HUB_DEFINITIONS))
        self.assertEqual(len(grouped), len(set(grouped)))
        for _, _, names in surface._MENU_GROUPS:
            self.assertEqual(len(names), 4)
        self.assertEqual(surface._hub_label("npc"), "NPCs")
        self.assertEqual(surface._hub_label("innerworld"), "Inner World")
        self.assertEqual(surface._hub_label("quest"), "Quests")
        for name in grouped:
            self.assertLessEqual(len(surface._hub_label(name)), 20, name)

    def test_the_menu_carries_facts_begin_back_and_the_admin_row(self):
        surface, hubs = _modules()
        surface._LAST_HUB.pop(7, None)
        plain = hubs._MENU_BUILDER(owner_id=7, is_admin=False, owner_name="T", facts="📍 **Greenriver Town**")
        labels = [b.label for b in _buttons(plain)]
        self.assertEqual(len(labels), 16)
        self.assertNotIn("Begin", labels)
        admin = hubs._MENU_BUILDER(owner_id=7, is_admin=True, owner_name="T", facts="")
        self.assertEqual(len(_buttons(admin)), 17)
        fresh = hubs._MENU_BUILDER(owner_id=7, is_admin=False, owner_name="T", facts="🌱 No cultivator yet.")
        self.assertIn("Begin", [b.label for b in _buttons(fresh)])
        surface._LAST_HUB[7] = "world"
        try:
            back = hubs._MENU_BUILDER(owner_id=7, is_admin=False, owner_name="T", facts="x")
            self.assertIn("Back to World", [b.label for b in _buttons(back)])
        finally:
            surface._LAST_HUB.pop(7, None)

    def test_the_menu_button_looks_the_facts_up_before_building(self):
        self.assertIn("facts = await menu_facts(interaction)", HUBS_SOURCE)
        self.assertIn("register_menu_facts(_menu_facts)", SURFACE_SOURCE)
        self.assertIn("await begin_command.callback(interaction)", SURFACE_SOURCE)
        self.assertIn('_LAST_HUB[int(interaction.user.id)] = name', SURFACE_SOURCE)

    def test_the_facts_line_names_no_character_or_where_and_what_stage(self):
        surface, _ = _modules()
        cards = _cards()
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(cards.DB, "get_character", AsyncMock(return_value=None)):
            self.assertTrue(asyncio.run(surface._menu_facts(interaction)).startswith("🌱"))
        character = {"location": "Greenriver Town", "realm_index": 0, "phase": 3, "cultivation": 12, "gender": "female"}
        with patch.object(cards.DB, "get_character", AsyncMock(return_value=character)), \
             patch.object(cards, "character_location_display", AsyncMock(return_value="Greenriver Town")), \
             patch.object(cards.ENGINE, "action", AsyncMock(return_value={"offers_received": [{"offer_id": 1}]})):
            facts = asyncio.run(surface._menu_facts(interaction))
        self.assertIn("Greenriver Town", facts)
        self.assertIn("Stage **3**/9", facts)
        self.assertIn("**1** trade offer waiting", facts)


class TheCultivationSheet(unittest.TestCase):
    def test_the_hub_opens_on_the_sheet_and_lists_stance_and_insight(self):
        surface, _ = _modules()
        hub = surface._HUB_BY_NAME["cultivation"]
        keys = [page.key for page in hub.pages]
        # Since v1.0.0-rc.4 the pages are four, and the stance and the
        # insight are actions gathered onto the first of them.
        self.assertEqual(keys[0], "cultivate")
        gathered = {c.name for c in (hub.pages[0].command, *hub.pages[0].extras)}
        self.assertLessEqual({"stance", "insight"}, gathered)
        self.assertIs(surface._status_provider_for(hub), surface._cultivation_hub_status)
        self.assertIs(surface._status_provider_for(surface._HUB_BY_NAME["economy"]), surface._economy_hub_status)
        self.assertIs(surface._status_provider_for(surface._HUB_BY_NAME["world"]), surface._player_hub_status)
        self.assertIn("status_provider=_cultivation_hub_status", SURFACE_SOURCE)

    def test_the_sheet_is_six_fields_from_one_engine_query(self):
        surface, _ = _modules()
        status = {
            "realm_index": 0, "realm": "Body Tempering", "stage": 9, "cultivation": 80, "cost": 100, "ready": False,
            "realm_gate": True, "gate_open": False, "insight_banked": False, "insight_cost": 5, "insight_xp": 3,
            "stance": "refine", "stance_label": "Refine", "stance_mult": 0.8, "cooldown_remaining": 120,
            "odds": {"tn": 13, "modifier": 4, "probability": 66, "movers": [{"label": "Will", "value": 2}, {"label": "Base", "value": 2}], "stage_nine": True},
            "period": "Dawn", "season": "Spring", "time_mult": 1.1, "root_resonance": True, "effect_mult": 0.9, "era_mult": 1.0, "manor_mult": 1.0, "soul_mult": 1.0, "storm_bonus": 0,
            "body_realm": "Skin Tempering", "body_stage": 2, "body_cultivation": 10, "body_cost": 57, "body_odds": {"probability": 88}, "dual_resonance": False,
            "insight_xp_per_refine": 2,
        }
        cards = _cards()
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(cards.DB, "get_character", AsyncMock(return_value={"gender": "female"})), \
             patch.object(cards.ENGINE, "action", AsyncMock(return_value=status)) as action:
            fields = asyncio.run(surface._cultivation_hub_status(interaction))
        action.assert_awaited_once_with("cultivation.status", 7, {})
        self.assertEqual([f.name for f in fields], ["☯️ Realm", "🧭 Stance", "🎲 Breakthrough", "🌤️ Today", "💪 Body", "💡 Insight XP"])
        text = "\n".join(f"{f.name}\n{f.value}" for f in fields)
        self.assertLess(len(text), 900)
        self.assertIn("Stage **9**/9", text)
        self.assertIn("**66%**", text)
        self.assertIn("Will +2", text)
        self.assertIn("🔒 Realm gate: bank an insight (**5 XP**)", text)
        self.assertIn("**Refine** x0.80", text)
        self.assertIn("effects x0.90", text)
        self.assertIn("88%", text)

    def test_the_sheet_falls_back_to_the_player_card_when_the_engine_is_away(self):
        surface, _ = _modules()
        cards = _cards()
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(cards.DB, "get_character", AsyncMock(return_value={"gender": "female"})), \
             patch.object(cards.ENGINE, "action", AsyncMock(side_effect=RuntimeError("down"))), \
             patch.object(surface, "_player_hub_status", AsyncMock(return_value=["card"])):
            self.assertEqual(asyncio.run(surface._cultivation_hub_status(interaction)), ["card"])


class TheStanceTheOddsAndTheGate(unittest.TestCase):
    def test_the_stance_root_is_guided_and_the_engine_stores_it(self):
        self.assertIn('@registered_root_command(name="stance"', CULTIVATION_SOURCE)
        self.assertIn("@app_commands.choices(stance=STANCE_CHOICES)", CULTIVATION_SOURCE)
        for value in ("circulate", "refine", "force"):
            self.assertIn(f'value="{value}"', CULTIVATION_SOURCE)
            self.assertIn(f'Key: stance{value.title()}', GO_STANCE)
        self.assertIn('"cultivation.stance"', GO_AUTHORITATIVE)
        self.assertIn("stance, err := loadCultivationStance(conn, userID)", GO_ACTIONS)
        self.assertIn("* stance.GainMult", GO_ACTIONS)
        self.assertIn("applyStanceToTraining(conn, userID, stance, p.GameMinute, now)", GO_ACTIONS)
        # Since v1.0.0-rc.5 the severity deepens with each untreated deviation.
        self.assertIn('applyCombatCondition(conn, userID, "qi_deviation", minI64(5, held+1), "cultivation", "force_stance", gameMinute)', GO_STANCE)

    def test_the_odds_and_the_roll_share_one_modifier(self):
        self.assertEqual(GO_ACTIONS.count("breakthroughModifier(c, mods, body, perfectBonus, resonance, innate)"), 1)
        self.assertEqual(GO_STANCE.count("breakthroughModifier(c, mods, body, perfectBonus, resonance, innate)"), 1)
        self.assertNotIn('mods.value(c.Attributes["will"], "will") + 2 + perfectBonus', GO_ACTIONS)
        self.assertIn("probability := breakthroughOdds(modifier, tn)", GO_ACTIONS)
        formatting = (BOT / "formatting.py").read_text(encoding="utf-8")
        self.assertIn('chance = f" · **{int(odds)}%** chance" if odds is not None else ""', formatting)

    def test_the_realm_gate_is_engine_law_with_a_way_through(self):
        self.assertIn("realmGateOpen(conn, userID, realm)", GO_ACTIONS)
        self.assertIn("the realm gate into %s is closed", GO_ACTIONS)
        self.assertIn('"cultivation.insight"', GO_AUTHORITATIVE)
        self.assertIn('"cultivation.status"', GO_AUTHORITATIVE)
        self.assertIn("DELETE FROM world_state WHERE key=?`, []any{cultivationInsightKey(userID)}", GO_ACTIONS)
        self.assertIn('@registered_root_command(name="insight"', CULTIVATION_SOURCE)
        self.assertIn("**/cultivation → Cultivate → Insight**", CULTIVATION_SOURCE)
        self.assertIn("**/cultivation → Cultivate → Stance**", CULTIVATION_SOURCE)


if __name__ == "__main__":
    unittest.main()

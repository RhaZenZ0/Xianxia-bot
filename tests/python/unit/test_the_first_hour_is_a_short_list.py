"""The first hour is a short list (v1.2.0).

Player feedback: *"Cultivation, Breakthrough, Explore, Shop, Craft, Forge,
Gather, Hunt, Mine, Quest until Foundation Establishment - these things are
enough"*, and *"I still forget where to go what to do."*

What this file holds is the shape the owner chose and the rules that keep it
honest, not the numbers: which pages open at which realm is a decision the owner
may take again, and a gate pinning `combat / Duels` to realm 2 would go red
exactly when the decision is taken (v1.0.8's rule). What may not drift:

- everything the owner's list names is open at Body Tempering, and nothing
  else opens *between* Body Tempering and Foundation Establishment - the whole
  point was one threshold, not a staircase;
- a status read is one predicate, stated once in the rules module and imported
  by the generator, so the menu and the generator cannot hold two ideas of it;
- the menu never leaves off a hub the beginner path or a household errand
  needs, it names what it leaves off with `/locked`, and an unreadable roster
  leaves nothing off;
- both doors into the menu ask the same provider, and nothing consulted on a
  press reads it - hiding is advertising, never a bound (rc.48);
- the trades' ranks are one ladder, and the examinations quote it.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.util
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.support import PROJECT_ROOT, code_only

APP = PROJECT_ROOT / "app"
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ROSTER = CONTENT.get("feature_unlocks") or {}
LEAVES: dict[str, int] = {str(k): int(v) for k, v in (ROSTER.get("leaves") or {}).items()}
SURFACE = (APP / "bot" / "surface.py").read_text(encoding="utf-8")
HUBS = (APP / "bot" / "hubs.py").read_text(encoding="utf-8")
RULES = (APP / "rules" / "feature_unlocks.py").read_text(encoding="utf-8")
AUTHOR = (PROJECT_ROOT / "scripts" / "author_feature_unlocks.py").read_text(encoding="utf-8")
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

# The owner's list, as the leaves that do each thing. The starter set is what
# a Body Tempering cultivator must be able to press from a panel.
STARTER_LEAVES = (
    "cultivate", "breakthrough", "insight",            # cultivation and the gate
    "explore", "hunt", "mine", "alchemy forage",       # the world: gather, hunt, mine
    "shop buy", "shop sell", "shop browse", "shop here",  # shop
    "craft", "learn", "profession exam",               # craft and forge
    "city board", "city accept",                       # quest (the journal is /quests, a tree command)
    "talk", "family enter", "family leave", "family errand", "family lesson",  # the path's own doors
    "seclusion start", "seclusion end",                # a retreat is cultivating
    "body cultivate", "beast status", "beast tame",    # the two the owner kept open
    "reset", "sheet",
)


def _function(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone; this gate is guarding a function that moved")


def _statements(source: str, name: str) -> str:
    node = _function(source, name)
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return "\n".join(ast.unparse(s) for s in body)


def _modules():
    with patch.dict(os.environ, ENV):
        surface = importlib.import_module("app.bot.surface")
        hubs = importlib.import_module("app.bot.hubs")
    return surface, hubs


def _live_pages() -> dict[str, dict[str, list[str]]]:
    surface, _ = _modules()
    return surface._hub_page_leaves()


def _walk(view) -> tuple[list, list[str]]:
    buttons, texts = [], []

    def walk(item):
        if item.__class__.__name__.endswith("Button"):
            buttons.append(item)
        if item.__class__.__name__ == "TextDisplay":
            texts.append(str(getattr(item, "content", "")))
        for child in getattr(item, "children", []) or []:
            walk(child)
    for child in view.children:
        walk(child)
    return buttons, texts


class TheRosterIsReal(unittest.TestCase):
    def test_the_roster_was_read(self):
        self.assertGreater(len(LEAVES), 50, "the roster is empty; the gate is broken, not the tree")
        self.assertTrue(_live_pages(), "no hub pages were read; the gate is broken, not the tree")


class TheThresholdIsOne(unittest.TestCase):
    def test_every_starter_leaf_is_open_at_body_tempering(self):
        pages = _live_pages()
        live = {leaf for hub in pages.values() for leaves in hub.values() for leaf in leaves}
        missing = sorted(leaf for leaf in STARTER_LEAVES if leaf not in live)
        self.assertEqual(missing, [], f"the starter set names leaves the hubs do not register: {missing}")
        held = sorted(f"{leaf!r} (opens at realm {LEAVES[leaf]})" for leaf in STARTER_LEAVES if leaf in LEAVES)
        self.assertEqual(held, [], "the owner's first-hour list is held back:\n  " + "\n  ".join(held))

    def test_nothing_opens_between_body_tempering_and_foundation_establishment(self):
        """One threshold, not a staircase: a door either belongs to the first
        hour or waits for Foundation Establishment at the earliest."""
        early = sorted(f"{leaf!r} (realm {realm})" for leaf, realm in LEAVES.items() if 0 < realm < 2)
        self.assertEqual(early, [], "a door opens at Qi Refining, between the two ends of the first hour:\n  "
                         + "\n  ".join(early))

    def test_the_qi_body_and_the_laws_wait_for_the_spiritual_world(self):
        """Meridians for the Spiritual World (the owner's call), gated two ways
        on purpose: the qi body by the roster, the Laws by their own content
        floor - a second statement of one rule is the rc.39 fault."""
        spiritual = next(i for i, realm in enumerate(CONTENT["realms"]) if realm.get("world") == "Spiritual World")
        qi_body = {leaf: realm for leaf, realm in LEAVES.items()
                   if leaf.split(" ")[0] in ("dantian", "meridian") and not leaf.endswith(" status")}
        self.assertTrue(qi_body, "no qi-body lever is gated at all")
        for leaf, realm in qi_body.items():
            self.assertEqual(realm, spiritual, f"{leaf!r} opens at realm {realm}, not the Spiritual World's first ({spiritual})")
        self.assertEqual(int(CONTENT["law_system"]["normal_min_realm_index"]), spiritual)
        self.assertFalse([leaf for leaf in LEAVES if leaf.startswith("law ")],
                         "the Laws are in the roster as well as behind their content floor: two statements of one rule")


class AStatusReadIsOnePredicate(unittest.TestCase):
    def test_the_generator_imports_the_rule_rather_than_keeping_its_own(self):
        defined_in_script = [n.name for n in ast.walk(ast.parse(AUTHOR))
                             if isinstance(n, ast.FunctionDef) and n.name == "is_status_read"]
        self.assertEqual(defined_in_script, [], "author_feature_unlocks.py defines its own is_status_read again; "
                                                "the menu and the generator would hold two ideas of a status read")
        self.assertIn("is_status_read", [a.name for n in ast.walk(ast.parse(AUTHOR)) if isinstance(n, ast.ImportFrom)
                                         and n.module == "app.rules.feature_unlocks" for a in n.names])
        from app.rules import feature_unlocks
        self.assertTrue(feature_unlocks.is_status_read("dantian status"))
        self.assertFalse(feature_unlocks.is_status_read("dantian refine"))


class TheMenuLeavesOffOnlyWhatHasNothingToDo(unittest.TestCase):
    def _hidden_at(self, realm: int) -> dict[str, int]:
        from app.rules import feature_unlocks
        return feature_unlocks.hidden_hubs(ROSTER, realm, _live_pages())

    def test_a_hub_with_one_open_lever_stays_on_the_board(self):
        hidden = self._hidden_at(0)
        pages = _live_pages()
        for hub, hub_pages in pages.items():
            levers = [leaf for leaves in hub_pages.values() for leaf in leaves if not leaf.endswith(" status")]
            open_levers = [leaf for leaf in levers if leaf not in LEAVES]
            if open_levers:
                self.assertNotIn(hub, hidden, f"{hub!r} is left off the menu while {open_levers[:3]} is open on it")
            elif levers:
                self.assertIn(hub, hidden, f"{hub!r} has no lever open at realm 0 and is still drawn")
                self.assertEqual(hidden[hub], min(LEAVES[leaf] for leaf in levers))

    def test_no_hub_the_beginner_path_or_an_errand_needs_is_left_off(self):
        hidden = self._hidden_at(0)
        pages = _live_pages()
        hub_of = {leaf: hub for hub, hub_pages in pages.items() for leaves in hub_pages.values() for leaf in leaves}
        needed = ("cultivate", "explore", "talk", "shop buy", "travel go", "alchemy forage", "mine", "craft",
                  "hunt", "family enter", "family lesson", "family errand", "breakthrough")
        offenders = sorted(f"{leaf!r} lives in {hub_of.get(leaf)!r}" for leaf in needed if hub_of.get(leaf) in hidden)
        self.assertEqual(offenders, [], "the menu collapsed a hub the beginner path needs:\n  " + "\n  ".join(offenders))

    def test_at_the_roster_ceiling_nothing_is_left_off(self):
        ceiling = max(LEAVES.values())
        self.assertEqual(self._hidden_at(ceiling), {})

    def test_an_unreadable_roster_leaves_nothing_off(self):
        from app.rules import feature_unlocks
        pages = _live_pages()
        for roster in (None, {}, {"leaves": None}, {"leaves": {"x": "not a number"}}):
            with self.subTest(roster=roster):
                self.assertEqual(feature_unlocks.hidden_hubs(roster, 0, pages), {})

    def test_the_collapsed_line_names_the_hubs_and_locked(self):
        statements = _statements(RULES, "collapsed_menu_line")
        self.assertIn("/locked", statements, "the menu's collapsed line no longer names /locked (read without its docstring)")
        from app.rules import feature_unlocks
        line = feature_unlocks.collapsed_menu_line(["Combat", "Abode"], "Foundation Establishment")
        for needle in ("**Combat**", "**Abode**", "**Foundation Establishment**", "/locked", "still work"):
            self.assertIn(needle, line)
        self.assertEqual(feature_unlocks.collapsed_menu_line([], "x"), "")


class TheMenuDrawsTheShape(unittest.TestCase):
    def test_hidden_hubs_are_left_off_and_named_and_the_step_is_at_the_top(self):
        surface, hubs = _modules()
        surface._LAST_HUB.pop(7, None)
        shape = {"hidden_hubs": {"combat": 2, "abode": 3}, "tutorial": "🧭 Next: **Iron from the Seam** — Break spirit iron out of a seam"}
        view = hubs._MENU_BUILDER(owner_id=7, is_admin=False, owner_name="T", facts="📍 **Greenriver Town**", shape=shape)
        buttons, texts = _walk(view)
        labels = [b.label for b in buttons]
        # Fourteen hubs and the daily five (v1.3.2).
        self.assertEqual(len(labels), 14 + len(surface.DAILY_ACTIONS), labels)
        self.assertNotIn("Combat", labels)
        self.assertNotIn("Abode", labels)
        joined = "\n".join(texts)
        self.assertIn("**Combat**", joined)
        self.assertIn("**Abode**", joined)
        self.assertIn("/locked", joined)
        self.assertIn("Iron from the Seam", joined)
        self.assertIn("Foundation Establishment", joined)

    def test_the_default_call_draws_every_hub(self):
        surface, hubs = _modules()
        surface._LAST_HUB.pop(7, None)
        buttons, texts = _walk(hubs._MENU_BUILDER(owner_id=7, is_admin=False, owner_name="T", facts="x"))
        self.assertEqual(len(buttons), 16 + len(surface.DAILY_ACTIONS))
        self.assertNotIn("/locked", "\n".join(texts))

    def test_both_doors_into_the_menu_ask_the_one_provider(self):
        self.assertIn("shape = await menu_shape(interaction)", code_only(HUBS))
        self.assertIn("menu_shape(interaction)", _statements(SURFACE, "menu"))
        self.assertIn("register_menu_shape(_menu_shape)", SURFACE)

    def test_the_tutorial_line_names_the_first_objective_still_short(self):
        surface, _ = _modules()
        rows = [{
            "quest_key": "beginner_iron", "status": "active",
            "progress": {"dig": 1},
            "terms": {"objectives": [
                {"id": "dig", "type": "gather", "count": 1, "label": "Break spirit iron out of a seam - **/world → Act → Mine**"},
                {"id": "stand", "type": "combat_win", "count": 1, "label": "Come out of one fight standing - **/world → Act → Hunt**"},
            ]},
        }]
        definition = {"title": "Iron from the Seam", "source_key": "beginner_path"}
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(surface.DB, "get_character", AsyncMock(return_value={"realm_index": 0})), \
             patch.object(surface.DB, "list_character_quests", AsyncMock(return_value=rows)), \
             patch.object(surface.QUESTS, "definition", AsyncMock(return_value=definition)):
            shape = asyncio.run(surface._menu_shape(interaction))
        self.assertIn("Iron from the Seam", shape["tutorial"])
        self.assertIn("Hunt", shape["tutorial"])
        self.assertNotIn("Mine**", shape["tutorial"])
        self.assertIn("combat", shape["hidden_hubs"])

    def test_nobody_without_a_character_is_shown_less(self):
        surface, _ = _modules()
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        with patch.object(surface.DB, "get_character", AsyncMock(return_value=None)):
            self.assertEqual(asyncio.run(surface._menu_shape(interaction)), {})


class HidingIsAdvertisingNeverABound(unittest.TestCase):
    def test_the_press_gate_never_reads_the_menus_shape(self):
        body = code_only(ast.unparse(_function(SURFACE, "_panel_gate")))
        for forbidden in ("menu_shape", "hidden_hubs", "_menu_shape"):
            self.assertNotIn(forbidden, body, f"_panel_gate consults {forbidden}: a hub left off the menu would become a refusal")


class TheRanksAreOneLadder(unittest.TestCase):
    def test_every_examination_quotes_the_ladder(self):
        from app.rules.progression_systems import PROFESSION_RANKS, profession_rank
        self.assertEqual(PROFESSION_RANKS[0], "Unranked")
        self.assertEqual(len(PROFESSION_RANKS), 10, "Unranked and nine tiers")
        self.assertEqual(profession_rank(9, "Forging"), "Tier 9 Forge Sovereign")
        self.assertEqual(profession_rank(40, "Alchemy"), "Tier 9 Pill Sovereign", "a level past the ladder reads as its top")
        self.assertEqual(profession_rank(1, "No Such Trade"), "Tier 1 Apprentice", "an unknown trade gets the bare tier, never a wrong word")
        self.assertEqual(profession_rank(0, "Forging"), "Unranked")
        for trade, exams in (CONTENT.get("profession_exams") or {}).items():
            for exam in exams:
                rank = int(exam["rank"])
                with self.subTest(trade=trade, rank=rank):
                    self.assertEqual(exam["rank_name"], profession_rank(rank, trade))
                    self.assertIn(profession_rank(rank, trade), exam["title"])
                    self.assertIn(profession_rank(rank, trade), exam["objectives"][0]["label"])

    def test_no_production_file_spells_a_retired_rank(self):
        retired = ("Journeyman", "Grade 1")
        offenders = []
        for path in sorted((APP).rglob("*.py")):
            text = code_only(path.read_text(encoding="utf-8"))
            for word in retired:
                if word in text:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {word}")
        self.assertEqual(offenders, [], "a retired rank name is spelled in production code (comments and docstrings ignored):\n  "
                         + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()

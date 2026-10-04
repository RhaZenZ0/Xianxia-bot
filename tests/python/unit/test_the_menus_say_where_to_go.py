"""The menus say where to go, and get there in one tap (v1.22.0).

Three reports drove it: *"Interface is overwhelming ... I still forget where to
go what to do"*, the middle of a nine-system hub being four presses of the
arrows away, and buttons reading `Npcinfo` and `Specialeffects`. What this file
holds:

- every hub with more than one system carries a jump select listing exactly
  the systems the player can see, and a pick lands on the system picked;
- the menu presses the tutorial's next step rather than only naming it, opens
  the journal and the cooldown card, and stays inside Discord's 40 components
  with every row it can draw;
- the Daily row says how long each of the five is still cooling down, read off
  the engine's own reading;
- a page's hand-set order names only leaves that page holds, and a leaf label
  override names only a leaf that exists - a typo in either would be silently
  ignored, which is the failure mode of `only` that `test_hub_pages.py` exists
  for;
- the Discord playtest skips the jump select when it answers a leaf's input
  steps, because the panel itself is often the message a leaf's result lands
  on, and picking the jump would change page mid-sweep.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from tests.support import PROJECT_ROOT, code_only

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
HARNESS = PROJECT_ROOT / "scripts" / "playtest_discord.py"


def _modules():
    with patch.dict(os.environ, ENV):
        return (importlib.import_module("app.bot.surface"),
                importlib.import_module("app.bot.hubs"),
                importlib.import_module("app.bot.commands.cooldowns"))


def _walk(item, out=None):
    out = [] if out is None else out
    out.append(item)
    for child in getattr(item, "children", []) or []:
        _walk(child, out)
    accessory = getattr(item, "accessory", None)
    if accessory is not None:
        _walk(accessory, out)
    return out


def _count(view) -> int:
    return sum(len(_walk(child)) for child in view.children)


def _of(view, cls_name: str) -> list:
    return [item for child in view.children for item in _walk(child) if item.__class__.__name__ == cls_name]


class EveryHubCanJump(unittest.TestCase):
    def test_a_hub_with_several_systems_lists_them_all_and_one_without_lists_none(self):
        _, hubs, _ = _modules()
        for definition in hubs.REGISTERED_HUBS:
            with self.subTest(hub=definition.name):
                view = hubs.LayoutHubView(1, definition, owner_name="T")
                selects = _of(view, "HubLayoutPageSelect")
                pages = view.visible_pages()
                if len(pages) <= 1:
                    self.assertEqual(selects, [], "a hub with one system was given somewhere to jump")
                    continue
                self.assertEqual(len(selects), 1)
                self.assertEqual([o.value for o in selects[0].options], [p.key for p in pages])
                self.assertTrue(selects[0].placeholder.startswith(hubs.PAGE_JUMP_PLACEHOLDER))
                # The heading names the system; the jump never repeats it as a value.
                self.assertFalse(any(o.default for o in selects[0].options))

    def test_a_pick_lands_on_the_system_picked(self):
        _, hubs, _ = _modules()
        definition = next(d for d in hubs.REGISTERED_HUBS if d.name == "economy")
        view = hubs.LayoutHubView(1, definition, owner_name="T")
        view.action_offset = 8
        select = _of(view, "HubLayoutPageSelect")[0]
        target = definition.pages[5].key
        select._values = [target]
        edits = []
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock(side_effect=lambda **kw: edits.append(kw))))
        with patch.object(type(select), "values", property(lambda self: self._values)):
            asyncio.run(select.callback(interaction))
        self.assertEqual(view.page_key, target)
        self.assertEqual(view.action_offset, 0)
        self.assertEqual(len(edits), 1)
        self.assertIn(f"### {hubs._page_emoji(target)} {definition.pages[5].label}",
                      "\n".join(getattr(i, "content", "") or "" for i in _walk(view.children[0])))

    def test_the_jump_only_ever_offers_what_the_player_can_see(self):
        _, hubs, _ = _modules()
        definition = next(d for d in hubs.REGISTERED_HUBS if d.name == "cultivation")
        view = hubs.LayoutHubView(1, definition, owner_name="T")
        ghost = next(p for p in definition.pages if p.key == "ghost")
        view.hidden_paths = {a.path: hubs.NOT_YOUR_PATH for a in hubs._leaf_actions(ghost)}
        view.rebuild()
        values = [o.value for o in _of(view, "HubLayoutPageSelect")[0].options]
        self.assertNotIn("ghost", values, "another path's system was offered as somewhere to jump")


class APageSaysItsOwnOrder(unittest.TestCase):
    def test_every_ordered_name_is_a_leaf_of_that_page(self):
        surface, hubs, _ = _modules()
        ordered = 0
        for definition in [*hubs.REGISTERED_HUBS, surface._ADMIN_HUB_DEFINITION]:
            for page in definition.pages:
                if not page.order:
                    continue
                ordered += 1
                with self.subTest(hub=definition.name, page=page.label):
                    leaves = [a.path.lstrip("/") for a in hubs._leaf_actions(page)]
                    self.assertEqual(sorted(set(page.order) - set(leaves)), [],
                                     "an ordered name is no leaf of this page, and would be silently ignored")
                    self.assertEqual(len(page.order), len(set(page.order)))
                    self.assertEqual(leaves[:len(page.order)], list(page.order))
        self.assertGreater(ordered, 0, "no page carries an order; the gate is reading nothing")

    def test_the_cultivate_page_leads_with_what_it_is_for(self):
        _, hubs, _ = _modules()
        definition = next(d for d in hubs.REGISTERED_HUBS if d.name == "cultivation")
        page = next(p for p in definition.pages if p.key == "cultivate")
        paths = [a.path for a in hubs._leaf_actions(page)]
        self.assertEqual(paths[0], "/cultivate")
        seclusion = [i for i, p in enumerate(paths) if p.startswith("/seclusion ")]
        self.assertEqual(seclusion, list(range(seclusion[0], seclusion[0] + 3)), "the seclusion buttons are apart again")


class ALabelIsAWord(unittest.TestCase):
    def test_every_override_names_a_leaf_that_exists_and_is_used(self):
        surface, hubs, _ = _modules()
        live = {a.path: a.label for d in [*hubs.REGISTERED_HUBS, surface._ADMIN_HUB_DEFINITION]
                for p in d.pages for a in hubs._leaf_actions(p)}
        for path, label in hubs.LEAF_LABELS.items():
            with self.subTest(path=path):
                self.assertIn(path, live, "a label override names no leaf")
                self.assertEqual(live[path], label)
        for bad in ("Npcinfo", "Specialeffects", "Worldevents", "Worldrules", "Daoheart", "Spatialkey",
                    "Manual Cultivate By", "Admin Npc Npcinspect"):
            self.assertNotIn(bad, live.values(), f"{bad!r} is a command name, not a label")

    def test_a_path_printed_with_the_old_label_still_resolves(self):
        _, hubs, _ = _modules()
        for text, path in (("**/npc → People → Npcinfo**", "/npcinfo"),
                           ("**/world → Almanac → Worldevents**", "/worldevents"),
                           ("**/cultivation → Arts → Study**", "/manual study")):
            with self.subTest(text=text):
                self.assertEqual([a.path for a in hubs.suggested_actions(text)], [path])


class TheMenuPointsSomewhere(unittest.TestCase):
    TUTORIAL = "🧭 Next: **Out of the Gate** — Look around outside - **/world → Act → Explore**"

    def _menu(self, surface, hubs, **shape):
        surface._LAST_HUB[7] = "craft"
        return hubs._MENU_BUILDER(owner_id=7, is_admin=True, owner_name="T", facts="📍 **Greenriver Town**", shape=shape)

    def test_the_next_step_is_a_button_onto_the_leaf_and_hub_the_path_names(self):
        surface, hubs, _ = _modules()
        menu = self._menu(surface, hubs, tutorial=self.TUTORIAL)
        nexts = _of(menu, "MenuNextButton")
        self.assertEqual(len(nexts), 1)
        self.assertEqual((nexts[0].hub, nexts[0].path), ("world", "/explore"))
        self.assertEqual(nexts[0].label, "Next: Explore")
        # It presses the way a Daily button presses: opens the hub, then the leaf.
        self.assertIs(type(nexts[0]).callback, surface.MenuDailyButton.callback)

    def test_a_breakthrough_named_on_the_cultivation_hub_opens_that_hub(self):
        surface, _, _ = _modules()
        step = surface._next_step("Break through - **/cultivation → Cultivate → Breakthrough**")
        self.assertEqual((step[0], step[1].path), ("cultivation", "/breakthrough"))
        step = surface._next_step("Go out hunting - **/hunt**")
        self.assertEqual(step[1].path, "/hunt")
        self.assertIsNone(surface._next_step("Break spirit iron out of a seam"))

    def test_the_journal_and_the_cooldowns_are_on_the_menu_for_a_cultivator_only(self):
        surface, hubs, _ = _modules()
        names = [b.command_name for b in _of(self._menu(surface, hubs), "MenuCommandButton")]
        self.assertEqual(names, [name for name, _, _ in surface.MENU_TOOLS])
        self.assertEqual(names, ["quests", "cooldowns"])
        fresh = hubs._MENU_BUILDER(owner_id=7, is_admin=False, owner_name="T", facts="🌱 No cultivator yet.")
        self.assertEqual(_of(fresh, "MenuCommandButton"), [], "somebody with no character was offered the journal")

    def test_the_fullest_menu_fits_discords_cap(self):
        surface, hubs, _ = _modules()
        menu = self._menu(surface, hubs, tutorial=self.TUTORIAL, daily_waits={"hunt": 700, "mine": -1})
        self.assertLessEqual(_count(menu), hubs._LAYOUT_COMPONENT_CAP)
        self.assertTrue(_of(menu, "MenuNextButton"))

    def test_a_tool_asks_the_gate_by_its_bare_name_and_then_runs_the_command(self):
        surface, _, _ = _modules()
        button = surface.MenuCommandButton("quests", "Quests", "📜")
        ran = AsyncMock()
        interaction = SimpleNamespace(user=SimpleNamespace(id=7), response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(surface, "_panel_refusal", AsyncMock(return_value=None)) as gate, \
             patch.object(surface.ACTIONS, "root", return_value=SimpleNamespace(callback=ran)):
            asyncio.run(button.callback(interaction))
        gate.assert_awaited_once_with(interaction.user, "quests")
        ran.assert_awaited_once_with(interaction)
        with patch.object(surface, "_panel_refusal", AsyncMock(return_value="The world is closed.")), \
             patch.object(surface.ACTIONS, "root", return_value=SimpleNamespace(callback=ran)):
            ran.reset_mock()
            asyncio.run(button.callback(interaction))
        ran.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()


class TheDailyRowSaysWhatIsCoolingDown(unittest.TestCase):
    def test_a_waiting_button_says_for_how_long_and_a_ready_one_stays_green(self):
        surface, hubs, _ = _modules()
        menu = hubs._MENU_BUILDER(owner_id=7, is_admin=False, owner_name="T", facts="📍 x",
                                  shape={"daily_waits": {"hunt": 700, "mine": -1, "explore": 5400}})
        daily = {b.root: b for b in _of(menu, "MenuDailyButton")}
        self.assertEqual(daily["hunt"].label, "Hunt · 12m")
        self.assertEqual(daily["mine"].label, "Mine · wait")
        self.assertEqual(daily["explore"].label, "Explore · 1h30m")
        self.assertIs(daily["hunt"].style, discord.ButtonStyle.secondary)
        self.assertIs(daily["cultivate"].style, discord.ButtonStyle.success)
        self.assertNotIn("·", daily["cultivate"].label)

    def test_the_waits_are_the_engines_reading(self):
        _, _, cooldowns = _modules()
        status = {"waits": [
            {"family": "hunt", "remaining_seconds": 700, "scheduled": True},
            {"family": "alchemy_forage", "remaining_seconds": 60, "scheduled": True},
            {"family": "mine", "remaining_game_minutes": 30, "scheduled": False},
            {"family": "manual", "subject": "x", "remaining_seconds": 9, "scheduled": True},
            {"family": "beast_feed", "subject": "3", "remaining_seconds": 9, "scheduled": True},
        ]}
        with patch.object(cooldowns.ENGINE, "action", AsyncMock(return_value=status)) as engine:
            waits = asyncio.run(cooldowns.daily_waits(7, ("cultivate", "explore", "hunt", "forage", "mine")))
        engine.assert_awaited_once_with("cooldown.status", 7, {})
        self.assertEqual(waits, {"hunt": 700, "forage": 60, "mine": -1})
        with patch.object(cooldowns.ENGINE, "action", AsyncMock(side_effect=RuntimeError("engine away"))):
            self.assertEqual(asyncio.run(cooldowns.daily_waits(7, ("hunt",))), {})

    def test_both_doors_into_the_menu_carry_the_waits(self):
        source = code_only((PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8"))
        self.assertEqual(source.count('daily_waits=shape.get("daily_waits")'), 1, "/menu does not pass the waits")
        self.assertEqual(source.count('daily_waits=(shape or {}).get("daily_waits")'), 1, "the hub's Menu button does not")


class ThePlaytestNeverAnswersTheJump(unittest.TestCase):
    def test_the_harness_skips_the_select_by_the_panels_own_placeholder(self):
        _, hubs, _ = _modules()
        tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
        assigned = {node.targets[0].id: node.value.value for node in tree.body
                    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)}
        self.assertEqual(assigned.get("PANEL_JUMP_PLACEHOLDER"), hubs.PAGE_JUMP_PLACEHOLDER)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "select_by_placeholder")
        self.assertIn("PANEL_JUMP_PLACEHOLDER", ast.unparse(function),
                      "select_by_placeholder no longer skips the panel's jump select")


if __name__ == "__main__":
    unittest.main()

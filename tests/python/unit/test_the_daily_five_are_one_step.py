"""The daily five are one step (v1.3.2).

Player feedback, with the `/cooldowns` reading pasted: *"For each command i
have to go to 3 steps. Time consuming. Thing should be friendly; Hunt, Gather,
Mine, Explore, Cultivate. With slash command or interface button. Instead of a
b then c."*

Four of the five were root commands already and none was in the command tree
(`TREE_COMMANDS`), so a hub page was the only door; forage was a group leaf
with no root at all. Each is a slash command now, the menu draws them as one
row, and the cooldown card names the command rather than the path. These hold
that the five doors all reach the same leaf, so nothing about what the action
does lives in more than one place.
"""
from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT, code_only

BOT = PROJECT_ROOT / "app" / "bot"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _modules():
    with patch.dict(os.environ, ENV):
        surface = importlib.import_module("app.bot.surface")
        hubs = importlib.import_module("app.bot.hubs")
        cooldowns = importlib.import_module("app.bot.commands.cooldowns")
        exploration = importlib.import_module("app.bot.commands.exploration")
    return surface, hubs, cooldowns, exploration


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


class EachOfTheFiveIsASlashCommand(unittest.TestCase):
    def test_the_five_are_in_the_tree_and_are_bound_roots(self):
        surface, _, _, _ = _modules()
        self.assertEqual(surface.DAILY_ACTIONS, ("cultivate", "explore", "hunt", "forage", "mine"))
        for root in surface.DAILY_ACTIONS:
            with self.subTest(root=root):
                self.assertIn(root, surface.TREE_COMMANDS, f"/{root} is not registered into the command tree")
                self.assertIsNotNone(surface.ACTIONS.root(root))

    def test_every_daily_root_is_the_leaf_the_menu_presses(self):
        """One leaf, several doors: the root the tree registers and the leaf
        the menu presses must be the same bound handler, or the two could
        drift the way `sync_world_catalog` did."""
        surface, hubs, _, exploration = _modules()
        for root, hub, path in surface._DAILY_LEAVES:
            with self.subTest(root=root):
                action = surface._daily_leaf(hub, path)
                self.assertIsNotNone(action, f"{hub} holds no leaf {path}")
                if root == "forage":
                    continue  # its root is a wrapper, held below
                self.assertIs(action.handler, surface.ACTIONS.handler_for(surface.ACTIONS.root(root)))

    def test_forage_is_the_same_handler_the_hub_presses(self):
        """`/forage` decides nothing: its body is one call to the registry's
        binding of `alchemy forage`, read off the source so a copy of the
        forage reply cannot grow here."""
        source = code_only((BOT / "commands" / "exploration.py").read_text(encoding="utf-8"))
        tree = ast.parse(source)
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "forage")
        body = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        self.assertEqual(len(body), 1, "the root forage does more than delegate")
        text = ast.unparse(body[0])
        self.assertIn("ACTIONS.handler_for(alchemy_forage)", text)


class TheMenuDrawsThemAsOneRow(unittest.TestCase):
    def test_a_cultivator_sees_five_daily_buttons_and_a_newcomer_none(self):
        surface, hubs, _, _ = _modules()
        surface._LAST_HUB.pop(7, None)
        menu = hubs._MENU_BUILDER(owner_id=7, is_admin=False, owner_name="T", facts="📍 **Greenriver Town**")
        daily = [b for b in _buttons(menu) if b.__class__.__name__ == "MenuDailyButton"]
        self.assertEqual([b.root for b in daily], list(surface.DAILY_ACTIONS))
        for button in daily:
            self.assertIsNotNone(surface._daily_leaf(button.hub, button.path), button.root)
        fresh = hubs._MENU_BUILDER(owner_id=7, is_admin=False, owner_name="T", facts="🌱 No cultivator yet.")
        self.assertEqual([b for b in _buttons(fresh) if b.__class__.__name__ == "MenuDailyButton"], [],
                         "somebody with no character was offered the daily row; /begin is all they can do")

    def test_the_press_opens_the_hub_in_place_and_runs_the_leaf(self):
        """Read off the source: the button must open the hub *and then* start
        the action through `_start_hub_action`, which is the path every hub
        button takes and where the panel gate checks the press."""
        source = code_only((BOT / "surface.py").read_text(encoding="utf-8"))
        tree = ast.parse(source)
        cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "MenuDailyButton")
        callback = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "callback")
        calls = [ast.unparse(n.func) for n in ast.walk(callback) if isinstance(n, ast.Call)]
        self.assertIn("open_hub_in_place", calls)
        self.assertIn("_start_hub_action", calls)
        self.assertLess(calls.index("open_hub_in_place"), calls.index("_start_hub_action"),
                        "the leaf ran before the panel it draws into existed")


class TheCardNamesTheCommand(unittest.TestCase):
    def test_the_cooldown_card_names_the_slash_command(self):
        surface, _, cooldowns, _ = _modules()
        for family, root in (("cultivate", "cultivate"), ("explore", "explore"), ("hunt", "hunt"),
                             ("mine", "mine"), ("alchemy_forage", "forage")):
            with self.subTest(family=family):
                self.assertEqual(cooldowns.FAMILY_LABELS[family][2], f"**/{root}**")

    def test_a_printed_root_still_earns_its_next_step_button(self):
        """`**/hunt**` names no hub, and before v1.3.2 `_hint_action` answered
        None for it: the reply lost the button the hub path used to earn."""
        surface, hubs, _, _ = _modules()
        for root, _, path in surface._DAILY_LEAVES:
            with self.subTest(root=root):
                action = hubs._hint_action(root, [])
                self.assertIsNotNone(action, f"**/{root}** earns no button")
                self.assertEqual(action.path, path)
        self.assertIsNone(hubs._hint_action("hunt", ["Somewhere"]), "a root with steps is not a path")


if __name__ == "__main__":
    unittest.main()

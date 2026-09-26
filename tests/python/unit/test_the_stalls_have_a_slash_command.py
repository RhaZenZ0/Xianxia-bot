"""`/stall` is a slash command of its own (v1.7.4).

Asked for the stall commands, the answer was `/stall board`, `/stall buy` and
the rest - and the owner said "not there". They were right: `stall_group` had
backed the `/economy → Market Stalls` page since v1.5.0 and was never added to
the command tree, so the name a player types reached nothing. That is `/learn`
(rc.43) again: a command that exists, answered by the engine, reached by no
door a player uses.

The tree tuple named only bound roots, because registration resolved each name
through `ACTIONS.root`, which knows no groups. It resolves through
`_tree_command` now, which answers a group from `_GROUP_ACTION_ROOTS` - the map
the hub pages are built from - so the group registered is the very object the
hub page's leaves belong to.
"""
from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

from discord import app_commands

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


class TheStallsHaveASlashCommand(unittest.TestCase):
    def test_stall_is_a_tree_command(self):
        surface = _surface()
        self.assertIn("stall", surface.TREE_COMMANDS, "/stall is not registered with Discord")

    def test_it_registers_the_group_the_hub_page_uses(self):
        surface = _surface()
        group = surface._tree_command("stall")
        self.assertIs(group, surface._GROUP_ACTION_ROOTS["stall"])
        self.assertIsInstance(group, app_commands.Group)
        leaves = {c.name for c in group.commands}
        self.assertTrue({"board", "buy", "status", "open", "list", "withdraw", "close"} <= leaves, sorted(leaves))

    def test_registration_resolves_every_tree_name_through_a_resolver_that_knows_groups(self):
        source = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef) and n.name == "register_command_surface")
        added = [ast.unparse(n.args[0]) for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("tree.add_command") and n.args]
        self.assertIn("_tree_command(name)", added,
                      "the tree tuple is resolved through ACTIONS.root again, which knows no groups")
        surface = _surface()
        for name in surface.TREE_COMMANDS:
            self.assertIsNotNone(surface._tree_command(name), name)


if __name__ == "__main__":
    unittest.main()

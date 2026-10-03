"""The qi breakthrough is a slash command (v1.19.5).

Asked for as "a command for cultivation breakthrough for qi". `/breakthrough`
has been a registered root since before the hubs, and it was never put in the
command tree, so the only door was `/cultivation → Main Progression →
Breakthrough` - the shape `/learn` had in rc.43 and `/stall` in v1.7.4. The
body's twin has been `/body breakthrough` since v1.9.1.

Held here: the tree registers it, the command Discord registers is the very
root the hub leaf presses (one handler, two doors), and that root is the qi
ladder's, not the body's.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import unittest
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


class TheQiBreakthroughHasASlashCommand(unittest.TestCase):
    def test_the_tree_registers_it(self):
        self.assertIn("breakthrough", _surface().TREE_COMMANDS)

    def test_the_slash_command_is_the_hub_leaf(self):
        surface = _surface()
        from app.bot.hubs import REGISTERED_HUBS, _leaf_actions

        leaves = [action for definition in REGISTERED_HUBS if definition.name == "cultivation"
                  for page in definition.pages for action in _leaf_actions(page)
                  if action.path == "/breakthrough"]
        self.assertEqual(len(leaves), 1, "the cultivation hub no longer carries the qi breakthrough leaf")
        self.assertIs(surface._tree_command("breakthrough"), leaves[0].command,
                      "the slash command and the hub leaf are two commands, free to drift apart")

    def test_it_is_the_qi_ladder(self):
        surface = _surface()
        callback = surface._tree_command("breakthrough").callback
        operations = {node.args[0].value for node in ast.walk(ast.parse(inspect.getsource(callback)))
                      if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "authoritative_action"
                      and node.args and isinstance(node.args[0], ast.Constant)}
        self.assertEqual(operations, {"cultivation.breakthrough"})


if __name__ == "__main__":
    unittest.main()

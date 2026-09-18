"""Maintenance mode: the bot's half of a closed world (v1.0.0-rc.41).

The engine refuses its ~150 authoritative player operations while the flag is
set, and that is the backstop. It cannot cover a read: `/sheet`, `/quests` and
every other card answer out of the presentation layer's own SQL and never
reach the engine's authoritative path, so without a gate on this side they
would answer happily out of a half-migrated database.

These hold the rules that make the gate safe to have at all: an administrator
is never refused (they are how the world reopens), and every unreadable shape
of the flag leaves the world open.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT, install_aiosqlite_shim

install_aiosqlite_shim()

BOT = PROJECT_ROOT / "app" / "bot"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

with patch.dict(os.environ, ENV):
    maintenance = importlib.import_module("app.bot.maintenance")


class _DB:
    def __init__(self, state):
        self.state = state
        self.reads = 0

    async def get_maintenance_mode(self):
        self.reads += 1
        return dict(self.state)


CLOSED = {"enabled": True, "reason": "updating to rc.41", "since": 1.0}
OPEN = {"enabled": False, "reason": "", "since": 0.0}

PLAYER = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(administrator=False))
GM = SimpleNamespace(id=2, guild_permissions=SimpleNamespace(administrator=True))


def run(coro):
    return asyncio.run(coro)


class AClosedWorldRefusesPlayers(unittest.TestCase):
    def setUp(self):
        maintenance.forget()

    def test_a_player_is_refused_in_the_operators_own_words(self):
        refusal = run(maintenance.refuse(_DB(CLOSED), PLAYER))
        self.assertIsNotNone(refusal)
        self.assertIn("closed for maintenance", refusal)
        self.assertIn("updating to rc.41", refusal)

    def test_an_open_world_refuses_nobody(self):
        self.assertIsNone(run(maintenance.refuse(_DB(OPEN), PLAYER)))

    def test_an_administrator_is_never_refused(self):
        """They are how the world reopens. If this ever fails, closing the
        world locks the operator out of their own server."""
        self.assertIsNone(run(maintenance.refuse(_DB(CLOSED), GM)))

    def test_an_admin_command_is_never_refused_whoever_runs_it(self):
        # The hub panel passes the leaf path; /admin's own leaves must work
        # from a panel as well as from the command line.
        self.assertIsNone(run(maintenance.refuse(_DB(CLOSED), PLAYER, command="admin server lockdown")))
        self.assertIsNotNone(run(maintenance.refuse(_DB(CLOSED), PLAYER, command="cultivation train")))


class TheFlagFailsTowardsPlay(unittest.TestCase):
    def setUp(self):
        maintenance.forget()

    def test_a_database_that_cannot_answer_leaves_the_world_open(self):
        class Broken:
            async def get_maintenance_mode(self):
                raise RuntimeError("no database")

        self.assertIsNone(run(maintenance.refuse(Broken(), PLAYER)))

    def test_the_cache_spares_a_burst_of_presses_a_burst_of_queries(self):
        db = _DB(CLOSED)

        async def press_five():
            for _ in range(5):
                await maintenance.refuse(db, PLAYER)

        run(press_five())
        self.assertEqual(db.reads, 1, "a panel's rapid clicks must cost one read")

    def test_the_lever_seeds_the_cache_so_the_next_command_obeys_at_once(self):
        maintenance.remember({"enabled": True, "reason": "just set", "since": 2.0})
        db = _DB(OPEN)
        refusal = run(maintenance.refuse(db, PLAYER))
        self.assertIsNotNone(refusal)
        self.assertIn("just set", refusal)
        self.assertEqual(db.reads, 0, "the operator's own write needs no round trip to confirm")


class EveryDoorIsGated(unittest.TestCase):
    """The four ways a player reaches the bot, each with its gate.

    Read off the source, because the wiring is what fails silently: a door
    added later with no gate would pass every behavioural test above.
    """

    def test_every_slash_command_passes_the_tree_check(self):
        bot = (BOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("class MaintenanceAwareTree(app_commands.CommandTree)", bot)
        self.assertIn("async def interaction_check", bot)
        self.assertIn("tree_cls=MaintenanceAwareTree", bot)

    def test_the_hub_panel_checks_on_the_press(self):
        hubs = (BOT / "hubs.py").read_text(encoding="utf-8")
        tree = ast.parse(hubs)
        invoke = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.AsyncFunctionDef) and n.name == "_invoke_action")
        source = ast.get_source_segment(hubs, invoke) or ""
        self.assertIn("_maintenance_refusal", source)
        # Before the handler runs, not after it has already acted.
        self.assertLess(source.index("_maintenance_refusal"), source.index("action.handler("))

    def test_the_typed_line_and_the_shorthand_are_gated(self):
        bot = (BOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("maintenance.refuse(DB, message.author)", bot)

    def test_the_picker_click_is_gated(self):
        typed = (BOT / "typed_play.py").read_text(encoding="utf-8")
        dispatch = typed[typed.index("async def dispatch("):]
        dispatch = dispatch[: dispatch.index('\n    if candidate.kind == "root":')]
        self.assertIn("maintenance.refuse", dispatch)

    def test_the_panel_gate_is_registered_from_above(self):
        """`hubs` sits below `runtime`, so it cannot reach DB itself; the
        surface registers the gate the way it registers hidden actions."""
        surface = (BOT / "surface.py").read_text(encoding="utf-8")
        self.assertIn("register_maintenance_gate(_panel_maintenance_gate)", surface)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

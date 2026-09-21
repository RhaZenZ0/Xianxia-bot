"""Every command a player can be given reaches a player (v1.0.0-rc.43).

`/learn` was written in rc.20, complete: an allowlisted engine action
(`recipe.learn`) that writes a method and spends the slip inside one
transaction, a Discord command that sends it, its own autocomplete over the
slips in the player's bags, and thirty-three method slips authored one per
recipe and stocked in sixty-eight of a hundred and twenty shops.

Nobody could reach it. `registered_root_command` put it in the registry, and
the two things that turn a registry entry into something a player can press -
`_MIGRATED_ROOTS`, which lands a root on a hub page, and the tuple in
`register_command_surface`, which adds a root to the command tree - named every
other root and not that one. So the command existed, the engine answered it,
the economy priced it, and no door opened onto it for twenty-three releases.

That is the same fault as `first_steps` (a quest seeded on every boot that
nothing handed over) and `forage_materials` (a roster nothing rolled against),
and it is invisible to every test that asks whether the hubs are well formed,
because a command on no hub is not a malformed hub - it is an absence.

This is the gate for the absence. It is cheap and total: the registry knows
every root, `surface` knows the two ways a root reaches a player, and the
difference must be empty or explained. `ALLOWED_UNREACHABLE` is empty and was
empty the day it was written - `/learn` was the only orphan of forty-five - so
an entry here is a new decision, never a backlog inherited from this one.
"""
from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

SURFACE = PROJECT_ROOT / "app" / "bot" / "surface.py"

# A registered root that deliberately reaches no player, with the reason it is
# allowed to. An entry naming a root that no longer exists, or one that is in
# fact reachable, fails below - so this can only shrink honestly.
ALLOWED_UNREACHABLE: dict[str, str] = {}


def _modules():
    with patch.dict(os.environ, ENV):
        return (importlib.import_module("app.bot.surface"),
                importlib.import_module("app.bot.registry"))


def _tree_roots() -> set[str]:
    """The roots `register_command_surface` adds to the command tree.

    **Imported, not parsed (v1.0.12).** This used to walk `surface.py` by AST
    for the tuple inside that function - rc.43's rule that a list kept in a
    test is a list that drifts, implemented the only way an inline tuple
    allows. Four places implemented that rule four ways, each needing its own
    "did the read find anything" self-check, and one of them gave up and wrote
    down a count that went stale. The tuple is `surface.TREE_COMMANDS` now, so
    there is nothing to read and nothing that can silently find nothing.
    """
    surface, _ = _modules()
    return set(surface.TREE_COMMANDS)


class EveryRegisteredRootOpensOnADoor(unittest.TestCase):
    def test_the_tree_tuple_is_still_where_this_test_thinks_it_is(self):
        # Kept as a self-check even though an import cannot come back empty the
        # way an AST walk could: an emptied tuple would make every root look
        # unreachable and turn the assertion below into noise.
        roots = _tree_roots()
        self.assertIn("begin", roots)
        self.assertIn("admin", roots)

    def test_no_registered_root_command_reaches_nobody(self):
        surface, registry = _modules()
        reachable = set(surface._MIGRATED_ROOTS) | _tree_roots()
        orphans = sorted(set(registry.ACTIONS.roots()) - reachable - set(ALLOWED_UNREACHABLE))
        self.assertEqual(orphans, [], (
            "these commands are registered and no player can reach them - they are on no hub page "
            "(app/bot/surface.py _MIGRATED_ROOTS) and are never added to the tree "
            "(register_command_surface). Put each on the hub page it belongs to, or name it in "
            f"ALLOWED_UNREACHABLE with the reason it is allowed to reach nobody: {orphans}"))

    def test_the_slip_reader_is_one_of_them(self):
        # Named outright rather than left to the sweep above, because this is
        # the command the gate exists for and a regression here is silent: the
        # slips stay on the shelves and nothing errors.
        surface, _ = _modules()
        self.assertIn("learn", surface._MIGRATED_ROOTS)

    def test_the_allowlist_carries_reasons_for_roots_that_exist(self):
        surface, registry = _modules()
        roots = set(registry.ACTIONS.roots())
        reachable = set(surface._MIGRATED_ROOTS) | _tree_roots()
        for name, reason in ALLOWED_UNREACHABLE.items():
            self.assertIn(name, roots, f"ALLOWED_UNREACHABLE names {name!r}, which is not a root")
            self.assertNotIn(name, reachable,
                             f"ALLOWED_UNREACHABLE names {name!r}, which a player can reach")
            self.assertGreaterEqual(len(reason.strip()), 20,
                                    f"ALLOWED_UNREACHABLE[{name!r}] needs a reason worth reading")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

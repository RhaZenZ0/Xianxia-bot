"""A reset takes the player's private rooms with it (v1.0.8).

Reported from live play: *"Reset should delete the threads."*

`character.reset` sweeps a player's rows across ~104 tables, and four of those
rows are the only record anywhere of a Discord thread the bot created for that
player - the expedition journal, their cave abode, their sect residence, and
any battle thread. The rows went and the threads stayed: an abandoned life's
private journal was left standing in Discord holding its whole scene log, with
nothing left in the database that could ever name it again.

**The rule was already written down, for the other case.**
`DB.all_managed_thread_ids`' own docstring says the threads must be collected
*"before the rows that reference them are wiped, since once the database is
gone there is no other way to find them again"* - and that was written for the
world-wide `reset_database.sh` wipe. The per-player lever that wipes exactly
those rows never applied it. That is the shape this repository keeps
recording: a fault someone wrote down and then fixed one table over
(`npcPoliticalMarriages`, v1.0.1).

`admin.player.erase` had the same hole and is fixed in the same release,
because there it is not clutter: an erasure that left the person's own private
thread standing would have removed them from the database and not from the
server.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
CORE = (APP / "database" / "core.py").read_text(encoding="utf-8")
THREADS = (APP / "bot" / "threads.py").read_text(encoding="utf-8")
RESET = (APP / "bot" / "commands" / "character.py").read_text(encoding="utf-8")
ERASE = (APP / "bot" / "admin" / "world_ops.py").read_text(encoding="utf-8")

# The two levers that delete a player's rows, and the handler each lives in.
LEVERS = (("character.reset", RESET, "reset"), ("admin.player.erase", ERASE, "admin_erase"))


def _function(source: str, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    """By AST: a multi-line `def` defeats an indentation slice (rc.59)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone; this gate is guarding a function that moved")


def _call_lines(fn: ast.AST, name: str) -> list[int]:
    """Where `name(...)` is called, by line. A call, never a substring: the
    comments in both handlers name these functions while explaining the
    ordering, and a scan that counted prose would pass on a handler that had
    stopped calling them (rc.52)."""
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if called == name:
            out.append(node.lineno)
    return out


class ThreadSourcesCannotDrift(unittest.TestCase):
    """The per-player enumeration is a subset of the world-wide one.

    Two lists of "which tables hold a thread id" in two places would be free to
    drift, and the way that goes wrong is silent: a new thread table added to
    one and not the other leaves a thread nothing can ever delete.
    """

    @staticmethod
    def _tables(node: ast.AST) -> set[str]:
        """Every table named in a SELECT literal under this node.

        Taken from wherever the statements live rather than from a fixed shape:
        the world-wide sweep keeps its SQL inline in the method, the per-player
        one keeps it in a class-level tuple beside it, and a reader that only
        knew one of those would silently find nothing - which makes every
        assertion built on it vacuous rather than red.
        """
        out = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and "FROM " in child.value:
                out.add(child.value.split("FROM ", 1)[1].split()[0])
        return out

    def _sources(self, name: str) -> set[str]:
        tree = ast.parse(CORE)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return self._tables(node)
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                return self._tables(node)
        raise AssertionError(f"{name} is gone; this gate is guarding something that moved")

    def test_the_reader_finds_the_world_wide_sweep(self):
        """Asserted before it is trusted (rc.57)."""
        world = self._sources("all_managed_thread_ids")
        self.assertIn(
            "expedition_threads", world,
            "the SQL reader found no known table in all_managed_thread_ids; the gate is broken, not the tree",
        )

    def test_every_player_thread_table_is_one_the_world_sweep_also_names(self):
        world = self._sources("all_managed_thread_ids")
        mine = self._sources("PLAYER_OWNED_THREAD_SOURCES")
        self.assertTrue(mine, "PLAYER_OWNED_THREAD_SOURCES names no table at all")
        self.assertEqual(
            sorted(mine - world), [],
            "player_thread_ids reads a table the world-wide sweep does not know about: "
            f"{sorted(mine - world)}. The two enumerations have drifted, so a world reset "
            "would leave those threads standing.",
        )

    def test_a_shared_room_is_not_one_players_to_delete(self):
        """A starter household is shared by everybody born into it and an event
        scene belongs to the event. Deleting either because one cultivator
        started over would take a room other players are standing in - the same
        line the reset already draws when it puts the household's welcome line
        back and leaves the house itself alone."""
        mine = self._sources("PLAYER_OWNED_THREAD_SOURCES")
        for shared in ("birth_family_household_threads", "event_threads"):
            self.assertNotIn(
                shared, mine,
                f"{shared} is shared, and player_thread_ids would have one player's reset "
                "delete a room that belongs to other people",
            )


class BothLeversReadBeforeTheyWipe(unittest.TestCase):
    def test_each_lever_collects_the_threads_and_then_deletes_them(self):
        for operation, source, handler in LEVERS:
            with self.subTest(operation=operation):
                fn = _function(source, handler)
                read = _call_lines(fn, "player_thread_ids")
                delete = _call_lines(fn, "delete_player_threads")
                self.assertTrue(
                    read,
                    f"{handler} never asks DB.player_thread_ids, so {operation} deletes the rows "
                    "that name this player's threads and leaves the threads standing in Discord "
                    "with nothing able to find them again",
                )
                self.assertTrue(
                    delete, f"{handler} collects the thread ids and never deletes the threads",
                )

    def test_the_ids_are_read_before_the_engine_is_called_and_deleted_after(self):
        """The ordering is the whole finding, and it is the one thing a reader
        of either handler cannot get from the fact that both calls are present.
        """
        for operation, source, handler in LEVERS:
            with self.subTest(operation=operation):
                fn = _function(source, handler)
                reads = _call_lines(fn, "player_thread_ids")
                deletes = _call_lines(fn, "delete_player_threads")
                self.assertTrue(reads, f"{handler} never asks DB.player_thread_ids")
                self.assertTrue(deletes, f"{handler} never calls delete_player_threads")
                read, delete = min(reads), min(deletes)
                engine = [
                    node.lineno for node in ast.walk(fn)
                    if isinstance(node, ast.Constant) and node.value == operation
                ]
                self.assertTrue(engine, f"{handler} no longer names {operation}")
                self.assertLess(
                    read, min(engine),
                    f"{handler} reads the thread ids after calling {operation}, which has already "
                    "deleted the rows that hold them - so it reads nothing and deletes nothing",
                )
                self.assertGreater(
                    delete, min(engine),
                    f"{handler} deletes the threads before {operation} has agreed to anything, so "
                    "a refused reset would still have destroyed the player's rooms",
                )


class DeletingAThreadNeverCostsTheReset(unittest.TestCase):
    def test_the_helper_swallows_what_discord_does(self):
        """The character is gone either way: an exception here could only
        change whether the player is told so."""
        fn = _function(THREADS, "delete_player_threads")
        handled = {
            name.id if isinstance(name, ast.Name) else getattr(name, "attr", "")
            for node in ast.walk(fn) if isinstance(node, ast.ExceptHandler)
            for name in (node.type.elts if isinstance(node.type, ast.Tuple) else [node.type])
            if node.type is not None
        }
        for expected in ("NotFound", "Forbidden", "HTTPException"):
            self.assertIn(
                expected, handled,
                f"delete_player_threads does not handle discord.{expected}, so a thread that is "
                "already gone or that the bot has lost permission on would raise out of a reset "
                "the engine has already committed",
            )

    def test_the_three_outcomes_are_counted_apart(self):
        """Already gone, refused, and deleted are three different things and a
        GM asked to clean up by hand needs to know which."""
        fn = _function(THREADS, "delete_player_threads")
        keys = {
            node.value for node in ast.walk(fn)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertTrue(
            {"deleted", "already_gone", "failed"} <= keys,
            "delete_player_threads stopped counting deleted/already_gone/failed apart",
        )


if __name__ == "__main__":
    unittest.main()

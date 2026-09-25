"""Command use is counted, and reorders nothing (v1.3.5).

The owner asked to see the most used commands, and decided that the numbers
change no order: the daily five stay first on the menu and every page keeps
its authored order. So the rule this file holds has two halves that pull in
opposite directions - every door a player uses a command through must count
it, and nothing that draws a panel may read the count.

The doors are read by AST rather than driven, for `test_seclusion_lockout`'s
reason: the wiring is what fails silently, and a door added later with no
recorder would pass every behavioural test of the recorder itself.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT, install_aiosqlite_shim

install_aiosqlite_shim()

from app.database import Database, OPERATIONAL_REQUIRED_TABLES  # noqa: E402
from app.database import core as dbcore  # noqa: E402

BOT = PROJECT_ROOT / "app" / "bot"


def _load_usage():
    # By path: `app.bot`'s package init builds the runtime, which wants a token.
    spec = importlib.util.spec_from_file_location("xianxia_usage_under_test", BOT / "usage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


usage = _load_usage()
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def run(coro):
    return asyncio.run(coro)


def _function(source: str, name: str) -> ast.AST:
    tree = ast.parse(source)
    node = next((n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
    assert node is not None, f"{name} is not in the source; the reader is broken, not the tree"
    return node


def _calls(node: ast.AST) -> list[str]:
    return [ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)]


class TheRecorderNeverRaisesAndNeverWaits(unittest.TestCase):
    def test_a_database_that_raises_costs_a_log_line(self):
        class Broken:
            async def record_command_use(self, path):
                raise RuntimeError("engine away")
        run(usage.record(Broken(), "/explore"))  # must not raise

    def test_a_blank_path_is_not_a_use(self):
        class Counting:
            def __init__(self):
                self.paths = []
            async def record_command_use(self, path):
                self.paths.append(path)
        db = Counting()
        run(usage.record(db, "   "))
        run(usage.record(db, "/hunt"))
        self.assertEqual(db.paths, ["/hunt"])

    def test_note_fires_and_the_handler_goes_on(self):
        class Slow:
            def __init__(self):
                self.paths = []
            async def record_command_use(self, path):
                await asyncio.sleep(0)
                self.paths.append(path)
        db = Slow()

        async def press():
            usage.note(db, "/mine")
            self.assertEqual(db.paths, [], "the press waited for the counter")
            await asyncio.sleep(0.01)
            self.assertEqual(db.paths, ["/mine"])
        run(press())

    def test_note_outside_a_loop_drops_the_write_rather_than_raising(self):
        class Never:
            async def record_command_use(self, path):
                raise AssertionError("ran without a loop")
        usage.note(Never(), "/mine")


class EveryDoorCounts(unittest.TestCase):
    def test_the_command_tree_counts_a_command_and_not_a_keystroke(self):
        bot = (BOT / "bot.py").read_text(encoding="utf-8")
        check = _function(bot, "interaction_check")
        self.assertIn("usage.note", _calls(check), "the command tree records nothing")
        source = ast.unparse(check)
        self.assertIn("InteractionType.application_command", source,
                      "an autocomplete request shares interaction_check and must not count as a use")
        # After the refusals, not before: a press the world refused is not a use.
        note_line = next(n.lineno for n in ast.walk(check)
                         if isinstance(n, ast.Call) and ast.unparse(n.func) == "usage.note")
        refusals = [n.lineno for n in ast.walk(check)
                    if isinstance(n, ast.Call) and ast.unparse(n.func).endswith(".refuse")]
        self.assertTrue(refusals and max(refusals) < note_line, "the tree counts before it refuses")

    def test_the_hub_press_counts_after_the_panel_gate(self):
        hubs = (BOT / "hubs.py").read_text(encoding="utf-8")
        invoke = _function(hubs, "_invoke_action")
        calls = _calls(invoke)
        self.assertIn("_record_leaf_use", calls)
        record_line = next(n.lineno for n in ast.walk(invoke)
                           if isinstance(n, ast.Call) and ast.unparse(n.func) == "_record_leaf_use")
        gate_line = next(n.lineno for n in ast.walk(invoke)
                         if isinstance(n, ast.Call) and ast.unparse(n.func) == "_panel_refusal")
        self.assertLess(gate_line, record_line, "a refused press was counted")
        surface = (BOT / "surface.py").read_text(encoding="utf-8")
        self.assertIn("register_usage_recorder(", surface, "the surface registers no recorder")

    def test_the_typed_line_counts_a_root_and_a_talk(self):
        typed = (BOT / "typed_play.py").read_text(encoding="utf-8")
        dispatch = _function(typed, "dispatch")
        notes = [n for n in ast.walk(dispatch)
                 if isinstance(n, ast.Call) and ast.unparse(n.func) == "usage.note"]
        self.assertEqual(len(notes), 2, "typed play has a root branch and a talk branch; each counts")
        for call in notes:
            self.assertEqual(ast.unparse(call.args[0]), "DB", "the handle is passed by value")

    def test_a_recorder_that_raises_costs_the_press_nothing(self):
        with patch.dict(os.environ, ENV):
            hubs = importlib.import_module("app.bot.hubs")
        seen = []

        def broken(path):
            seen.append(path)
            raise RuntimeError("no database")
        previous = hubs._USAGE_RECORDER
        try:
            hubs.register_usage_recorder(broken)
            hubs._record_leaf_use("/travel")  # must not raise
        finally:
            hubs.register_usage_recorder(previous)
        self.assertEqual(seen, ["/travel"])


class NothingReorders(unittest.TestCase):
    """The owner's call: count, and change no order."""

    DRAWERS = ("_leaf_actions", "page_actions", "_menu_shape", "menu_shape", "build_menu")

    def test_no_panel_drawer_reads_the_counts(self):
        offenders = []
        for name in ("hubs.py", "surface.py"):
            source = (BOT / name).read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                body = ast.unparse(node)
                if "command_usage_counts" in body or "_USAGE_RECORDER" in body and node.name not in (
                        "register_usage_recorder", "_record_leaf_use"):
                    offenders.append(f"{name}:{node.name}")
        self.assertEqual(offenders, [], "something that draws a panel reads the counts")

    def test_the_counts_have_one_reader_and_it_is_the_gms_card(self):
        readers = []
        for path in sorted(BOT.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("command_usage_counts"):
                    readers.append(path.relative_to(BOT).as_posix())
        self.assertEqual(readers, ["admin/server_setup.py"])


class TheCardShowsThem(unittest.TestCase):
    def _setup(self):
        with patch.dict(os.environ, ENV):
            return importlib.import_module("app.bot.admin.server_setup")

    def test_an_empty_count_says_so(self):
        mod = self._setup()

        class Empty:
            async def command_usage_counts(self):
                return {}
        with patch.object(mod, "DB", Empty()):
            lines = run(mod.most_used_command_lines())
        self.assertIn("none recorded yet", "\n".join(lines))

    def test_the_top_ten_are_printed_most_used_first(self):
        mod = self._setup()
        counts = {f"/cmd{i:02d}": 100 - i for i in range(12)}

        class Some:
            async def command_usage_counts(self):
                return counts
        with patch.object(mod, "DB", Some()):
            lines = run(mod.most_used_command_lines())
        text = "\n".join(lines)
        self.assertIn("`/cmd00` — **100**", text)
        self.assertNotIn("/cmd11", text)
        self.assertIn("and 2 more", text)

    def test_an_unreadable_count_is_unknown_not_zero(self):
        mod = self._setup()

        class Down:
            async def command_usage_counts(self):
                raise ConnectionError("engine away")
        with patch.object(mod, "DB", Down()):
            lines = run(mod.most_used_command_lines())
        self.assertIn("unknown", "\n".join(lines))


class TheTableRoundTrips(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "usage.sqlite3")
        await self.db.init()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_the_table_is_one_the_probe_requires(self):
        self.assertIn("command_usage", OPERATIONAL_REQUIRED_TABLES)

    async def test_presses_add_up_by_path(self):
        for path in ("/explore", "/explore", "/hunt"):
            await self.db.record_command_use(path)
        self.assertEqual(await self.db.command_usage_counts(), {"/explore": 2, "/hunt": 1})

    async def test_the_window_is_thirty_days_and_the_cleanup_prunes_past_it(self):
        now = 1_800_000_000.0
        with patch("time.time", return_value=now):
            await self.db.record_command_use("/today")
            async with self.db._connect() as conn:
                for age, path in ((5, "/recent"), (31, "/old")):
                    await conn.execute(
                        "INSERT INTO command_usage(path, day, presses) VALUES(?,?,7)",
                        (path, dbcore._usage_day(now - age * 86400)),
                    )
                await conn.commit()
            self.assertEqual(set(await self.db.command_usage_counts()), {"/today", "/recent"})
            counts = await self.db.maintenance_cleanup(0)
            self.assertEqual(counts.get("command_usage"), 1, "the cleanup did not prune the 31-day-old row")
            async with self.db._connect() as conn:
                cur = await conn.execute("SELECT path FROM command_usage ORDER BY path")
                left = [r[0] for r in await cur.fetchall()]
        self.assertEqual(left, ["/recent", "/today"])

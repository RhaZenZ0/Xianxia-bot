"""What a rebirth wipes must be tables the bot really builds (v1.12.3).

`reincarnateAction` clears the incarnation-scoped tables from two lists of names
in `lifecycle_actions.go` (`incarnationScopedTables` and, for the tables a later
schema added, `incarnationScopedLaterTables`). The Go tests build their schema
by hand, so a migration that renames one of those tables breaks no build and
fails no Go test: the later list skips a table that is not there, and the first
list errors only at the rebirth itself. A flame (schema 70) and a spirit sense
(schema 71) were each missing from the list for exactly that reason - nothing
compared it with the real schema.

It belongs in Python for `test_character_reset.py`'s reason: only here is the
real schema available to ask. It holds that every name in both lists is a table
the bootstrap makes, with the `user_id` column the wipe deletes by, and that the
two tables a rebirth used to keep are named.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT

LIFECYCLE_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "lifecycle_actions.go").read_text(encoding="utf-8")


def _list(name: str) -> list[str]:
    match = re.search(rf"var {name} = \[\]string\{{(.*?)\}}", LIFECYCLE_GO, re.S)
    if not match:
        raise AssertionError(f"{name} is not a string-slice literal any more")
    names = re.findall(r'"([a-z_0-9]+)"', match.group(1))
    if not names:
        raise AssertionError(f"{name} parsed to nothing; the reader is broken, not the tree")
    return names


class TheRebirthWipesRealTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        path = Path(cls._dir.name) / "schema.sqlite3"
        env = {
            **os.environ,
            "DISCORD_TOKEN": "test-token",
            "GUILD_ID": "123456789012345678",
            "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
            "DATABASE_PATH": str(path),
        }
        result = subprocess.run(
            [sys.executable, "-m", "app.database.bootstrap"],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"could not bootstrap the schema: {result.stderr[-2000:]}")
        db = sqlite3.connect(path)
        cls.columns: dict[str, set[str]] = {}
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            cls.columns[table] = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def test_the_reader_found_both_lists(self):
        self.assertGreater(len(_list("incarnationScopedTables")), 30, "the reader found too few tables; it is broken, not the tree")
        self.assertIn("inventory", _list("incarnationScopedTables"))

    def test_every_table_a_rebirth_wipes_exists_and_is_keyed_by_user(self):
        for name in ("incarnationScopedTables", "incarnationScopedLaterTables"):
            for table in _list(name):
                with self.subTest(list=name, table=table):
                    self.assertIn(table, self.columns, f"{table} is wiped by a rebirth and is not a table the bootstrap makes")
                    self.assertIn("user_id", self.columns[table], f"{table} has no user_id for the wipe to delete by")

    def test_the_flame_and_the_spirit_sense_are_wiped(self):
        later = _list("incarnationScopedLaterTables")
        for table in ("character_flames", "character_spirit_sense"):
            self.assertIn(table, later, f"{table} outlives a rebirth: it opens the top grade of a craft in a body that never made it")


if __name__ == "__main__":
    unittest.main()

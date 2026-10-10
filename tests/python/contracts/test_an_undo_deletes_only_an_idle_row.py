"""A row a lever made is deleted by its undo only when nothing is on it.

`admin.audit.undo_last` reverses a GM lever. The perfection, trade-rank and Law
levers can each *make* the row they write to, and the player goes on playing on
that row: starts the path on it, crafts on it, sits with the Law. An undo that
deleted the row took all of that with it - the quests a path had walked, the
successes an examination reads, the sittings a samsara echo reads.

So the one statement allowed to remove such a row is `deleteIfIdle`
(`go_core/internal/game/admin_undo_rows.go`), and it removes the row only when
every column is back at the value the DDL gives a row nobody has touched. Those
values are three Go lists, and a list is exactly the thing that goes stale: a
column added to `realm_perfection`, `profession_progress` or `law_progress` and
missed in its list would be deleted with the row whatever play had put in it,
and no Go test would notice, because the Go fixtures build their tables by hand
and would not carry the new column either.

That is what this holds, and it belongs in Python for `test_character_reset.py`'s
reason: only here is the schema the bot actually builds available to ask.
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

ROWS_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "admin_undo_rows.go").read_text(encoding="utf-8")

# Which tables each list stands for. A list with no table here is a list nobody
# holds to the schema, which is the fault.
LIST_TABLES = {
    "perfectionRowDefaults": ("realm_perfection", "body_realm_perfection"),
    "professionRowDefaults": ("profession_progress",),
    "lawRowDefaults": ("law_progress",),
}

# Written by every statement that touches the row, never part of "idle".
NOT_STATE = {"updated_at"}


def row_default_lists() -> dict[str, dict[str, str]]:
    """Each `var xRowDefaults = []rowDefault{...}` as column -> the DDL's dflt_value text."""
    lists: dict[str, dict[str, str]] = {}
    for name, body in re.findall(r"var (\w+) = \[\]rowDefault\{(.*?)\n\}", ROWS_GO, re.S):
        entries: dict[str, str] = {}
        for column, integer, text in re.findall(r'\{"(\w+)",\s*(?:int64\((-?\d+)\)|"([^"]*)")\}', body):
            entries[column] = integer if integer != "" else f"'{text}'"
        lists[name] = entries
    return lists


class AnIdleRowIsWhatTheSchemaSays(unittest.TestCase):
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
        # table -> {column: dflt_value}, for the state columns only: not a key
        # and not the timestamp every write stamps.
        cls.state: dict[str, dict[str, str | None]] = {}
        for table in {t for tables in LIST_TABLES.values() for t in tables}:
            rows = list(db.execute(f'PRAGMA table_info("{table}")'))
            cls.state[table] = {
                row[1]: row[4] for row in rows if row[5] == 0 and row[1] not in NOT_STATE
            }
        db.close()
        cls.lists = row_default_lists()
        # The reader is asserted before it is trusted: against an empty parse
        # every check below would pass by finding nothing to disagree with, so
        # it fails the whole class here, ahead of any of them.
        if set(cls.lists) != set(LIST_TABLES):
            raise AssertionError(
                "the reader found no rowDefault list, or a list this gate does not know; "
                f"found {sorted(cls.lists)}; the gate is broken, not the tree"
            )
        if "completed" not in cls.lists["perfectionRowDefaults"]:
            raise AssertionError(
                "perfectionRowDefaults no longer names `completed`: a perfected realm would be idle and deleted"
            )

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def test_the_schema_actually_bootstrapped(self):
        for table, columns in self.state.items():
            self.assertGreaterEqual(len(columns), 2, f"the bootstrapped schema holds too few state columns for {table}")

    def test_every_list_is_mapped_to_a_table(self):
        for name in self.lists:
            self.assertIn(name, LIST_TABLES, f"{name} is a row-default list no table is held against")

    def test_each_list_names_every_state_column_of_its_tables(self):
        for name, tables in LIST_TABLES.items():
            for table in tables:
                with self.subTest(list=name, table=table):
                    missing = set(self.state[table]) - set(self.lists[name])
                    extra = set(self.lists[name]) - set(self.state[table])
                    self.assertFalse(
                        missing,
                        f"{table} has column(s) {sorted(missing)} that {name} does not name, so a row with a value "
                        "there would still be called idle and deleted by an undo",
                    )
                    self.assertFalse(extra, f"{name} names {sorted(extra)}, which {table} does not have")

    def test_each_default_is_the_one_the_ddl_gives(self):
        for name, tables in LIST_TABLES.items():
            for table in tables:
                for column, want in self.lists[name].items():
                    with self.subTest(list=name, table=table, column=column):
                        self.assertEqual(
                            self.state[table].get(column), want,
                            f"{name} says an idle {table}.{column} is {want}, but the DDL gives "
                            f"{self.state[table].get(column)}; an undo would call a touched row idle",
                        )


if __name__ == "__main__":
    unittest.main()

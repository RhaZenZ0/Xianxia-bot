"""What bounds a reset must exist in the database the bot actually builds.

`character.reset` (v1.0.1) lets a player abandon a life they have only just
begun without a GM. Two things bound it, and both are rows rather than code:
the reset's own count is read from `event_log`, and the engine's own bookkeeping
is kept out of the sweep so the framework can still record the action that erased
the actor.

Every one of those is a "table.column" string inside `characterResetKeep`, ANDed
onto a DELETE that is generated from the live schema. So a migration that
renames one of those tables does not break the build, does not error at runtime,
and does not fail a single Go test - the fixtures in that package build their
schema by hand and would go on carrying the old name. It silently deletes what
the keep was protecting, which for `event_log` means **the count of resets stops
counting and answers 1 for ever**, and for the two authoritative tables means a
reset that fails with `stale expected_version` after doing its work.

That is what this closes, and it belongs in Python for the same reason
`test_privacy_erasure.py` does: only here is the real schema available to ask.
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

RESET_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "character_reset.go").read_text(encoding="utf-8")
PRIVACY_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "privacy_actions.go").read_text(encoding="utf-8")


def keep_entries() -> dict[str, str]:
    """The "table.column" -> predicate pairs of characterResetKeep."""
    match = re.search(r"func characterResetKeep\(\) map\[string\]string \{\s*return map\[string\]string\{(.*?)\n\t\}", RESET_GO, re.S)
    if not match:
        raise AssertionError("characterResetKeep is not a map literal any more")
    body = match.group(1)
    entries = dict(re.findall(r'"([a-z_]+\.[a-z_]+)":\s*(.+?),\n', body))
    if not entries:
        raise AssertionError("characterResetKeep parsed to nothing; the reader is broken, not the tree")
    return entries


def subject_columns() -> set[str]:
    match = re.search(r"var erasureSubjectColumns = map\[string\]bool\{(.*?)\n\}", PRIVACY_GO, re.S)
    if not match:
        raise AssertionError("erasureSubjectColumns is not a map literal any more")
    return set(re.findall(r'"([a-z_0-9]+)":\s*true', match.group(1)))


class WhatBoundsAResetIsInTheRealSchema(unittest.TestCase):
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
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        cls.columns: dict[str, set[str]] = {}
        for (table,) in tables:
            cls.columns[table] = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def test_the_schema_actually_bootstrapped(self):
        # Guards every check below: against an empty database they would all
        # pass by finding nothing to disagree with.
        self.assertGreater(len(self.columns), 150, "the bootstrapped schema is too small to be real")
        self.assertIn("event_log", self.columns, "the reader found no event_log; the sweep is broken, not the tree")

    def test_every_kept_column_exists_in_the_real_schema(self):
        for key in keep_entries():
            table, column = key.split(".", 1)
            with self.subTest(key=key):
                self.assertIn(table, self.columns, f"characterResetKeep protects {key}, but no such table is built")
                self.assertIn(column, self.columns[table], f"characterResetKeep protects {key}, but that column is gone")

    def test_every_kept_column_is_one_the_sweep_would_otherwise_delete(self):
        """A keep on a column erasure never touches protects nothing at all."""
        subjects = subject_columns()
        for key in keep_entries():
            _, column = key.split(".", 1)
            with self.subTest(key=key):
                self.assertIn(
                    column, subjects,
                    f"{key} is kept from the reset, but {column!r} is not an erasure subject column, "
                    "so the sweep would never have deleted it and the keep is decoration",
                )

    def test_the_count_is_read_from_the_table_it_is_kept_in(self):
        """The count and the keep must name the same place.

        They are two statements a file apart - `characterResetsUsedTx` selects
        from a table and `characterResetKeep` protects one - and if they ever
        named different tables the count would be read from rows the reset had
        just deleted, and would answer 1 for ever.
        """
        select = re.search(r"characterResetsUsedTx.*?FROM (\w+) WHERE", RESET_GO, re.S)
        self.assertIsNotNone(select, "characterResetsUsedTx no longer selects from anywhere")
        counted = select.group(1)
        kept = {key.split(".", 1)[0] for key in keep_entries()}
        self.assertIn(
            counted, kept,
            f"the reset count is read from {counted}, which the reset does not keep",
        )


if __name__ == "__main__":
    unittest.main()

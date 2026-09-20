"""A column a migration adds is the migration's alone (v1.0.0-rc.57).

v1.0.0-rc.56 added `seclusion_sessions.ends_real_ts` in two places: the base
`executescript` DDL in `Database.init`, and migration 57's
`ALTER TABLE ... ADD COLUMN`. The base script runs on every boot, *before* the
migrations, so on a **fresh** database it created the table already carrying the
column and migration 57 then failed with `duplicate column name`. Bootstrapping
a new world was impossible; upgrading an existing one worked, because there the
`CREATE TABLE IF NOT EXISTS` is a no-op and the ALTER has something to do.

Nothing caught it, for a reason worth keeping:

* the migration runner **has** a guard for a duplicate-column ALTER, but it
  named `sqlite3.OperationalError` only. That is what local aiosqlite raises -
  the transport every pytest fixture uses. Production goes through the Go
  engine, which wraps the same SQLite message in a `RemoteDatabaseError`, so the
  guard was live in every test and dead in every deployment. It catches both
  now, but a guard is not a licence: the convention below is what keeps
  migrations honest, and the guard is for the recovery case its own comment
  describes.
* `test_startup_health.py` bootstraps a fresh database and **passed**, because
  it bootstraps it over the local transport. A fixture that cannot fail the way
  production fails is not testing production - this repo's own rule, and here it
  was the migration runner itself.

So the check is the convention, not the guard: all 59 `ADD COLUMN` migrations
that preceded rc.56 named a column the base DDL does not, and that is the rule.
"""
from __future__ import annotations

import re
import unittest

from tests.support import PROJECT_ROOT

CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")


def _migrations_and_base() -> tuple[str, str]:
    """The SCHEMA_MIGRATIONS tuple, and everything else.

    The split is not `CORE[:start]`: `SCHEMA_MIGRATIONS` is declared near the
    top of the module (line ~232) and `Database.init`'s base DDL is ~2,500
    lines *below* it, so slicing at the tuple leaves the DDL out entirely and
    the check silently sees no tables at all. The first version of this gate
    did exactly that and passed against the broken tree.
    """
    start = CORE.index("SCHEMA_MIGRATIONS: tuple")
    end = CORE.index("\nclass _ObservedCursor")
    return CORE[start:end], CORE[:start] + CORE[end:]


def _base_columns(base: str, table: str) -> set[str]:
    """The columns the base DDL's `CREATE TABLE` for `table` declares.

    Per table, not per file: a bare search for the column name matches `tier`
    or `location` on any of 169 tables and reports six clashes that are not
    clashes.
    """
    columns: set[str] = set()
    for block in re.finditer(
        rf"CREATE TABLE (?:IF NOT EXISTS )?{table}\s*\((.*?)\n\s*\);", base, re.S | re.I
    ):
        for line in block.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            declared = re.match(r"(\w+)\s+(INTEGER|REAL|TEXT|BLOB|NUMERIC)\b", line, re.I)
            if declared:
                columns.add(declared.group(1).lower())
    return columns


class AMigrationsColumnIsTheMigrationsAlone(unittest.TestCase):
    def test_no_added_column_is_also_in_the_base_ddl(self):
        migrations, base = _migrations_and_base()
        added = re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", migrations)
        self.assertGreaterEqual(len(added), 59, "the ADD COLUMN migrations could not be read")
        # The reader works: a table the base DDL really does declare must come
        # back with columns, or a broken split would make every check vacuous.
        self.assertIn("ends_game_minute", _base_columns(base, "seclusion_sessions"))
        clashes = sorted({f"{t}.{c}" for t, c in added if c.lower() in _base_columns(base, t)})
        self.assertEqual(
            clashes, [],
            "a migration adds a column the base DDL already creates, so a FRESH database "
            "fails on 'duplicate column name' while an upgrade succeeds. Remove it from the "
            "base executescript and leave the migration as its only writer.",
        )

    def test_the_duplicate_column_guard_sees_the_production_transport(self):
        """The guard is not the fix for the rule above, but it must at least
        work where it matters: db-init and the bot reach SQLite through the Go
        engine, and that transport raises its own error type."""
        guard = CORE[CORE.index("async def _run_schema_migrations"):]
        guard = guard[: guard.index("\n    async def ") if "\n    async def " in guard else len(guard)]
        self.assertIn("duplicate column name", guard, "the guard is gone")
        handler = re.search(r"except \(([^)]+)\) as exc:", guard)
        self.assertIsNotNone(handler, "the duplicate-column guard no longer catches a tuple of errors")
        caught = {name.strip() for name in handler.group(1).split(",")}
        self.assertIn("sqlite3.OperationalError", caught, "the local transport's error")
        self.assertIn("RemoteDatabaseError", caught,
                      "the Go transport's error - the one production actually raises")


if __name__ == "__main__":
    unittest.main()

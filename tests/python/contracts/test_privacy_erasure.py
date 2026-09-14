"""Every column that names a person must have an erasure decision attached.

`admin.player.erase` discovers its targets from the live schema rather than
from a list, which is the only way it can still be right after the next
migration. That design has one hole: a column holding a Discord id under a
*name nobody has seen before* would be discovered by neither the subject set
nor anything else, and would silently retain data forever.

This is the check that closes it, and it belongs in Python because the Go
package's own fixtures build a schema by hand - they could only ever prove the
fixture is classified. Here the real database is bootstrapped (all 182 tables of
schema 46), introspected, and held against the maps in
`go_core/internal/game/privacy_actions.go`.

It fails in the direction that matters. A new table with a `user_id` needs no
change - the subject set already covers that name, and the erasure will find
it. What fails the build is a column named something *new* that looks like a
person, because that is exactly the case a human has to rule on: is this the
subject (delete), shared world state (anonymise), or the audit trail (keep)?
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

PRIVACY_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "privacy_actions.go").read_text(encoding="utf-8")

# How this test recognises "a column that names a person". Deliberately wider
# than the subject set: the whole job is to catch names nobody has classified.
# `_count`/`_total` are excluded because "user_count" is a number, not a person.
LOOKS_LIKE_A_PERSON = re.compile(r"(^|_)user(_id|_a|_b)?$|^actor_id$|_user_id$")
NOT_A_PERSON = re.compile(r"count|total|limit|cap$|_num$")


def go_map_keys(name: str) -> set[str]:
    """The "table.column" keys of one map literal in privacy_actions.go."""
    match = re.search(rf"var {name} = map\[string\]string\{{(.*?)\n\}}", PRIVACY_GO, re.S)
    if not match:
        raise AssertionError(f"{name} is not a map literal in privacy_actions.go any more")
    return set(re.findall(r'"([a-z_]+\.[a-z_]+)":', match.group(1)))


def go_subject_columns() -> set[str]:
    match = re.search(r"var erasureSubjectColumns = map\[string\]bool\{(.*?)\n\}", PRIVACY_GO, re.S)
    if not match:
        raise AssertionError("erasureSubjectColumns is not a map literal any more")
    return set(re.findall(r'"([a-z_0-9]+)":\s*true', match.group(1)))


class TheRealSchemaIsFullyClassified(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Bootstrap the real database once and read its shape."""
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
        cls.columns: list[tuple[str, str, bool]] = []
        tables = db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for table, sql in tables:
            if sql and sql.strip().upper().startswith("CREATE VIRTUAL"):
                continue
            for column in db.execute(f'PRAGMA table_info("{table}")'):
                cls.columns.append((table, column[1], bool(column[3])))
        cls.table_count = len(tables)
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def person_columns(self) -> list[tuple[str, str, bool]]:
        return [
            (table, column, notnull)
            for table, column, notnull in self.columns
            if LOOKS_LIKE_A_PERSON.search(column) and not NOT_A_PERSON.search(column)
        ]

    def test_the_schema_actually_bootstrapped(self):
        # Guards the rest of the class: if the bootstrap silently produced an
        # empty database, every check below would pass by finding nothing.
        self.assertGreater(self.table_count, 150, "the bootstrapped schema is too small to be real")

    def test_every_column_that_names_a_person_has_a_decision(self):
        subjects = go_subject_columns()
        unclassified = sorted({
            f"{table}.{column}"
            for table, column, _ in self.person_columns()
            if column not in subjects
        })
        self.assertEqual(
            unclassified, [],
            "These columns hold a Discord id and no erasure decision:\n  "
            + "\n  ".join(unclassified)
            + "\n\nAdd the column name to erasureSubjectColumns in "
            "go_core/internal/game/privacy_actions.go (and, if the row is shared "
            "world state or the audit trail, to erasureAnonymise or erasureKeep). "
            "Leaving it out means a player's erasure silently misses it.",
        )

    def test_the_exception_maps_name_columns_that_exist(self):
        # An exemption for a table that has been renamed away is worse than no
        # exemption: it reads as a considered decision and does nothing.
        live = {f"{table}.{column}" for table, column, _ in self.columns}
        for name in ("erasureKeep", "erasureAnonymise"):
            with self.subTest(map=name):
                for key in sorted(go_map_keys(name)):
                    self.assertIn(
                        key, live,
                        f"{name} exempts {key}, which no longer exists in the schema",
                    )

    def test_a_not_null_exemption_cannot_be_anonymised_with_null(self):
        """The bug this pins would abort a real player's erasure halfway.

        An anonymised column that is NOT NULL has to take the sentinel instead.
        The Go side reads nullability from the schema at runtime, so this only
        has to prove the schema still agrees with that being necessary.
        """
        notnull = {f"{t}.{c}" for t, c, nn in self.columns if nn}
        anonymised = go_map_keys("erasureAnonymise")
        constrained = sorted(anonymised & notnull)
        self.assertIn(
            "player_families.founder_user_id", constrained,
            "player_families.founder_user_id stopped being NOT NULL - if that is "
            "deliberate the note in erasureAnonymise should be updated",
        )
        self.assertIn(
            "NotNull", PRIVACY_GO,
            "the erasure no longer reads nullability from the schema, so a NOT NULL "
            "anonymise column would fail the whole erasure",
        )

    def test_the_audit_trail_is_the_only_thing_kept(self):
        self.assertEqual(
            go_map_keys("erasureKeep"), {"admin_audit_log.admin_user_id"},
            "something other than the audit trail is being kept through an erasure - "
            "that needs saying in docs/PRIVACY.md, not just in the code",
        )

    def test_the_content_bearing_tables_are_erased_rather_than_anonymised(self):
        """The tables that actually hold what a player wrote must be deleted.

        Anonymising these would leave the text in place with the name filed off,
        which is not what anyone asking for their data back means.
        """
        anonymised = go_map_keys("erasureAnonymise")
        live = {f"{table}.{column}" for table, column, _ in self.columns}
        for key in ("scene_history.user_id", "rag_memories.user_id", "characters.user_id"):
            with self.subTest(key=key):
                self.assertIn(key, live, f"{key} is gone from the schema; this test needs rewriting")
                self.assertNotIn(key, anonymised, f"{key} carries player-written text and must be deleted")


class ErasureIsReachableAndAudited(unittest.TestCase):
    def test_it_is_wired_into_the_action_dispatch(self):
        actions = (PROJECT_ROOT / "go_core" / "internal" / "game" / "actions.go").read_text(encoding="utf-8")
        self.assertIn('case "admin.player.erase":', actions)
        self.assertIn("adminErasePlayer(conn, req.ActorID, req.Payload)", actions)

    def test_it_writes_to_the_admin_audit_log(self):
        # The repo's rule for every Admin Console action (CLAUDE.md), and here
        # it is also the evidence that a request was honoured.
        self.assertIn('auditAdmin(conn, adminUserID, "admin.player.erase"', PRIVACY_GO)

    def test_the_audit_row_carries_counts_rather_than_the_erased_content(self):
        # A record of an erasure that quotes the erased data is not an erasure.
        audit_call = PRIVACY_GO[PRIVACY_GO.index('auditAdmin(conn, adminUserID, "admin.player.erase"'):]
        audit_call = audit_call[:audit_call.index("); err != nil")]
        for forbidden in ("name", "content", "summary", "location"):
            self.assertNotIn(
                f'"{forbidden}"', audit_call,
                f"the erasure audit row carries {forbidden!r} from the erased data",
            )
        self.assertIn("rows_deleted", audit_call)


if __name__ == "__main__":
    unittest.main()

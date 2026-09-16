"""The content file as tables (schema 51), and who is allowed to write them.

Nine `content_*` tables mirror content/world.json with real columns. They are
written by the engine alone - `go_core/internal/contentsync` - from the file
itself, hash-gated, in one transaction, with deletes; every row still carries
the entry's exact bytes in `data_json`, and the typed columns beside it are a
projection. The projection is defined once, in Go (`contentsync.Sections`),
and the Python migration that creates the tables is held to it here, so a
column added on one side without the other fails a test rather than boot.

The other half is ordering. The tables are filled by the engine, but created
by Python's migration, which in the compose stack runs after the engine is
already healthy - so the engine's own apply at start finds nothing to write.
db-init calls `/v1/content/sync` the moment the migration has run and the bot
calls it again at CATALOG_READY; that is what puts rows in the tables before
any reader, in every boot order, and it is asserted here rather than hoped.
"""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

from app.database import OPERATIONAL_REQUIRED_TABLES
from app.database.core import CONTENT_FOR_CATALOG, Database, content_table_for
from tests.support import PROJECT_ROOT

GO = PROJECT_ROOT / "go_core" / "internal"
CONTENTSYNC = (GO / "contentsync" / "contentsync.go").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")

CONTENT_TABLES = (
    "content_npcs", "content_locations", "content_items", "content_recipes", "content_sects",
    "content_shops", "content_merchants", "content_manuals", "content_techniques",
)


def go_projection() -> dict[str, dict[str, str]]:
    """table -> {column: SQL type}, read off `contentsync.Sections`."""
    out: dict[str, dict[str, str]] = {}
    blocks = re.split(r'\{Table: "', CONTENTSYNC)[1:]
    for block in blocks:
        table = block.split('"', 1)[0]
        body = block.split("}},", 1)[0]
        cols = {}
        for name, _key, kind in re.findall(r'\{"(\w+)", "([\w.]+)", (Text|Integer|Flag)\}', body):
            cols[name] = "TEXT" if kind == "Text" else "INTEGER"
        out[table] = cols
    return out


def migration_columns(table: str) -> dict[str, str]:
    """column -> declared type, read off the CREATE TABLE in the migration."""
    match = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r" \((.*?)\n\s*\)", CORE, re.S)
    assert match, f"no CREATE TABLE for {table} in the migration"
    cols = {}
    for piece in match.group(1).split(","):
        piece = piece.strip()
        if not piece:
            continue
        name, typ = piece.split()[0], piece.split()[1]
        cols[name] = typ
    return cols


class TheProjectionIsDefinedOnce(unittest.TestCase):
    def test_the_migration_creates_exactly_the_columns_go_writes(self):
        projection = go_projection()
        self.assertEqual(set(projection), set(CONTENT_TABLES), "the nine tables, no more, no fewer")
        for table, go_cols in projection.items():
            with self.subTest(table=table):
                ddl = migration_columns(table)
                fixed = {"name": "TEXT", "data_json": "TEXT", "updated_at": "REAL"}
                self.assertEqual(
                    ddl, {**fixed, **go_cols},
                    f"{table}: the migration's columns must be exactly name + the Go projection + data_json/updated_at",
                )

    def test_the_ghost_road_fields_are_projected(self):
        """The plan called these the canary: `deathQiGroundMultiplier` prices a
        location by road_site, district and settlement_type, and a projection
        that dropped one would flatten the whole path to 1.0 without an error.
        Go reads its own parsed catalogue for that rule, so the tables are not
        on that path - but the columns are what a dashboard or a query uses to
        ask 'which locations are ruins', and they are held here by name."""
        cols = go_projection()["content_locations"]
        for field in ("road_site", "district", "settlement_type"):
            self.assertIn(field, cols)

    def test_every_content_table_is_in_the_readiness_probe(self):
        for table in CONTENT_TABLES:
            self.assertIn(table, OPERATIONAL_REQUIRED_TABLES)


class OnlyTheEngineWrites(unittest.TestCase):
    def test_no_python_writes_a_content_table(self):
        for path in (PROJECT_ROOT / "app").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for verb in ("INSERT INTO content_", "DELETE FROM content_", "UPDATE content_"):
                self.assertNotIn(verb, source, f"{path.relative_to(PROJECT_ROOT)} writes a content table; only contentsync may")

    def test_the_engine_owns_the_apply_on_all_three_doors(self):
        server = (GO / "server" / "server.go").read_text(encoding="utf-8")
        self.assertIn('mux.HandleFunc("/v1/content/sync", s.contentSync)', server)
        self.assertIn("contentsync.ApplyPath(databasePath, worldPath", server, "the guarded apply at engine start")
        actions = (GO / "game" / "actions.go").read_text(encoding="utf-8")
        self.assertIn('case "admin.content.reload":', actions)
        reload = (GO / "game" / "content_actions.go").read_text(encoding="utf-8")
        self.assertIn("contentsync.ApplyInTx(conn, worldPath", reload, "the admin path applies inside its own transaction")
        self.assertIn('auditAdmin(conn, adminUserID, "admin.content.reload"', reload)
        self.assertLess(reload.index("ApplyInTx"), reload.index("auditAdmin"))
        self.assertLess(reload.index("auditAdmin"), reload.index("conn.Commit()"), "rows and their audit row land in one commit")


class TheTablesAreFullBeforeAnyoneReads(unittest.TestCase):
    def test_db_init_syncs_the_moment_the_migration_has_run(self):
        bootstrap = (PROJECT_ROOT / "app" / "database" / "bootstrap.py").read_text(encoding="utf-8")
        body = bootstrap[bootstrap.index("async def bootstrap("):]
        self.assertLess(body.index("await database.init()"), body.index("await database.sync_content()"))

    def test_the_bot_syncs_before_it_counts(self):
        bot = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.assertLess(bot.index("await DB.sync_world_catalog(WORLD.data)"), bot.index("await DB.sync_content()"))
        self.assertLess(bot.index("await DB.sync_content()"), bot.index("await DB.catalog_counts()"))

    def test_sync_content_is_one_post_to_the_sync_endpoint(self):
        remote = (PROJECT_ROOT / "app" / "database" / "remote.py").read_text(encoding="utf-8")
        self.assertIn('/v1/content/sync"', remote)
        body = remote[remote.index("async def sync_content"):]
        body = body[:body.index("\n    async def ")]
        self.assertIn("X-Xianxia-Engine-Token", body)
        self.assertIn(".post(", body)


class ReadersFollowTheEngine(unittest.TestCase):
    def test_engine_backed_reads_go_to_content_and_local_reads_stay_on_catalog(self):
        for catalog, content in CONTENT_FOR_CATALOG.items():
            with self.subTest(catalog=catalog):
                self.assertEqual(content_table_for(catalog, True), content)
                self.assertEqual(content_table_for(catalog, False), catalog)
        with self.assertRaises(ValueError):
            content_table_for("characters", True)

    def test_the_database_switch_reads_the_transport(self):
        # A real directory: Database.__init__ creates the parent, and a path
        # under / is only creatable by root - which the first version of this
        # test was, locally, and CI's runner is not.
        with tempfile.TemporaryDirectory() as scratch:
            db = Database(Path(scratch) / "switch.sqlite3")
            self.assertIsNone(db._go_transport)
            self.assertEqual(db.content_table("catalog_npcs"), "catalog_npcs")
            db._go_transport = object()  # any engine-backed transport
            self.assertEqual(db.content_table("catalog_npcs"), "content_npcs")

    def test_every_catalogue_reader_resolves_through_the_switch(self):
        tree = ast.parse(CORE)
        for name in ("_catalog_get", "search_catalog", "catalog_counts"):
            node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            calls = {c.func.attr for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
            with self.subTest(reader=name):
                self.assertIn("content_table", calls, f"{name} must pick its table through content_table()")

    def test_the_dashboard_uses_the_same_switch(self):
        server = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("FROM catalog_npcs", server)
        self.assertNotIn("FROM catalog_locations", server)
        self.assertIn("content_table('catalog_npcs')", server)
        self.assertIn("content_table('catalog_locations')", server)


class TheGMSyncTellsTheTruth(unittest.TestCase):
    def test_it_re_reads_the_file_on_both_sides(self):
        ops = (PROJECT_ROOT / "app" / "bot" / "admin" / "world_ops.py").read_text(encoding="utf-8")
        branch = ops[ops.index('if action.value=="sync":'):]
        branch = branch[:branch.index('if action.value=="vacuum":')]
        self.assertIn("World(WORLD.content_path)", branch, "a fresh parse from disk, not the in-memory copy")
        self.assertNotIn("sync_world_catalog(WORLD.data)", branch)
        self.assertIn('"admin.content.reload"', branch)
        self.assertIn("next restart", branch, "it says what does not apply live")


if __name__ == "__main__":
    unittest.main()

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
from app.database.core import SCHEMA_MIGRATIONS, Database
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
        self.assertLess(bot.index("await DB.seed_world_territories(WORLD.data)"), bot.index("await DB.sync_content()"))
        self.assertLess(bot.index("await DB.sync_content()"), bot.index("await DB.catalog_counts()"))

    def test_sync_content_is_one_post_to_the_sync_endpoint(self):
        remote = (PROJECT_ROOT / "app" / "database" / "remote.py").read_text(encoding="utf-8")
        self.assertIn('/v1/content/sync"', remote)
        body = remote[remote.index("async def sync_content"):]
        body = body[:body.index("\n    async def ")]
        self.assertIn("X-Xianxia-Engine-Token", body)
        self.assertIn(".post(", body)


RETIRED_MIRRORS = (
    "catalog_locations",
    "catalog_npcs",
    "catalog_recipes",
    "catalog_manuals",
    "catalog_techniques",
)


class ThereIsOneCatalogueAndTheEngineWritesIt(unittest.TestCase):
    """v1.0.0-rc.40: the five Python-written mirrors are gone.

    Schema 51 shipped `content_*` beside them and picked between the two with
    `content_table_for(table, engine_backed)`, because pytest has no engine to
    fill the new tables. Migration 52 drops the mirrors: production has one
    writer and one reader path, and a local run seeds the same tables from the
    fixture. These tests are the gate for the switch staying gone.
    """

    def test_no_running_code_names_a_retired_mirror(self):
        """The names survive in exactly one place: the migration list.

        A historical migration is how an old database walks forward, so the
        CREATE statements that made these tables stay where they are and
        migration 52's DROPs sit beside them - the same rule migration 44
        followed for the unused core ledger. Everywhere else, naming one of
        these tables now means reading a table that is not there.
        """
        offenders = []
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")) + sorted((PROJECT_ROOT / "scripts").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            body = path.read_text(encoding="utf-8")
            if path.name == "core.py":
                head, _, rest = body.partition("SCHEMA_MIGRATIONS")
                _, _, tail = rest.partition("\n)\n\n")  # the list's closing paren
                body = head + tail
            body = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
            for table in RETIRED_MIRRORS:
                if table in body:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {table}")
        self.assertEqual(offenders, [], f"retired catalogue mirrors still referenced: {offenders}")

    def test_the_switch_itself_is_gone(self):
        self.assertNotIn("content_table_for", CORE)
        self.assertNotIn("CONTENT_FOR_CATALOG", CORE)
        server = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("content_table_for", server)
        self.assertNotIn("def content_table", server)

    def test_every_catalogue_reader_names_a_content_table(self):
        tree = ast.parse(CORE)
        wanted = {
            "_catalog_get": "content_",
            "search_catalog": "content_",
            "catalog_counts": "content_",
        }
        for name, prefix in wanted.items():
            node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            literals = {
                c.value for c in ast.walk(node)
                if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value.startswith("cat")
            }
            with self.subTest(reader=name):
                self.assertEqual(literals, set(), f"{name} still names a catalog_* table")
                self.assertNotIn("content_table(", ast.get_source_segment(CORE, node) or "")
        # And the callers that pick the table for _catalog_get name content_*.
        for getter in ("get_location_definition", "get_npc_definition", "get_recipe_definition"):
            node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == getter)
            source = ast.get_source_segment(CORE, node) or ""
            with self.subTest(getter=getter):
                self.assertIn('"content_', source)

    def test_the_dashboard_reads_the_content_tables_directly(self):
        server = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
        self.assertIn("FROM content_npcs", server)
        self.assertIn("FROM content_locations", server)

    def test_the_readiness_probe_no_longer_expects_them(self):
        for table in RETIRED_MIRRORS:
            self.assertNotIn(table, OPERATIONAL_REQUIRED_TABLES)

    def test_migration_52_drops_each_of_them(self):
        # The migration drill proves nothing else vanished with them, across
        # every historical schema. This is the direct check that the drop is
        # there at all, and that its name says what it does.
        version, name, statements = next(m for m in SCHEMA_MIGRATIONS if m[0] == 52)
        self.assertEqual((version, name), (52, "retire_python_catalog_mirrors"))
        joined = "\n".join(statements)
        for table in RETIRED_MIRRORS:
            self.assertIn(f"DROP TABLE IF EXISTS {table}", joined)


class TheGMSyncTellsTheTruth(unittest.TestCase):
    def test_it_re_reads_the_file_on_both_sides(self):
        ops = (PROJECT_ROOT / "app" / "bot" / "admin" / "world_ops.py").read_text(encoding="utf-8")
        branch = ops[ops.index('if action.value=="sync":'):]
        branch = branch[:branch.index('if action.value=="vacuum":')]
        self.assertIn("World(WORLD.content_path)", branch, "a fresh parse from disk, not the in-memory copy")
        self.assertNotIn("sync_world_catalog", branch)
        self.assertNotIn("catalog_*", branch, "the catalogue is the engine's alone since v1.0.0-rc.40")
        self.assertIn("seed_world_territories(fresh.data)", branch)
        self.assertIn('"admin.content.reload"', branch)
        self.assertIn("next restart", branch, "it says what does not apply live")


if __name__ == "__main__":
    unittest.main()

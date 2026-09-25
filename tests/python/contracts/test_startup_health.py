import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tests.support import install_aiosqlite_shim

install_aiosqlite_shim()

from app.database import Database, OPERATIONAL_REQUIRED_TABLES, SCHEMA_VERSION
from app.database import bootstrap as database_bootstrap
from app.ops.health import HealthServer, HealthState, STARTUP_PHASES
from app.ops import healthcheck
from app.ops.operations import AlertDispatcher


class SchemaMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "schema.sqlite3"

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_the_readiness_probe_names_every_table_a_bootstrap_makes(self):
        """The probe is exact, and this is what keeps it exact.

        It was a sample of twenty-seven written for v0.20.7 and never touched
        again. By schema 49 it still named two catalogue mirrors nothing reads
        for their content, and did not name `npc_civilization_state` - the
        table the whole simulation runs on - nor `inventory`,
        `character_quests`, `battles`, or anything added in the twenty-nine
        releases since. A probe meant to tell a healthy versioned database from
        an empty file SQLite made at the same path would not have noticed the
        simulation's own table going missing.

        A sample cannot be kept honest, because nothing says which tables
        belong in it. This holds the set against a real bootstrap rather than
        against another list, so a table added without being listed fails here
        and the set cannot drift again.
        """
        await Database(self.path).init()
        with closing(sqlite3.connect(self.path)) as conn:
            # FTS5 virtual tables and their shadow tables are excluded: they
            # are made by CREATE VIRTUAL TABLE and rebuilt from their base
            # tables, so their absence is a different fault.
            actual = {
                str(row[0]) for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'"
                )
            }
        unguarded = sorted(actual - OPERATIONAL_REQUIRED_TABLES)
        self.assertEqual(
            unguarded, [],
            "these tables exist in a fresh database and the readiness probe would not "
            "notice them missing - add them to OPERATIONAL_REQUIRED_TABLES: " + ", ".join(unguarded),
        )
        phantom = sorted(OPERATIONAL_REQUIRED_TABLES - actual)
        self.assertEqual(
            phantom, [],
            "the readiness probe requires tables a fresh database does not have, so it would "
            "report a healthy database unhealthy: " + ", ".join(phantom),
        )

    async def test_the_probe_would_notice_an_empty_file(self):
        """Its actual job, stated as a test: a database that is not the one we
        think it is must fail, and a healthy one must pass."""
        await Database(self.path).init()
        health = await Database(self.path).operational_health()
        self.assertTrue(health.get("schema_intact"), health)
        self.assertEqual(health.get("missing_tables"), [])

        empty = Path(self.tmp.name) / "empty.sqlite3"
        with closing(sqlite3.connect(empty)) as conn:
            conn.execute("CREATE TABLE placeholder(x INTEGER)")
        health = await Database(empty).operational_health()
        self.assertFalse(health.get("schema_intact"), health)
        self.assertGreater(len(health.get("missing_tables") or []), 100)

    async def test_fresh_database_records_explicit_schema_version(self):
        db = Database(self.path)
        await db.init()
        status = await db.get_schema_status()
        self.assertEqual(status["current"], SCHEMA_VERSION)
        self.assertEqual(status["supported"], SCHEMA_VERSION)
        self.assertTrue(status["compatible"])
        self.assertGreaterEqual(status["migrations"], 2)

        with closing(sqlite3.connect(self.path)) as conn:
            version = conn.execute(
                "SELECT current_version FROM schema_version WHERE singleton=1"
            ).fetchone()[0]
            migration = conn.execute(
                "SELECT name FROM schema_migrations WHERE version=?", (SCHEMA_VERSION,)
            ).fetchone()[0]
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(migration, "a_stall_in_the_city")
        with closing(sqlite3.connect(self.path)) as conn:
            objects = {row[0]: row[1] for row in conn.execute(
                "SELECT name,type FROM sqlite_master WHERE name IN (?,?,?,?,?,?,?,?,?,?)",
                ("authoritative_actor_versions", "authoritative_action_receipts",
                 "authoritative_entity_versions", "domain_events",
                 "character_events", "npc_events", "sect_events",
                 "character_creation_family_options", "birth_family_household_threads",
                 "idx_birth_families_starter_key"),
            ).fetchall()}
        self.assertEqual(objects.get("authoritative_actor_versions"), "table")
        self.assertEqual(objects.get("authoritative_action_receipts"), "table")
        self.assertEqual(objects.get("authoritative_entity_versions"), "table")
        self.assertEqual(objects.get("domain_events"), "table")
        self.assertEqual(objects.get("character_events"), "view")
        self.assertEqual(objects.get("npc_events"), "view")
        self.assertEqual(objects.get("sect_events"), "view")
        self.assertEqual(objects.get("character_creation_family_options"), "table")
        self.assertEqual(objects.get("birth_family_household_threads"), "table")
        self.assertEqual(objects.get("idx_birth_families_starter_key"), "index")

        await db.record_startup_event("boot-test", "DATABASE_READY", detail={"schema_version": SCHEMA_VERSION})
        with closing(sqlite3.connect(self.path)) as conn:
            event = conn.execute(
                "SELECT phase,status,detail_json FROM startup_events WHERE boot_id='boot-test'"
            ).fetchone()
        self.assertEqual(event[0], "DATABASE_READY")
        self.assertEqual(event[1], "ready")
        self.assertEqual(json.loads(event[2])["schema_version"], SCHEMA_VERSION)

    async def test_legacy_unversioned_database_upgrades_in_place(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                "CREATE TABLE legacy_marker (value TEXT NOT NULL)"
            )
            conn.execute("INSERT INTO legacy_marker(value) VALUES('preserve-me')")
            conn.commit()

        db = Database(self.path)
        await db.init()
        status = await db.get_schema_status()
        self.assertTrue(status["compatible"])
        with closing(sqlite3.connect(self.path)) as conn:
            marker = conn.execute("SELECT value FROM legacy_marker").fetchone()[0]
        self.assertEqual(marker, "preserve-me")

    async def test_version_one_database_migrates_to_current_without_data_loss(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
            conn.execute("INSERT INTO legacy_marker(value) VALUES('v1-preserve-me')")
            conn.execute(
                "CREATE TABLE schema_version (singleton INTEGER PRIMARY KEY CHECK(singleton=1), current_version INTEGER NOT NULL, updated_at REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)"
            )
            conn.execute("INSERT INTO schema_version(singleton,current_version,updated_at) VALUES(1,1,0)")
            conn.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(1,'formal_startup_health_observability',0)")
            conn.commit()

        db = Database(self.path)
        await db.init()
        status = await db.get_schema_status()
        self.assertEqual(status["current"], SCHEMA_VERSION)
        self.assertTrue(status["compatible"])
        with closing(sqlite3.connect(self.path)) as conn:
            marker_value = conn.execute("SELECT value FROM legacy_marker").fetchone()[0]
            migration = conn.execute("SELECT name FROM schema_migrations WHERE version=2").fetchone()[0]
            equipment_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='equipment_instances'"
            ).fetchone()
            manor_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sect_manors'"
            ).fetchone()
        self.assertEqual(marker_value, "v1-preserve-me")
        self.assertEqual(migration, "advanced_world_combat_operations")
        self.assertIsNotNone(equipment_table)
        self.assertIsNotNone(manor_table)

    async def test_newer_schema_is_rejected_as_unsafe_downgrade(self):
        db = Database(self.path)
        await db.init()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                "UPDATE schema_version SET current_version=? WHERE singleton=1",
                (SCHEMA_VERSION + 1,),
            )
            conn.commit()
        with self.assertRaisesRegex(RuntimeError, "newer than this bot supports"):
            await db.init()

    async def test_database_operational_probe_checks_wal_and_schema(self):
        db = Database(self.path)
        await db.init()
        probe = await db.operational_health()
        self.assertTrue(probe["ok"])
        self.assertTrue(probe["schema_intact"])
        self.assertEqual(probe["missing_tables"], [])
        self.assertEqual(probe["schema_version"], SCHEMA_VERSION)
        self.assertEqual(probe["journal_mode"], "wal")
        self.assertGreaterEqual(probe["latency_ms"], 0)

    async def test_database_operational_probe_detects_replaced_or_partial_schema(self):
        db = Database(self.path)
        await db.init()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("DROP TABLE server_config")
            conn.commit()

        probe = await db.operational_health()
        self.assertFalse(probe["ok"])
        self.assertFalse(probe["schema_intact"])
        self.assertIn("server_config", probe["missing_tables"])

    async def test_database_bootstrap_repairs_missing_tables_without_erasing_rows(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
            conn.execute("INSERT INTO legacy_marker(value) VALUES('preserve-me')")
            conn.commit()

        with patch.dict(os.environ, {"DATABASE_PATH": str(self.path)}, clear=False):
            result = await database_bootstrap.bootstrap()

        self.assertEqual(result["schema_version"], SCHEMA_VERSION)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(
                conn.execute("SELECT value FROM legacy_marker").fetchone()[0],
                "preserve-me",
            )


class HealthServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = HealthState(supported_schema_version=SCHEMA_VERSION)
        self.server = HealthServer(self.state, host="127.0.0.1", port=0)
        await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()

    async def request(self, path: str):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.bound_port)
        writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        data = await reader.read()
        writer.close()
        await writer.wait_closed()
        header, body = data.split(b"\r\n\r\n", 1)
        status = int(header.split(b" ", 2)[1])
        return status, body.decode()

    async def test_liveness_is_available_before_readiness(self):
        status, body = await self.request("/livez")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "alive")

        status, body = await self.request("/healthz")
        self.assertEqual(status, 503)
        self.assertFalse(json.loads(body)["ready"])

    async def test_readiness_requires_all_named_phases_schema_and_checks(self):
        self.state.set_schema_version(SCHEMA_VERSION)
        self.state.set_check("database", True, journal_mode="wal")
        for phase in STARTUP_PHASES:
            self.state.mark_phase(phase)
        status, body = await self.request("/readyz")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        self.assertEqual(set(payload["phases"]), set(STARTUP_PHASES))
        env = {"HEALTHCHECK_HOST": "127.0.0.1", "HEALTH_PORT": str(self.server.bound_port)}
        with patch.dict(os.environ, env, clear=False):
            code = await asyncio.to_thread(healthcheck.main)
        self.assertEqual(code, 0)


    async def test_private_discord_control_requires_token_and_dispatches(self):
        calls = []

        async def handler(action, payload):
            calls.append((action, payload))
            return {"ok": True, "action": action, "result": payload}

        server = HealthServer(
            self.state, host="127.0.0.1", port=0,
            control_handler=handler, control_token="private-dashboard-control-token",
        )
        await server.start()
        try:
            async def post(token: str):
                reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
                body = json.dumps({"action": "sync_commands", "payload": {"reason": "test"}}).encode()
                request = (
                    "POST /control/discord HTTP/1.1\r\n"
                    "Host: localhost\r\n"
                    f"X-Xianxia-Control: {token}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode() + body
                writer.write(request); await writer.drain()
                data = await reader.read(); writer.close(); await writer.wait_closed()
                header, response_body = data.split(b"\r\n\r\n", 1)
                return int(header.split(b" ", 2)[1]), json.loads(response_body)

            status, payload = await post("wrong-token")
            self.assertEqual(status, 403)
            self.assertEqual(calls, [])
            status, payload = await post("private-dashboard-control-token")
            self.assertEqual(status, 200)
            self.assertEqual(payload["action"], "sync_commands")
            self.assertEqual(calls, [("sync_commands", {"reason": "test"})])
        finally:
            await server.stop()

    async def test_metrics_expose_phase_and_schema_gauges(self):
        self.state.set_schema_version(SCHEMA_VERSION)
        self.state.mark_phase("DATABASE_READY")
        status, body = await self.request("/metrics")
        self.assertEqual(status, 200)
        self.assertIn("xianxia_schema_version", body)
        self.assertIn('phase="DATABASE_READY"', body)
        self.assertIn("xianxia_startup_ready", body)


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    """Slow-query logging, the observability snapshot and the alert webhook
    (merged from test_completed_advanced_systems.py in v0.20.3; that file's
    three seeded characters were never read by its one surviving test)."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_slow_query_observability_persists_and_reaches_the_webhook(self):
        obs_db = Database(Path(self.tmp.name) / "observed.sqlite3", slow_query_ms=0)
        await obs_db.init()
        await obs_db.operational_health()
        flushed = await obs_db.flush_slow_query_log()
        self.assertGreater(flushed, 0)
        snapshot = await obs_db.observability_snapshot()
        self.assertGreater(snapshot["recent_slow_queries_1h"], 0)
        alert_id = await obs_db.record_operational_alert(
            "test_alert", severity="warning", message="test", detail={"slow": True}, delivered=False,
        )
        self.assertGreater(alert_id, 0)

        received = []
        async def handler(reader, writer):
            header = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in header.decode("latin1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            body = await reader.readexactly(length) if length else b""
            received.append(json.loads(body.decode("utf-8")))
            writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain(); writer.close(); await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            dispatcher = AlertDispatcher(f"http://127.0.0.1:{port}/alert", cooldown_seconds=0)
            self.assertTrue(await dispatcher.send("slow_query_pressure", "threshold exceeded", details={"count": 5}))
            await asyncio.sleep(0.02)
        finally:
            server.close(); await server.wait_closed()
        self.assertEqual(received[0]["key"], "slow_query_pressure")


if __name__ == "__main__":
    unittest.main()

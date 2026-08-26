from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.dashboard import AdminDashboardController, DashboardServer, DashboardSettings, ReadOnlyDashboardStore
from app.database import Database
from app.game import World
from app.simulation import WorldSimulator

ROOT = PROJECT_ROOT


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dashboard.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.world = World(ROOT / "content" / "world.json")
        self.sim = WorldSimulator(self.db, self.world.data)
        await self.sim.initialize(0)
        self.store = ReadOnlyDashboardStore(self.path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_overview_reads_schema_and_simulation(self):
        data = await self.store.overview()
        self.assertEqual(data["schema_version"], 17)
        self.assertIn("clock", data)
        self.assertGreater(data["counts"]["npcs_alive"], 0)
        self.assertTrue(data["simulations"])

    async def test_dashboard_views_read_current_world_data(self):
        self.assertIn("rows", await self.store.npcs(limit=10))
        self.assertIn("birth_families", await self.store.families())
        self.assertIn("sects", await self.store.sects())
        self.assertIn("wars", await self.store.conflicts())
        self.assertIn("world_events", await self.store.events())
        self.assertIn("players", await self.store.players())
        self.assertIn("memories", await self.store.rag())
        self.assertIn("minds", await self.store.decisions())
        self.assertIn("rows", await self.store.timeline())

    async def test_store_connection_is_query_only(self):
        with self.assertRaises(Exception):
            async with self.store._connect() as db:
                await db.execute("INSERT INTO world_state(key,value_json,updated_at) VALUES('dashboard_write_test','{}',0)")

    def test_basic_auth_is_constant_scope(self):
        settings = DashboardSettings(self.path, "127.0.0.1", 8090, "gm", "a-very-long-private-dashboard-token")
        server = DashboardServer(settings)
        token = base64.b64encode(b"gm:a-very-long-private-dashboard-token").decode("ascii")
        self.assertTrue(server._authorized({"authorization": f"Basic {token}"}))
        wrong = base64.b64encode(b"gm:wrong-token-that-is-long-enough").decode("ascii")
        self.assertFalse(server._authorized({"authorization": f"Basic {wrong}"}))

    async def test_admin_controller_can_be_explicitly_read_only(self):
        admin = AdminDashboardController(self.store, "", False)
        snapshot = await admin.snapshot()
        self.assertFalse(snapshot["enabled"])
        with self.assertRaises(PermissionError):
            await admin.run("world.advance_time", {"minutes": 60})

    def test_static_dashboard_contains_real_admin_console(self):
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Admin Console", html)
        self.assertIn("/api/admin/action", js)
        self.assertIn("player.grant_currency", js)
        self.assertIn("database.vacuum", js)


if __name__ == "__main__":
    unittest.main()

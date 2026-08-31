from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_simulation_fixture
install_aiosqlite_shim()

from app.dashboard import (
    AdminDashboardController, DASHBOARD_GET_API_PATHS, DASHBOARD_VIEW_ENDPOINTS, DashboardServer, DashboardSettings,
    DiscordDashboardController, ReadOnlyDashboardStore,
)
from app.dashboard_contract import DASHBOARD_API_VERSION, DASHBOARD_REVIEWED_SCHEMA_VERSION
from app.database import Database, SCHEMA_VERSION
from app.health import HealthServer, HealthState
from app.game import World
from app.simulation import WorldSimulator

ROOT = PROJECT_ROOT


class _NoopSimulationEngine:
    async def bootstrap_simulation(self, game_minute):
        return {"npc_moods_initialized": 0, "clan_branches_created": 0, "retainer_groups_created": 0, "clan_relations_created": 0}

    async def force_simulation(self, system, steps, game_minute):
        return {"system": system, "due_steps": steps, "applied_steps": steps, "summary": "test seed"}


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dashboard.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.world = World(ROOT / "content" / "world.json")
        await seed_simulation_fixture(self.db, self.world.data, 0)
        self.store = ReadOnlyDashboardStore(self.path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_overview_reads_schema_and_simulation(self):
        data = await self.store.overview()
        self.assertEqual(data["schema_version"], 24)
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

    async def test_newer_systems_are_exposed_to_dashboard(self):
        capabilities = await self.store.capabilities()
        self.assertEqual(capabilities["api_version"], DASHBOARD_API_VERSION)
        self.assertEqual(capabilities["implementation"]["reviewed_schema_version"], DASHBOARD_REVIEWED_SCHEMA_VERSION)
        self.assertTrue(capabilities["implementation"]["schema_review_current"])
        for system in ("cultivation", "crafting", "exploration", "economy", "dynasties"):
            self.assertTrue(capabilities["systems"][system]["available"], capabilities["systems"][system])

        cultivation = await self.store.cultivation()
        self.assertIn("roots", cultivation)
        self.assertIn("tribulations", cultivation)
        self.assertIn("seclusion", cultivation)

        crafting = await self.store.crafting()
        self.assertIn("professions", crafting)
        self.assertIn("alchemy", crafting)
        self.assertIn("spirit_beasts", crafting)
        self.assertIn("cave_abodes", crafting)

        exploration = await self.store.exploration()
        self.assertIn("events", exploration)
        self.assertIn("secret_realms", exploration)
        self.assertIn("caravans", exploration)

        economy = await self.store.economy()
        self.assertIn("markets", economy)
        self.assertIn("auctions", economy)
        self.assertIn("black_market_posts", economy)

        dynasties = await self.store.dynasties()
        self.assertIn("history", dynasties)
        self.assertIn("leads", dynasties)
        self.assertIn("claims", dynasties)
        self.assertIn("conflicts", dynasties)

    def test_frontend_backend_api_contract_stays_in_sync(self):
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        js_paths = {m.split("?", 1)[0] for m in re.findall(r"/api/[A-Za-z0-9_./?=&${}-]+", js)}
        get_refs = js_paths - {"/api/admin/action", "/api/discord/action"}
        self.assertTrue(get_refs <= DASHBOARD_GET_API_PATHS, sorted(get_refs - DASHBOARD_GET_API_PATHS))
        for endpoint in DASHBOARD_VIEW_ENDPOINTS.values():
            self.assertIn(endpoint, get_refs, f"Backend dashboard view endpoint is not consumed by frontend: {endpoint}")
        nav_views = set(re.findall(r'data-view="([a-z_]+)"', html))
        loader_match = re.search(r"const loaders=\{([^}]+)\};", js)
        self.assertIsNotNone(loader_match)
        loader_views = set(re.findall(r"([a-z_]+):load[A-Za-z]+", loader_match.group(1)))
        self.assertEqual(nav_views, loader_views)

    async def test_new_dashboard_api_routes_return_json(self):
        settings = DashboardSettings(self.path, "127.0.0.1", 0, "gm", "a-very-long-private-dashboard-token", False)
        dashboard = DashboardServer(settings)
        server = await asyncio.start_server(dashboard._handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        token = base64.b64encode(b"gm:a-very-long-private-dashboard-token").decode("ascii")
        try:
            for path in ("/api/capabilities", *sorted(set(DASHBOARD_VIEW_ENDPOINTS.values()))):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write((
                    f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Basic {token}\r\nConnection: close\r\n\r\n"
                ).encode("ascii"))
                await writer.drain()
                raw = await reader.read()
                writer.close()
                await writer.wait_closed()
                header, body = raw.split(b"\r\n\r\n", 1)
                self.assertIn(b" 200 ", header.split(b"\r\n", 1)[0], path)
                self.assertIsInstance(json.loads(body.decode("utf-8")), dict)
        finally:
            server.close()
            await server.wait_closed()

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

    async def test_discord_dashboard_proxy_uses_private_bot_control_endpoint(self):
        state = HealthState(supported_schema_version=SCHEMA_VERSION)
        calls = []

        async def handler(action, payload):
            calls.append((action, payload))
            return {"ok": True, "action": action, "result": {"connected": True, "guild": {"name": "Xianxia RP"}}}

        server = HealthServer(
            state, host="127.0.0.1", port=0, control_handler=handler,
            control_token="dashboard-discord-control-token",
        )
        await server.start()
        try:
            control = DiscordDashboardController(
                f"http://127.0.0.1:{server.bound_port}", "dashboard-discord-control-token", True
            )
            snapshot = await control.snapshot()
            self.assertTrue(snapshot["connected"])
            self.assertTrue(snapshot["control_available"])
            result = await control.run("sync_commands", {"reason": "dashboard test"})
            self.assertTrue(result["ok"])
            self.assertEqual(calls, [("status", {}), ("sync_commands", {"reason": "dashboard test"})])
        finally:
            await server.stop()

    def test_static_dashboard_contains_real_admin_console(self):
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Admin Console", html)
        self.assertIn("Discord Setup", html)
        self.assertIn("/api/admin/action", js)
        self.assertIn("/api/discord/action", js)
        self.assertIn("Full Setup", js)
        self.assertIn("sync_commands", js)
        self.assertIn("bind_channels", js)
        self.assertIn("player.grant_currency", js)
        self.assertIn("database.vacuum", js)
        self.assertIn("Cultivation", html)
        self.assertIn("Crafting & Assets", html)
        self.assertIn("Samsara Dynasties", html)
        self.assertIn("/api/cultivation", js)
        self.assertIn("/api/dynasties", js)


if __name__ == "__main__":
    unittest.main()

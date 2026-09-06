from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character, seed_simulation_fixture
install_aiosqlite_shim()

from app.dashboard.server import (
    AdminDashboardController, DASHBOARD_VIEW_ENDPOINTS, DashboardServer, DashboardSettings,
    DiscordDashboardController, ReadOnlyDashboardStore,
)
from app.dashboard.contract import DASHBOARD_API_VERSION, DASHBOARD_REVIEWED_SCHEMA_VERSION
from app.database import Database, SCHEMA_VERSION
from app.ops.health import HealthServer, HealthState
from app.rules.game import World

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
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
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

    async def test_new_dashboard_api_routes_return_json(self):
        settings = DashboardSettings(self.path, "127.0.0.1", 0, "gm", "a-very-long-private-dashboard-token", admin_writes=False)
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

    async def test_dashboard_admin_writes_attribute_to_configured_actor_id(self):
        # Regression guard for the dashboard-attribution fix: every write used to
        # be logged as ActorID 0 ("unattributed") no matter who ran it. Both the
        # generic ACTION_MAP dispatch path and the manual branches that go
        # through _audit() (e.g. backup.create) must now carry the configured
        # dashboard_actor_id instead.
        admin = AdminDashboardController(self.store, "http://fake-engine.invalid", True, 42)
        calls: list[tuple[str, int]] = []

        class _StubEngine:
            async def action(self, operation, actor_id, payload, **kwargs):
                calls.append((operation, actor_id))
                return {"ok": True}

        class _StubTransport:
            async def create_backup(self):
                return {"name": "test-backup"}

        admin.engine = _StubEngine()
        admin.transport = _StubTransport()

        await admin.run("player.karma", {"user_id": 1, "delta": 5})
        self.assertEqual(calls[-1], ("admin.player.karma", 42))

        await admin.run("backup.create", {})
        self.assertEqual(calls[-1], ("admin.audit", 42))

    async def test_dashboard_adjust_item_resolves_names_and_refuses_unknown_items(self):
        # The Adjust Inventory card is a free-text field and the engine stores
        # whatever it is handed: typing the display name "Bugslayer Sword" once
        # produced an inventory row no catalog lookup could match, so the sword
        # could never be bound. Names now resolve to ids; nonsense is refused
        # before the engine is called.
        admin = AdminDashboardController(self.store, "http://fake-engine.invalid", True, 42)
        calls: list[dict] = []

        class _StubEngine:
            async def action(self, operation, actor_id, payload, **kwargs):
                calls.append(dict(payload))
                return {"ok": True}

        admin.engine = _StubEngine()
        admin.transport = object()

        for typed in ("bugslayer_sword", "Bugslayer Sword", "  bugslayer sword ", "BUGSLAYER_SWORD"):
            await admin.run("player.adjust_item", {"user_id": 1, "item_id": typed, "quantity": 1})
            self.assertEqual(calls[-1]["item_id"], "bugslayer_sword", typed)
        self.assertEqual(len(calls), 4)

        with self.assertRaises(ValueError) as ctx:
            await admin.run("player.adjust_item", {"user_id": 1, "item_id": "Bugslayer Blade", "quantity": 1})
        self.assertIn("bugslayer_sword", str(ctx.exception))
        with self.assertRaises(ValueError):
            await admin.run("player.adjust_item", {"user_id": 1, "item_id": "", "quantity": 1})
        self.assertEqual(len(calls), 4, "unknown items must never reach the engine")

        # Other actions are untouched by the resolver.
        await admin.run("player.karma", {"user_id": 1, "delta": 5})
        self.assertEqual(len(calls), 5)

    async def test_dashboard_adjust_item_removal_reaches_a_misspelt_row_as_typed(self):
        # The repair for a phantom row is "-1 of the misspelt id" from the same
        # card. With the resolver in front, "Bugslayer Sword" would now map to
        # bugslayer_sword and the phantom row would be unreachable - so a
        # removal targets a row that exists under the typed string as-is.
        # Grants never get this bypass, and a removal of a string that matches
        # no row still resolves like a grant.
        self.assertTrue(await seed_character(
            self.db, user_id=7, discord_name="tester-7", name="Tester Seven",
            origin="Greenriver Town", path="Sword Cultivator", spiritual_root="Fire",
            concept="dashboard test", location="Greenriver Town",
            attributes={"body": 2, "agility": 2, "spirit": 2, "insight": 2, "will": 2, "presence": 2},
            qi_max=10, vitality_max=20,
        ))
        await self.db.add_items(7, {"Bugslayer Sword": 1})
        admin = AdminDashboardController(self.store, "http://fake-engine.invalid", True, 42)
        calls: list[dict] = []

        class _StubEngine:
            async def action(self, operation, actor_id, payload, **kwargs):
                calls.append(dict(payload))
                return {"ok": True}

        admin.engine = _StubEngine()
        admin.transport = object()

        await admin.run("player.adjust_item", {"user_id": 7, "item_id": "Bugslayer Sword", "quantity": -1})
        self.assertEqual(calls[-1]["item_id"], "Bugslayer Sword")
        await admin.run("player.adjust_item", {"user_id": 7, "item_id": "Bugslayer Sword", "quantity": 1})
        self.assertEqual(calls[-1]["item_id"], "bugslayer_sword", "a grant must never store the raw string")
        await admin.run("player.adjust_item", {"user_id": 8, "item_id": "Bugslayer Sword", "quantity": -1})
        self.assertEqual(calls[-1]["item_id"], "bugslayer_sword", "no such row for user 8, so the resolver applies")
        with self.assertRaises(ValueError):
            await admin.run("player.adjust_item", {"user_id": 7, "item_id": "Nonsense Blade", "quantity": -1})

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
        # Better admin dashboard: character-sheet editing, NPC/world-state editing,
        # player moderation, and bulk/server-wide actions (all 9 new admin.* ops).
        self.assertIn("player.set_realm", js)
        self.assertIn("player.set_resource_caps", js)
        self.assertIn("player.adjust_item", js)
        self.assertIn("player.reset_cooldowns", js)
        self.assertIn("player.force_end_scene", js)
        self.assertIn("npc.relocate", js)
        self.assertIn("world_event.end", js)
        self.assertIn("bulk.grant_currency", js)
        self.assertIn("bulk.reset_cooldowns", js)
        self.assertIn("/api/player", js)
        # Sect membership + progression-detail admin controls (6 new admin.* ops).
        self.assertIn("player.set_sect", js)
        self.assertIn("player.set_realm_perfection", js)
        self.assertIn("player.set_spiritual_root", js)
        self.assertIn("player.set_bloodline", js)
        self.assertIn("player.set_physique", js)
        self.assertIn("player.set_tribulation", js)
        # Backup restore + debuff/condition clearing.
        self.assertIn("backup.restore", js)
        self.assertIn("player.clear_condition", js)
        # v0.19.29: world-time scale wiring.
        self.assertIn("timeScale", js)
        # v0.19.29: dynasty/samsara admin write path.
        self.assertIn("player.force_reincarnation_ready", js)
        # v0.19.29: crafting-adjacent admin write path.
        self.assertIn("player.set_pill_toxicity", js)
        self.assertIn("player.set_beast_stats", js)
        self.assertIn("player.remove_equipment", js)
        self.assertIn("player.set_abode_access", js)
        # v0.19.29: mute/freeze moderation.
        self.assertIn("player.set_moderation", js)
        # v0.19.29: undo the most recent admin action.
        self.assertIn("audit.undo_last", js)


if __name__ == "__main__":
    unittest.main()

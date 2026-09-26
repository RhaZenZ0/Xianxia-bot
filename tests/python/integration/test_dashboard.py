from __future__ import annotations

import asyncio
import base64
import json
import time
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
from app.rules.quests import MAX_OBJECTIVES, OBJECTIVE_TYPES

ROOT = PROJECT_ROOT


class _NoopSimulationEngine:
    async def bootstrap_simulation(self):
        return {"npc_moods_initialized": 0, "clan_branches_created": 0, "retainer_groups_created": 0, "clan_relations_created": 0}

    async def force_simulation(self, system, steps):
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

    async def test_the_clock_is_the_engines_when_there_is_one(self):
        """The GM's clock is a read-through (v1.0.0-rc.39).

        The dashboard used to keep its own copy of the anchor arithmetic. Every
        test here runs without GAME_ENGINE_URL, so only the local-SQLite branch
        was ever exercised - and that branch must derive nothing either.
        """
        class _ClockEngine:
            async def world_clock(self):
                return {"game_minute": 987654, "scale": 7}

        self.store._engine = _ClockEngine()
        clock = (await self.store.overview())["clock"]
        self.assertEqual(clock["game_minute"], 987654)
        self.assertEqual(clock["scale"], 7)
        # And with no engine the stored anchor is reported as it stands, never
        # advanced by the real time elapsed since it was written: an anchor at
        # 4000 written an hour ago at scale 4 would read 4240 under the old
        # arithmetic.
        self.store._engine = None
        async with self.db._connect() as write:
            await write.execute(
                "INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,0)"
                " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (json.dumps({"anchor_game_minute": 4000, "anchor_real_ts": time.time() - 3600, "scale": 4}),),
            )
            await write.commit()
        async with self.store._connect() as db:
            frozen = await self.store._world_clock(db)
        self.assertEqual(frozen["game_minute"], 4000)
        self.assertEqual(frozen["scale"], 4)

    async def test_overview_reads_schema_and_simulation(self):
        data = await self.store.overview()
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertIn("clock", data)
        self.assertGreater(data["counts"]["npcs_alive"], 0)
        self.assertTrue(data["simulations"])

    async def test_dashboard_views_read_current_world_data(self):
        npcs = await self.store.npcs(limit=10)
        self.assertIn("rows", npcs)
        # v1.6.0: the running events' cast is on the roster; the graves moved
        # to Deeds & Fates.
        self.assertIn("event_casts", npcs)
        self.assertNotIn("graves", npcs)
        self.assertIn("birth_families", await self.store.families())
        sects = await self.store.sects()
        self.assertIn("sects", sects)
        # Memberships moved to Members & Lineage; the two columns nothing ever
        # wrote are gone from the cards.
        self.assertNotIn("memberships", sects)
        self.assertTrue(all("prestige" not in row and "treasury_stones" not in row for row in sects["sects"]))
        self.assertIn("summary", await self.store.npc_population())
        self.assertIn("couples", await self.store.npc_families())
        self.assertIn("feuds", await self.store.npc_society())
        self.assertIn("graves", await self.store.npc_deeds())
        self.assertIn("members", await self.store.sect_members())
        self.assertIn("attempts", await self.store.sect_recruitment())
        self.assertIn("treasury", await self.store.sect_holdings())
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

    async def test_player_detail_carries_the_rows_the_editor_pre_fills_from(self):
        """The Player Editor reads one endpoint and fills every lever from it,
        so the endpoint returns the row each lever writes - empty where the
        character has none, never absent - and the empty sheet for nobody."""
        self.assertEqual(await self.store.player_detail(0), {})
        self.assertTrue(await seed_character(
            self.db, user_id=9, discord_name="tester-9", name="Tester Nine",
            origin="Greenriver Town", path="Sword Cultivator", spiritual_root="Fire",
            concept="editor test", location="Greenriver Town",
            attributes={"body": 2, "agility": 2, "spirit": 2, "insight": 2, "will": 2, "presence": 2},
            qi_max=10, vitality_max=20,
        ))
        async with self.db._connect() as db:
            await db.execute("UPDATE currency_wallets SET balance=12 WHERE user_id=9 AND currency_id='low_spirit_stone'")
            await db.execute(
                "INSERT INTO spirit_beasts(user_id,name,species,created_at,updated_at) VALUES(9,'Ember','fox',0,0)")
            await db.execute(
                "INSERT INTO equipment_instances(user_id,item_id,slot,durability,max_durability,bound_at,updated_at) VALUES(9,'iron_sword','weapon',10,10,0,0)")
            await db.commit()
        detail = await self.store.player_detail(9)
        for key in ("wallets", "root", "bloodlines", "physique", "tribulations", "perfection", "beasts", "equipment",
                    "abode", "guests", "alchemy", "fate"):
            with self.subTest(key=key):
                self.assertIn(key, detail)
        self.assertIn(("low_spirit_stone", 12), [(w["currency_id"], w["balance"]) for w in detail["wallets"]])
        self.assertEqual([b["name"] for b in detail["beasts"]], ["Ember"])
        self.assertEqual([(e["item_id"], e["slot"]) for e in detail["equipment"]], [("iron_sword", "weapon")])
        self.assertIn("beast_id", detail["beasts"][0], "the lever needs the id, so the row carries it")
        self.assertIn("equipment_id", detail["equipment"][0])
        self.assertEqual(detail["abode"], {}, "no property is an empty row, not a missing key")
        self.assertEqual(detail["bloodlines"], [])

    async def test_player_detail_carries_the_quest_journal_the_quest_levers_act_on(self):
        """The Quests card (v1.4.1) picks the quest and objective a lever needs,
        so each held quest comes back with its objectives and how far along each is."""
        self.assertTrue(await seed_character(
            self.db, user_id=11, discord_name="tester-11", name="Tester Eleven",
            origin="Greenriver Town", path="Sword Cultivator", spiritual_root="Fire",
            concept="quest test", location="Greenriver Town",
            attributes={"body": 2, "agility": 2, "spirit": 2, "insight": 2, "will": 2, "presence": 2},
            qi_max=10, vitality_max=20,
        ))
        async with self.db._connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO quest_definitions(quest_key,title,objectives_json,rewards_json,status,created_at,updated_at) "
                "VALUES('stuck_lesson','The Last Lesson','[{\"id\":\"x\",\"type\":\"family_lesson\",\"count\":1,\"label\":\"Take the lesson\"}]','{}','approved',0,0)")
            await db.execute(
                "INSERT INTO character_quests(user_id,quest_key,status,progress_json,created_at,updated_at) VALUES(11,'stuck_lesson','active','{}',0,0)")
            await db.commit()
        journal = {q["quest_key"]: q for q in (await self.store.player_detail(11))["quests"]}
        self.assertEqual(journal["stuck_lesson"]["title"], "The Last Lesson")
        self.assertEqual(journal["stuck_lesson"]["status"], "active")
        self.assertEqual(journal["stuck_lesson"]["objectives"],
                         [{"id": "x", "type": "family_lesson", "label": "Take the lesson", "count": 1, "progress": 0}])

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
        async with self.db._connect() as db:
            await db.execute("INSERT INTO inventory(user_id,item_id,quantity) VALUES(7,'Bugslayer Sword',1)")
            await db.commit()
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
        # v0.25.0: nav labels are HTML text now, so an ampersand is escaped.
        self.assertIn("Crafting &amp; Assets", html)
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
        # v1.0.0-rc.38: the disappearance a GM can stage, and its undo.
        self.assertIn("npc.set_missing", js)
        self.assertIn("missing:true", js)
        self.assertIn("missing:false", js)
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
        # v1.0.0-rc.37: the realm card carries the body ladder, sent only as a pair.
        self.assertIn('id="realmBodyIndex"', js)
        self.assertIn('id="realmBodyPhase"', js)
        self.assertIn("payload.body_realm_index=Number(bodyIndex);payload.body_phase=Number(bodyPhase)", js)
        self.assertIn("(bodyIndex==='')!==(bodyPhase==='')", js)
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

    def test_the_player_editor_owns_every_per_player_lever(self):
        """The Player Editor (v1.0.0-rc.37).

        The Admin Console carried sixteen cards that began with a Player
        select, each blind to what the character already had. They are one
        view now, pre-filled from the rows each lever writes, and the console
        keeps only the levers that act on the world or the server. Every
        player.* action the controller maps is reachable from the editor and
        none is drawn on the console any more, so a lever cannot quietly live
        in both places or in neither.
        """
        from app.dashboard.contract import _javascript_async_function_segments
        from app.dashboard.server import AdminDashboardController

        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        segments = _javascript_async_function_segments(js)
        editor, console = segments["loadPlayerEditor"], segments["loadAdmin"]
        per_player = sorted(a for a in AdminDashboardController.ACTION_MAP if a.startswith("player."))
        self.assertGreaterEqual(len(per_player), 24)
        for action in per_player:
            with self.subTest(action=action):
                self.assertIn(f"'{action}'", editor, "the editor drives it")
                self.assertNotIn(f"'{action}'", console, "the console no longer does")
        self.assertIn("'/api/player?user_id='", editor)
        self.assertIn("openPlayerEditor(", console, "the console launches the editor")
        self.assertIn("data-edit-player", segments["showPlayer"], "the player drawer opens it")
        self.assertIn('data-view="player_editor"', html)
        self.assertIn("player_editor:loadPlayerEditor", js)
        # A snowflake never goes through Number(): the editor keeps the id the
        # server returned, and compares with sameId.
        self.assertIn("EDIT_UID=String(uid??'')", js)
        self.assertNotIn("Number(EDIT_UID)", js)
        self.assertIn("user_id:uid", editor)
        self.assertNotIn("user_id:Number(", editor)


if __name__ == "__main__":
    unittest.main()


class QuestsWorkbenchTests(unittest.IsolatedAsyncioTestCase):
    """The Quests view (v0.24.0).

    The workbench exists because the same quest was reviewable on two screens
    depending on whether it had a giver, and editable on neither. These assert
    the two halves of the fix: one read that covers every producer, and a save
    path held to the same content rules the Forge is.
    """

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "quests.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.world = World(ROOT / "content" / "world.json")
        await seed_simulation_fixture(self.db, self.world.data, 0)
        self.store = ReadOnlyDashboardStore(self.path)
        location = sorted(
            name for name, loc in self.world.locations.items() if not (loc or {}).get("private")
        )[0]
        self.location = location
        await self.db.sync_commission_pool([
            {"quest_key": "static_one", "title": "First Steps Beneath Heaven",
             "description": "Learn what a cultivator is for.", "source_type": "system",
             "objectives": [{"id": "explore_1", "type": "explore", "target": location, "count": 1}],
             "rewards": {"insight_xp": 10}, "giver_npc": "", "realm_band": "", "tier": 1,
             "deadline_game_minutes": 0, "variants": []},
            {"quest_key": "commission_one", "title": "The Replaced Crate",
             "description": "A crate went out that should not have.", "source_type": "authored",
             "objectives": [{"id": "explore_1", "type": "explore", "target": location, "count": 1}],
             "rewards": {"spirit_stones": 40}, "giver_npc": "Steward Qiao", "realm_band": "0-3",
             "tier": 1, "deadline_game_minutes": 4320, "variants": []},
        ])
        await self.db.set_quest_definition_status("static_one", "approved")
        await self.db.set_quest_definition_status("commission_one", "approved")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_one_read_covers_every_producer_of_a_quest(self):
        data = await self.store.quests()
        by_key = {row["quest_key"]: row for row in data["definitions"]}
        # Both are here, in one list, with the same columns. Before v0.24.0 the
        # first was on Exploration read-only and the second on Commissions.
        self.assertEqual(by_key["static_one"]["source"], "static")
        self.assertEqual(by_key["commission_one"]["source"], "commission")
        self.assertEqual(data["summary"]["approved"], 2)
        self.assertEqual(data["summary"]["never_taken"], 2)

    async def test_the_read_carries_the_vocabulary_the_editor_binds_to(self):
        vocab = (await self.store.quests())["vocabulary"]
        # The editor's pickers come from here rather than from a copy in the
        # browser, so a type the engine cannot complete cannot be chosen.
        self.assertEqual(set(vocab["objective_types"]), set(OBJECTIVE_TYPES))
        self.assertIn(self.location, vocab["locations"])
        self.assertEqual(vocab["max_objectives"], MAX_OBJECTIVES)
        self.assertEqual(vocab["hold_policies"], ["keep", "migrate", "revoke"])

    async def test_the_budget_matches_the_one_the_bot_holds_the_forge_to(self):
        # Two processes read the same environment variables. If these drift, a
        # GM's hand-written quest and a forged one are held to different rules
        # and the review inbox starts lying about which is over budget. Read as
        # source rather than imported: app.ops.config builds a whole Settings
        # object, and the dashboard deliberately does not.
        source = (ROOT / "app" / "ops" / "config.py").read_text(encoding="utf-8")
        self.assertIn('("QUEST_REWARD_MAX_XP", 50), ("QUEST_REWARD_MAX_STONES", 200), ("QUEST_REWARD_MAX_ITEMS", 3)', source)
        self.assertEqual(ReadOnlyDashboardStore.quest_budget(), {"max_xp": 50, "max_stones": 200, "max_items": 3})

    async def test_coverage_names_the_places_nothing_points_at(self):
        coverage = (await self.store.quests())["coverage"]
        self.assertNotIn(self.location, coverage["unreferenced_locations"])
        self.assertGreater(coverage["unreferenced_location_count"], 0)
        counts = {row["type"]: row["count"] for row in coverage["objective_types"]}
        self.assertEqual(counts["explore"], 2)
        self.assertEqual(counts["sect_trial"], 0)
        self.assertEqual(coverage["unknown_targets"], [])

    async def test_coverage_reports_an_approved_quest_that_points_at_nothing(self):
        # Content edits can strand one. A player can accept it and never finish.
        await self.db.save_quest_definition(
            {"quest_key": "dead_end", "title": "The Road To Nowhere",
             "description": "It goes somewhere that is not there any more.",
             "objectives": [{"id": "explore_1", "type": "explore", "target": "Atlantis", "count": 1}],
             "rewards": {}},
            status="draft", origin="gm_prompt", created_by=1)
        await self.db.set_quest_definition_status("dead_end", "approved")
        coverage = (await self.store.quests())["coverage"]
        self.assertEqual([t["target"] for t in coverage["unknown_targets"]], ["Atlantis"])

    async def test_held_rows_say_whether_the_terms_are_pinned(self):
        self.assertTrue(await seed_character(
            self.db, user_id=1, discord_name="holder", name="Tester", origin=self.location,
            path="Qi Refiner", spiritual_root="Fire", concept="quest holder", location=self.location,
            attributes={"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 4},
            qi_max=20, vitality_max=20, created_game_minute=0,
        ))
        async with self.db._connect() as conn:
            await conn.execute(
                """INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,
                       commission,variant_index,terms_json,created_at,updated_at)
                   VALUES(1,'static_one','active','{}',0,0,0,?,0,0)""",
                (json.dumps({"objectives": [], "rewards": {"insight_xp": 10}}),),
            )
            await conn.commit()
        data = await self.store.quests()
        held = [row for row in data["held"] if row["quest_key"] == "static_one"]
        self.assertEqual(len(held), 1)
        self.assertTrue(held[0]["terms_json"])
        self.assertEqual({row["quest_key"]: row["held_now"] for row in data["definitions"]}["static_one"], 1)


class QuestSaveValidationTests(unittest.IsolatedAsyncioTestCase):
    """The save path is held to the Forge's own gate.

    A GM editing by hand and a model drafting must not be allowed to disagree
    about what a valid quest is - two vocabularies is how forged drafts and
    commissions ended up on separate screens under separate rules.
    """

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "save.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.world = World(ROOT / "content" / "world.json")
        await seed_simulation_fixture(self.db, self.world.data, 0)
        self.store = ReadOnlyDashboardStore(self.path)
        self.admin = AdminDashboardController(self.store, "", True, actor_id=9)
        self.location = sorted(
            name for name, loc in self.world.locations.items() if not (loc or {}).get("private")
        )[0]

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def _draft(self, **overrides):
        draft = {
            "title": "The Bell That Rings Itself",
            "description": "A temple bell that nobody rings has started ringing.",
            "objectives": [{"type": "explore", "target": self.location, "count": 1}],
            "rewards": {"insight_xp": 20},
        }
        draft.update(overrides)
        return draft

    async def test_a_valid_quest_is_normalised_and_keyed_from_its_title(self):
        saved, notes = await self.admin._validated_quest(self._draft())
        # `quest_` and not `forge_`: the key says who wrote it, and the pool
        # must not claim a model did when a GM typed it.
        self.assertEqual(saved["quest_key"], "quest_the_bell_that_rings_itself")
        self.assertEqual(saved["hold_policy"], "keep")
        self.assertEqual(saved["objectives"][0]["type"], "explore")
        self.assertEqual(notes, [])

    async def test_an_npc_the_world_does_not_have_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            await self.admin._validated_quest(self._draft(
                objectives=[{"type": "talk", "target": "Elder Su Yin Who Is Not Real", "count": 1}]))
        self.assertIn("cannot be saved", str(caught.exception))

    async def test_a_quest_with_no_objectives_is_refused(self):
        with self.assertRaises(ValueError):
            await self.admin._validated_quest(self._draft(objectives=[]))

    async def test_a_new_quest_never_lands_on_an_existing_key(self):
        # admin.quest.save treats a known key as an edit, so a collision would
        # quietly overwrite a different quest that happened to be named alike.
        await self.db.save_quest_definition(
            {"quest_key": "quest_the_bell_that_rings_itself", "title": "The Bell That Rings Itself",
             "description": "An older quest by the same name.",
             "objectives": [{"id": "explore_1", "type": "explore", "target": self.location, "count": 1}],
             "rewards": {}},
            status="approved", origin="gm_prompt", created_by=1)
        saved, _ = await self.admin._validated_quest(self._draft())
        self.assertEqual(saved["quest_key"], "quest_the_bell_that_rings_itself_2")

    async def test_editing_an_existing_quest_keeps_its_key(self):
        saved, _ = await self.admin._validated_quest(self._draft(quest_key="already_here"))
        self.assertEqual(saved["quest_key"], "already_here")

    async def test_the_hold_policy_is_carried_through_to_the_engine(self):
        saved, _ = await self.admin._validated_quest(self._draft(quest_key="k", hold_policy="migrate"))
        self.assertEqual(saved["hold_policy"], "migrate")


class NavigationGroupingTests(unittest.TestCase):
    """The sidebar (v0.25.0).

    Twenty-three views used to be one flat row of tabs, sticky, which at laptop
    width wrapped onto three rows and ate 120px of every screen permanently.
    They are now seven groups (NPCs and Sects since v1.6.0). The gate already checks that every view has a
    button and a loader; this checks the thing the gate cannot see - that every
    button actually sits under a heading, in exactly one group.
    """

    def setUp(self):
        self.html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")

    def test_every_view_sits_under_exactly_one_group_heading(self):
        import re
        from app.dashboard.contract import DASHBOARD_VIEW_ENDPOINTS

        groups: dict[str, list[str]] = {}
        current = None
        for match in re.finditer(r'<div class="navgroup[^"]*">([^<]+)</div>|data-view="([a-z_]+)"', self.html):
            heading, view = match.group(1), match.group(2)
            if heading is not None:
                current = heading.strip()
                groups.setdefault(current, [])
            else:
                self.assertIsNotNone(current, f"{view} appears before any group heading")
                groups[current].append(view)

        placed = [view for views in groups.values() for view in views]
        self.assertEqual(sorted(placed), sorted(DASHBOARD_VIEW_ENDPOINTS))
        self.assertEqual(len(placed), len(set(placed)), "a view is listed in two groups")
        self.assertEqual(len(groups), 7, f"expected seven groups, got {list(groups)}")
        # NPCs and Sects are heads of their own (v1.6.0), between the world
        # and the players, and neither page is left behind under World.
        self.assertEqual(list(groups)[:4], ["World", "NPCs", "Sects", "Players"])
        self.assertEqual(groups["NPCs"], ["npcs", "npc_population", "npc_families", "npc_society", "npc_deeds"])
        self.assertEqual(groups["Sects"], ["sects", "sect_members", "sect_recruitment", "sect_holdings"])
        self.assertNotIn("npcs", groups["World"])
        self.assertNotIn("sects", groups["World"])
        # The views that can change the world are kept apart from the
        # twenty-one that only read it. Narration routing joined them: it
        # cannot touch canonical state, but it is an audited engine write and
        # it decides what every player reads, so it belongs on this side.
        # The Player Editor (v1.0.0-rc.37) is where one character's levers
        # went; it writes through the same audited engine actions.
        self.assertEqual(groups["Admin"], ["discord", "narration", "player_editor", "admin"])

    def test_the_shell_carries_the_pieces_the_page_template_needs(self):
        for required in ('id="navFilter"', 'id="crumb"', 'id="railToggle"', 'id="worldClock"', 'id="drawer"'):
            self.assertIn(required, self.html)


class _PinnedClockPages(unittest.IsolatedAsyncioTestCase):
    """A real bootstrap with the simulation fixture and the clock pinned, so the
    pages' day and year arithmetic has a fixed minute to be read against."""

    NOW = 200 * 24 * 60

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "pages.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.world = World(ROOT / "content" / "world.json")
        await seed_simulation_fixture(self.db, self.world.data, 0)
        self.store = ReadOnlyDashboardStore(self.path)
        self.store._engine = None
        async with self.db._connect() as db:
            await db.execute(
                "INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,0)"
                " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (json.dumps({"anchor_game_minute": self.NOW, "anchor_real_ts": time.time(), "scale": 0}),),
            )
            rows = await (await db.execute("SELECT npc_name,home_location FROM npc_civilization_state ORDER BY npc_name LIMIT 12")).fetchall()
            await db.commit()
        self.names = [r[0] for r in rows]
        self.homes = {r[0]: r[1] for r in rows}

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _run(self, *statements):
        async with self.db._connect() as db:
            for sql, params in statements:
                await db.execute(sql, params)
            await db.commit()


class NpcPagesTests(_PinnedClockPages):
    """The NPCs head (v1.6.0): each page reads what the simulation wrote."""

    async def test_population_counts_the_missing_the_stalled_the_away_and_the_old(self):
        missing, stalled, away, old = self.names[:4]
        ten_days = 10 * 24 * 60
        year = 60 * 24 * 30 * 12
        await self._run(
            ("UPDATE npc_civilization_state SET status='missing',missing_since_game_minute=?,activity='Whereabouts unknown' WHERE npc_name=?",
             (self.NOW - ten_days, missing)),
            ("UPDATE npc_civilization_state SET activity='Stalled at the ascension gate' WHERE npc_name=?", (stalled,)),
            ("UPDATE npc_civilization_state SET current_location='Somewhere Far Away' WHERE npc_name=?", (away,)),
            # 70 at creation, born a year before now, lifespan 72: a year left.
            ("INSERT INTO npc_life_state(npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,updated_at) VALUES(?,?,70,72,0)",
             (old, self.NOW - year)),
        )
        data = await self.store.npc_population()
        self.assertEqual(data["summary"]["missing"], 1)
        row = next(r for r in data["missing"] if r["npc_name"] == missing)
        self.assertEqual(row["days_missing"], 10)
        self.assertIn(stalled, [r["npc_name"] for r in data["stalled"]])
        self.assertIn(away, [r["npc_name"] for r in data["away_from_home"]])
        self.assertEqual([r["npc_name"] for r in data["near_end_of_lifespan"]], [old])
        self.assertEqual(data["near_end_of_lifespan"][0]["age_years"], 71.0)
        self.assertEqual(data["summary"]["near_end_of_lifespan"], 1)
        # The region join is what makes "who is where" worth reading.
        self.assertTrue(any(r["population"] for r in data["whereabouts"]), data["whereabouts"][:3])

    async def test_a_married_pair_is_one_row_and_the_rest_of_the_family_is_read(self):
        a, b, c, d, widow = self.names[:5]
        await self._run(
            ("INSERT INTO npc_life_state(npc_name,relationship_status,spouse_name,children_count,updated_at) VALUES(?,'married',?,1,0)", (a, b)),
            ("INSERT INTO npc_life_state(npc_name,relationship_status,spouse_name,children_count,updated_at) VALUES(?,'married',?,1,0)", (b, a)),
            ("INSERT INTO npc_social_relations(npc_a,npc_b,relation_type,affinity,started_game_minute,updated_at) VALUES(?,?,'marriage',80,4321,0)",
             (min(a, b), max(a, b))),
            ("INSERT INTO npc_social_relations(npc_a,npc_b,relation_type,affinity,updated_at) VALUES(?,?,'courtship',40,0)",
             (min(c, d), max(c, d))),
            ("INSERT INTO npc_life_state(npc_name,relationship_status,updated_at) VALUES(?,'widowed',0)", (widow,)),
            ("INSERT INTO npc_descendants(child_name,parent_a,parent_b,birth_game_minute,generated_as_npc,created_at,updated_at) VALUES('Grown Child',?,?,0,1,0,0)",
             (a, b)),
            ("INSERT INTO world_history_events(source_key,event_type,title,summary,actor_name,game_minute,created_at,updated_at) VALUES('coa:1','npc_coming_of_age','Of age','Grown',?,777,0,0)",
             ("Grown Child",)),
            ("INSERT INTO birth_families(family_id,family_name,surname,archetype,location,head_name,created_at,updated_at) VALUES(901,'Test House','Test','alchemy_family','Greenriver Town','Head',0,0)", ()),
            ("INSERT INTO birth_family_npcs(family_id,name,relation,created_at) VALUES(901,'Uncle Test','uncle',0)", ()),
        )
        data = await self.store.npc_families()
        pairs = [(r["npc_a"], r["npc_b"]) for r in data["couples"]]
        self.assertEqual(pairs, [(min(a, b), max(a, b))], "a married pair is one row, not two")
        self.assertEqual(data["couples"][0]["married_since"], 4321)
        self.assertEqual([(r["npc_a"], r["npc_b"]) for r in data["courtships"]], [(min(c, d), max(c, d))])
        self.assertEqual([r["npc_name"] for r in data["widows"]], [widow])
        child = next(r for r in data["children"] if r["child_name"] == "Grown Child")
        self.assertEqual(child["came_of_age_game_minute"], 777)
        self.assertEqual(next(r for r in data["relatives"] if r["name"] == "Uncle Test")["family_name"], "Test House")

    async def test_society_tells_a_feud_from_a_friendship(self):
        a, b, c, d = self.names[:4]
        await self._run(
            ("INSERT INTO npc_social_relations(npc_a,npc_b,relation_type,grudge,updated_at) VALUES(?,?,'grudge',45,0)", (min(a, b), max(a, b))),
            ("INSERT INTO npc_social_relations(npc_a,npc_b,relation_type,affinity,updated_at) VALUES(?,?,'acquaintance',60,0)", (min(c, d), max(c, d))),
            ("INSERT INTO npc_disciple_bonds(master_name,disciple_name,status,updated_at) VALUES(?,?,'active',0)", (a, c)),
            ("INSERT INTO npc_disciple_bonds(master_name,disciple_name,status,updated_at) VALUES(?,?,'ended',0)", (b, d)),
            ("INSERT INTO npc_registry(name,origin,created_at,updated_at) VALUES('Made By The World','descendant',0,0)", ()),
        )
        data = await self.store.npc_society()
        feud_pairs = {(r["npc_a"], r["npc_b"]) for r in data["feuds"]}
        friend_pairs = {(r["npc_a"], r["npc_b"]) for r in data["friendships"]}
        self.assertIn((min(a, b), max(a, b)), feud_pairs)
        self.assertNotIn((min(a, b), max(a, b)), friend_pairs)
        self.assertIn((min(c, d), max(c, d)), friend_pairs)
        self.assertNotIn((min(c, d), max(c, d)), feud_pairs)
        self.assertEqual({r["status"] for r in data["disciple_bonds"]}, {"active", "ended"})
        self.assertEqual([r["name"] for r in data["registry"]], ["Made By The World"])
        self.assertEqual(data["registry_by_origin"], [{"origin": "descendant", "n": 1}])

    async def test_the_deeds_feed_shows_a_gm_the_hidden_rows_a_player_never_sees(self):
        dead = self.names[0]
        await self._run(
            ("INSERT INTO world_history_events(source_key,event_type,title,summary,visibility,actor_type,actor_name,target_name,game_minute,created_at,updated_at)"
             " VALUES('rob:1','npc_grave_robbery','A grave emptied','Nobody saw','hidden','npc','Robber Ko',?,500,0,0)", (dead,)),
            ("INSERT INTO world_history_events(source_key,event_type,title,summary,visibility,actor_type,actor_name,game_minute,created_at,updated_at)"
             " VALUES('promo:1','sect_promotion','Promoted','Rose a rank','public','npc','Rising Disciple',400,0,0)", ()),
            ("INSERT INTO world_history_events(source_key,event_type,title,summary,visibility,actor_type,actor_name,game_minute,created_at,updated_at)"
             " VALUES('player:1','location_discovery','A player found a road','Road','public','player','Some Player',300,0,0)", ()),
            ("INSERT INTO world_history_events(source_key,event_type,title,summary,visibility,actor_type,actor_name,game_minute,created_at,updated_at)"
             " VALUES('bt:1','npc_breakthrough','Broke through','A realm','public','npc','Rising Disciple',200,0,0)", ()),
            ("INSERT INTO npc_graves(npc_name,location,home_location,died_game_minute,created_at,updated_at) VALUES(?,'Wild Hills','Greenriver Town',100,0,0)", (dead,)),
            ("INSERT INTO npc_life_state(npc_name,death_game_minute,cause_of_death,updated_at) VALUES(?,100,'lost in the hills',0)", (dead,)),
        )
        data = await self.store.npc_deeds()
        keys = {r["source_key"] for r in data["rows"]}
        self.assertIn("rob:1", keys, "a hidden deed must reach the GM")
        self.assertIn("promo:1", keys, "an NPC-acted row counts even without the npc_ prefix")
        self.assertNotIn("player:1", keys)
        self.assertEqual([r["source_key"] for r in (await self.store.npc_deeds(visibility="hidden"))["rows"]], ["rob:1"])
        self.assertEqual([r["source_key"] for r in (await self.store.npc_deeds(q="Robber"))["rows"]], ["rob:1"])
        self.assertEqual([r["source_key"] for r in (await self.store.npc_deeds(event_type="npc_breakthrough"))["rows"]], ["bt:1"])
        self.assertEqual(len((await self.store.npc_deeds(limit=0))["rows"]), 1)
        self.assertEqual(data["graves"][0]["robbed_by"], "Robber Ko")
        self.assertEqual(data["deaths"][0]["cause_of_death"], "lost in the hills")
        self.assertEqual([r["source_key"] for r in data["breakthroughs"]], ["bt:1"])

    async def test_the_roster_lists_only_a_running_events_cast_and_filters_the_missing(self):
        missing = self.names[0]
        await self._run(
            ("INSERT INTO world_events(event_key,event_type,title,location,payload_json,active,starts_at,ends_at) VALUES('live','recruitment','A Delegation','Greenriver Town','{}',1,0,9e9)", ()),
            ("INSERT INTO world_events(event_key,event_type,title,location,payload_json,active,starts_at,ends_at) VALUES('over','festival','Last Year','Greenriver Town','{}',0,0,1)", ()),
            ("INSERT INTO world_event_npcs(event_key,npc_key,name,title,role,sect_name,can_recommend,created_at) VALUES('live','elder','Visiting Elder Wu','Elder','Decides who is taken','Azure Cloud Sect',1,0)", ()),
            ("INSERT INTO world_event_npcs(event_key,npc_key,name,title,created_at) VALUES('over','host','Old Host','Host',0)", ()),
            ("UPDATE npc_civilization_state SET status='missing',missing_since_game_minute=1 WHERE npc_name=?", (missing,)),
        )
        casts = (await self.store.npcs())["event_casts"]
        self.assertEqual([r["name"] for r in casts], ["Visiting Elder Wu"])
        self.assertEqual(casts[0]["sect_name"], "Azure Cloud Sect")
        self.assertEqual(casts[0]["can_recommend"], 1)
        self.assertEqual([r["npc_name"] for r in (await self.store.npcs(status="missing"))["rows"]], [missing])


class SectPagesTests(_PinnedClockPages):
    """The Sects head (v1.6.0)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        for uid, name in ((21, "Master Lin"), (22, "Disciple Yu"), (23, "Outsider Hao")):
            self.assertTrue(await seed_character(
                self.db, user_id=uid, discord_name=f"u{uid}", name=name, origin="Greenriver Town",
                path="Sword Cultivator", spiritual_root="Fire", concept="sect page", location="Greenriver Town",
                attributes={"body": 2, "agility": 2, "spirit": 2, "insight": 2, "will": 2, "presence": 2},
                qi_max=10, vitality_max=20,
            ))

    async def test_members_lineage_and_requests_are_read(self):
        await self._run(
            ("INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at,contribution_points) VALUES(21,'Azure Cloud Sect','Elder',40,1,90)", ()),
            ("INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at,contribution_points) VALUES(22,'Azure Cloud Sect','Outer Disciple',10,2,5)", ()),
            ("INSERT INTO sect_lineage(disciple_user_id,master_user_id,accepted_at,attention) VALUES(22,21,3,7)", ()),
            ("INSERT INTO disciple_requests(disciple_user_id,master_user_id,status,created_at) VALUES(23,21,'pending',4)", ()),
        )
        data = await self.store.sect_members()
        self.assertEqual([r["name"] for r in data["members"]], ["Master Lin", "Disciple Yu"])
        self.assertEqual(data["by_sect"][0]["members"], 2)
        self.assertEqual(data["by_sect"][0]["contribution"], 95)
        self.assertEqual((data["lineage"][0]["master_name"], data["lineage"][0]["disciple_name"]), ("Master Lin", "Disciple Yu"))
        self.assertEqual((data["requests"][0]["disciple_name"], data["requests"][0]["status"]), ("Outsider Hao", "pending"))

    async def test_the_player_editor_reads_the_sect_rank_it_pre_fills(self):
        """The card pre-fills `rank_level`; before v1.6.0 it was never selected,
        so it read 0 and saving the card unchanged demoted the player."""
        await self._run(
            ("INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(21,'Azure Cloud Sect','Elder',40,1)", ()),
        )
        player = (await self.store.player_detail(21))["player"]
        self.assertEqual(player["rank_level"], 40)
        self.assertEqual(player["rank_name"], "Elder")

    async def test_recruitment_reads_the_stored_tn_and_only_sect_keyed_standing(self):
        await self._run(
            ("INSERT INTO sect_recruitment_attempts(user_id,sect_name,attempt_type,result,score,target,game_minute,created_at) VALUES(23,'Azure Cloud Sect','trial','failed',11,17,50,0)", ()),
            ("INSERT INTO sect_recommendations(user_id,npc_name,sect_name,bonus,status,created_at,updated_at) VALUES(23,'Elder Wu','Azure Cloud Sect',2,'active',0,0)", ()),
            ("INSERT INTO sect_politics_state(sect_name,updated_at) VALUES('Azure Cloud Sect',0) ON CONFLICT(sect_name) DO NOTHING", ()),
            ("INSERT INTO faction_reputation(user_id,faction_key,score,updated_at) VALUES(23,'Azure Cloud Sect',25,0)", ()),
            ("INSERT INTO faction_reputation(user_id,faction_key,score,updated_at) VALUES(23,'craft_hall:Alchemy',5,0)", ()),
            ("INSERT INTO character_sect_discoveries(user_id,sect_name,discovery_kind,created_at) VALUES(23,'Azure Cloud Sect','envoy',0)", ()),
            ("INSERT INTO hidden_sect_membership(user_id,sect_name,rank_name,status,updated_at) VALUES(22,'Heaven-Devouring Demon Sect','Shadow Initiate','active',0)", ()),
        )
        data = await self.store.sect_recruitment()
        self.assertEqual((data["attempts"][0]["score"], data["attempts"][0]["target"]), (11, 17), "the TN is the one the engine stored")
        self.assertEqual(data["recommendations"][0]["npc_name"], "Elder Wu")
        self.assertEqual([(r["sect_name"], r["score"]) for r in data["standing"]], [("Azure Cloud Sect", 25)])
        self.assertEqual(data["discoveries"][0]["discovery_kind"], "envoy")
        self.assertEqual(data["hidden"][0]["player_name"], "Disciple Yu")

    async def test_holdings_read_the_manor_its_projects_and_the_real_treasury(self):
        # The holdings are foreign-keyed to `sects`, as they are in production,
        # so the parent row is seeded first.
        await self._run(
            ("INSERT INTO sects(sect_name,updated_at) VALUES('Azure Cloud Sect',0) ON CONFLICT(sect_name) DO NOTHING", ()),
            ("INSERT INTO sect_manors(sect_name,name,base_location,qi_array_level,founded_by_user_id,created_at,updated_at) VALUES('Azure Cloud Sect','Cloud Manor','Greenriver Town',2,21,0,0)", ()),
            ("INSERT INTO sect_manor_projects(sect_name,user_id,project_type,facility_key,from_level,to_level,cost_json,created_at) VALUES('Azure Cloud Sect',21,'upgrade','qi_array',1,2,'{}',0)", ()),
            ("INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES('Azure Cloud Sect','spirit_herb',30)", ()),
            ("INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES('Azure Cloud Sect','spirit_iron',12)", ()),
            ("INSERT INTO sect_politics_state(sect_name,updated_at) VALUES('Azure Cloud Sect',0) ON CONFLICT(sect_name) DO NOTHING", ()),
        )
        data = await self.store.sect_holdings()
        self.assertEqual(data["manors"][0]["founder_name"], "Master Lin")
        self.assertEqual((data["projects"][0]["from_level"], data["projects"][0]["to_level"]), (1, 2))
        self.assertEqual([(r["item_id"], r["quantity"]) for r in data["treasury"]], [("spirit_herb", 30), ("spirit_iron", 12)])
        card = next(r for r in (await self.store.sects())["sects"] if r["sect_name"] == "Azure Cloud Sect")
        self.assertEqual((card["treasury_items"], card["treasury_units"]), (2, 42))


class AttentionFeedTests(unittest.IsolatedAsyncioTestCase):
    """Overview's attention feed (v0.25.0).

    The one genuinely new thing in the layout rebuild. Nothing in it is new
    data - every item is a row that already existed on another page, and the
    gap it closes is that a GM had no reason to visit that page today.
    """

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "attention.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.world = World(ROOT / "content" / "world.json")
        await seed_simulation_fixture(self.db, self.world.data, 0)
        self.store = ReadOnlyDashboardStore(self.path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _seed_player(self, user_id=1, name="Tester"):
        self.assertTrue(await seed_character(
            self.db, user_id=user_id, discord_name=f"u{user_id}", name=name, origin="Greenriver Town",
            path="Qi Refiner", spiritual_root="Fire", concept="seed", location="Greenriver Town",
            attributes={"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 4},
            qi_max=20, vitality_max=20, created_game_minute=0,
        ))

    async def test_a_quiet_world_asks_for_nothing(self):
        self.assertEqual((await self.store.overview())["attention"], [])

    async def test_quest_drafts_are_reported_with_the_page_that_holds_them(self):
        await self.db.save_quest_definition(
            {"quest_key": "d1", "title": "A Draft", "description": "waiting",
             "objectives": [{"id": "explore_1", "type": "explore", "target": "Greenriver Town", "count": 1}],
             "rewards": {}}, status="draft", origin="gm_prompt", created_by=1)
        item = next(a for a in (await self.store.overview())["attention"] if a["kind"] == "quest_drafts")
        self.assertEqual(item["count"], 1)
        self.assertEqual(item["view"], "quests")
        self.assertIn("1 quest draft waiting", item["text"])

    async def test_a_frozen_player_with_a_recorded_reason_is_not_reported(self):
        # The point is an unexplained hold, not a hold. A GM who wrote down why
        # does not need to be told about it every time they open the dashboard.
        await self._seed_player()
        async with self.db._connect() as conn:
            await conn.execute("UPDATE characters SET is_frozen=1, moderation_reason='griefing' WHERE user_id=1")
            await conn.commit()
        kinds = {a["kind"] for a in (await self.store.overview())["attention"]}
        self.assertNotIn("moderation_unexplained", kinds)

        async with self.db._connect() as conn:
            await conn.execute("UPDATE characters SET moderation_reason='' WHERE user_id=1")
            await conn.commit()
        kinds = {a["kind"] for a in (await self.store.overview())["attention"]}
        self.assertIn("moderation_unexplained", kinds)

    async def test_lag_is_only_reported_when_a_system_has_missed_its_own_tick(self):
        # A 4,320-minute system 500 minutes behind is early, not late. Reporting
        # raw lag would make the feed cry wolf on every slow-interval system.
        async with self.db._connect() as conn:
            await conn.execute(
                "UPDATE world_simulation_state SET interval_game_minutes=4320,last_game_minute=0 WHERE system=?",
                ("black_markets",))
            await conn.commit()
        kinds = {a["kind"] for a in (await self.store.overview())["attention"]}
        self.assertNotIn("simulation_lag", kinds)

    async def test_the_feed_puts_the_worst_thing_first(self):
        # The draft is collected before the lag is, so only the sort can put the
        # missed tick at the top - which is where it belongs, because it is the
        # one item that means the world has stopped moving.
        await self.db.save_quest_definition(
            {"quest_key": "d1", "title": "A Draft", "description": "waiting",
             "objectives": [{"id": "explore_1", "type": "explore", "target": "Greenriver Town", "count": 1}],
             "rewards": {}}, status="draft", origin="gm_prompt", created_by=1)
        async with self.db._connect() as conn:
            await conn.execute(
                "UPDATE world_simulation_state SET interval_game_minutes=10,last_game_minute=0 WHERE system=?",
                ("black_markets",))
            await conn.commit()
        items = (await self.store.overview())["attention"]
        self.assertEqual([a["kind"] for a in items][:2], ["simulation_lag", "quest_drafts"])
        self.assertEqual(items[0]["severity"], "bad")


class AiRoutingViewTests(unittest.TestCase):
    """v0.27.0: the GM page for narration routing.

    The router's chains, counters and audit verdicts live in the bot process's
    memory rather than in SQLite, so this view is the one that has to come
    through the bot control plane. It reads and never commands, which is why it
    writes no admin_audit_log row.
    """

    def setUp(self):
        self.server = (ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
        self.bot = (ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")

    def test_the_endpoint_is_served_off_the_bot_control_plane_not_sqlite(self):
        self.assertIn('if path == "/api/ai_routing":', self.server)
        self.assertIn("await self.discord.ai_routing()", self.server)
        start = self.server.index("async def ai_routing")
        body = self.server[start:self.server.index("async def snapshot", start)]
        self.assertIn('self._request("ai_routing")', body)
        # No database session: the numbers this page shows are not in SQLite.
        self.assertNotIn("_connect()", body)

    def test_the_bot_answers_the_action_with_the_routers_own_snapshot(self):
        self.assertIn('if action == "ai_routing":', self.bot)
        self.assertIn("AI_ROUTER.health_snapshot()", self.bot)

    def test_it_is_read_only(self):
        # Every state-changing GM action writes to admin_audit_log. This one
        # changes nothing, so it must not be reachable as a POST action either.
        from app.dashboard.contract import DASHBOARD_POST_API_PATHS
        self.assertNotIn("/api/ai_routing", DASHBOARD_POST_API_PATHS)
        loader = self.js[self.js.index("async function loadAiRouting"):self.js.index("async function loadDiscordSetup")]
        for mutation in ("adminPost(", "discordPost(", "method:'POST'"):
            self.assertNotIn(mutation, loader)

    def test_the_page_explains_a_route_that_is_off_rather_than_hiding_it(self):
        # The failure an operator cannot otherwise see: a key is set, the route
        # is silently absent, and narration quietly runs on everything else.
        loader = self.js[self.js.index("async function loadAiRouting"):self.js.index("async function loadDiscordSetup")]
        self.assertIn("probe_retired", loader)
        self.assertIn("google", loader.lower())
        self.assertIn("never retired", loader)

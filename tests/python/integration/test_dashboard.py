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
from app.rules.quests import MAX_OBJECTIVES, OBJECTIVE_TYPES

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
    They are now five groups. The gate already checks that every view has a
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
        self.assertEqual(len(groups), 5, f"expected five groups, got {list(groups)}")
        # The two views that can change the world are kept apart from the
        # twenty-one that only read it.
        self.assertEqual(groups["Admin"], ["discord", "admin"])

    def test_the_shell_carries_the_pieces_the_page_template_needs(self):
        for required in ('id="navFilter"', 'id="crumb"', 'id="railToggle"', 'id="worldClock"', 'id="drawer"'):
            self.assertIn(required, self.html)


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

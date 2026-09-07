import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character, bot_package_source
install_aiosqlite_shim()

from app.database import Database
from app.ops.core_services import (
    CombatService, ExplorationService, LocationSceneService,
    NPCRelationshipService, QuestService,
)
from app.rules.quests import QUEST_DEFINITIONS

ATTRS = {"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 4}


class RecordingEngine:
    """Boundary fake: records requests and returns canned engine responses.

    It intentionally does not reimplement authoritative Go mechanics.
    """

    def __init__(self, db=None):
        self.calls = []
        self.db = db

    async def authoritative_action(self, operation, actor_id, payload, *, action_id, expected_version=None):
        payload = dict(payload)
        self.calls.append((str(operation), int(actor_id), payload))
        # v0.22.0: accepting a quest is `commission.accept`. The real rules -
        # one at a time, the deadline, the locked terms - are Go's and are
        # covered by go_core/internal/game/commission_actions_test.go; the fake
        # only writes the row so the progress path downstream has one to find.
        if operation == "commission.accept" and self.db is not None:
            now = time.time()
            async with self.db._connect() as conn:
                await conn.execute(
                    """INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,
                           commission,variant_index,created_at,updated_at)
                       VALUES(?,?,'active','{}',0,0,?,?,?)
                       ON CONFLICT(user_id,quest_key) DO NOTHING""",
                    (int(actor_id), str(payload["quest_key"]), int(payload.get("variant_index", 0)), now, now),
                )
                await conn.commit()
        return {"result": {"quest_key": payload.get("quest_key"), "status": "active"}}

    async def action(self, operation, actor_id, payload):
        payload = dict(payload)
        self.calls.append((str(operation), int(actor_id), payload))
        if operation == "scene.transition":
            return {"user_id": int(actor_id), **payload}
        if operation == "relationship.update":
            return {
                "user_id": int(actor_id), "npc_name": payload["npc_name"],
                "trust": 12, "respect": 7, "fear": 0, "affection": 0,
                "debt": 0, "grudge": 0, "encounter_count": 1,
                "last_summary": payload.get("summary", ""),
            }
        if operation == "quest.progress":
            return {"touched": False, "complete": False, "progress": {}}
        if operation == "combat.apply_damage":
            return {"vitality": 9, "vitality_max": 20}
        if operation == "cultivation.reward":
            return {"cultivation_awarded": 4}
        raise AssertionError(f"Unexpected authoritative operation: {operation}")


class CoreServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "core-services.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.assertTrue(await seed_character(self.db,
            user_id=7001, discord_name="core-services", name="Seven",
            origin="Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
            concept="core service architecture test", location="Greenriver Town", attributes=ATTRS,
            qi_max=20, vitality_max=20, created_game_minute=10,
        ))
        self.engine = RecordingEngine(self.db)
        self.scenes = LocationSceneService(self.db, engine=self.engine)
        self.relationships = NPCRelationshipService(self.db, engine=self.engine)
        self.quests = QuestService(self.db, QUEST_DEFINITIONS, engine=self.engine)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_current_schema_has_core_npc_and_rag_tables(self):
        with sqlite3.connect(self.path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({
            "player_scene_state", "npc_relationships", "character_quests",
            "core_state_versions", "core_request_log",
            "npc_mind_state", "npc_player_memories",
            "rag_memories", "rag_memories_fts", "rag_canon_documents", "rag_canon_fts",
            "world_history_events", "world_history_fts",
        }.issubset(tables))


    async def test_domain_service_boundaries_delegate_without_discord(self):
        exploration = ExplorationService(self.scenes)
        state = await exploration.enter_expedition(
            7001, physical_location="Greenriver Town", channel_id=999, guild_id=123,
        )
        self.assertEqual(state.scene_type, "expedition")
        self.assertEqual(state.channel_id, 999)
        self.assertIsInstance(CombatService(self.db, engine=self.engine), CombatService)

    async def test_scene_transition_keeps_physical_location_separate_from_active_scene(self):
        state = await self.scenes.enter_scene(
            7001, physical_location="Greenriver Town", scene_type="expedition",
            scene_key="expedition:7001", scene_label="Greenriver Expedition", channel_id=1234,
        )
        self.assertEqual(state.physical_location, "Greenriver Town")
        self.assertEqual(state.scene_type, "expedition")
        self.assertEqual(state.channel_id, 1234)
        operation, actor_id, payload = self.engine.calls[-1]
        self.assertEqual(operation, "scene.transition")
        self.assertEqual(actor_id, 7001)
        self.assertEqual(payload["physical_location"], "Greenriver Town")
        self.assertEqual(payload["scene_type"], "expedition")


    async def test_authoritative_services_route_to_go_boundary_without_reimplementing_rules(self):
        relationship = await self.relationships.record_encounter(
            7001, "Elder Pine", summary="A respectful greeting.", deltas={"trust": 15}
        )
        self.assertEqual(relationship["trust"], 12)

        await self.quests.accept(7001, "first_steps", action_id="test:accept")
        changed = await self.quests.progress(7001, "explore", game_minute=110)
        self.assertEqual(changed, [])

        combat = CombatService(self.db, engine=self.engine)
        self.assertEqual((await combat.apply_damage(9, 7001, -5))["vitality"], 9)
        operations = [call[0] for call in self.engine.calls]
        self.assertIn("relationship.update", operations)
        self.assertIn("commission.accept", operations)
        self.assertIn("quest.progress", operations)
        self.assertIn("combat.apply_damage", operations)
        combat_payload = next(call[2] for call in self.engine.calls if call[0] == "combat.apply_damage")
        self.assertEqual(combat_payload["damage"], 0)

    def test_player_and_info_interfaces_are_wired(self):
        root = PROJECT_ROOT
        source = bot_package_source()
        self.assertIn('@registered_root_command(name="me"', source)
        self.assertIn('@registered_root_command(name="quests"', source)
        self.assertIn('class XianxiaInfoView', source)
        self.assertIn('self.add_view(XianxiaInfoView())', source)
        self.assertIn('NARRATOR_QUEUE', source)


if __name__ == "__main__":
    unittest.main()

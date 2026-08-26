import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION
from app.core_services import (
    CombatService, CultivationService, ExplorationService, LocationSceneService,
    NPCRelationshipService, QuestService, SectService,
)
from app.quests import QUEST_DEFINITIONS

ATTRS = {"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 4}


class RecordingEngine:
    """Boundary fake: records requests and returns canned engine responses.

    It intentionally does not reimplement authoritative Go mechanics.
    """

    def __init__(self):
        self.calls = []

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
        self.assertTrue(await self.db.create_character(
            user_id=7001, discord_name="core-services", name="Seven",
            origin="Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
            concept="core service architecture test", location="Greenriver Town", attributes=ATTRS,
            qi_max=20, vitality_max=20, created_game_minute=10,
        ))
        self.engine = RecordingEngine()
        self.scenes = LocationSceneService(self.db, engine=self.engine)
        self.relationships = NPCRelationshipService(self.db, engine=self.engine)
        self.quests = QuestService(self.db, QUEST_DEFINITIONS, engine=self.engine)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_current_schema_has_core_npc_and_rag_tables(self):
        self.assertEqual(SCHEMA_VERSION, 17)
        with sqlite3.connect(self.path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({
            "player_scene_state", "npc_relationships", "character_quests",
            "core_state_versions", "core_request_log",
            "npc_mind_state", "npc_player_memories",
            "rag_memories", "rag_memories_fts", "rag_canon_documents", "rag_canon_fts",
            "world_history_events", "world_history_fts",
        }.issubset(tables))

    async def test_persistent_npc_mind_and_salient_player_memories(self):
        state = await self.db.upsert_npc_mind_state(
            "Elder Pine", current_goal="Protect the valley", mood="guarded",
            focus_target="Azure Cloud Sect", recent_event="Reviewed the outer wards.",
            goal_progress=41, game_minute=50,
        )
        self.assertEqual(state["mood"], "guarded")
        loaded = await self.db.get_npc_mind_state("Elder Pine")
        self.assertEqual(loaded["current_goal"], "Protect the valley")
        await self.db.add_npc_player_memory(
            7001, "Elder Pine", memory_kind="vow",
            summary="Seven promised to return the jade token.", salience=82,
            source="talk", game_minute=51,
        )
        rows = await self.db.list_npc_player_memories(7001, "Elder Pine", 6, mark_recalled=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["memory_kind"], "vow")
        rows2 = await self.db.list_npc_player_memories(7001, "Elder Pine", 6)
        self.assertEqual(rows2[0]["recalled_count"], 1)

    async def test_domain_service_boundaries_delegate_without_discord(self):
        exploration = ExplorationService(self.scenes)
        state = await exploration.enter_expedition(
            7001, physical_location="Greenriver Town", channel_id=999, guild_id=123,
        )
        self.assertEqual(state.scene_type, "expedition")
        self.assertEqual(state.channel_id, 999)
        self.assertIsInstance(CombatService(self.db, engine=self.engine), CombatService)
        self.assertIsInstance(CultivationService(self.db, engine=self.engine), CultivationService)
        self.assertIsInstance(SectService(self.db), SectService)

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

    async def test_property_scene_keeps_entrance_as_physical_location(self):
        abode = await self.db.establish_abode(
            7001, "Quiet Bamboo Cave", "Greenriver Town", property_type="cave_abode"
        )
        await self.db.set_location(7001, abode["location_key"])
        state = await self.scenes.current(7001)
        self.assertEqual(state.physical_location, "Greenriver Town")
        self.assertEqual(state.scene_type, "player_property")
        self.assertEqual(state.scene_key, abode["location_key"])

    async def test_authoritative_services_route_to_go_boundary_without_reimplementing_rules(self):
        relationship = await self.relationships.record_encounter(
            7001, "Elder Pine", summary="A respectful greeting.", deltas={"trust": 15}
        )
        self.assertEqual(relationship["trust"], 12)

        await self.quests.accept(7001, "first_steps", game_minute=100)
        changed = await self.quests.progress(7001, "explore", game_minute=110)
        self.assertEqual(changed, [])

        combat = CombatService(self.db, engine=self.engine)
        self.assertEqual((await combat.apply_damage(9, 7001, -5))["vitality"], 9)
        cultivation = CultivationService(self.db, engine=self.engine)
        self.assertEqual(await cultivation.reward(7001, cultivation=10), 4)

        operations = [call[0] for call in self.engine.calls]
        self.assertIn("relationship.update", operations)
        self.assertIn("quest.progress", operations)
        self.assertIn("combat.apply_damage", operations)
        self.assertIn("cultivation.reward", operations)
        combat_payload = next(call[2] for call in self.engine.calls if call[0] == "combat.apply_damage")
        self.assertEqual(combat_payload["damage"], 0)

    def test_player_and_info_interfaces_are_wired(self):
        root = PROJECT_ROOT
        source = (root / "app" / "bot" / "main.py").read_text(encoding="utf-8")
        self.assertIn('@registered_root_command(name="me"', source)
        self.assertIn('@registered_root_command(name="quests"', source)
        self.assertIn('class XianxiaInfoView', source)
        self.assertIn('self.add_view(XianxiaInfoView())', source)
        self.assertIn('NARRATOR_QUEUE', source)


if __name__ == "__main__":
    unittest.main()

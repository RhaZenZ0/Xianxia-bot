import tempfile
import time
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION

ATTRS = {"body": 4, "agility": 4, "spirit": 6, "insight": 6, "will": 5, "presence": 4, "heart": 5}


class EventSpecificGuiPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "event_gui.sqlite3")
        await self.db.init()
        self.assertTrue(await self.db.create_character(
            user_id=7001, discord_name="Zi Dian", name="Zi Dian", origin="Greenriver Town",
            path="Qi Refiner", spiritual_root="Lightning", concept="event tester",
            location="Greenriver Town", attributes=ATTRS, qi_max=25, vitality_max=25,
            created_game_minute=0,
        ))
        self.assertTrue(await self.db.activate_world_event(
            event_key="beast-tide:test", dedupe_key="", event_type="random_event", title="Beast Tide",
            location="Greenriver Town",
            payload={"definition_id":"beast_tide","category":"Beast Tide","severity":6,"consequence_text":"Roads and farms are threatened."},
            ends_at=time.time()+3600,
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_schema_is_v17(self):
        self.assertEqual(SCHEMA_VERSION, 17)

    async def test_event_actions_persist_and_aggregate(self):
        first = await self.db.record_world_event_action(
            event_key="beast-tide:test", user_id=7001, action_key="investigate", stance="investigate",
            attribute="insight", total=18, tn=14, success=True, contribution_delta=2,
            investigation_delta=2, detail="Found the migration route.", game_minute=100,
        )
        self.assertEqual(first["contribution"], 2)
        second = await self.db.record_world_event_action(
            event_key="beast-tide:test", user_id=7001, action_key="defend", stance="defend",
            attribute="body", total=17, tn=15, success=True, contribution_delta=3,
            support_delta=2, combat_victory=True, detail="Held the gate.", game_minute=110,
        )
        self.assertEqual(second["contribution"], 5)
        self.assertEqual(second["investigation"], 2)
        self.assertEqual(second["support"], 2)
        self.assertEqual(second["combat_victories"], 1)
        self.assertEqual(second["actions_taken"], 2)
        rows = await self.db.list_world_event_participants("beast-tide:test")
        self.assertEqual(rows[0]["character_name"], "Zi Dian")
        actions = await self.db.get_world_event_actions("beast-tide:test")
        self.assertEqual(len(actions), 2)

    async def test_event_aftermath_becomes_world_history(self):
        await self.db.record_world_event_action(
            event_key="beast-tide:test", user_id=7001, action_key="defend", stance="defend",
            success=True, contribution_delta=8, support_delta=8, combat_victory=True, game_minute=200,
        )
        row = await self.db.finalize_world_event_history("beast-tide:test", game_minute=240)
        self.assertIsNotNone(row)
        self.assertEqual(row["event_type"], "world_event_response")
        again = await self.db.finalize_world_event_history("beast-tide:test", game_minute=245)
        self.assertEqual(again["source_key"], row["source_key"])
        history = await self.db.list_world_history(limit=10)
        self.assertEqual(sum(1 for x in history if x["source_key"] == row["source_key"]), 1)


if __name__ == "__main__":
    unittest.main()

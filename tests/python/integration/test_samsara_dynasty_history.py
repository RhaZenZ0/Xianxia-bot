import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, seed_character

install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION


ATTRS = {"body": 4, "agility": 4, "spirit": 4, "insight": 4, "will": 4, "presence": 4}


class SamsaraDynastyHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dynasty.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        created = await seed_character(
            self.db,
            user_id=404,
            discord_name="dynasty_test",
            name="Han Shuren",
            origin="Han Family, Riverguard City",
            path="Beast Binder",
            spiritual_root="Wood",
            concept="persistent Samsara dynasty history",
            location="Riverguard City",
            attributes=ATTRS,
            qi_max=20,
            vitality_max=20,
        )
        self.assertTrue(created)

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_schema_24_contains_dynasty_investigation_and_conflict_tables(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 26)
        with sqlite3.connect(self.path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("samsara_dynasty_history", tables)
            self.assertIn("samsara_ancestral_leads", tables)
            self.assertIn("samsara_investigation_quests", tables)
            self.assertIn("samsara_dynasty_claims", tables)
            self.assertIn("samsara_dynasty_conflicts", tables)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(samsara_dynasty_history)")}
        self.assertTrue({
            "source_family_name",
            "destination_family_name",
            "lineage_status",
            "blood_continuity",
            "evidence_json",
            "investigation_level",
        }.issubset(columns))

    async def test_history_query_preserves_replacement_as_blood_unrelated(self) -> None:
        evidence = [
            "The former branch ended without heirs.",
            "A successor deed names the replacement house.",
            "Blood records confirm no blood continuity.",
        ]
        async with self.db._connect() as conn:
            await conn.execute(
                """INSERT INTO samsara_dynasty_history(
                       user_id,incarnation_number,source_family_name,source_family_archetype,source_world,
                       destination_family_name,destination_family_archetype,destination_world,
                       lineage_status,blood_continuity,event_kind,summary,evidence_json,
                       investigation_level,investigation_count,created_game_minute,created_at,updated_at
                   ) VALUES(404,2,'Han Family','martial_household','Mortal World',
                            'Ji Stone-Marrow House','spirit_body_house','Spiritual World',
                            'extinct_branch_replaced',0,'extinction_and_replacement',
                            'The old branch died out and Ji House replaced it.',?,3,3,100,1.0,1.0)""",
                (json.dumps(evidence),),
            )
            await conn.execute(
                """INSERT INTO samsara_dynasty_history(
                       user_id,incarnation_number,source_family_name,source_family_archetype,source_world,
                       destination_family_name,destination_family_archetype,destination_world,
                       lineage_status,blood_continuity,event_kind,summary,evidence_json,
                       investigation_level,investigation_count,created_game_minute,created_at,updated_at
                   ) VALUES(404,3,'Ji Stone-Marrow House','spirit_body_house','Spiritual World',
                            'He Tide-Listening House','immortal_river_house','Immortal World',
                            'no_known_connection',0,'unrelated_rebirth',
                            'No reliable connection exists.','[]',0,0,200,2.0,2.0)"""
            )
            await conn.commit()

        rows = await self.db.get_samsara_dynasty_history(404)
        self.assertEqual([int(row["incarnation_number"]) for row in rows], [3, 2])
        replacement = rows[1]
        self.assertEqual(replacement["lineage_status"], "extinct_branch_replaced")
        self.assertEqual(int(replacement["blood_continuity"]), 0)
        self.assertEqual(replacement["evidence"], evidence)


    async def test_legacy_state_returns_sites_quests_claims_and_conflicts(self) -> None:
        async with self.db._connect() as conn:
            await conn.execute(
                """INSERT INTO samsara_dynasty_history(
                       user_id,incarnation_number,source_family_name,source_family_archetype,source_world,
                       destination_family_name,destination_family_archetype,destination_world,
                       lineage_status,blood_continuity,event_kind,summary,evidence_json,
                       investigation_level,investigation_count,created_game_minute,created_at,updated_at
                   ) VALUES(404,2,'Han Family','martial_household','Mortal World',
                            'Ji Stone-Marrow House','spirit_body_house','Spiritual World',
                            'extinct_branch_replaced',0,'extinction_and_replacement',
                            'The old branch died out and Ji House replaced it.','[]',3,3,100,1.0,1.0)"""
            )
            cur = await conn.execute(
                "SELECT history_id FROM samsara_dynasty_history WHERE user_id=404 AND incarnation_number=2"
            )
            history_id = int((await cur.fetchone())[0])
            await conn.execute(
                """INSERT INTO samsara_ancestral_leads(
                       user_id,history_id,lead_kind,name,location,world_name,description,status,clue_required,danger,
                       evidence_weight,created_game_minute,created_at,updated_at
                   ) VALUES(404,?,'archive','Sealed Han Registry','Stoneheart Spirit City','Spiritual World',
                            'A surviving archive.','discovered',1,10,15,100,1.0,1.0)""",
                (history_id,),
            )
            lead_cur = await conn.execute(
                "SELECT lead_id FROM samsara_ancestral_leads WHERE history_id=?",
                (history_id,),
            )
            lead_id = int((await lead_cur.fetchone())[0])
            await conn.execute(
                """INSERT INTO samsara_investigation_quests(
                       user_id,history_id,lead_id,quest_kind,title,description,status,progress,target,reward_evidence,
                       created_game_minute,created_at,updated_at
                   ) VALUES(404,?,?,'archive_research','Break the Archive Seals','Compare the records.',
                            'available',0,1,15,100,1.0,1.0)""",
                (history_id, lead_id),
            )
            await conn.execute(
                """INSERT INTO samsara_dynasty_claims(
                       user_id,history_id,claim_type,dynasty_name,target_family_name,target_world,status,
                       legitimacy,support,opposition,blood_based,created_game_minute,created_at,updated_at
                   ) VALUES(404,?,'replacement_challenge','Han Family','Ji Stone-Marrow House','Spiritual World',
                            'contested',80,60,55,0,120,1.0,1.0)""",
                (history_id,),
            )
            claim_cur = await conn.execute(
                "SELECT claim_id FROM samsara_dynasty_claims WHERE history_id=?",
                (history_id,),
            )
            claim_id = int((await claim_cur.fetchone())[0])
            await conn.execute(
                """INSERT INTO samsara_dynasty_conflicts(
                       user_id,claim_id,history_id,conflict_type,opponent_name,opponent_family_name,stakes,
                       status,created_game_minute,created_at,updated_at
                   ) VALUES(404,?,?,'replacement_legacy_dispute','Ji Stone-Marrow House','Ji Stone-Marrow House',
                            'Legacy rights','active',120,1.0,1.0)""",
                (claim_id, history_id),
            )
            await conn.commit()

        state = await self.db.get_samsara_legacy_state(404, history_id=history_id)
        self.assertEqual(len(state["leads"]), 1)
        self.assertEqual(len(state["quests"]), 1)
        self.assertEqual(len(state["claims"]), 1)
        self.assertEqual(len(state["conflicts"]), 1)
        self.assertEqual(state["claims"][0]["blood_based"], 0)


if __name__ == "__main__":
    unittest.main()

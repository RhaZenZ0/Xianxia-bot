import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION
from app.game import World
from app.simulation import MINUTES_PER_DAY, WorldSimulator
from app.worldtime import MINUTES_PER_YEAR

ROOT = PROJECT_ROOT


class NPCAutonomousLifeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "npc-life.sqlite3")
        await self.db.init()
        self.world = World(ROOT / "content" / "world.json")
        self.sim = WorldSimulator(self.db, self.world.data)
        await self.sim.initialize(0)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_schema_is_v16(self):
        self.assertEqual(SCHEMA_VERSION, 17)

    async def test_life_tables_and_initial_state_exist(self):
        async with self.db._connect() as db:
            cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {str(r[0]) for r in await cur.fetchall()}
        self.assertTrue({"npc_life_state", "npc_social_relations", "npc_disciple_bonds", "npc_descendants"}.issubset(tables))
        state = await self.sim.npc_status("Elder Su Yan")
        self.assertIsNotNone(state)
        self.assertGreaterEqual(int(state["health"]), 1)
        self.assertTrue(state["sect_rank"])
        self.assertEqual(state["relationship_status"], "single")

    async def test_natural_aging_can_kill_and_write_history(self):
        async with self.db._connect() as db:
            await db.execute("UPDATE npc_civilization_state SET realm_index=0,phase=1,status='alive' WHERE npc_name='Elder Su Yan'")
            await db.execute("UPDATE npc_life_state SET birth_game_minute=0,age_at_creation_years=90,natural_lifespan_years=70 WHERE npc_name='Elder Su Yan'")
            await db.commit()
        run = await self.sim.force_run("npc_life", 1, 7 * MINUTES_PER_DAY)
        self.assertIn("deaths", run.summary)
        state = await self.sim.npc_status("Elder Su Yan")
        self.assertEqual(state["status"], "dead")
        rows = await self.db.list_world_history(event_type="death", limit=20)
        self.assertTrue(any(r["actor_name"] == "Elder Su Yan" for r in rows))

    async def test_friendship_can_become_marriage_and_create_descendant(self):
        a, b = "Madam Pei Suyin", "Magistrate Xu Wenbo"
        pair = tuple(sorted((a, b)))
        async with self.db._connect() as db:
            await db.execute("UPDATE npc_civilization_state SET status='dead' WHERE npc_name NOT IN (?,?)", (a,b))
            await db.execute("UPDATE npc_civilization_state SET current_location='Greenriver Town',status='alive' WHERE npc_name IN (?,?)", (a,b))
            await db.execute("UPDATE npc_life_state SET relationship_status='single',spouse_name='',health=100 WHERE npc_name IN (?,?)", (a,b))
            await db.execute(
                "INSERT OR REPLACE INTO npc_social_relations(npc_a,npc_b,affinity,trust,grudge,relation_type,status,started_game_minute,last_interaction_game_minute,updated_at) VALUES(?,?,90,90,0,'close_friend','active',0,0,0)",
                pair,
            )
            await db.commit()

        calls = {"group": 0}
        def choose(seq):
            if seq and isinstance(seq[0], dict):
                item = seq[calls["group"] % len(seq)]
                calls["group"] += 1
                return item
            if 0 in seq:
                return 0
            return seq[0]

        with patch("app.simulation.world.secrets.choice", side_effect=choose), patch("app.simulation.world.secrets.randbelow", return_value=0):
            await self.sim.force_run("npc_life", 1, 2 * MINUTES_PER_YEAR)

        sa = await self.sim.npc_status(a)
        sb = await self.sim.npc_status(b)
        self.assertEqual(sa["relationship_status"], "married")
        self.assertEqual(sa["spouse_name"], b)
        self.assertEqual(sb["spouse_name"], a)
        async with self.db._connect() as db:
            cur = await db.execute("SELECT * FROM npc_descendants WHERE (parent_a=? AND parent_b=?) OR (parent_a=? AND parent_b=?)", (a,b,b,a))
            children = await cur.fetchall()
        self.assertGreaterEqual(len(children), 1)
        marriages = await self.db.list_world_history(event_type="marriage", limit=10)
        births = await self.db.list_world_history(event_type="descendant_birth", limit=10)
        self.assertTrue(marriages)
        self.assertTrue(births)

    async def test_grudge_can_cause_real_fight_and_injury(self):
        a, b = "Madam Pei Suyin", "Magistrate Xu Wenbo"
        pair = tuple(sorted((a, b)))
        async with self.db._connect() as db:
            await db.execute("UPDATE npc_civilization_state SET status='dead' WHERE npc_name NOT IN (?,?)", (a,b))
            await db.execute("UPDATE npc_civilization_state SET current_location='Greenriver Town',realm_index=1,phase=3,status='alive',activity='Managing affairs' WHERE npc_name IN (?,?)", (a,b))
            await db.execute("UPDATE npc_life_state SET health=100,injury='',injury_severity=0 WHERE npc_name IN (?,?)", (a,b))
            await db.execute(
                "INSERT OR REPLACE INTO npc_social_relations(npc_a,npc_b,affinity,trust,grudge,relation_type,status,started_game_minute,last_interaction_game_minute,updated_at) VALUES(?,?,-50,-20,90,'blood_feud','active',0,0,0)",
                pair,
            )
            await db.commit()

        calls = {"group": 0}
        def choose(seq):
            if seq and isinstance(seq[0], dict):
                item = seq[calls["group"] % len(seq)]
                calls["group"] += 1
                return item
            if 0 in seq:
                return 0
            return seq[0]

        with patch("app.simulation.world.secrets.choice", side_effect=choose), patch("app.simulation.world.secrets.randbelow", return_value=0):
            await self.sim.force_run("npc_life", 1, 7 * MINUTES_PER_DAY)

        sa = await self.sim.npc_status(a)
        sb = await self.sim.npc_status(b)
        injured = [s for s in (sa,sb) if int(s.get("injury_severity") or 0) > 0 or s.get("status") == "dead"]
        self.assertTrue(injured)
        battles = await self.db.list_world_history(event_type="major_battle", limit=10)
        deaths = await self.db.list_world_history(event_type="death", limit=10)
        self.assertTrue(battles or deaths)

    async def test_trusted_higher_rank_npc_can_take_disciple(self):
        master, disciple = "Elder Su Yan", "Yan Kuo"
        pair = tuple(sorted((master, disciple)))
        async with self.db._connect() as db:
            await db.execute("UPDATE npc_civilization_state SET status='dead' WHERE npc_name NOT IN (?,?)", (master,disciple))
            await db.execute("UPDATE npc_civilization_state SET current_location='Cloudspine Foothills',faction='Azure Reed Sect',status='alive' WHERE npc_name IN (?,?)", (master,disciple))
            await db.execute("UPDATE npc_civilization_state SET realm_index=4,phase=3 WHERE npc_name=?", (master,))
            await db.execute("UPDATE npc_civilization_state SET realm_index=1,phase=3 WHERE npc_name=?", (disciple,))
            await db.execute("UPDATE npc_life_state SET sect_rank='Elder',relationship_status='single' WHERE npc_name=?", (master,))
            await db.execute("UPDATE npc_life_state SET sect_rank='Outer Disciple',relationship_status='single' WHERE npc_name=?", (disciple,))
            await db.execute(
                "INSERT OR REPLACE INTO npc_social_relations(npc_a,npc_b,affinity,trust,grudge,relation_type,status,started_game_minute,last_interaction_game_minute,updated_at) VALUES(?,?,60,70,0,'friend','active',0,0,0)",
                pair,
            )
            await db.commit()

        calls = {"group": 0}
        def choose(seq):
            if seq and isinstance(seq[0], dict):
                item = seq[calls["group"] % len(seq)]
                calls["group"] += 1
                return item
            if 0 in seq:
                return 0
            return seq[0]

        with patch("app.simulation.world.secrets.choice", side_effect=choose), patch("app.simulation.world.secrets.randbelow", return_value=0):
            await self.sim.force_run("npc_life", 1, 7 * MINUTES_PER_DAY)

        async with self.db._connect() as db:
            cur = await db.execute("SELECT * FROM npc_disciple_bonds WHERE master_name=? AND disciple_name=? AND status='active'", (master,disciple))
            bond = await cur.fetchone()
        self.assertIsNotNone(bond)
        rows = await self.db.list_world_history(event_type="discipleship", limit=10)
        self.assertTrue(any(r["actor_name"] == master and r["target_name"] == disciple for r in rows))


if __name__ == "__main__":
    unittest.main()

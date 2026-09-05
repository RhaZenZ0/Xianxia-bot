import json
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
install_aiosqlite_shim()

from app.alchemy import alchemy_output, alchemy_quality, pill_toxicity_value, medicine_toxicity_effect
from app.effects import medicine_toxicity_effect
from app.birthfamily import family_forage_bonus, family_profession_bonus, generate_family_options
from app.database import Database, SCHEMA_VERSION
from app.game import World
from app.simulation import WorldSimulator

ROOT = PROJECT_ROOT
class _NoopSimulationEngine:
    async def bootstrap_simulation(self, game_minute: int):
        return {"npc_moods_initialized": 0, "clan_branches_created": 0, "retainer_groups_created": 0, "clan_relations_created": 0}

    async def force_simulation(self, system, steps, game_minute):
        return {"system": system, "due_steps": steps, "applied_steps": steps, "summary": "test seed"}


ATTRS = {"body": 4, "agility": 3, "spirit": 6, "insight": 6, "will": 5, "presence": 4}


class AlchemyBeastExpansionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "expansion.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        ok = await seed_character(self.db,
            user_id=909, discord_name="alchemist", name="Azure Alchemist",
            origin="Greenriver Town", path="Beast Binder", spiritual_root="Wood",
            concept="alchemy and beast integration test", location="Greenriver Town",
            attributes=ATTRS, qi_max=40, vitality_max=35, created_game_minute=0,
        )
        self.assertTrue(ok)
        self.world = World(ROOT / "content" / "world.json")
        self.sim = WorldSimulator(self.db, self.world.data, engine=_NoopSimulationEngine())
        await self.sim.initialize(0)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_schema_v6_retains_expansion_tables(self):
        self.assertEqual(SCHEMA_VERSION, 27)
        import sqlite3
        with sqlite3.connect(self.path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"alchemy_state", "alchemy_batches", "wild_beast_encounters"}.issubset(tables))

    async def test_schema_v4_database_migrates_to_v16_without_losing_character(self):
        import sqlite3
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TABLE alchemy_batches")
            conn.execute("DROP TABLE alchemy_state")
            conn.execute("DROP TABLE wild_beast_encounters")
            conn.execute("ALTER TABLE server_config RENAME TO server_config_v6")
            conn.execute("""CREATE TABLE server_config (
                guild_id INTEGER PRIMARY KEY,
                announcement_channel_id INTEGER,
                event_scene_channel_id INTEGER,
                home_scene_channel_id INTEGER,
                updated_at REAL NOT NULL
            )""")
            conn.execute("""INSERT INTO server_config(
                guild_id,announcement_channel_id,event_scene_channel_id,home_scene_channel_id,updated_at
            ) SELECT guild_id,announcement_channel_id,event_scene_channel_id,home_scene_channel_id,updated_at
              FROM server_config_v6""")
            conn.execute("DROP TABLE server_config_v6")
            conn.execute("ALTER TABLE cave_abodes RENAME TO cave_abodes_v10")
            conn.execute("""CREATE TABLE cave_abodes (
                user_id INTEGER PRIMARY KEY, location_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, base_location TEXT NOT NULL,
                grade TEXT NOT NULL DEFAULT 'Mortal', cultivation_level INTEGER NOT NULL DEFAULT 1, alchemy_level INTEGER NOT NULL DEFAULT 0,
                forge_level INTEGER NOT NULL DEFAULT 0, formation_level INTEGER NOT NULL DEFAULT 0, defense_level INTEGER NOT NULL DEFAULT 0,
                thread_id INTEGER, thread_channel_id INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""")
            conn.execute("""INSERT INTO cave_abodes(
                user_id,location_key,name,base_location,grade,cultivation_level,alchemy_level,forge_level,formation_level,defense_level,
                thread_id,thread_channel_id,created_at,updated_at
            ) SELECT user_id,location_key,name,base_location,grade,cultivation_level,alchemy_level,forge_level,formation_level,defense_level,
                     thread_id,thread_channel_id,created_at,updated_at FROM cave_abodes_v10""")
            conn.execute("DROP TABLE cave_abodes_v10")
            # >=5, not a hardcoded upper bound - this test rolls the DB back to v4 and
            # replays every later migration, so every migration after v4 must be cleared
            # regardless of how many now exist (avoids a UNIQUE-constraint failure on
            # replay each time a new migration is added, as happened here twice already).
            conn.execute("DELETE FROM schema_migrations WHERE version>=5")
            conn.execute("UPDATE schema_version SET current_version=4 WHERE singleton=1")
            conn.commit()
        await self.db.init()
        character = await self.db.get_character(909)
        self.assertEqual(character["name"], "Azure Alchemist")
        status = await self.db.get_schema_status()
        self.assertEqual(status["current"], 27)

    def test_alchemy_quality_scales_output_without_item_instances(self):
        self.assertEqual(alchemy_quality(0, success=True).label, "Ordinary")
        self.assertEqual(alchemy_quality(5, success=True).label, "Superior")
        flawless = alchemy_quality(9, success=True)
        self.assertEqual(flawless.output_multiplier, 3)
        self.assertEqual(alchemy_output({"qi_pill": 1}, flawless), {"qi_pill": 3})

    def test_alchemy_family_has_bounded_inherited_bonus(self):
        family = next(item for item in generate_family_options() if item["id"] == "alchemy_family")
        self.assertEqual(family_profession_bonus(family, "Alchemy"), 2)
        self.assertEqual(family_profession_bonus(family, "Forging"), 0)
        self.assertEqual(family_forage_bonus(family), 2)

    async def test_pill_toxicity_persists_and_decays_with_world_time(self):
        state = await self.db.add_pill_toxicity(909, 50, game_minute=100)
        self.assertEqual(state["pill_toxicity"], 50)
        later = await self.db.get_alchemy_state(909, game_minute=100 + 24 * 60)
        self.assertEqual(later["pill_toxicity"], 48)
        payload = medicine_toxicity_effect(later["pill_toxicity"])
        self.assertIsNotNone(payload)
        self.assertTrue(any(m["stat"] == "cultivation_gain" for m in payload["modifiers"]))


    async def test_active_effect_reads_filter_time_without_deleting_rows(self):
        await self.db.apply_effect(
            909,
            effect_key="expired_test",
            name="Expired Test",
            source_type="test",
            source_id="expired",
            effect={"modifiers": [{"stat": "alchemy_bonus", "operation": "add", "value": 2}]},
            starts_game_minute=100,
            duration_game_minutes=10,
        )
        await self.db.apply_effect(
            909,
            effect_key="future_test",
            name="Future Test",
            source_type="test",
            source_id="future",
            effect={"modifiers": [{"stat": "alchemy_bonus", "operation": "add", "value": 3}]},
            starts_game_minute=200,
            duration_game_minutes=100,
        )

        effects = await self.db.get_active_effects(909, 150)
        keys = {effect["effect_key"] for effect in effects}
        self.assertNotIn("expired_test", keys)
        self.assertNotIn("future_test", keys)

        import sqlite3
        with sqlite3.connect(self.path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM active_effects WHERE user_id=? AND source_type='test'",
                (909,),
            ).fetchone()[0]
        self.assertEqual(count, 2)



    def test_hunt_candidates_expose_taming_traits(self):
        beast = self.world.random_hunt(4)
        for key in ("taming_tn", "rank", "element", "temperament", "bloodline", "intelligence"):
            self.assertIn(key, beast)

    def test_pill_detection_assigns_medicinal_residue(self):
        qi_pill = self.world.items["qi_pill"]
        self.assertGreater(pill_toxicity_value("qi_pill", qi_pill), 0)
        self.assertEqual(pill_toxicity_value("spirit_iron", self.world.items["spirit_iron"]), 0)


if __name__ == "__main__":
    unittest.main()

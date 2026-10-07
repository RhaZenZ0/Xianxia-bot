"""Game systems: battle presentation, connected systems, alchemy and beast expansion, the technique catalog.

Merged from:

test_battle.py — (no docstring)

test_connected_systems.py — (no docstring)

test_alchemy_beast_expansion.py — (no docstring)

test_technique_catalog.py — The manual / technique catalog: content counts, executability, sync to
SQLite. (Was test_advanced_forward_port.py + test_forbidden_arts.py; renamed
and merged in v0.20.3 - the port it was named after finished in v0.18.)
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character, seed_content_tables

install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION  # noqa: E402
from app.rules.battle import matchup_label, suppression_label, vitality_band, vitality_bar, vitality_percentage  # noqa: E402
from app.rules.game import World  # noqa: E402
from app.rules.realm_hubs import realm_hub_by_location, REALM_HUBS  # noqa: E402
from app.simulation import WorldSimulator  # noqa: E402


# --- from test_battle.py ---

class BattlePresentationTests(unittest.TestCase):
    def test_vitality_bars_include_color_counts_and_percentages(self):
        self.assertEqual(vitality_percentage(8, 10), 80)
        self.assertIn("🟩", vitality_bar(8, 10))
        self.assertIn("8/10", vitality_bar(8, 10))
        self.assertIn("80%", vitality_bar(8, 10))
        self.assertEqual(vitality_band(5, 10)[0], "🟨")
        self.assertEqual(vitality_band(1, 10)[0], "🟥")

    def test_matchup_and_suppression_labels_cover_battle_context(self):
        self.assertIn("advantage", matchup_label(3, 5, 1, 5).lower())
        self.assertIn("Evenly", matchup_label(2, 4, 2, 4))
        self.assertEqual(suppression_label(0), "None")
        self.assertIn("2 counters", suppression_label(2))


# --- from test_connected_systems.py ---

ATTRS = {"body": 4, "agility": 3, "spirit": 5, "insight": 4, "will": 5, "presence": 2}
ROOT = PROJECT_ROOT


class ConnectedSystemsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "connected.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        for uid, name in ((101, "Junior"), (202, "Senior")):
            ok = await seed_character(self.db, 
                user_id=uid, discord_name=name.lower(), name=name, origin="Greenriver Town",
                path="Formation Adept", spiritual_root="Wind", concept="connected systems test",
                location="Greenriver Town", attributes=ATTRS, qi_max=30, vitality_max=30,
            )
            self.assertTrue(ok)
        async with self.db._connect() as conn:
            await conn.execute("UPDATE characters SET realm_index=1,phase=2 WHERE user_id=202")
            await conn.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_the_connected_system_tables_and_partner_echo_columns_exist(self):
        with sqlite3.connect(self.path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in {
                "character_fate", "fate_ledger", "disciple_requests", "deployed_location_arrays",
                "black_market_posts", "black_market_stock", "realm_hub_channels", "dao_partnerships",
            }:
                self.assertIn(name, tables)
            reinc_cols = {row[1] for row in conn.execute("PRAGMA table_info(reincarnation_state)")}
            self.assertTrue({"partner_echo", "partner_name"}.issubset(reinc_cols))


    async def test_four_realm_hubs_map_discord_channels_to_canonical_locations(self):
        self.assertEqual(set(REALM_HUBS), {"Mortal World", "Spiritual World", "Immortal World", "Celestial World"})
        for index, (world, hub) in enumerate(REALM_HUBS.items(), 1):
            matched = realm_hub_by_location(hub["location"])
            self.assertEqual(matched[0], world)
            await self.db.set_realm_hub_channel(
                guild_id=999, world_name=world, location=hub["location"], channel_id=1000 + index, category_id=777,
            )
        rows = await self.db.get_realm_hub_channels(999)
        self.assertEqual(len(rows), 4)
        mapped = await self.db.get_realm_hub_by_channel(999, 1001)
        self.assertEqual(mapped["location"], REALM_HUBS["Mortal World"]["location"])

    def test_content_catalog_connects_realm_hubs_and_formation_inscription(self):
        world = World(ROOT / "content" / "world.json")
        for hub in REALM_HUBS.values():
            loc = world.locations[hub["location"]]
            self.assertTrue(loc.get("realm_hub"))
            self.assertGreaterEqual(len(loc.get("encounters", [])), 5)
        # v1.0.0-rc.15: the two arts are separate. Inscription had been one of
        # the eight professions with no recipe of its own, while every talisman
        # was filed under Formation; the array disks stayed with the arrays and
        # the talismans went to the inscribers.
        by_profession = {}
        for recipe in world.recipes.values():
            by_profession.setdefault(recipe.get("profession"), []).append(recipe)
        self.assertGreaterEqual(len(by_profession.get("Formation", [])), 2)
        self.assertGreaterEqual(len(by_profession.get("Inscription", [])), 5)
        self.assertIn("minor_qi_gathering_array_disk", world.items)
        self.assertIn("swift_wind_talisman", world.items)


# --- from test_alchemy_beast_expansion.py ---

ROOT_EXPANSION = PROJECT_ROOT
class _NoopSimulationEngine:
    async def bootstrap_simulation(self):
        return {"npc_moods_initialized": 0, "clan_branches_created": 0, "retainer_groups_created": 0, "clan_relations_created": 0}

    async def force_simulation(self, system, steps):
        return {"system": system, "due_steps": steps, "applied_steps": steps, "summary": "test seed"}


ATTRS_EXPANSION = {"body": 4, "agility": 3, "spirit": 6, "insight": 6, "will": 5, "presence": 4}


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
            attributes=ATTRS_EXPANSION, qi_max=40, vitality_max=35, created_game_minute=0,
        )
        self.assertTrue(ok)
        self.world = World(ROOT_EXPANSION / "content" / "world.json")
        self.sim = WorldSimulator(self.db, self.world.data, engine=_NoopSimulationEngine())
        await self.sim.initialize(0)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_a_schema_v4_database_migrates_to_current_without_losing_the_character(self):
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
        self.assertEqual(status["current"], SCHEMA_VERSION)


# --- from test_technique_catalog.py ---

class TechniqueCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "catalog.sqlite3")
        await self.db.init()
        self.world = World(PROJECT_ROOT / "content" / "world.json")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_catalog_scale_and_the_demonic_branch(self):
        # 148 generated on the six-path cycle, 12 authored sect entry manuals
        # (v0.21.4; two per higher world since v0.39.0), 23 for the seventh path
        # the cycle never reached and 13 household traditions (both v1.0.3).
        self.assertEqual(len(self.world.manuals), 197)  # +1 the Stygian Ghost Scripture (v1.3.4)
        self.assertEqual(len(self.world.techniques), 681)
        evil_manuals = [m for m in self.world.manuals.values() if str(m.get("alignment", "")).lower() == "demonic"]
        evil_techniques = [t for t in self.world.techniques.values() if str((self.world.manuals.get(str(t.get("manual"))) or {}).get("alignment", "")).lower() == "demonic"]
        self.assertEqual(len(evil_manuals), 53)  # +6 for the Ghost Cultivator (v1.0.3), +1 the Stygian Ghost Scripture (v1.3.4)
        self.assertEqual(len(evil_techniques), 199)
        self.assertIn("blood_sea_palm", self.world.techniques)
        self.assertEqual(self.world.sects["Blood River Sect"]["alignment"], "Demonic")
        hidden = self.world.sects.get("Heaven-Devouring Demon Sect")
        self.assertTrue(hidden and hidden.get("hidden"))
        self.assertEqual(hidden.get("karma_initiation"), -200)
        for key in ("npc_principles", "sect_principles", "family_principles"):
            self.assertTrue(self.world.world_rules[key], key)

    async def test_technique_executability_matrix_uses_39_subtests(self):
        # 39 deterministic samples exercise the generated content contract without
        # turning this into 528 nearly identical test methods.
        samples = sorted(self.world.techniques.items())[:39]
        self.assertEqual(len(samples), 39)
        for technique_id, technique in samples:
            with self.subTest(technique=technique_id):
                self.assertIn(str(technique.get("manual")), self.world.manuals)
                self.assertTrue(str(technique.get("name", "")).strip())
                self.assertGreaterEqual(int(technique.get("qi_cost", 0)), 0)
                self.assertTrue(
                    int(technique.get("damage", 0)) > 0
                    or int(technique.get("heal", 0)) > 0
                    or int(technique.get("suppress_turns", 0)) > 0
                )

    async def test_the_content_tables_hold_every_manual_and_technique(self):
        # The engine fills content_* in production; pytest has no engine, so
        # the fixture writes the same three columns every reader touches
        # (v1.0.0-rc.40, when the catalog_* mirrors were retired).
        await seed_content_tables(self.db, self.world.data)
        async with self.db._connect() as conn:
            # Against the catalogue rather than against a literal: this test is
            # "the tables hold every manual", and a second copy of the count is
            # free to drift from the one `test_world_catalog.py`
            # pins (v1.0.3).
            cur = await conn.execute("SELECT COUNT(*) FROM content_manuals")
            self.assertEqual((await cur.fetchone())[0], len(self.world.manuals))
            cur = await conn.execute("SELECT COUNT(*) FROM content_techniques")
            self.assertEqual((await cur.fetchone())[0], len(self.world.techniques))

    async def test_seeding_the_territory_map_gives_every_location_a_node_and_an_era(self):
        await self.db.seed_world_territories(self.world.data)
        territories = await self.db.get_territories()
        self.assertEqual(len(territories), len(self.world.locations))
        era = await self.db.get_current_era()
        self.assertEqual(era["name"], "Jade Meridian Awakening Era")


if __name__ == "__main__":
    unittest.main()

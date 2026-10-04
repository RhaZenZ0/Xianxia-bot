"""The heavens lead on (v1.23.2, schema 77).

v1.16.0 seeded `realm_road_7` with an empty `follow_on`, because the realm road
stopped at the seam. v1.18.0 carried the road through the upper worlds and
pointed that stage at `realm_road_8` in the content, and the seeding is
insert-only - so on any world running since v1.16.0, finishing the stage (by
play, or by the GM's Complete in the Player Editor) handed nothing over.
Migration 77 re-points it where the chain is still the empty one it was seeded
with, and leaves a GM's own chain alone.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

WORLD = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
os.environ.update({k: v for k, v in ENV.items() if k not in os.environ})

SEEDED_AT_V1_16_0 = json.dumps({"follow_on": "", "realm_index": 7})


def _core():
    from tests.support import install_aiosqlite_shim
    install_aiosqlite_shim()
    from app.database import core
    return core


class TheContentChainsThroughTheSeam(unittest.TestCase):
    def test_the_seventh_stage_names_the_eighth(self):
        stages = {s["quest_key"]: s for s in WORLD["realm_road"]}
        self.assertEqual(stages["realm_road_7"].get("follow_on"), "realm_road_8")
        self.assertIn("realm_road_8", stages)

    def test_the_migration_writes_what_the_seeder_would(self):
        from app.rules.quests import realm_road_seed_rows

        class _World:
            data = WORLD

        row = next(r for r in realm_road_seed_rows(_World()) if r["quest_key"] == "realm_road_7")
        core = _core()
        statement = next(m for m in core.SCHEMA_MIGRATIONS if int(m[0]) == 77)[2][0]
        self.assertIn(f"seed_json='{json.dumps(row['seed'])}'", statement,
                      "migration 77 writes a seed a fresh world would not carry")
        self.assertIn(f"seed_json='{SEEDED_AT_V1_16_0}'", statement)


class ARunningWorldIsRepointed(unittest.IsolatedAsyncioTestCase):
    async def _world_at_76(self, seed: str) -> Path:
        core = _core()
        from app.database import Database
        path = Path(tempfile.mkdtemp()) / "heavens.sqlite3"
        migrations = tuple(m for m in core.SCHEMA_MIGRATIONS if int(m[0]) <= 76)
        with patch.object(core, "SCHEMA_VERSION", 76), patch.object(core, "SCHEMA_MIGRATIONS", migrations):
            await Database(path).init()
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO quest_definitions(quest_key,title,description,source_type,source_key,objectives_json,"
                         "rewards_json,status,origin,created_at,updated_at,seed_json) VALUES('realm_road_7','The Heavens','',"
                         "'system','realm_road','[]','{}','approved','content',0,0,?)", (seed,))
            conn.commit()
        await Database(path).init()
        return path

    def _seed(self, path: Path) -> dict:
        with sqlite3.connect(path) as conn:
            version = conn.execute("SELECT current_version FROM schema_version WHERE singleton=1").fetchone()[0]
            seed = conn.execute("SELECT seed_json FROM quest_definitions WHERE quest_key='realm_road_7'").fetchone()[0]
        self.assertGreaterEqual(version, 77, "the migration did not run; the reader is broken, not the tree")
        return json.loads(seed)

    async def test_the_seeded_empty_chain_is_pointed_on(self):
        seed = self._seed(await self._world_at_76(SEEDED_AT_V1_16_0))
        self.assertEqual(seed.get("follow_on"), "realm_road_8",
                         "finishing the heavens still hands nothing over on a world seeded at v1.16.0")

    async def test_a_gms_chain_is_kept(self):
        own = json.dumps({"follow_on": "forge_my_own_stage", "realm_index": 7})
        seed = self._seed(await self._world_at_76(own))
        self.assertEqual(seed.get("follow_on"), "forge_my_own_stage", "a chain a GM re-pointed was overwritten")


if __name__ == "__main__":
    unittest.main()

"""The NPC-master picker offers nobody the engine will turn away for being full (v1.34.0).

`discipleship.npc_request` refuses a master who already keeps `max_disciples`
cultivators ("... already keeps 3 disciples and will take no more"), and the
picker read the master's rank and realm and never the count, so a full master was
the first name on the list and a dead end. The roster now carries the count the
engine counts, and the picker leaves a full master out.
"""
from __future__ import annotations

import importlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import PROJECT_ROOT, install_aiosqlite_shim, install_openai_shim, seed_character

install_aiosqlite_shim()
install_openai_shim()

from app.database import Database  # noqa: E402
from app.rules.game import World  # noqa: E402

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
ATTRS = {"body": 4, "agility": 3, "spirit": 5, "insight": 4, "will": 5, "presence": 2}
WORLD = World(PROJECT_ROOT / "content" / "world.json")
SECT = "Azure Sword Sect"
NPC_MASTER_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "npc_master.go").read_text(encoding="utf-8")


def _elder(name: str, *, realm: int = 5) -> dict:
    return {"name": name, "realm_index": realm, "phase": 1, "sect_rank": "Elder"}


class TheRosterCountsWhatTheEngineCounts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "masters.sqlite3")
        await self.db.init()
        now = time.time()
        async with self.db._connect() as conn:
            for name in ("Elder Full", "Elder Free"):
                await conn.execute(
                    "INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,faction,realm_index,phase,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (name, "Greenriver Town", "Greenriver Town", "Mortal World", "elder", SECT, 5, 1, now))
                await conn.execute("INSERT INTO npc_life_state(npc_name,sect_rank,updated_at) VALUES(?,?,?)", (name, "Elder", now))
            await conn.commit()
        rule = WORLD.npc_master_rule()
        self.assertGreater(rule["max_disciples"], 0, "no shipped cap, so the fixture proves nothing")
        for index in range(rule["max_disciples"]):
            uid = 9000 + index
            self.assertTrue(await seed_character(
                self.db, user_id=uid, discord_name=f"d{index}", name=f"Disciple {index}", origin="Greenriver Town",
                path="Qi Refiner", spiritual_root="Wood", concept="a pupil", location="Greenriver Town",
                attributes=ATTRS, qi_max=30, vitality_max=30))
            async with self.db._connect() as conn:
                await conn.execute("PRAGMA foreign_keys=ON;")
                await conn.execute(
                    "INSERT INTO npc_mentorships(disciple_user_id,master_npc_name,sect_name,accepted_game_minute,attention,created_at)"
                    " VALUES(?,?,?,?,?,?)", (uid, "Elder Full", SECT, 0, 0, now))
                await conn.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_the_roster_carries_the_count(self):
        roster = {row["name"]: row for row in await self.db.get_sect_npc_roster(SECT)}
        self.assertEqual(set(roster), {"Elder Full", "Elder Free"}, "the reader finds nobody, so the count proves nothing")
        self.assertEqual(roster["Elder Full"]["disciples"], WORLD.npc_master_rule()["max_disciples"])
        self.assertEqual(roster["Elder Free"]["disciples"], 0)

    async def test_the_picker_leaves_out_the_master_the_engine_would_refuse(self):
        with patch.dict(os.environ, ENV):
            sect = importlib.import_module("app.bot.commands.sect")
        people = await self.db.get_sect_npc_roster(SECT)
        offered = [row["name"] for row in sect._masters_who_would_hear(people, WORLD.npc_master_rule(), power=0)]
        self.assertEqual(offered, ["Elder Free"],
                         "the picker offers a master who already keeps the most disciples the engine allows")


class TheEngineRefusesWhatThePickerLeavesOut(unittest.TestCase):
    """The Go half, read as source: the count the picker uses is the engine's own."""

    def test_the_engine_counts_mentorships_by_master_and_refuses_at_the_cap(self):
        self.assertRegex(NPC_MASTER_GO, r"SELECT COUNT\(\*\) FROM npc_mentorships WHERE master_npc_name=\?")
        self.assertRegex(NPC_MASTER_GO, r"i64\(r\.Rows\[0\]\[0\]\) >= rule\.MaxDisciples")
        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        self.assertIn("SELECT COUNT(*) FROM npc_mentorships m WHERE m.master_npc_name=c.npc_name", core)

    def test_a_row_with_no_count_is_offered_not_hidden(self):
        with patch.dict(os.environ, ENV):
            sect = importlib.import_module("app.bot.commands.sect")
        rule = WORLD.npc_master_rule()
        self.assertEqual([r["name"] for r in sect._masters_who_would_hear([_elder("Elder Unknown")], rule, power=0)],
                         ["Elder Unknown"])
        full = {**_elder("Elder Full"), "disciples": rule["max_disciples"]}
        self.assertEqual(sect._masters_who_would_hear([full], rule, power=0), [])
        self.assertEqual(sect._masters_who_would_hear([{**full, "disciples": rule["max_disciples"] - 1}], rule, power=0), [{**full, "disciples": rule["max_disciples"] - 1}])


if __name__ == "__main__":
    unittest.main()

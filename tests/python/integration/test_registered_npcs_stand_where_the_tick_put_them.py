"""A registered person is offered where the tick put them (v1.25.0).

`npc_registry.location` is where somebody was put and nothing moves it once a
simulation row exists. While the registry held a household's relatives, who
never leave home, that did not matter; a sect's twenty-five generated people
travel like anybody else, so `DB.list_registered_npcs_at` - which feeds the
`/talk` picker - answers from the simulation row when there is one, in the
order `current_npc_location` already keeps.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim
install_aiosqlite_shim()

from app.database import Database


class RegisteredPeopleStandWhereTheTickPutThem(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "registry.sqlite3")
        await self.db.init()
        now = time.time()
        async with self.db._connect() as conn:
            for name, origin, place in (("Uncle Bao", "birth_family", "birth_family:1"),
                                        ("Shen Chen", "sect", "Azure Cloud Mountain Gate")):
                await conn.execute(
                    """INSERT INTO npc_registry(name,origin,role,realm,location,sect_affiliation,created_game_minute,created_at,updated_at)
                       VALUES(?,?,'','Body Tempering',?,'',0,?,?)""", (name, origin, place, now, now))
            # The tick has walked the sect elder into the city.
            await conn.execute(
                """INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,faction,
                   wealth,influence,ambition,realm_index,phase,status,activity,last_game_minute,updated_at)
                   VALUES('Shen Chen','Azure Cloud Mountain Gate','Cloudblade City','Mortal World','Elder','Azure Cloud Sect',
                   10,10,10,4,1,'alive','',0,?)""", (now,))
            await conn.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def names_at(self, place):
        return [row["name"] for row in await self.db.list_registered_npcs_at(place)]

    async def test_somebody_with_no_simulation_row_stands_where_they_were_put(self):
        self.assertEqual(await self.names_at("birth_family:1"), ["Uncle Bao"])

    async def test_somebody_the_tick_moved_is_not_offered_where_they_were_put(self):
        self.assertEqual(await self.names_at("Azure Cloud Mountain Gate"), [],
                         "the picker offered a sect elder at the gate the simulation walked them away from")

    async def test_and_is_offered_where_they_stand(self):
        rows = await self.db.list_registered_npcs_at("Cloudblade City")
        self.assertEqual([r["name"] for r in rows], ["Shen Chen"])
        self.assertEqual(rows[0]["location"], "Cloudblade City")

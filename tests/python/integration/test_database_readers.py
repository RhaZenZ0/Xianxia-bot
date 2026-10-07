"""Database readers: search, known recipes, command usage, registered NPCs, private scene routing.

Merged from:

test_a_search_escapes_its_wildcards.py — `search_catalog` escapes LIKE's wildcards (v1.2.1).

The needle was `%{query}%` with no ESCAPE clause, so a GM typing `_` into an
Admin Console picker got every name in the table rather than a narrowed one,
and a `%` typed as text was a wildcard. Driven against a real database rather
than read off the source, because the spelling of an escape is exactly the
thing a source read gets wrong.

test_known_recipes.py — The reader that tells a player what they have learned must actually run.

`DB.get_known_recipes` (v1.0.1) is the first Python reader `character_recipes`
has ever had. Its first version raised on every call - it did `dict(row)`
without setting `db.row_factory`, so a row came back as a bare tuple, `dict`
walked the first string instead, and the page died with *"dictionary update
sequence element #0 has length 19"*, 19 being the length of "Swift-Wind
Talisman".

**No source read would have caught it and no gate did.**
`test_authority_boundary` requires every state function to have a production
caller, and it had one; `test_a_recipe_tells_you_what_it_needs` requires the
page to call it, and it did. What neither asks is whether the call *works*, and
nothing else executed it - so the Discord playtest found it, on a leaf that had
passed for as long as the leaf had existed.

That is the argument for this file: a new reader gets a test that calls it
against a real database, not only a gate that says it is referenced.

test_the_dashboard_shows_the_most_used_commands.py — The GM dashboard shows the most-used commands (v1.22.0).

The bot has counted every press since v1.3.5 (`command_usage`), and the only
place a GM could read the counts was Discord's `/admin server observability`.
Player Activity carries the card now. What is held:

- it reads the same table over the same window the bot prunes to, most used
  first, with the totals the card prints beside it;
- an unreadable count is `None` - "unknown" on the page - never an empty list
  a GM would read as "nobody plays", the `engine —` footer lesson (v1.0.8);
- the table is registered to the view that reads it, and the page draws it.

test_registered_npcs_stand_where_the_tick_put_them.py — A registered person is offered where the tick put them (v1.25.0).

`npc_registry.location` is where somebody was put and nothing moves it once a
simulation row exists. While the registry held a household's relatives, who
never leave home, that did not matter; a sect's twenty-five generated people
travel like anybody else, so `DB.list_registered_npcs_at` - which feeds the
`/talk` picker - answers from the simulation row when there is one, in the
order `current_npc_location` already keeps.

test_private_scene_routing.py — (no docstring)
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character, seed_content_tables

install_aiosqlite_shim()

from app.dashboard.contract import DASHBOARD_SYSTEM_TABLES  # noqa: E402
from app.dashboard.server import ReadOnlyDashboardStore  # noqa: E402
from app.database import Database  # noqa: E402
from app.database.core import _usage_day, COMMAND_USAGE_DAYS  # noqa: E402
from app.rules.game import World  # noqa: E402


# --- from test_a_search_escapes_its_wildcards.py ---

class ASearchEscapesItsWildcards(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "search.sqlite3")
        await self.db.init()
        await seed_content_tables(self.db, World(PROJECT_ROOT / "content" / "world.json").data)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_a_plain_search_still_finds_its_name(self):
        self.assertIn("Gate Captain Yue Dong", await self.db.search_catalog("npc", "yue dong", 25))

    async def test_an_underscore_matches_nothing_rather_than_everything(self):
        self.assertEqual(await self.db.search_catalog("npc", "_", 25), [])
        self.assertEqual(await self.db.search_catalog("npc", "%", 25), [])

    async def test_a_backslash_is_text_too(self):
        self.assertEqual(await self.db.search_catalog("npc", "\\", 25), [])


# --- from test_known_recipes.py ---

ATTRS = {"body": 4, "agility": 3, "spirit": 5, "insight": 4, "will": 5, "presence": 2}


class KnownRecipesReadBack(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "recipes.sqlite3")
        await self.db.init()
        self.assertTrue(await seed_character(
            self.db, user_id=101, discord_name="scribe", name="Scribe", origin="Greenriver Town",
            path="Formation Adept", spiritual_root="Wind", concept="a talisman scribe",
            location="Greenriver Town", attributes=ATTRS, qi_max=30, vitality_max=30,
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_a_cultivator_who_knows_nothing_reads_back_nothing(self):
        self.assertEqual(await self.db.get_known_recipes(101), [])

    async def test_every_learned_method_reads_back_with_its_columns(self):
        async with self.db._connect() as conn:
            await conn.execute(
                "INSERT INTO character_recipes(user_id,recipe,source,learned_game_minute) VALUES(?,?,?,?)",
                (101, "Swift-Wind Talisman", "slip", 120),
            )
            await conn.execute(
                "INSERT INTO character_recipes(user_id,recipe,source,learned_game_minute) VALUES(?,?,?,?)",
                (101, "Stone-Skin Talisman", "family_lesson", 40),
            )
            await conn.commit()

        known = await self.db.get_known_recipes(101)
        # Each row is a mapping with the three columns the status page reads.
        # The old version raised here rather than returning anything at all.
        self.assertEqual([entry["recipe"] for entry in known],
                         ["Swift-Wind Talisman", "Stone-Skin Talisman"],
                         "newest first, by learned_game_minute")
        self.assertEqual(known[0]["source"], "slip")
        self.assertEqual(int(known[1]["learned_game_minute"]), 40)

    async def test_one_cultivators_methods_are_not_anothers(self):
        self.assertTrue(await seed_character(
            self.db, user_id=202, discord_name="smith", name="Smith", origin="Greenriver Town",
            path="Body Refiner", spiritual_root="Earth", concept="a smith",
            location="Greenriver Town", attributes=ATTRS, qi_max=30, vitality_max=30,
        ))
        async with self.db._connect() as conn:
            await conn.execute(
                "INSERT INTO character_recipes(user_id,recipe,source) VALUES(?,?,?)",
                (202, "Recovery Pill", "slip"),
            )
            await conn.commit()
        self.assertEqual(await self.db.get_known_recipes(101), [])
        self.assertEqual([e["recipe"] for e in await self.db.get_known_recipes(202)], ["Recovery Pill"])


# --- from test_the_dashboard_shows_the_most_used_commands.py ---

APP_JS = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")


class TheCardReadsTheCounts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dashboard.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        self.store = ReadOnlyDashboardStore(self.path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _seed(self, path: str, day: str, presses: int) -> None:
        async with self.db._connect() as db:
            await db.execute("INSERT INTO command_usage(path, day, presses) VALUES(?,?,?)", (path, day, presses))
            await db.commit()

    async def test_most_used_first_inside_the_window_with_totals(self):
        today = _usage_day()
        yesterday = _usage_day(time.time() - 86400)
        long_ago = _usage_day(time.time() - (COMMAND_USAGE_DAYS + 5) * 86400)
        await self._seed("/explore", today, 7)
        await self._seed("/explore", yesterday, 5)
        await self._seed("/cultivate", today, 9)
        await self._seed("/hunt", long_ago, 500)  # pruned window: not counted
        data = await self.store.players()
        self.assertEqual(data["command_usage_days"], COMMAND_USAGE_DAYS)
        rows = [(r["path"], r["presses"], r["days_used"]) for r in data["command_usage"]]
        self.assertEqual(rows, [("/explore", 12, 2), ("/cultivate", 9, 1)])
        self.assertEqual(data["command_usage"][0]["last_day"], today)
        self.assertEqual(data["command_usage_total"], 21)
        self.assertEqual(data["command_usage_paths"], 2)

    async def test_nothing_recorded_is_an_empty_list(self):
        data = await self.store.players()
        self.assertEqual(data["command_usage"], [])
        self.assertEqual(data["command_usage_total"], 0)

    async def test_an_unreadable_count_is_unknown_not_empty(self):
        async with self.db._connect() as db:
            await db.execute("DROP TABLE command_usage")
            await db.commit()
        data = await self.store.players()
        self.assertIsNone(data["command_usage"], "an unreadable count was drawn as nobody pressing anything")
        self.assertIn("command_usage", data["command_usage_error"])
        # The rest of the page still answers.
        self.assertIn("players", data)


class ThePageDrawsIt(unittest.TestCase):
    def test_the_table_belongs_to_player_activity_and_the_page_draws_the_card(self):
        self.assertIn("command_usage", DASHBOARD_SYSTEM_TABLES["players"])
        self.assertIn("${commandUsageCard(d)}", APP_JS)
        card = APP_JS.split("function commandUsageCard(d){", 1)[1].split("\n", 1)[0]
        self.assertIn("d.command_usage==null", card, "an unreadable count is no longer told apart from an empty one")
        self.assertIn("unknown", card)


# --- from test_registered_npcs_stand_where_the_tick_put_them.py ---

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


# --- from test_private_scene_routing.py ---

ROOT = PROJECT_ROOT
ATTRS_SCENES = {"body": 4, "agility": 4, "spirit": 4, "insight": 4, "will": 4, "presence": 4}


class PrivateSceneRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "scenes.sqlite3")
        await self.db.init()
        self.assertTrue(await seed_character(self.db,
            user_id=1801, discord_name="sceneuser", name="Jin Wei",
            origin="Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
            concept="wanderer", location="Greenriver Town", attributes=ATTRS_SCENES,
            qi_max=20, vitality_max=20, created_game_minute=10,
        ))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_info_expedition_and_scene_threads_are_stored(self):
        await self.db.set_server_channels(
            77, announcement_channel_id=1, event_scene_channel_id=2,
            home_scene_channel_id=3, log_channel_id=4, begin_channel_id=5,
            info_channel_id=6, exploration_channel_id=7,
        )
        cfg = await self.db.get_server_config(77)
        self.assertEqual(cfg["info_channel_id"], 6)
        self.assertEqual(cfg["exploration_channel_id"], 7)

        await self.db.set_expedition_thread(
            77, 1801, thread_id=8001, parent_channel_id=7, last_location="Greenriver Town"
        )
        row = await self.db.get_expedition_thread(77, 1801)
        self.assertEqual(row["thread_id"], 8001)
        self.assertEqual((await self.db.get_expedition_thread_by_thread(8001))["user_id"], 1801)
        await self.db.update_expedition_location(77, 1801, "Moonfen Marsh")
        self.assertEqual((await self.db.get_expedition_thread(77, 1801))["last_location"], "Moonfen Marsh")

        async with self.db._connect() as db:
            cur = await db.execute(
                "INSERT INTO birth_families(family_name,surname,archetype,tier,wealth,influence,stability,alignment_bias,location,head_name,head_gender,head_title,head_realm_index,head_phase,treasury_balance,generation,created_game_minute,last_simulated_game_minute,history_json,line_status,clan_structure,bloodline_name,bloodline_affinity,bloodline_trait,bloodline_purity,branch_count,retainer_count,confederacy_name,created_at,updated_at,starter_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("Han Family","Han","martial_household",2,42,48,62,0,"Greenriver Town","Han Wei","male","Patriarch",0,1,168,1,10,10,"[]","active","martial_household","None","None","None",0,1,4,"None",0,0,"starter:mortal_world:martial_household"),
            )
            family_id = int(cur.lastrowid)
            await db.commit()
        await self.db.set_birth_family_household_thread(77, family_id, thread_id=8101, parent_channel_id=3)
        household = await self.db.get_birth_family_household_thread(77, family_id)
        self.assertEqual(household["thread_id"], 8101)
        self.assertEqual((await self.db.get_birth_family_household_thread_by_thread(8101))["family_id"], family_id)

    async def test_sect_abode_is_one_persistent_private_location_per_member(self):
        async with self.db._connect() as db:
            await db.execute(
                "INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(1801,'Azure Cloud Sect','Outer Disciple',10,0)"
            )
            await db.commit()
        abode = await self.db.ensure_sect_abode(
            1801, sect_name="Azure Cloud Sect", name="Jin Wei's Disciple Courtyard", base_location="Cloudspine Foothills"
        )
        self.assertEqual(abode["location_key"], "sect_abode:1801")
        await self.db.set_sect_abode_thread(1801, thread_id=9001, thread_channel_id=3)
        self.assertEqual((await self.db.get_sect_abode_by_thread(9001))["sect_name"], "Azure Cloud Sect")
        self.assertEqual((await self.db.get_sect_abode_by_location("sect_abode:1801"))["user_id"], 1801)


if __name__ == "__main__":
    unittest.main()

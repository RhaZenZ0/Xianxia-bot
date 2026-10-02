"""The Celestial ring turns (v1.19.1).

A city's gates come from its roads, and both ends of a road face opposite ways,
so the Celestial World's cities always formed a ring - but at Starroad
Celestial City and at Lunar Shadow Celestial City one gate faced both of the
city's roads, and a walk around the ring went east, doubled back west, and
doubled back again: ten east gates and nine west against two north and two
south. The two folds turn north and south now (`scripts/author_celestial_compass.py`),
six gates are renamed to the side their road leaves by, and migration 75
carries a running world onto the new names.

What is held here: no city faces all of its roads by one gate, no world's
compass is lopsided, the frozen rename pairs are the script's, nothing in the
content names an old gate, the migration rewrites every place column a running
world can hold one in - and every place-like column a fresh bootstrap makes is
either rewritten or named below with the reason it is not.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

WORLD = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LOCATIONS = WORLD["locations"]
CITIES = sorted({h["entrance_location"] for h in WORLD["auction_houses"].values()})

# Every place-like column a fresh bootstrap makes that migration 75 does not
# rewrite, with the reason. The vocabulary is `PLACE_WORDS`; a column that
# matches it and is in neither the migration nor this map fails the gate, so a
# column nobody decided about cannot be missed by the next rename either.
LEFT_ALONE = {
    ("admin_audit_log", "target"): "a log records what was done to what, as it was then",
    ("battles", "target_key"): "the opponent's name, never a place",
    ("cave_abodes", "location_key"): "an abode: key, never a gate",
    ("sect_abodes", "location_key"): "a sect residence key, never a gate",
    ("personal_worlds", "location_key"): "a personal_world: key, never a gate",
    ("characters", "origin"): "the origin a character was created with, not where they stand",
    ("npc_registry", "origin"): "how a registered person came to be (descendant, gm...)",
    ("quest_definitions", "origin"): "who drafted a quest (catalogue, forge, gm)",
    ("npc_mind_state", "focus_target"): "a person an NPC is thinking about",
    ("reincarnation_state", "target_world"): "a world, and no world was renamed",
    ("samsara_dynasty_claims", "target_family_name"): "a family's name",
    ("samsara_dynasty_claims", "target_world"): "a world, and no world was renamed",
    ("samsara_dynasty_history", "destination_family_name"): "a family's name",
    ("samsara_dynasty_history", "destination_family_archetype"): "a household archetype",
    ("samsara_dynasty_history", "destination_world"): "a world, and no world was renamed",
    ("world_action_events", "target_type"): "a kind of target, not a target",
    ("world_history_events", "target_type"): "a kind of target, not a target",
    ("world_event_actions", "target"): "a node of an event's site",
    ("world_event_participation", "last_target"): "a node of an event's site",
}
PLACE_WORDS = re.compile(r"(location|destination|origin|city|place|home|site|territory_key|scene_key|scene_label|target|actor_key)")


def _core():
    from tests.support import install_aiosqlite_shim
    install_aiosqlite_shim()
    from app.database import core
    return core


def _gate_sides(world: str) -> Counter:
    return Counter(loc["gate"] for loc in LOCATIONS.values() if loc.get("world") == world and loc.get("district") == "gate")


class TheCompassIsARing(unittest.TestCase):
    def test_the_reader_sees_the_world(self):
        """Asserted before it is trusted (rc.57)."""
        self.assertEqual(len(CITIES), 48)
        self.assertEqual(sum(_gate_sides("Celestial World").values()), 24)

    def test_no_city_faces_every_road_by_one_gate(self):
        for city in CITIES:
            roads = LOCATIONS[city].get("roads") or []
            with self.subTest(city=city):
                if len(roads) > 1:
                    self.assertGreater(len(LOCATIONS[city].get("gates") or {}), 1,
                                       f"{city} faces all {len(roads)} of its roads by one gate: the compass folds back on itself there")

    def test_no_world_compass_is_lopsided(self):
        for world in ("Mortal World", "Spiritual World", "Immortal World", "Celestial World"):
            sides = _gate_sides(world)
            with self.subTest(world=world):
                self.assertEqual(set(sides), {"North", "South", "East", "West"}, sides)
                self.assertLessEqual(max(sides.values()), 2 * min(sides.values()), f"{world}: {dict(sides)}")

    def test_the_frozen_pairs_are_the_scripts(self):
        import author_celestial_compass as script
        core = _core()
        self.assertEqual(dict(core.CELESTIAL_GATE_RENAMES_AT_V1_19_1), script.GATE_RENAMES,
                         "a later rename is a migration of its own, not an edit to this one")

    def test_every_old_gate_is_gone_and_every_new_one_stands(self):
        core = _core()
        text = json.dumps(WORLD, ensure_ascii=False)
        for old, new in core.CELESTIAL_GATE_RENAMES_AT_V1_19_1:
            with self.subTest(gate=old):
                # Not assertNotIn: its message would print the whole content file.
                self.assertFalse(old in text, f"the content still names {old}")
                self.assertEqual(LOCATIONS[new].get("district"), "gate")
                self.assertEqual(LOCATIONS[new]["gate"], new.split()[-2])


_SERIAL = itertools.count(1000)


def _fill(conn, table: str, values: dict) -> None:
    """Insert one row, filling every NOT NULL column without a default."""
    cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    rowid_alias = sum(1 for c in cols if c[5]) == 1
    row = dict(values)
    for _, name, typ, notnull, default, pk in cols:
        if name in row or default is not None or (pk and rowid_alias and typ.upper() == "INTEGER"):
            continue
        if not notnull and not pk:
            continue
        row[name] = next(_SERIAL) if typ.upper() in ("INTEGER", "REAL") else f"{table}-{name}-{next(_SERIAL)}"
    names = ",".join(row)
    conn.execute(f"INSERT INTO {table}({names}) VALUES({','.join('?' for _ in row)})", list(row.values()))


class ARunningWorldIsCarriedOntoTheNewGates(unittest.IsolatedAsyncioTestCase):
    async def _world_at(self, version: int) -> Path:
        core = _core()
        from app.database import Database
        path = Path(tempfile.mkdtemp()) / "ring.sqlite3"
        migrations = tuple(m for m in core.SCHEMA_MIGRATIONS if int(m[0]) <= version)
        with patch.object(core, "SCHEMA_VERSION", version), patch.object(core, "SCHEMA_MIGRATIONS", migrations):
            await Database(path).init()
        return path

    async def test_every_place_column_is_rewritten(self):
        core = _core()
        from app.database import Database
        old, new = "Starroad Celestial City East Gate", "Starroad Celestial City North Gate"
        merged_old, merged_new = "Celestial River City West Gate", "Celestial River City South Gate"
        path = await self._world_at(74)
        with sqlite3.connect(path) as conn:
            _fill(conn, "characters", {"user_id": 1, "name": "Ring Walker", "location": old})
            by_table: dict[str, dict] = {}
            for table, column in core.PLACE_COLUMNS_AT_V1_19_1:
                by_table.setdefault(table, {})[column] = old
            for table, values in by_table.items():
                if table == "characters":
                    continue
                if "user_id" in {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")}:
                    values["user_id"] = 1
                _fill(conn, table, values)
            # A discovery of both the folded gate and the gate it folds into:
            # the one that stood is kept and the old one goes.
            for place in (merged_old, merged_new, old):
                _fill(conn, "character_location_discoveries", {"user_id": 1, "location": place})
            # A claim on the renamed gate with a war over it: the war follows.
            conn.execute("INSERT INTO territory_state(territory_key,name,region,controller_type,controller_key,prosperity,updated_at) VALUES(?,?,?,?,?,?,0)",
                         (old, old, "Celestial World", "sect", "Void Serpent Cult", 77))
            _fill(conn, "territory_wars", {"attacker_key": "a", "defender_key": "b", "territory_key": old})
            for place in (merged_old, merged_new):
                conn.execute("INSERT INTO territory_state(territory_key,name,region,updated_at) VALUES(?,?,?,0)", (place, place, "Celestial World"))
            # Live state written as text: a journey on the road, a quest's target.
            conn.execute("INSERT INTO world_state(key,value_json,updated_at) VALUES('road_transit:1',?,0)",
                         (json.dumps({"origin": "Celestial River City", "destination": old, "route": [old]}),))
            _fill(conn, "quest_definitions", {"quest_key": "walk_the_gate", "objectives_json": json.dumps([{"type": "explore", "target": old, "label": f"Walk the {old} twice"}])})
            conn.commit()
        await Database(path).init()
        with sqlite3.connect(path) as conn:
            version = conn.execute("SELECT current_version FROM schema_version WHERE singleton=1").fetchone()[0]
            self.assertGreaterEqual(version, 75, "the migration did not run; the reader is broken, not the tree")
            for table, column in core.PLACE_COLUMNS_AT_V1_19_1:
                with self.subTest(column=f"{table}.{column}"):
                    values = {r[0] for r in conn.execute(f"SELECT {column} FROM {table}")}
                    self.assertNotIn(old, values)
                    self.assertIn(new, values, f"{table}.{column} was not carried onto the new gate")
            discovered = sorted(r[0] for r in conn.execute("SELECT location FROM character_location_discoveries WHERE user_id=1"))
            self.assertEqual(discovered, sorted([merged_new, new]), "the folded gate's discovery is kept once, on the gate that stood")
            territory = {r[0]: r for r in conn.execute("SELECT territory_key,controller_type,controller_key,prosperity FROM territory_state")}
            self.assertNotIn(old, territory)
            self.assertNotIn(merged_old, territory)
            self.assertEqual(territory[new][1:], ("sect", "Void Serpent Cult", 77), "the claim moved with its gate, whole")
            self.assertEqual([r[0] for r in conn.execute("SELECT territory_key FROM territory_wars")], [new], "the war followed its ground")
            transit = json.loads(conn.execute("SELECT value_json FROM world_state WHERE key='road_transit:1'").fetchone()[0])
            self.assertEqual((transit["destination"], transit["route"]), (new, [new]))
            objectives = conn.execute("SELECT objectives_json FROM quest_definitions WHERE quest_key='walk_the_gate'").fetchone()[0]
            self.assertNotIn(old, objectives)
            self.assertEqual(json.loads(objectives)[0]["target"], new)
            # The seeded rows dangle keys of their own; the one key the
            # migration moves is the war's, and it must point at a row.
            self.assertEqual(conn.execute("PRAGMA foreign_key_check(territory_wars)").fetchall(), [])


class EveryPlaceColumnIsDecided(unittest.TestCase):
    def test_every_place_like_column_is_decided(self):
        core = _core()
        decided = {(t, c) for t, c in core.PLACE_COLUMNS_AT_V1_19_1}
        decided |= {(t, c) for t, c, _ in core.PLACE_COLUMNS_KEYED}
        decided |= {("territory_state", "territory_key"), ("territory_wars", "territory_key")}
        self.assertFalse(decided & set(LEFT_ALONE), "a column is both rewritten and left alone")
        db = await_bootstrap()
        with sqlite3.connect(db) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql NOT LIKE 'CREATE VIRTUAL%'")]
            found = set()
            for table in tables:
                if table.startswith(("content_", "sqlite_")) or "_fts" in table:
                    continue
                for _, column, typ, *_ in conn.execute(f"PRAGMA table_info('{table}')"):
                    if typ.upper() in ("INTEGER", "REAL"):
                        continue
                    if PLACE_WORDS.search(column):
                        found.add((table, column))
        self.assertIn(("characters", "location"), found, "the schema reader found nothing; the gate is broken, not the tree")
        undecided = sorted(found - decided - set(LEFT_ALONE))
        self.assertEqual(undecided, [], "a place-like column nobody decided about: rewrite it in migration 75's lists or say why not in LEFT_ALONE")
        stale = sorted((decided | set(LEFT_ALONE)) - found)
        self.assertEqual(stale, [], "a decided column the schema no longer has")


def await_bootstrap() -> Path:
    from app.database import Database
    _core()
    path = Path(tempfile.mkdtemp()) / "fresh.sqlite3"
    asyncio.run(Database(path).init())
    return path


if __name__ == "__main__":
    unittest.main()

"""A world offers its own work (v1.19.2).

A commission's `realm_band` is an inclusive realm range read by two doors to
the same work: the offer an NPC makes in conversation (`realm_band_allows` in
`app/rules/commissions.py`) and the city's notice board. Every upper-world
city commission was authored with a Mortal band - "2-4" in the Spiritual
World, "4-8" in the Immortal and Celestial - so in conversation none of the
seventy-eight was ever offered to anybody who could stand in its world, while
the board, which read no band at all, offered all of them to anybody.

Held here: every commission's band lies inside the realms of the world it is
posted in (the rule that would have caught it), the frozen migration pairs
are the authoring script's, the board reads the band the conversation reads,
and migration 76 moves a running world's pool while obeying a GM's own edit.
"""
from __future__ import annotations

import importlib
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
sys.path.insert(0, str(ROOT / "scripts"))

WORLD = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _realms_of(world: str) -> range:
    """The realm indices that stand in a world, read off the ladder."""
    indices = [i for i, realm in enumerate(WORLD["realms"]) if realm.get("world") == world]
    return range(min(indices), max(indices) + 1)


def _band(text: str) -> tuple[int, int] | None:
    text = str(text or "").strip()
    if not text:
        return None
    low, _, high = text.partition("-")
    return int(low), int(high or low)


class EveryBandSitsInItsOwnWorld(unittest.TestCase):
    def test_the_reader_sees_the_world(self):
        """Asserted before it is trusted (rc.57)."""
        import author_commission_bands as script
        self.assertEqual(len(WORLD["commissions"]), 140)
        self.assertEqual(list(_realms_of("Mortal World")), list(range(0, 8)))
        self.assertEqual(script.commission_world(WORLD, next(c for c in WORLD["commissions"]
                                                                if c["quest_key"] == "commission_city_starroad_celestial_city_1")),
                         "Celestial World")

    def test_every_commission_is_offered_where_it_is_posted(self):
        import author_commission_bands as script
        for commission in WORLD["commissions"]:
            band = _band(commission.get("realm_band"))
            if band is None:
                continue
            world = script.commission_world(WORLD, commission)
            realms = _realms_of(world)
            with self.subTest(quest=commission["quest_key"]):
                self.assertTrue(band[0] in realms and band[1] in realms,
                                f"{commission['quest_key']} is posted in the {world} (realms {realms.start}-{realms.stop - 1}) "
                                f"and banded {commission['realm_band']}: nobody standing there can be offered it")

    def test_the_frozen_pairs_are_the_scripts(self):
        import author_commission_bands as script
        core = _core()
        frozen = {key: (old, new) for old, new, keys in core.COMMISSION_BANDS_AT_V1_19_2 for key in keys}
        self.assertEqual(frozen, script.moved(WORLD), "a later re-banding is a migration of its own, not an edit to this one")
        self.assertEqual(len(frozen), 78)


def _core():
    from tests.support import install_aiosqlite_shim
    install_aiosqlite_shim()
    from app.database import core
    return core


class TheBoardReadsTheBand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, ENV):
            cls.ex = importlib.import_module("app.bot.commands.exploration")

    def test_a_city_board_offers_its_work_to_its_own_realms_alone(self):
        city = "Starroad Celestial City"
        everything = {q["quest_key"] for q in self.ex._city_board(city)}
        self.assertIn("commission_city_starroad_celestial_city_1", everything, "the reader is broken, not the tree")
        self.assertEqual({q["quest_key"] for q in self.ex._city_board(city, 26)}, everything)
        self.assertEqual(self.ex._city_board(city, 5), [], "a Mortal cultivator was offered Celestial work on the board")

    def test_the_board_and_the_conversation_agree(self):
        from app.rules.commissions import realm_band_allows
        for city in ("Cloudblade City", "Jade Crown Spirit City", "Heavenblade Immortal City", "Starroad Celestial City"):
            for realm in (0, 4, 10, 20, 28):
                with self.subTest(city=city, realm=realm):
                    want = {q["quest_key"] for q in self.ex._city_board(city)
                            if realm_band_allows(str(q.get("realm_band") or ""), realm)}
                    self.assertEqual({q["quest_key"] for q in self.ex._city_board(city, realm)}, want)


class ARunningWorldIsRebanded(unittest.IsolatedAsyncioTestCase):
    async def test_the_pool_moves_and_a_gms_edit_is_kept(self):
        core = _core()
        from app.database import Database
        path = Path(tempfile.mkdtemp()) / "bands.sqlite3"
        migrations = tuple(m for m in core.SCHEMA_MIGRATIONS if int(m[0]) <= 75)
        with patch.object(core, "SCHEMA_VERSION", 75), patch.object(core, "SCHEMA_MIGRATIONS", migrations):
            await Database(path).init()
        rows = {
            "commission_city_starroad_celestial_city_1": "4-8",
            "commission_city_jade_crown_spirit_city_1": "2-4",
            "commission_city_heavenblade_immortal_city_1": "4-8",
            "commission_city_heavenblade_immortal_city_2": "17-20",  # a GM's own edit
        }
        with sqlite3.connect(path) as conn:
            for key, band in rows.items():
                conn.execute("INSERT INTO quest_definitions(quest_key,title,description,source_type,source_key,objectives_json,"
                             "rewards_json,status,origin,created_at,updated_at,realm_band) VALUES(?,?,'','commission',?,'[]','{}',"
                             "'approved','catalogue',0,0,?)", (key, key, key, band))
            conn.commit()
        await Database(path).init()
        with sqlite3.connect(path) as conn:
            version = conn.execute("SELECT current_version FROM schema_version WHERE singleton=1").fetchone()[0]
            bands = dict(conn.execute("SELECT quest_key,realm_band FROM quest_definitions WHERE quest_key LIKE 'commission_city_%'"))
        self.assertGreaterEqual(version, 76, "the migration did not run; the reader is broken, not the tree")
        self.assertEqual(bands["commission_city_starroad_celestial_city_1"], "26-31")
        self.assertEqual(bands["commission_city_jade_crown_spirit_city_1"], "9-14")
        self.assertEqual(bands["commission_city_heavenblade_immortal_city_1"], "18-23")
        self.assertEqual(bands["commission_city_heavenblade_immortal_city_2"], "17-20", "a band a GM edited was overwritten")


if __name__ == "__main__":
    unittest.main()

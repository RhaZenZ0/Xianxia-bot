"""The door into a sect, from the Discord side (v1.1.0).

Reported in Discord: at a Major Sect Recruitment world event a player talked
to the Visiting Elder, pressed him, and was told they were impatient and not
recruited. The owner answered *"get a recommendation, do quests for an elder
of a sect so your rating goes up, then travel to the sect for intake"* - and
another player asked **"What menu?"**. Every link of that road was broken: the
event named no sect, a realm-0 player could not reach the trial, no road leads
to any sect gate so exploring never finds one, the envoys' hall wrote no
route, `road_to_a_sect` could never complete, no sect work was open to
somebody in no sect, and the recommendation's "+N" was never added to a roll.

The engine half is held in Go (`sect_doors_test.go`). What is held here is
what only Python can get wrong:

- **Migration 61** reaches a running world: the columns, the two labels in the
  definition *and* in a quest a player already holds, and the outsider seed on
  the twelve keys - leaving what a GM has edited alone.
- **One statement of which work is open to outsiders**: the Python rule's
  prefix is the engine's, the Forge can never draft it, and the offer ladder
  offers sect work to somebody in no sect and never to a rival.
- **No narrator settles membership**, in either prompt, and `/talk` tells it
  what an NPC's sect ties let them do.
- **The delegation is reachable**: a running event's cast answers where it is
  standing, and the panel names the sect and the real doors.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT, install_aiosqlite_shim

install_aiosqlite_shim()

from app.database import Database  # noqa: E402
from app.database.core import SCHEMA_MIGRATIONS  # noqa: E402
from app.rules import commissions as rules  # noqa: E402
from app.rules.game import World  # noqa: E402
from app.rules.quests import QUEST_DEFINITIONS, validate_quest_definition  # noqa: E402

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
MIGRATION = next(m for m in SCHEMA_MIGRATIONS if m[0] == 61)
OLD_LABELS = ("Discover a sect", "Attempt a sect entrance trial")


def _updates() -> list[str]:
    return [s for s in MIGRATION[2] if s.lstrip().upper().startswith("UPDATE")]


class MigrationSixtyOneReachesARunningWorld(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "world.sqlite3"
        asyncio.run(Database(self.path).init())

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_is_the_migration_it_says_it_is(self):
        self.assertEqual(MIGRATION[1], "a_sect_takes_applicants_where_it_is_found")

    def test_the_columns_land(self):
        with closing(sqlite3.connect(self.path)) as conn:
            npcs = {r[1] for r in conn.execute("PRAGMA table_info(world_event_npcs)")}
            nodes = {r[1] for r in conn.execute("PRAGMA table_info(world_event_nodes)")}
        self.assertLessEqual({"sect_name", "can_recommend"}, npcs)
        self.assertIn("reveals_sect", nodes)

    def _seed_old_world(self, conn, *, gm_label: str | None = None):
        objectives = json.dumps([
            {"id": "sect_discovery", "type": "sect_discovery", "count": 1, "label": gm_label or OLD_LABELS[0]},
            {"id": "sect_trial", "type": "sect_trial", "count": 1, "label": OLD_LABELS[1]},
        ])
        conn.execute("UPDATE quest_definitions SET objectives_json=? WHERE quest_key='road_to_a_sect'", (objectives,))
        if not conn.execute("SELECT 1 FROM quest_definitions WHERE quest_key='road_to_a_sect'").fetchone():
            conn.execute(
                "INSERT INTO quest_definitions(quest_key,title,description,objectives_json,rewards_json,created_at,updated_at)"
                " VALUES('road_to_a_sect','A Road Toward a Sect','',?,'{}',0,0)", (objectives,))
        return objectives

    def test_both_labels_are_rewritten_in_the_definition_and_in_a_held_quest(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            objectives = self._seed_old_world(conn)
            columns = {r[1] for r in conn.execute("PRAGMA table_info(character_quests)")}
            self.assertIn("terms_json", columns, "the held-quest table lost its terms column; re-read the migration")
            conn.execute(
                "INSERT INTO character_quests(user_id,quest_key,status,progress_json,terms_json,created_at,updated_at)"
                " VALUES(7,'road_to_a_sect','active','{}',?,0,0)", (json.dumps({"objectives": json.loads(objectives)}),))
            for statement in _updates():
                conn.execute(statement)
            definition = conn.execute("SELECT objectives_json FROM quest_definitions WHERE quest_key='road_to_a_sect'").fetchone()[0]
            held = conn.execute("SELECT terms_json FROM character_quests WHERE user_id=7").fetchone()[0]
        wanted = [o["label"] for o in QUEST_DEFINITIONS["road_to_a_sect"]["objectives"]]
        self.assertEqual([o["label"] for o in json.loads(definition)], wanted,
                         "migration 61 and app/rules/quests.py must write the same two labels")
        self.assertEqual([o["label"] for o in json.loads(held)["objectives"]], wanted,
                         "a player already holding the quest kept the labels that named no menu")

    def test_a_label_a_gm_edited_is_left_alone(self):
        with closing(sqlite3.connect(self.path)) as conn:
            self._seed_old_world(conn, gm_label="Find the sect your master named")
            for statement in _updates():
                conn.execute(statement)
            labels = [o["label"] for o in json.loads(conn.execute(
                "SELECT objectives_json FROM quest_definitions WHERE quest_key='road_to_a_sect'").fetchone()[0])]
        self.assertEqual(labels[0], "Find the sect your master named")

    def test_the_seeded_keys_are_exactly_the_contents_outsider_work(self):
        authored = sorted(c["quest_key"] for c in CONTENT["commissions"]
                          if (c.get("seed") or {}).get("outsider_standing"))
        self.assertEqual(len(authored), 12, "the content no longer opens twelve sects' entry-level work")
        seeded = sorted(re.findall(r"'(commission_[a-z_]+)'", "\n".join(_updates())))
        self.assertEqual(seeded, authored, "migration 61 and the content file disagree about which work is open")
        for key in authored:
            row = next(c for c in CONTENT["commissions"] if c["quest_key"] == key)
            self.assertEqual(int(row.get("tier") or 1), 1, f"{key}: only a sect's entry-level work is the way in")
            self.assertTrue(rules.open_to_outsiders(row), key)

    def test_a_seed_a_gm_set_is_not_overwritten(self):
        statement = next(s for s in _updates() if "outsider_standing" in s)
        self.assertIn("seed_json IN ('', '{}')", statement)


class WhichWorkIsOpenToOutsiders(unittest.TestCase):
    def test_the_prefix_is_the_engines(self):
        source = (GO / "commission_actions.go").read_text(encoding="utf-8")
        match = re.search(r'sectCommissionPrefix\s*=\s*"([^"]+)"', source)
        self.assertIsNotNone(match, "the engine's prefix was not read; the reader is broken, not the tree")
        self.assertEqual(rules.SECT_COMMISSION_PREFIX, match.group(1))

    def test_a_forge_draft_can_never_open_sect_work(self):
        seed = {"outsider_standing": 25}
        self.assertFalse(rules.open_to_outsiders({"quest_key": "forge_a", "requires_sect": "Azure Cloud Sect", "seed": seed}))
        self.assertFalse(rules.open_to_outsiders({"quest_key": "commission_a", "requires_sect": "", "seed": seed}))
        self.assertFalse(rules.open_to_outsiders({"quest_key": "commission_a", "requires_sect": "Azure Cloud Sect", "seed": {}}))
        world = World(PROJECT_ROOT / "content" / "world.json")
        draft = {"title": "Watch the gate", "description": "Stand a watch at the gate.", "seed": seed,
                 "objectives": [{"type": "explore", "target": "Greenriver Town"}], "rewards": {"insight_xp": 5}}
        definition, errors = validate_quest_definition(draft, world, {"max_xp": 50, "max_stones": 50, "max_items": 2})
        self.assertIsNotNone(definition, f"the draft did not validate, so this proves nothing: {errors}")
        self.assertNotIn("seed", definition, "a validated Forge draft carried a seed")

    def _offer(self, player_sect: str):
        pool = [row for row in CONTENT["commissions"] if row["quest_key"] == "commission_azure_gate_roster"]
        self.assertTrue(pool, "the Azure Cloud gate roster commission is gone; pick another")
        return rules.choose_offer(giver="Gate Elder Jian Mu", relationship=None, held_commission=None,
                                  cooldown_game_minutes=0, pool=pool, taken_keys=set(), realm_index=0,
                                  user_id=7, game_minute=0, player_sect=player_sect)

    def test_somebody_in_no_sect_is_offered_the_way_in(self):
        self.assertEqual(self._offer("").kind, "offer")

    def test_a_disciple_is_offered_their_own_sects_work(self):
        self.assertEqual(self._offer("Azure Cloud Sect").kind, "offer")

    def test_a_rival_is_still_refused(self):
        offer = self._offer("Black Serpent Clan")
        self.assertEqual((offer.kind, offer.reason), ("nothing", "sect members only"))


class NoNarratorSettlesMembership(unittest.TestCase):
    def test_both_prompts_carry_the_rule(self):
        narrator = importlib.import_module("app.ai.narrator")
        for prompt in (narrator.SYSTEM_PROMPT, narrator.ROUTINE_SYSTEM_PROMPT):
            self.assertIn("No NPC grants, promises, or refuses sect membership", prompt)

    def test_talk_tells_the_model_what_the_npc_may_do(self):
        narrator = importlib.import_module("app.ai.narrator")
        source = (PROJECT_ROOT / "app" / "ai" / "narrator.py").read_text(encoding="utf-8")
        self.assertIn("NPC SECT TIES: {sect_ties}", source)
        elder = narrator._npc_sect_ties({"sect_affiliation": "Azure Cloud Sect", "can_recommend": True})
        self.assertIn("Azure Cloud Sect", elder)
        self.assertIn("never in this conversation", elder)
        self.assertIn("speaks for no sect", narrator._npc_sect_ties({}))


class TheDelegationIsReachable(unittest.TestCase):
    def _locations(self):
        with patch.dict(os.environ, ENV):
            return importlib.import_module("app.bot.locations")

    def test_a_cast_member_stands_where_the_event_is(self):
        locations = self._locations()

        async def no_registry(_name):
            return None

        async def cast(name):
            return {"location": "Greenriver Town", "event_cast": True} if name == "Visiting Elder Wen" else None

        with patch.object(locations.DB, "get_registered_npc", no_registry), \
                patch.object(locations.DB, "get_event_npc_definition", cast), \
                patch.object(locations.SIM, "npc_status", no_registry):
            where = asyncio.run(locations.current_npc_location("Visiting Elder Wen", "Morning"))
        self.assertEqual(where, "Greenriver Town",
                         "a cast member with no answer reads as 'do not filter by location' - talkable from anywhere")

    def test_the_panel_names_the_sect_and_the_real_doors(self):
        with patch.dict(os.environ, ENV):
            scene = importlib.import_module("app.bot.ui.event_scene")
        cast = [{"name": "Visiting Elder Wen", "role": "Speaks for the sect", "sect_name": "Azure Cloud Sect", "can_recommend": 1},
                {"name": "Disciple Lu", "role": "Carries the register", "sect_name": "Azure Cloud Sect", "can_recommend": 0}]
        site = [{"node_type": "task", "name": "The Entrance Trial", "remaining": 3, "total": 4, "tn": 12,
                 "attribute": "will", "reveals_sect": "Azure Cloud Sect"}]
        view = scene.EventSceneView.__new__(scene.EventSceneView)
        for key, value in {"expires_at": 9e12, "category": "Sect Recruitment", "severity": 3,
                           "location": "Greenriver Town", "title": "Major Sect Recruitment"}.items():
            setattr(view, key, value)
        embed = view.embed(cast=cast, site=site)
        text = embed.description + "\n".join(f"{f.name}\n{f.value}" for f in embed.fields)
        self.assertIn("This delegation speaks for the **Azure Cloud Sect**", text)
        self.assertIn("**/sect → Recruitment → Recommendation**", text)
        self.assertIn("clearing it shows you the Azure Cloud Sect's gate", text)
        self.assertIn("may sponsor you", text)

    def test_the_recommendation_picker_reaches_the_cast(self):
        source = (PROJECT_ROOT / "app" / "bot" / "commands" / "sect.py").read_text(encoding="utf-8")
        body = source.split("async def sect_recommender_autocomplete(")[1].split("\nasync def ")[0]
        self.assertIn("DB.list_active_event_npcs(here)", body)
        self.assertIn('row.get("can_recommend")', body)


if __name__ == "__main__":
    unittest.main()

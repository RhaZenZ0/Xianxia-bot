"""A road for every realm (v1.16.0).

The beginner path carries a new cultivator to their first gate with a place in
every label and then stops at the sect road. From realm 1 to realm 7 the places
that matter in the Mortal World - the capital, the Forge Terraces and their
flame, the Boar King in Greenriver's hills, the marsh and its serpent, the
ninth stage, the Sword Grave, the heavens - were found by reading the panel or
not at all. `realm_road` in content/world.json is the same mechanism one realm
further, seven times: an ordinary giver-less quest per realm, seeded the way
the beginner path is, chained by `follow_on`, and handed over by the crossing
into its realm so a cultivator already standing at Core Formation holds the
road from there.

This file holds the Python half. The content is whole and in realm order; every
stage passes the validator the Forge is held to; every door a label names is
drawn at the stage's own realm (v1.0.9's rule one quest further: a quest
naming a button the curriculum hides is an illegible opening); the seeder makes
ordinary quests and the bot seeds them; the menu's next-step line reads the
road; and the engine wire exists. The engine half - the crossing handing a
stage over, and a city's gate counting as the city - is `realm_road_test.go`.

Merged from:

test_the_heavens_lead_on.py — The heavens lead on (v1.23.2, schema 77).

v1.16.0 seeded `realm_road_7` with an empty `follow_on`, because the realm road
stopped at the seam. v1.18.0 carried the road through the upper worlds and
pointed that stage at `realm_road_8` in the content, and the seeding is
insert-only - so on any world running since v1.16.0, finishing the stage (by
play, or by the GM's Complete in the Player Editor) handed nothing over.
Migration 77 re-points it where the chain is still the empty one it was seeded
with, and leaves a GM's own chain alone.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import PROJECT_ROOT

from app.ops.core_services import HANDED_OVER_BY_A_ROSTER
from app.rules.game import World
from app.rules.quests import OBJECTIVE_TYPES, realm_road_seed_rows, validate_quest_definition


WORLD = World(PROJECT_ROOT / "content" / "world.json")
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
STAGES = list(CONTENT.get("realm_road") or [])
LEAVES: dict[str, int] = {str(k): int(v) for k, v in ((CONTENT.get("feature_unlocks") or {}).get("leaves") or {}).items()}
GO = PROJECT_ROOT / "go_core" / "internal" / "game"
# `**/combat → Boss Raids → Claim**`, as an objective label spells a command.
LABEL_PATH = re.compile(r"\*\*(/[^*]+)\*\*")
BUDGET = {"max_xp": 1000, "max_stones": 1000, "max_items": 5}


def _leaf_paths() -> dict[tuple[str, ...], str]:
    """The live hubs' `(hub, page, leaf) -> leaf key` as the curriculum spells
    it (`boss claim`), read off the registry so a renamed leaf fails here
    rather than silently resolving to nothing."""
    os.environ.setdefault("DISCORD_TOKEN", "gate")
    os.environ.setdefault("GUILD_ID", "123456789012345678")
    os.environ.setdefault("ENGINE_AUTH_TOKEN", "gate-token-1234567890")
    os.environ.setdefault("DATABASE_PATH", "data/gate.sqlite3")
    os.environ.setdefault("HEALTH_PORT", "18097")
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    import app.bot.surface  # noqa: F401  (registers the hubs)
    from app.bot.hubs import REGISTERED_HUBS, _leaf_actions

    out: dict[tuple[str, ...], str] = {}
    for hub in REGISTERED_HUBS:
        for page in hub.pages:
            for action in _leaf_actions(page):
                key = str(action.path).lstrip("/")
                out[(str(hub.name).casefold(), str(page.label).casefold(), str(action.label).casefold())] = key
                out.setdefault((str(hub.name).casefold(), str(action.label).casefold()), key)
    return out


def _code(source: str, name: str) -> str:
    """One function's statements without its docstring (rc.52)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            statements = node.body
            if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant):
                statements = statements[1:]
            return "\n".join(ast.get_source_segment(source, s) or "" for s in statements)
    raise AssertionError(f"{name} is gone; this gate is guarding a function that moved")


def go_function(filename: str, name: str) -> str:
    source = (GO / filename).read_text(encoding="utf-8")
    start = source.index(f"func {name}(")
    end = source.find("\nfunc ", start + 1)
    return source[start:end if end != -1 else len(source)]


class TheRoadIsWhole(unittest.TestCase):
    def test_the_content_was_read(self):
        self.assertGreaterEqual(len(STAGES), 7, "content/world.json carries no realm road; the gate is broken, not the tree")
        self.assertGreater(len(LEAVES), 50, "no feature_unlocks roster; the gate is broken, not the tree")

    def test_one_stage_per_realm_in_order(self):
        """Realm 0 is the beginner path's, so the road runs from the first
        crossing to the top of the ladder - every rung once, in the ladder's
        own order. v1.16.0 stopped it at the Mortal seam; v1.18.0 carries it
        through the three upper worlds, which the world-flow study found were
        the Mortal World again with nothing saying where to go."""
        ladder = [str(r.get("name")) for r in CONTENT.get("realms") or []]
        realms = [int(s.get("realm_index") or 0) for s in STAGES]
        self.assertEqual(realms, list(range(1, len(ladder))),
                         f"the road should cover realms 1..{len(ladder) - 1} once each in order, got {realms}")
        keys = [str(s.get("quest_key")) for s in STAGES]
        self.assertEqual(len(set(keys)), len(keys), "two stages share a quest key")

    def test_every_stage_opens_by_asking_for_its_own_realm(self):
        """The stage is handed over at the crossing *and* by the stage before
        it, so a player may hold it one realm early; its first objective says
        where it starts, and names the realm the engine will report."""
        for stage in STAGES:
            with self.subTest(stage=stage["quest_key"]):
                first = dict(stage["objectives"][0])
                self.assertEqual(first.get("type"), "breakthrough")
                self.assertEqual(first.get("target"), WORLD.realm_name(int(stage["realm_index"])),
                                 "the first objective names a realm other than the stage's own")

    def test_the_chain_runs_the_whole_road_and_stops_at_the_top(self):
        """One chain from the first crossing to the top rung, every seam
        included: the ascension quest at each seam is handed over beside the
        road by the cleared tribulation, never instead of it, so a stage at
        the seam still names the stage in the world above."""
        for index, stage in enumerate(STAGES):
            with self.subTest(stage=stage["quest_key"]):
                follow_on = str(stage.get("follow_on") or "")
                if index == len(STAGES) - 1:
                    self.assertEqual(follow_on, "", "the last stage chains on; nothing stands above the top rung")
                else:
                    self.assertEqual(follow_on, str(STAGES[index + 1]["quest_key"]), "a stage chains past its successor")

    def test_every_world_above_the_mortal_has_a_road(self):
        """The study's finding: the three upper worlds had the content and
        nothing pointing at it. Each world's stages must between them name its
        capital, its flame, its raid boss and a secret realm standing in it."""
        by_world: dict[str, list[dict]] = {}
        for stage in STAGES:
            world = str(CONTENT["realms"][int(stage["realm_index"])]["world"])
            by_world.setdefault(world, []).append(stage)
        self.assertEqual(sorted(by_world), sorted({str(r["world"]) for r in CONTENT["realms"]}))
        flames = {str(f["world"]): key for key, f in CONTENT["flame_system"]["flames"].items()}
        capitals = {str(d["world"]): name for name, d in CONTENT["locations"].items() if d.get("realm_hub")}
        realms_in = {world: {key for key, r in CONTENT["secret_realms"].items()
                             if str((CONTENT["locations"].get(str(r.get("location"))) or {}).get("world")) == world}
                     for world in by_world}
        for world, stages in by_world.items():
            with self.subTest(world=world):
                targets = {(str(o["type"]), str(o.get("target") or "")) for s in stages for o in s["objectives"]}
                self.assertIn(("travel", capitals[world]), targets, f"no stage walks to {world}'s capital")
                self.assertIn(("flame_capture", flames[world]), targets, f"no stage captures {world}'s flame")
                self.assertTrue(any(t == "raid_win" for t, _ in targets), f"no stage raids in {world}")
                self.assertTrue(any(t == "realm_enter" and target in realms_in[world] for t, target in targets),
                                f"no stage enters a secret realm standing in {world}")

    def test_every_stage_passes_the_validator_the_forge_is_held_to(self):
        for stage in STAGES:
            with self.subTest(stage=stage["quest_key"]):
                definition, errors = validate_quest_definition(stage, WORLD, BUDGET)
                self.assertEqual(errors, [])
                self.assertIsNotNone(definition)
                for objective in stage["objectives"]:
                    self.assertIn(str(objective["type"]), OBJECTIVE_TYPES)
                    self.assertRegex(str(objective["label"]), r"\*\*/[a-z]+",
                                     "an objective that names no command leaves the player where they were")

    def test_every_door_a_label_names_is_drawn_at_the_stages_realm(self):
        """v1.0.9's floor one quest further: the curriculum must not hide a
        door the road names from the realm the road names it at."""
        leaves = _leaf_paths()
        self.assertGreater(len(leaves), 100, "the hub registry came back with no leaves; the gate is broken, not the tree")
        offenders, resolved = [], 0
        for stage in STAGES:
            realm = int(stage["realm_index"])
            for objective in stage["objectives"]:
                for path in LABEL_PATH.findall(str(objective.get("label") or "")):
                    segments = tuple(seg.strip().lstrip("/").casefold() for seg in path.split("→") if seg.strip())
                    key = leaves.get(segments) or leaves.get((segments[0], segments[-1]))
                    self.assertIsNotNone(key, f"{stage['quest_key']}: the label names {path!r}, which is no hub leaf")
                    resolved += 1
                    opens = LEAVES.get(key, 0)
                    if opens > realm:
                        offenders.append(f"{stage['quest_key']} (realm {realm}) names {path!r} = {key!r}, which opens at realm {opens}")
        self.assertGreater(resolved, 10, "no label resolved to a leaf; the gate is broken, not the tree")
        self.assertEqual(offenders, [], "a realm road stage names a door the curriculum hides at that realm:\n  "
                         + "\n  ".join(offenders))


class TheSeederMakesOrdinaryQuests(unittest.TestCase):
    def test_a_stage_is_a_giverless_row_that_remembers_its_realm(self):
        rows = {r["quest_key"]: r for r in realm_road_seed_rows(WORLD)}
        self.assertEqual(sorted(rows), sorted(str(s["quest_key"]) for s in STAGES))
        for stage in STAGES:
            with self.subTest(stage=stage["quest_key"]):
                row = rows[stage["quest_key"]]
                self.assertEqual(row["giver_npc"], "")
                self.assertEqual(row["deadline_game_minutes"], 0)
                self.assertEqual(row["source_key"], "realm_road")
                self.assertEqual(row["seed"]["realm_index"], int(stage["realm_index"]))
                self.assertEqual(row["seed"]["follow_on"], str(stage.get("follow_on") or ""))

    def test_the_bot_and_the_engine_playtest_both_seed_it(self):
        bot = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("realm_road_seed_rows(WORLD)", bot, "a seeder nothing calls seeds nothing")
        playtest = (PROJECT_ROOT / "scripts" / "playtest_engine.py").read_text(encoding="utf-8")
        self.assertIn("realm_road_seed_rows(content)", playtest)

    def test_the_journal_never_offers_it(self):
        self.assertIn("realm_road", HANDED_OVER_BY_A_ROSTER)


class TheRoadIsHandedOver(unittest.TestCase):
    def test_the_crossing_hands_the_stage_over_in_the_engine(self):
        """The wire from the qi breakthrough to the grant; the behaviour is
        `realm_road_test.go`'s, which drives the crossing itself."""
        body = go_function("cultivation_actions.go", "cultivationBreakthrough")
        self.assertIn("grantRealmRoadTx(conn, catalog, userID, newRealm, p.GameMinute)", body)
        # The grant sits behind the qi ladder's realm crossing: the lines
        # just above the call must be that condition, not a stage or the body.
        lines = body[:body.index("grantRealmRoadTx")].rstrip().split("\n")
        above = lines[-2] if len(lines) > 1 else ""
        self.assertIn("!body && newRealm != realm", above, "the grant must sit behind a qi realm crossing")

    def test_a_citys_gate_is_the_city_for_a_quest(self):
        body = go_function("actions.go", "matchQuestEvent")
        self.assertIn("questTargetCity(catalog, *p.Target, p.ObjectiveType, objectives)", body)

    def test_the_menu_names_the_next_step_on_the_road(self):
        surface = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")
        self.assertIn('"realm_road"', _code(surface, "_menu_shape"),
                      "the menu's next-step line reads the beginner path alone and goes blank at the first gate")


# --- from test_the_heavens_lead_on.py ---

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

WORLD_HEAVENS = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
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
        stages = {s["quest_key"]: s for s in WORLD_HEAVENS["realm_road"]}
        self.assertEqual(stages["realm_road_7"].get("follow_on"), "realm_road_8")
        self.assertIn("realm_road_8", stages)

    def test_the_migration_writes_what_the_seeder_would(self):
        from app.rules.quests import realm_road_seed_rows

        class _World:
            data = WORLD_HEAVENS

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

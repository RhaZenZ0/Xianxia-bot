"""Quest progress: caught-up stages, the GM's grant lever, objective doors, good-deed karma and war history.

Merged from:

test_a_caught_up_stage_is_told.py — A tutorial stage the engine catches a graduate up on is told to them
(v1.2.1).

v1.2.0's catch-up handed added beginner-path stages over at the end of every
ordinary quest report and wrote them into the result as `caught_up`, and no
Python read the key - so the stage arrived in the journal and nobody was told.

test_a_gm_can_hand_over_the_next_quest.py — The GM's grant lever and its picker (v1.23.2).

Reported from the dashboard: a player who had finished "A Road Toward a Sect"
held no active quest, so the Quests card offered nothing to do - Complete and
Report act only on a quest already held. The sect road chains to nothing and
the realm road is handed over only at a breakthrough crossing, so somebody
already past the crossing was never put on it. The card now offers every
quest the engine's door would hand over and starts on the likely next one.
The engine half (`admin.player.quest_grant`) is held in Go.

test_every_objective_says_where.py — Every quest objective says where it is done (v1.27.0).

Labels written for the beginner path and the realm road name their door
(`**/world → City → Envoys**`), and a panel turns the door into a button.
Every commission label - 375 of the content file's 524 - and every label the
Forge builds from `OBJECTIVE_TYPES` named none. `OBJECTIVE_PATHS` is the one
place each type's door is written, and `labelled_objective` adds it to a label
that carries no path of its own.

test_good_deeds_are_worth_karma.py — Good deeds are worth karma, and the reply says so (v1.9.1).

The engine pays it and caps it (`karma_deeds.go`, held by
`karma_deeds_test.go`); this holds the line every reply prints and that each
of the four places a deed happens prints it.

test_the_wars_reach_the_forge.py — A sect war reaches the Quest Forge and the narrator's memory (v1.27.0).

The Forge's procedural draft and RAG's notable-type bonus keyed on
`war_started` and `war_resolved`, which no writer in this tree has ever
produced: the engine writes `territory_war`, `territory_war_resolved`,
`territory_war_ally` and `territory_claimed`. So no war - the loudest thing the
world does - ever shaped a drafted quest, and none counted as notable to the
narrator. And the declaration was written at significance 78, two short of
the Forge's floor.

These read the event types off the Go source, so a new kind of war row fails
here the day it is written rather than being invisible to both readers.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.support import install_aiosqlite_shim

install_aiosqlite_shim()

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.dashboard.server import AdminDashboardController, grantable_quests, suggest_next_quest
from app.rules.advanced_runtime import deed_karma_line
from app.rules.quests import (
    OBJECTIVE_PATHS,
    OBJECTIVE_TYPES,
    WAR_HISTORY_EVENT_ACTIONS,
    labelled_objective,
    next_objective_label,
)

pytestmark = pytest.mark.unit

# --- from test_a_caught_up_stage_is_told.py ---


ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


with patch.dict(os.environ, ENV):
    announce_quest_progress = importlib.import_module("app.bot.character_state").announce_quest_progress


class Response:
    def __init__(self):
        self.sent = []

    def is_done(self):
        return False

    async def send_message(self, text, **_):
        self.sent.append(text)


class ACaughtUpStageIsTold(unittest.TestCase):
    def test_the_stage_is_announced_as_a_new_quest(self):
        response = Response()
        interaction = SimpleNamespace(response=response, followup=SimpleNamespace())
        asyncio.run(announce_quest_progress(interaction, [{
            "quest_key": "beginner_iron", "title": "Iron from the Seam", "caught_up": True,
            "objectives": [{"type": "gather", "target": "spirit_iron", "count": 3, "label": "Mine spirit iron"}],
        }]))
        self.assertEqual(len(response.sent), 1)
        self.assertIn("New quest: Iron from the Seam", response.sent[0])
        self.assertNotIn("Quest progress", response.sent[0], "a handed-over stage was told as progress on it")

# --- from test_a_gm_can_hand_over_the_next_quest.py ---


def _definition(key: str, source: str = "", **seed) -> dict:
    return {"quest_key": key, "title": key.replace("_", " ").title(), "source_key": source, "seed_json": json.dumps(seed)}


GRANTABLE = [
    _definition("errand_forging_cores", "household_errand:Forging"),
    _definition("realm_road_3", "realm_road", follow_on="realm_road_4", realm_index=3),
    _definition("realm_road_1", "realm_road", follow_on="realm_road_2", realm_index=1),
    _definition("realm_road_2", "realm_road", follow_on="realm_road_3", realm_index=2),
]


class ThePickerStartsOnTheNextQuest(unittest.TestCase):
    def test_past_the_crossing_after_the_sect_road_suggests_their_realms_stage(self):
        held = [{"quest_key": "road_to_a_sect", "status": "completed", "seed_json": "{}"}]
        self.assertEqual(suggest_next_quest(held, GRANTABLE, 2), "realm_road_2")

    def test_a_finished_quests_chain_comes_first(self):
        held = [{"quest_key": "realm_road_1", "status": "completed", "seed_json": json.dumps({"follow_on": "realm_road_2"})}]
        self.assertEqual(suggest_next_quest(held, GRANTABLE, 3), "realm_road_2")

    def test_a_player_on_the_road_is_not_handed_a_second_stage(self):
        held = [{"quest_key": "realm_road_1", "status": "active", "seed_json": "{}"}]
        self.assertEqual(suggest_next_quest(held, GRANTABLE, 3), "")

    def test_nothing_offered_suggests_nothing(self):
        self.assertEqual(suggest_next_quest([], [], 2), "")

    def test_the_realm_road_leads_the_list_in_realm_order(self):
        keys = [q["quest_key"] for q in grantable_quests(GRANTABLE)]
        self.assertEqual(keys, ["realm_road_1", "realm_road_2", "realm_road_3", "errand_forging_cores"])


class TheCardDrivesTheLever(unittest.TestCase):
    def test_the_lever_is_mapped_and_pressed_from_the_editor(self):
        self.assertEqual(AdminDashboardController.ACTION_MAP.get("player.quest_grant"), "admin.player.quest_grant")
        js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(js, r"run\('player\.quest_grant'")
        self.assertIn("p.grantable_quests", js)
        self.assertIn("p.suggested_quest", js)

    def test_the_offer_is_what_the_engine_door_takes(self):
        source = (ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
        query = re.search(r"grantable_rows = await self\._fetchall\(.*?\"\"\"(.*?)\"\"\"", source, re.S)
        self.assertIsNotNone(query, "the grantable read could not be found; the gate is broken, not the tree")
        sql = query.group(1)
        for clause in ("status='approved'", "COALESCE(d.giver_npc,'')=''", "NOT EXISTS"):
            self.assertIn(clause, sql, f"the picker would offer what admin.player.quest_grant refuses ({clause} missing)")

# --- from test_every_objective_says_where.py ---


def _hubs():
    with patch.dict(os.environ, ENV):
        importlib.import_module("app.bot.surface")
        return importlib.import_module("app.bot.hubs")


class EveryObjectiveSaysWhere(unittest.TestCase):
    def test_every_type_has_a_door(self) -> None:
        self.assertEqual(sorted(set(OBJECTIVE_TYPES) - set(OBJECTIVE_PATHS)), [])
        self.assertEqual(sorted(set(OBJECTIVE_PATHS) - set(OBJECTIVE_TYPES)), [])

    def test_every_door_is_a_button(self) -> None:
        hubs = _hubs()
        for kind, path in OBJECTIVE_PATHS.items():
            with self.subTest(kind=kind):
                actions = hubs.suggested_actions(path)
                self.assertEqual(len(actions), 1, f"{kind}: {path} resolves to no action")

    def test_a_label_without_a_path_is_given_one(self) -> None:
        label = labelled_objective({"id": "a", "type": "talk", "label": "Speak with Bo Tan"})
        self.assertEqual(label, "Speak with Bo Tan - **/npc → People → Talk**")
        self.assertIn("**/npc → People → Talk**", next_objective_label([{"id": "a", "type": "talk", "label": "Speak with Bo Tan"}], {}))

    def test_a_label_that_names_its_door_is_left_alone(self) -> None:
        written = "Learn where a sect takes applicants - **/world → City → Envoys**"
        self.assertEqual(labelled_objective({"id": "a", "type": "sect_discovery", "label": written}), written)

    def test_the_readers_ask_the_one_helper(self) -> None:
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        for path in ("app/bot/ui/commissions.py", "app/bot/commands/character.py"):
            text = (root / path).read_text(encoding="utf-8")
            self.assertTrue(re.search(r"labelled_objective\(", text), path)

# --- from test_good_deeds_are_worth_karma.py ---


SURFACES = {
    "app/bot/ui/event_scene.py": 2,    # a world-event action and an engage
    "app/bot/commands/battle.py": 1,   # an event battle that takes a site's last beast
    "app/bot/commands/scene.py": 1,    # the Resolve scene action
    "app/bot/commands/exploration.py": 1,  # a personal event helped to its end
}


class TheLine(unittest.TestCase):
    def test_a_paid_deed_says_what_and_why(self):
        line = deed_karma_line({"deed": "event_site_cleared", "karma_delta": 3, "karma_score": 12})
        self.assertEqual(line, "☯️ Karma **+3** for clearing the last of the site → **+12**")

    def test_a_deed_that_paid_nothing_prints_nothing(self):
        self.assertEqual(deed_karma_line(None), "")
        self.assertEqual(deed_karma_line({"deed": "scene_resolve", "karma_delta": 0}), "")

    def test_every_deed_the_engine_names_has_words(self):
        go = (ROOT / "go_core" / "internal" / "game").glob("*.go")
        named = set()
        for path in go:
            if path.name.endswith("_test.go"):
                continue
            text = path.read_text(encoding="utf-8")
            for deed in ("world_event_good_deed", "scene_resolve", "personal_event_helped", "event_site_cleared"):
                if f'"{deed}"' in text:
                    named.add(deed)
        self.assertEqual(len(named), 4, f"the engine names {sorted(named)}; the reader is broken, not the tree")
        for deed in named:
            self.assertNotIn("a good deed", deed_karma_line({"deed": deed, "karma_delta": 1}),
                             f"{deed} has no words of its own")


class EveryDeedIsPrinted(unittest.TestCase):
    def test_each_surface_prints_the_line(self):
        for rel, want in SURFACES.items():
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and getattr(n.func, "id", "") == "deed_karma_line"]
            self.assertGreaterEqual(len(calls), want, f"{rel} does not print the karma a good deed paid")

# --- from test_the_wars_reach_the_forge.py ---


GAME = ROOT / "go_core" / "internal" / "game"


WAR_FILES = ("sect_war.go", "sect_war_peace.go", "territory_actions.go")


# The event type is the third argument of recordWorldHistoryTx, or the second
# element of RecordTerritoryClaimedTx's parameter list.
HISTORY_CALL = re.compile(r'recordWorldHistoryTx\(conn,[^\n]*?\),\s*"([a-z_]+)"', re.S)


CLAIM_ROW = re.compile(r'\[\]any\{source,\s*"([a-z_]+)"')


def _war_event_types() -> set[str]:
    found: set[str] = set()
    for name in WAR_FILES:
        text = (GAME / name).read_text(encoding="utf-8")
        found.update(HISTORY_CALL.findall(text))
        found.update(CLAIM_ROW.findall(text))
    return found


class TheWarsReachTheForge(unittest.TestCase):
    def test_the_reader_finds_the_war_rows(self) -> None:
        # A reader asserted before it is trusted (rc.57): an empty read would
        # make the next test vacuous.
        self.assertTrue({"territory_war", "territory_war_resolved"} <= _war_event_types(), _war_event_types())

    def test_every_war_row_the_engine_writes_is_one_the_readers_know(self) -> None:
        unknown = sorted(_war_event_types() - set(WAR_HISTORY_EVENT_ACTIONS))
        self.assertEqual(unknown, [], "the engine writes war history the Forge and RAG never read")

    def test_both_readers_ask_the_one_table(self) -> None:
        for path in ("app/rules/quests.py", "app/ai/rag.py"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("WAR_HISTORY_EVENT_ACTIONS", text.split("WAR_HISTORY_EVENT_ACTIONS: dict", 1)[-1], path)

    def test_a_declaration_is_loud_enough_for_the_forge(self) -> None:
        text = (GAME / "sect_war.go").read_text(encoding="utf-8")
        match = re.search(r'"territory_war",\s*\n?\s*attacker\+" declares on "\+defender, summary, (\d+),', text)
        self.assertIsNotNone(match, "the declaration's history row could not be read")
        config = (ROOT / "app" / "ops" / "config.py").read_text(encoding="utf-8")
        floor = int(re.search(r'QUEST_FORGE_MIN_SIGNIFICANCE", "(\d+)"', config).group(1))
        self.assertGreaterEqual(int(match.group(1)), floor)

    def test_a_drafted_war_quest_asks_for_resolution(self) -> None:
        from app.rules.game import World
        from app.rules.quests import procedural_quest_from_event
        world = World(ROOT / "content" / "world.json")
        draft = procedural_quest_from_event(
            {"history_id": 1, "event_type": "territory_war", "title": "A war", "summary": "", "location": "Greenriver Town"},
            world, {"max_xp": 50, "max_stones": 200, "max_items": 3},
        )
        actions = [o["target"] for o in draft["objectives"] if o["type"] == "scene_action"]
        self.assertEqual(actions, ["resolve"])


if __name__ == "__main__":
    unittest.main()

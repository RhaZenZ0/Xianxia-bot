"""A sect war reaches the Quest Forge and the narrator's memory (v1.27.0).

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

import re
import unittest
from pathlib import Path

import pytest

from app.rules.quests import WAR_HISTORY_EVENT_ACTIONS

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
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

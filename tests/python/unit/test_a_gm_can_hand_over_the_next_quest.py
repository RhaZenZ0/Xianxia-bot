"""The GM's grant lever and its picker (v1.23.2).

Reported from the dashboard: a player who had finished "A Road Toward a Sect"
held no active quest, so the Quests card offered nothing to do - Complete and
Report act only on a quest already held. The sect road chains to nothing and
the realm road is handed over only at a breakthrough crossing, so somebody
already past the crossing was never put on it. The card now offers every
quest the engine's door would hand over and starts on the likely next one.
The engine half (`admin.player.quest_grant`) is held in Go.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.dashboard.server import AdminDashboardController, grantable_quests, suggest_next_quest  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()

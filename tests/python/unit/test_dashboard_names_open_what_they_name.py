"""A name in a dashboard table opens what it names (v1.27.0).

The player drawer and the NPC drawer existed and six views linked their names;
nineteen others printed a player or an NPC as plain text, so a GM reading who
bid on a lot or who holds a grudge had to go and search for them. `table()`
now renders every column keyed on a known name field through one helper,
`linkedCell`, and the click is handled once, on the document.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

APP = (Path(__file__).resolve().parents[3] / "dashboard" / "app.js").read_text(encoding="utf-8")


def _body(name: str) -> str:
    start = APP.index(f"function {name}(")
    depth, i = 0, APP.index("){", start) + 1
    for j in range(i, len(APP)):
        depth += {"{": 1, "}": -1}.get(APP[j], 0)
        if depth == 0:
            return APP[i:j + 1]
    raise AssertionError(f"{name} has no balanced body")


class DashboardNamesOpenWhatTheyName(unittest.TestCase):
    def test_the_table_renders_a_bare_key_through_the_link_rule(self) -> None:
        body = _body("table")
        self.assertIn("linkedCell(r,h[1])", body)
        self.assertNotIn("esc(r[h[1]])", body, "a bare key is printed past the link rule")

    def test_the_link_rule_knows_the_player_and_npc_name_fields(self) -> None:
        body = _body("linkedCell")
        self.assertIn("playerLink(", body)
        self.assertIn("npcLink(", body)
        for key in ("player_name", "seller_name", "bidder_name", "owner_name"):
            self.assertRegex(APP, rf"PLAYER_NAME_IDS=\{{[^}}]*\b{key}:")
        self.assertRegex(APP, r"NPC_NAME_KEYS=new Set\(\[[^\]]*'npc_name'")

    def test_one_handler_opens_every_link(self) -> None:
        self.assertTrue(re.search(r"document\.addEventListener\('click'", APP))
        self.assertIn("showPlayer(player.dataset.player)", APP)
        self.assertIn("showNpc(npc.dataset.npc)", APP)

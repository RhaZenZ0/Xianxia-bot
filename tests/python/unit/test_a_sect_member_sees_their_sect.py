"""A sect member sees their sect (v1.19.4).

Asked for from play: *"if you in a sect if your not at the needed stage open
all the commands for it."* A sect is joined at its gate from realm 0, and the
curriculum held the sect's own pages to Qi Refining and its territory and war
to the Nascent Soul, so a new member saw the sect they had just joined as one
collapsed line. Membership is the introduction the curriculum stands in for,
so a member is free of the realm curriculum on the sect hub whatever their realm.

**v1.25.0 narrowed what that opens, on the owner's call**: *"it should only show
what you can do at your sect ranks."* The curriculum still steps aside for a
member - that is what this file holds - and the rank floors
(`sect_system.rank_floors`) padlock the doors above a member's rank through
`PROGRESSION_GATES`, held in `tests/python/contracts/test_sect_doors_follow_rank.py`.

Held here, behaviourally: a member at realm 0 has nothing of the sect hub held
back and somebody in no sect does (the self-check that keeps the first half
from being vacuous); the panel, the menu and `/locked` read one answer; the
exemption only ever opens; and a failed membership read opens nothing.
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _surface():
    with patch.dict(os.environ, ENV):
        import app.bot.surface as surface
    return surface


class FakeDB:
    def __init__(self, membership, *, realm=0, fails=False):
        self.membership, self.realm, self.fails = membership, realm, fails

    async def get_character(self, user_id):
        return {"user_id": user_id, "realm_index": self.realm}

    async def get_sect_membership(self, user_id):
        if self.fails:
            raise RuntimeError("engine unreachable")
        return self.membership


def _interaction(uid=7):
    return SimpleNamespace(user=SimpleNamespace(id=uid))


class ASectMemberSeesTheirSect(unittest.TestCase):
    def setUp(self):
        self.surface = _surface()
        self.sect_leaves = {
            leaf for leaves in self.surface._hub_page_leaves()["sect"].values() for leaf in leaves
        }

    def _unlocks(self, db):
        with patch.object(self.surface, "DB", db):
            return asyncio.run(self.surface._curriculum_unlocks(_interaction()))

    def test_somebody_in_no_sect_is_held_back_from_it(self):
        locked = {path.lstrip("/") for path in self._unlocks(FakeDB(None))}
        held = sorted(locked & self.sect_leaves)
        self.assertTrue(held, "the curriculum holds nothing of the sect hub back at realm 0; the test is vacuous")
        self.assertIn("territory claim", held)

    def test_a_member_at_realm_0_is_not_held_back_by_the_realm_curriculum(self):
        locked = {path.lstrip("/") for path in self._unlocks(FakeDB({"sect_name": "Azure Cloud Sect"}))}
        self.assertEqual(sorted(locked & self.sect_leaves), [],
                         "a sect member is still held back from their own sect")
        self.assertIn("abode establish", locked, "membership opened a door outside the sect hub")

    def test_a_failed_membership_read_opens_nothing(self):
        locked = {path.lstrip("/") for path in self._unlocks(FakeDB(None, fails=True))}
        self.assertTrue(locked & self.sect_leaves, "an unreadable membership opened the sect doors")

    def test_the_menu_asks_the_same_exemption(self):
        """Read rather than driven: the menu never leaves the sect hub off at
        realm 0 today, because Recruitment's two doors are open there, so a
        behavioural test passes whether or not the menu asks (its drill did).
        What is held is that it asks, so a roster that moves Recruitment cannot
        make the menu and the panel disagree about a member's sect."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(self.surface._menu_shape).lstrip())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and getattr(node.func, "attr", "") == "hidden_hubs"]
        self.assertEqual(len(calls), 1)
        opened = [kw for kw in calls[0].keywords if kw.arg == "opened"]
        self.assertTrue(opened and "_curriculum_opened" in ast.unparse(opened[0].value),
                        "the menu does not pass the sect member's exemption to hidden_hubs")


class TheExemptionOnlyOpens(unittest.TestCase):
    def test_opened_drops_a_locked_leaf_and_locks_nothing_new(self):
        from app.rules import feature_unlocks as unlocks

        roster = CONTENT["feature_unlocks"]
        plain = unlocks.locked_leaves(roster, 0)
        opened = unlocks.locked_leaves(roster, 0, opened={"/territory claim", "cultivate"})
        self.assertIn("territory claim", plain)
        self.assertNotIn("territory claim", opened)
        self.assertEqual(set(opened), set(plain) - {"territory claim"})

    def test_locked_lists_what_the_panels_hold_back(self):
        """`/locked` asks the panels' provider, so a member's sect doors are
        not listed as waiting there either."""
        surface = _surface()
        import app.bot.commands.locked as locked_card
        import app.bot.hubs as hubs

        sent = {}

        async def send_message(text, **kwargs):
            sent["text"] = text

        async def require_character(interaction):
            return {"realm_index": 0}

        interaction = SimpleNamespace(user=SimpleNamespace(id=7), response=SimpleNamespace(send_message=send_message))
        db = FakeDB({"sect_name": "Azure Cloud Sect"})
        with patch.object(surface, "DB", db), patch.object(locked_card, "require_character", require_character), \
                patch.object(hubs, "_NOT_YET_UNLOCKED", surface._curriculum_unlocks):
            asyncio.run(locked_card.locked.callback(interaction))
        self.assertNotIn("Territory", sent.get("text", ""), sent.get("text", ""))
        self.assertIn("doors are waiting", sent.get("text", ""))


if __name__ == "__main__":
    unittest.main()

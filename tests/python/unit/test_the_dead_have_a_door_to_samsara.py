"""A cultivator who dies has a door to Samsara, whatever realm they died in (v1.34.0).

Found by a review that followed a death through the menus. The death notice, the
engine's reset refusal and `require_character` all send the dead to
`/character -> Samsara -> Reincarnate`, and `reincarnate` and `afterlife` have no
slash command of their own - the hub is the only road. The curriculum held that
page, and `soul` and `inheritances` beside it, to realm 5, and
`_curriculum_opened` (the provider that already steps aside for a sect member)
never asked whether the character was alive. A cultivator who died at realm 2
met a collapsed "opens at realm 5" line where the way back to a life should be.

Held here, behaviourally: a deceased realm-2 character has the Samsara page's
leaves, `soul` and `inheritances` drawn and no collapsed line on that page; a
living one at the same realm is held back (the self-check that keeps the first
half from being vacuous); the exemption only ever opens, at every realm the
roster names; and a failed read opens nothing without taking the sect's
exemption with it.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ROSTER = CONTENT["feature_unlocks"]

THE_FOUR = ("afterlife", "reincarnate", "soul", "inheritances")


def _modules():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface"), importlib.import_module("app.bot.hubs")


class FakeDB:
    def __init__(self, *, status="alive", realm=2, membership=None, character_fails=False, sect_fails=False):
        self.status, self.realm, self.membership = status, realm, membership
        self.character_fails, self.sect_fails = character_fails, sect_fails

    async def get_character(self, user_id):
        if self.character_fails:
            raise RuntimeError("engine unreachable")
        return {"user_id": user_id, "realm_index": self.realm, "life_status": self.status}

    async def get_sect_membership(self, user_id):
        if self.sect_fails:
            raise RuntimeError("engine unreachable")
        return self.membership


def _interaction(uid=7):
    return SimpleNamespace(user=SimpleNamespace(id=uid))


class TheDeadHaveADoorToSamsara(unittest.TestCase):
    def setUp(self):
        self.surface, self.hubs = _modules()
        definition = next(d for d in self.hubs.REGISTERED_HUBS if d.name == "character")
        self.samsara = next(p for p in definition.pages if p.label == "Samsara")
        self.definition = definition

    def _locked(self, db):
        with patch.object(self.surface, "DB", db):
            return asyncio.run(self.surface._curriculum_unlocks(_interaction()))

    def _view(self, db):
        view = self.hubs.LayoutHubView(7, self.definition, owner_name="T")
        view.unlocks_at = self._locked(db)
        view.page_key = self.samsara.key
        view.rebuild()
        return view

    def test_the_four_leaves_are_on_the_hub_and_gated_at_all(self):
        """The reader works: the four leaves exist on the character hub and the
        roster holds each of them above realm 2, or the rest is vacuous."""
        leaves = {a.path.lstrip("/") for p in self.definition.pages for a in self.hubs._leaf_actions(p)}
        for leaf in THE_FOUR:
            self.assertIn(leaf, leaves, f"{leaf!r} is not a leaf of the character hub")
            self.assertGreater(int(ROSTER["leaves"][leaf]), 2, f"{leaf!r} is no longer gated above realm 2")

    def test_a_living_realm_2_cultivator_is_held_back_from_them(self):
        locked = {path.lstrip("/") for path in self._locked(FakeDB(status="alive"))}
        self.assertEqual(sorted(set(THE_FOUR) - locked), [], "the curriculum no longer holds the Samsara leaves from the living")

    def test_a_dead_cultivator_at_realm_2_has_a_door_to_samsara(self):
        db = FakeDB(status="deceased", realm=2)
        locked = {path.lstrip("/") for path in self._locked(db)}
        self.assertEqual(
            sorted(set(THE_FOUR) & locked), [],
            "a dead cultivator at realm 2 has no door to Samsara: the curriculum still holds "
            "these back, and the death notice, the reset refusal and require_character send them there",
        )
        view = self._view(db)
        self.assertIn(self.samsara.key, [p.key for p in view.visible_pages()])
        drawn = [a.path.lstrip("/") for a in view.page_actions(self.samsara)]
        for leaf in ("afterlife", "reincarnate", "reset"):
            self.assertIn(leaf, drawn, f"the Samsara page does not draw {leaf!r} for the dead")
        self.assertEqual(view.unlock_line(self.samsara), "",
                         "the Samsara page still prints its collapsed 'opens at realm 5' line for the dead")
        overview = next(p for p in self.definition.pages if p.label == "Overview")
        overview_drawn = [a.path.lstrip("/") for a in view.page_actions(overview)]
        for leaf in ("soul", "inheritances"):
            self.assertIn(leaf, overview_drawn, f"Overview does not draw {leaf!r} for the dead")

    def test_the_living_page_is_a_collapsed_line_so_the_dead_one_is_not_vacuous(self):
        view = self._view(FakeDB(status="alive"))
        self.assertTrue(view.unlock_line(self.samsara), "a living realm-2 cultivator's Samsara page prints no collapsed line")

    def test_no_leaf_the_dead_need_is_held_back_at_any_realm(self):
        """The floor, at every realm the roster names: what a soul between lives
        needs is never in the locked set for a deceased character."""
        realms = sorted({0, *[int(v) for v in ROSTER["leaves"].values()]})
        self.assertGreater(len(realms), 3, "the roster names no realms; the walk is vacuous")
        need = self.surface._the_dead_need()
        self.assertTrue(set(THE_FOUR) <= need, "the set the dead are shown lost one of the four")
        for realm in realms:
            with self.subTest(realm=realm):
                locked = {path.lstrip("/") for path in self._locked(FakeDB(status="deceased", realm=realm))}
                self.assertEqual(sorted(need & locked), [],
                                 f"a dead cultivator at realm {realm} is held back from what they need")

    def test_it_only_opens_never_locks(self):
        for realm in (0, 2, 4, 5):
            alive = set(self._locked(FakeDB(status="alive", realm=realm)))
            dead = set(self._locked(FakeDB(status="deceased", realm=realm)))
            self.assertEqual(dead, alive - {"/" + leaf for leaf in self.surface._the_dead_need()})

    def _opened(self, db):
        with patch.object(self.surface, "DB", db):
            return {leaf.lstrip("/") for leaf in asyncio.run(self.surface._curriculum_opened(7))}

    def test_a_failed_read_opens_nothing_and_does_not_take_the_other_with_it(self):
        self.assertEqual(self._opened(FakeDB(status="deceased", character_fails=True)), set(),
                         "an unreadable life status opened something")
        sect = {leaf for leaves in self.surface._hub_page_leaves()["sect"].values() for leaf in leaves}
        member = FakeDB(status="alive", membership={"sect_name": "Azure Cloud Sect"}, character_fails=True)
        self.assertEqual(self._opened(member), sect, "a failed life read took the sect member's exemption with it")
        dead = FakeDB(status="deceased", sect_fails=True)
        self.assertEqual(self._opened(dead), set(self.surface._the_dead_need()),
                         "a failed sect read took the Samsara doors with it")
        self.assertEqual(self._opened(FakeDB(status="deceased", membership={"sect_name": "x"})),
                         sect | set(self.surface._the_dead_need()))


class TheDoorsDrawnForTheDeadAdmitThem(unittest.TestCase):
    """A leaf the curriculum draws for the dead is a door the dead can walk
    through. The review that read this release found `inheritances` drawn on
    the dead cultivator's Overview page while its own command called
    `require_character` with the default `allow_deceased=False`, so the door
    the page offered answered "X is dead... Use /character -> Samsara": the
    test above held the drawing and never pressed the leaf. Read off the
    syntax tree: every command registered under a name in `_the_dead_need()`
    passes `allow_deceased=True` to every `require_character` it calls."""

    COMMANDS = PROJECT_ROOT / "app" / "bot" / "commands"

    @classmethod
    def _registered(cls) -> dict[str, ast.AsyncFunctionDef]:
        found = {}
        for path in sorted(cls.COMMANDS.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    continue
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call) and getattr(deco.func, "id", "") == "registered_root_command":
                        name = next((k.value.value for k in deco.keywords
                                     if k.arg == "name" and isinstance(k.value, ast.Constant)), None)
                        if name:
                            found[name] = node
        return found

    def test_every_door_drawn_for_the_dead_lets_them_in(self):
        surface, _ = _modules()
        need = sorted(surface._the_dead_need())
        registered = self._registered()
        self.assertGreater(len(registered), 20, "the command reader found almost nothing")
        for leaf in need:
            with self.subTest(leaf=leaf):
                self.assertIn(leaf, registered, f"{leaf!r} is drawn for the dead and no command is registered under it")
                calls = [n for n in ast.walk(registered[leaf])
                         if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "require_character"]
                for call in calls:
                    admits = any(k.arg == "allow_deceased" and isinstance(k.value, ast.Constant) and k.value.value is True
                                 for k in call.keywords)
                    self.assertTrue(
                        admits,
                        f"/{leaf} is drawn for the dead and its require_character refuses them: "
                        "a surface offering what its own door refuses",
                    )


if __name__ == "__main__":
    unittest.main()

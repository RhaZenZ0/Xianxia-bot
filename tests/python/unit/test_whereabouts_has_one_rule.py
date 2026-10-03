"""An NPC's whereabouts are said one way, and a missing person is never placed (v1.20.2).

Asked "why would a player see npc inspect". `/npcinfo` is meant to be seen, but
it, `/talk` and the sect recommendation each printed `current_npc_location`
straight back - and that resolver answers a missing person's *true* position on
purpose (schema 47), so the pickers list them only where a searcher stands.
Typing a missing person's name did a disappearance quest's whole search, and
the two refusals named places the player had never discovered.

The rule already existed once, in the journal (v1.8.3): a place the player has
not found is not named, and a missing person is never placed. `npc_whereabouts`
is that rule, and every reply asks it now. These tests drive the three replies
with the resolver answering a place the reply must not name, and an AST check
holds that no command interpolates the resolver's answer into a reply.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
COMMANDS = ROOT / "app" / "bot" / "commands"
ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

with patch.dict(os.environ, ENV):
    importlib.import_module("app.bot.surface")
    locations = importlib.import_module("app.bot.locations")
    scene = importlib.import_module("app.bot.commands.scene")
    sect = importlib.import_module("app.bot.commands.sect")

HIDDEN_PLACE = "Ashen Hollow"
HOME = "Greenriver Town"
NPC = "Herbalist Mo Lan"


class _Sim:
    def __init__(self, state):
        self.state = state

    async def npc_status(self, name):
        return dict(self.state)


class _DB:
    def __init__(self, definition):
        self.definition = definition

    async def get_npc_definition(self, name):
        return dict(self.definition)


class _Relationships:
    async def get(self, user_id, npc):
        return {"encounter_count": 0}

    def public_label(self, relationship):
        return "Strangers"


class _Response:
    def __init__(self):
        self.text = None

    async def send_message(self, content=None, **kwargs):
        self.text = str(content)

    async def defer(self, **kwargs):
        return None


def _interaction():
    return SimpleNamespace(user=SimpleNamespace(id=1), response=_Response(), channel_id=0)


def _run(module, handler, *args, status, player_at=HOME, visible=lambda place: True, definition=None):
    """Drive `handler` with the resolver answering HIDDEN_PLACE."""
    character = {"user_id": 1, "location": player_at, "name": "Shen Rui", "realm_index": 0}
    definition = definition or {"role": "Herbalist", "realm": "Qi Refining", "location": HOME}
    state = {"status": status, "home_location": HOME, "current_location": HIDDEN_PLACE}

    async def require(interaction, **kwargs):
        return character

    async def world_time():
        return SimpleNamespace(period="evening", total_minutes=0)

    async def resolved(name, period=None):
        return HIDDEN_PLACE

    async def is_visible(user_id, c, place):
        return visible(place)

    async def display(c):
        return str(c.get("location"))

    patches = [
        patch.object(locations, "SIM", _Sim(state)),
        patch.object(locations, "current_npc_location", resolved),
        patch.object(locations, "_location_is_visible", is_visible),
        patch.object(module, "require_character", require),
        patch.object(module, "DB", _DB(definition)),
        patch.object(module, "current_world_time", world_time),
        patch.object(module, "current_npc_location", resolved),
        patch.object(module, "character_location_display", display),
    ]
    if module is scene:
        patches += [patch.object(scene, "SIM", _Sim(state)),
                    patch.object(scene, "_location_is_visible", is_visible),
                    patch.object(scene, "NPC_RELATIONSHIPS", _Relationships())]
    interaction = _interaction()
    for p in patches:
        p.start()
    try:
        asyncio.run(handler(interaction, *args))
    finally:
        for p in reversed(patches):
            p.stop()
    if interaction.response.text is None:
        raise AssertionError(f"{handler.__name__} sent nothing")
    return interaction.response.text


def _npcinfo(**kwargs):
    return _run(scene, scene.npc_info_command.callback, NPC, **kwargs)


def _talk(**kwargs):
    return _run(scene, scene.talk.callback.__wrapped__, NPC, "Hello", **kwargs)


def _recommendation(**kwargs):
    sponsor = {"role": "Elder", "realm": "Core Formation", "location": HOME,
               "can_recommend": True, "sect_affiliation": _a_recruiting_sect()}
    return _run(sect, sect.sect_recruitment_recommendation.callback.__wrapped__, NPC,
                definition=sponsor, **kwargs)


def _a_recruiting_sect():
    for name in sorted(scene.WORLD.sects):
        if sect.recruitment_definition(scene.WORLD.sects, name):
            return name
    raise AssertionError("no sect has a recruitment definition; the fixture is broken, not the tree")


REPLIES = {"/npcinfo": _npcinfo, "/talk": _talk, "recommendation": _recommendation}


class AMissingPersonIsNeverPlaced(unittest.TestCase):
    def test_no_reply_names_where_a_missing_person_is(self):
        for name, reply in REPLIES.items():
            with self.subTest(reply=name):
                text = reply(status="missing")
                self.assertNotIn(HIDDEN_PLACE, text, f"{name} did the search for the player")
                self.assertIn("missing", text)

    def test_a_searcher_standing_there_has_found_them(self):
        text = _npcinfo(status="missing", player_at=HIDDEN_PLACE)
        self.assertIn("is here with you", text)

    def test_the_card_answers_whether_or_not_they_are_somewhere_you_have_been(self):
        """"No reliable knowledge" against a card would itself say whether the
        missing person stands somewhere the player has been."""
        text = _npcinfo(status="missing", visible=lambda place: place != HIDDEN_PLACE)
        self.assertNotIn("no reliable knowledge", text)
        self.assertIn("missing", text)


class AnUndiscoveredPlaceIsNotNamed(unittest.TestCase):
    def test_no_reply_names_a_place_the_player_has_not_found(self):
        for name, reply in REPLIES.items():
            if name == "/npcinfo":
                continue  # the card refuses outright for an alive NPC there, as it always has
            with self.subTest(reply=name):
                text = reply(status="alive", visible=lambda place: place != HIDDEN_PLACE)
                self.assertNotIn(HIDDEN_PLACE, text)
                self.assertIn("somewhere you have not been", text)

    def test_a_place_the_player_has_found_is_still_named(self):
        """Remote lookups are designed (v1.8.3): the rule narrows what is said,
        not whether a known place may be."""
        for name, reply in REPLIES.items():
            with self.subTest(reply=name):
                self.assertIn(HIDDEN_PLACE, reply(status="alive"))


def _tainted_reply_lines(path: Path) -> list[str]:
    """Lines where a value resolved by `current_npc_location` - directly, or
    through a name assigned from it - is formatted into an f-string."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tainted: set[str] = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                calls = any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "current_npc_location"
                            for n in ast.walk(node.value))
                uses = any(isinstance(n, ast.Name) and n.id in tainted for n in ast.walk(node.value))
                if calls or uses:
                    tainted |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(function):
            if isinstance(node, ast.FormattedValue) and any(
                isinstance(n, ast.Name) and n.id in tainted for n in ast.walk(node.value)
            ):
                found.append(f"{path.name}:{node.lineno} in {function.name}")
    return found


class NoReplyPrintsTheResolver(unittest.TestCase):
    def test_no_command_formats_a_resolved_location_into_a_reply(self):
        offenders = [line for path in sorted(COMMANDS.glob("*.py")) for line in _tainted_reply_lines(path)]
        self.assertEqual(offenders, [], "say an NPC's whereabouts through locations.npc_whereabouts")

    def test_the_reader_sees_the_shape_it_forbids(self):
        source = (
            "async def f(npc):\n"
            "    where = await current_npc_location(npc)\n"
            "    shown = where or 'x'\n"
            "    return f'{npc} is at {shown}'\n"
        )
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "probe.py"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(len(_tainted_reply_lines(path)), 1, "the reader is broken, not the tree")


if __name__ == "__main__":
    unittest.main()

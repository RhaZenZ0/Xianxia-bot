"""The narrator's public profile of an NPC asks the registry (v1.2.1).

`DB.get_npc_definition` resolves catalogue -> registry -> a running event's
cast (rc.27), and `NarratorContextBuilder._public_npc` resolved catalogue ->
cast, so a matured descendant, a household relative or a GM's NPC reached the
narrator with no personality, speech, want or fear.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from tests.support import install_aiosqlite_shim

install_aiosqlite_shim()

from app.ai.narrator_context import NarratorContextBuilder  # noqa: E402


class FakeDB:
    def __init__(self, registered=None, cast=None):
        self.registered = registered
        self.cast = cast
        self.asked = []

    async def get_registered_npc(self, name):
        self.asked.append(("registry", name))
        return self.registered

    async def get_event_npc_definition(self, name):
        self.asked.append(("cast", name))
        return self.cast


def builder(db):
    return NarratorContextBuilder(db=db, simulator=SimpleNamespace(), world=SimpleNamespace(npcs={"Yue Dong": {"personality": "stern"}}), engine=SimpleNamespace(), max_chars=7000)


class TheNarratorAsksTheRegistry(unittest.TestCase):
    def test_the_catalogue_still_answers_first(self):
        db = FakeDB(registered={"personality": "wrong"})
        self.assertEqual(asyncio.run(builder(db)._public_npc("Yue Dong")), {"personality": "stern"})
        self.assertEqual(db.asked, [])

    def test_a_registered_npc_reaches_the_narrator_with_their_traits(self):
        db = FakeDB(registered={"name": "Shen Wei", "personality": "wry", "speech": "clipped", "want": "peace", "fear": "debt"})
        self.assertEqual(asyncio.run(builder(db)._public_npc("Shen Wei"))["personality"], "wry")
        self.assertEqual(db.asked, [("registry", "Shen Wei")], "the registry was not the second door")

    def test_the_cast_is_still_the_last_door(self):
        db = FakeDB(registered=None, cast={"name": "Captain Ren", "role": "militia"})
        self.assertEqual(asyncio.run(builder(db)._public_npc("Captain Ren"))["role"], "militia")
        self.assertEqual([kind for kind, _ in db.asked], ["registry", "cast"])


if __name__ == "__main__":
    unittest.main()

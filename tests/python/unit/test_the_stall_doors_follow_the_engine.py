"""The panel's stall doors agree with the engine's (v1.12.3).

Two anticipations of a refusal had drifted from the rule they anticipate, and
a door that is drawn where the engine refuses it, or hidden where the engine
would let the player through, is the `/talk` picker's fault in a new place.

* **Tending.** `stallOwnerHereTx` refuses List, Withdraw and Close unless the
  player stands in the city *their stall* is kept in. The panel drew them in
  any city with a shop, so a keeper in another city pressed a door that could
  only refuse.
* **Opening.** `stall.open` asks `accessRealmIndex()`, the higher of the qi and
  body ladders. The panel compared the qi realm alone, so a body cultivator
  ahead of their qi stage had the door hidden behind a lock line that was wrong.

Driven through `_progression_hidden_actions` with a fake database, because the
source reads correctly in both the right and the wrong version: the comparison
is one operand, and what it compares is decided by the value it is handed.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

TENDING = ("/stall list", "/stall withdraw", "/stall close")


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


class _Db:
    """Every read answers "nothing there" except the stall the test gives."""

    def __init__(self, stall=None):
        self._stall = stall

    async def get_player_stall(self, _uid):
        return self._stall

    def __getattr__(self, name):
        async def nothing(*_a, **_k):
            return None
        return nothing


def _hidden(surface, character, stall):
    interaction = SimpleNamespace(user=SimpleNamespace(id=7))
    with patch.object(surface, "DB", _Db(stall)):
        return asyncio.run(surface._progression_hidden_actions(interaction, character))


def _character(surface, location, *, realm=0, body_realm=0):
    return {"location": location, "realm_index": realm, "body_realm_index": body_realm, "phase": 1, "body_phase": 1}


def _two_cities(surface):
    """Two cities that keep shops, each with a district or gate, from the
    shipped content."""
    cities = []
    for name, data in surface.WORLD.locations.items():
        if any(str(shop.get("city") or "") == name for shop in surface.WORLD.shops.values()):
            parts = [part for part, p in surface.WORLD.locations.items()
                     if str(p.get("outside_location") or "") == name and (p.get("district") or p.get("shop"))]
            if parts:
                cities.append((name, sorted(parts)[0]))
        if len(cities) == 2:
            break
    return cities


class TendingIsDoneInTheStallsOwnCity(unittest.TestCase):
    def setUp(self):
        self.surface = _surface()
        found = _two_cities(self.surface)
        self.assertEqual(len(found), 2, "the content read found no two shop cities; the gate is broken, not the tree")
        (self.home, self.home_part), (self.other, _part) = found
        self.stall = {"user_id": 7, "city": self.home, "name": "Lin's Table", "currency_id": "low_spirit_stone"}

    def test_the_keeper_in_another_city_is_not_shown_the_tending_doors(self):
        hidden = _hidden(self.surface, _character(self.surface, self.other, realm=3), self.stall)
        for path in TENDING:
            self.assertIn(path, hidden, f"{path} is drawn in {self.other}, where the stall is not - the engine refuses it")
            self.assertIn(self.home, hidden[path], "the lock line does not say where the stall stands")

    def test_the_keeper_in_their_own_city_or_any_part_of_it_is_shown_them(self):
        for place in (self.home, self.home_part):
            with self.subTest(place=place):
                hidden = _hidden(self.surface, _character(self.surface, place, realm=3), self.stall)
                for path in TENDING:
                    self.assertNotIn(path, hidden, f"{path} is hidden from the keeper standing in {place}")

    def test_a_private_room_is_not_the_stalls_city(self):
        hidden = _hidden(self.surface, _character(self.surface, "birth_family:1", realm=3), self.stall)
        for path in TENDING:
            self.assertIn(path, hidden)


class OpeningAsksTheHigherLadder(unittest.TestCase):
    def setUp(self):
        self.surface = _surface()
        self.floor = self.surface.STALL_MIN_REALM_INDEX
        self.assertGreater(self.floor, 0, "the stall's realm floor read as zero; the gate is broken, not the tree")
        self.city = _two_cities(self.surface)[0][0]

    def test_a_body_cultivator_ahead_of_their_qi_stage_may_open(self):
        hidden = _hidden(self.surface, _character(self.surface, self.city, realm=0, body_realm=self.floor), None)
        self.assertNotIn("/stall open", hidden, "the door is hidden from somebody the engine would let in")

    def test_the_qi_ladder_alone_still_opens_it(self):
        hidden = _hidden(self.surface, _character(self.surface, self.city, realm=self.floor, body_realm=0), None)
        self.assertNotIn("/stall open", hidden)

    def test_neither_ladder_at_the_floor_hides_it_and_names_the_higher_stage(self):
        below = self.floor - 1
        hidden = _hidden(self.surface, _character(self.surface, self.city, realm=0, body_realm=below), None)
        self.assertIn("/stall open", hidden)
        self.assertIn(self.surface.WORLD.realm_name(below), hidden["/stall open"],
                      "the lock line names the qi stage, not the stage the engine compares")


if __name__ == "__main__":
    unittest.main()

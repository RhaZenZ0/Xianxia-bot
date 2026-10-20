"""A quest pays in the coin of its giver's world (v1.35.0).

The rule is the engine's (`questRewardCurrencyTx`, driven in
`quest_reward_currency_test.go`). What Python holds is the display twin the
cards and the board name a coin with, `WORLD.quest_giver_currency`: a twin is
one authored fact with two readers, so it is computed here a third time off
the raw content file, where two wrong halves cannot agree (rc.55, v1.0.9).
"""
from __future__ import annotations

import json
import unittest

from app.rules import commissions as rules
from app.rules.game import World
from tests.support import PROJECT_ROOT

CONTENT = PROJECT_ROOT / "content" / "world.json"
GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "quest_reward_currency.go"


class TheTwinReadsTheGiversWorld(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(CONTENT)
        cls.raw = json.loads(CONTENT.read_text(encoding="utf-8"))

    def test_every_shipped_giver_pays_the_coin_of_the_world_they_stand_in(self):
        givers = dict(self.raw.get("commission_givers") or {})
        locations = dict(self.raw.get("locations") or {})
        currencies = dict(self.raw.get("currencies") or {})
        self.assertGreaterEqual(len(givers), 10, "the reader found too few givers to be trusted")
        upper = 0
        for name, giver in sorted(givers.items()):
            home = str(giver.get("location") or (self.raw.get("npcs", {}).get(name) or {}).get("location") or "")
            world = str((locations.get(home) or {}).get("world") or "")
            want = sorted(k for k, c in currencies.items() if str(c.get("world") or "") == world and int(c.get("tier") or 0) == 1)
            self.assertTrue(want, f"{name} stands at {home!r} in {world!r}, a world with no tier-1 coin")
            self.assertEqual(self.world.quest_giver_currency(name), want[0], name)
            upper += world != "Mortal World"
        self.assertGreater(upper, 0, "no giver stands above the Mortal World; the check cannot see a wrong coin")

    def test_a_giver_the_content_does_not_place_names_no_coin(self):
        self.assertIsNone(self.world.quest_giver_currency("Nobody In Particular"))
        self.assertIsNone(self.world.quest_giver_currency(""))

    def test_the_two_halves_read_the_same_two_sources(self):
        # The engine takes the world off the giver's home and, failing that, the
        # stage's realm; the twin reads the giver's home through the same door's
        # twin. A reader that moved to a third source would drift.
        go = GO.read_text(encoding="utf-8")
        self.assertIn("commissionGiverHome(", go)
        self.assertIn("realmWorldGo(", go)
        self.assertIn("characterBaseCurrencyTx(", go, "a quest with no world of its own pays where the player stands")

    def test_the_card_names_the_coin_it_is_handed(self):
        self.assertIn("40 Low Spirit Crystal", rules.format_rewards({"spirit_stones": 40}, coin="Low Spirit Crystal"))
        self.assertIn("40 spirit stones", rules.format_rewards({"spirit_stones": 40}))


if __name__ == "__main__":
    unittest.main()

"""A lot's public card names only what its consignor could read (v1.12.3).

An NPC's find is consigned to the house unappraised (schema 45), and
`/auction browse` titles such a lot "Unidentified Lot" with the house's grade
band to anybody who has not read one before - the whole point of
`/economy → Auction House → Appraise`. Since v1.7.0 the tick posts a card for
every open lot the world listed, and the card titled it with
`WORLD.item_name(item_id)` and never checked `appraised`: every reader of the
channel was handed the answer the board withholds.

A card is public, so it asks the one rule (`lot_identity`) with nobody known.
Driven through the card rather than read, because the title is one expression
and the source reads correctly in both versions.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _feed():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.auction_feed")


def _lot(**extra) -> dict:
    lot = {"auction_id": 7, "house_id": "", "item_id": "qi_pill", "quantity": 1, "currency_id": "low_spirit_stone",
           "starting_bid": 10, "current_bid": 0, "active": 1, "ends_at": 2_000_000_000,
           "seller_user_id": None, "seller_npc_name": "Digger Wu", "appraised": 0, "grade_band": "of middling grade"}
    lot.update(extra)
    return lot


class AnUnreadLotIsNotNamedOnItsCard(unittest.TestCase):
    def setUp(self):
        self.feed = _feed()
        self.house = next(iter(sorted(self.feed.WORLD.auction_houses)))
        self.name = self.feed.WORLD.item_name("qi_pill")
        self.assertTrue(self.name and self.name != "Unidentified Lot", "the fixture item has no name; the gate is broken, not the tree")

    def _card_text(self, lot, state="open") -> str:
        card = asyncio.run(self.feed.lot_card(self.house, lot, state=state))
        return card.text()

    def test_an_unappraised_lot_is_an_unidentified_lot_with_its_grade_band(self):
        text = self._card_text(_lot())
        self.assertIn("Unidentified Lot", text)
        self.assertNotIn(self.name, text, "the public card named a lot its consignor could not read: " + text[:200])
        self.assertIn("of middling grade", text, "the house's word on the lot is missing from the card")
        self.assertIn("Appraise", text, "the card does not say how to read it")

    def test_the_settled_cards_keep_it_unnamed_too(self):
        for state in ("sold", "unsold"):
            with self.subTest(state=state):
                self.assertNotIn(self.name, self._card_text(_lot(current_bid=40, merchant_buyer="madam_wen"), state=state))

    def test_an_appraised_lot_is_named(self):
        text = self._card_text(_lot(appraised=1))
        self.assertIn(self.name, text)
        self.assertNotIn("Unidentified", text)

    def test_the_card_and_the_board_read_one_rule(self):
        # The board's per-player `known` set is the only thing a card lacks:
        # both ask the same function, which lives below the commands.
        with patch.dict(os.environ, ENV):
            economy = importlib.import_module("app.bot.commands.economy")
        self.assertIs(economy.lot_identity, self.feed.lot_identity)
        self.assertEqual(self.feed.lot_identity(_lot(), {"qi_pill"})[0], self.name,
                         "somebody who has read one before still names it on sight")


if __name__ == "__main__":
    unittest.main()

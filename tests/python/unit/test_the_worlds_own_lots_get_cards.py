"""Every open lot gets a card, not only a player's own (v1.7.0).

Reported as "I have got no updates on auction channel". A lot card was posted in
exactly one place - `announce_lot`, called from a player's own `/auction sell` -
while the tick's `settle_lots` only ever edited cards that already existed. So
every lot the world listed itself (`npc_finds.go` consigns an NPC's find, or a
grave-robber's keepsake, to the nearest house) sat on the floor with no card,
and on a small server that is most of the floor. And had one been posted, it
would have read "Seller: None": an NPC lot carries `seller_npc_name` and no
seller id.

`sync_lots` posts the missing cards on the tick. This drives it against a fake
database and a fake channel rather than reading its source, because the source
reads correctly in both versions - it is which lots reach `announce_lot` that
changed.
"""
from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _feed():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.auction_feed")


class FakeMessage:
    # Since v1.9.0 a card is a Components V2 view; the fake keeps the card the
    # view carries, which is what these tests read.
    def __init__(self, message_id: int, embed, channel=None):
        self.id = message_id
        self.embed = embed
        self.channel = channel

    async def edit(self, *, view, content=None, embed=None):
        self.embed = view.card

    async def delete(self):
        self.channel.messages.pop(self.id, None)


class FakeChannel:
    def __init__(self, channel_id: int = 900):
        self.id = channel_id
        self.messages: dict[int, FakeMessage] = {}

    async def send(self, *, view):
        message = FakeMessage(1000 + len(self.messages), view.card, self)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id: int):
        return self.messages[message_id]


class FakeGuild:
    id = 42


class FakeDB:
    def __init__(self, lots: list[dict], bound_houses: set[str]):
        self.lots = {int(lot["auction_id"]): dict(lot) for lot in lots}
        self.bound = bound_houses
        self.cards: dict[int, dict] = {}

    async def list_active_auctions(self, house_id=None):
        return [dict(lot) for lot in self.lots.values() if int(lot.get("active") or 0)]

    async def get_auction(self, auction_id):
        lot = self.lots.get(int(auction_id))
        return dict(lot) if lot else None

    async def get_auction_house_channels(self, guild_id):
        return [{"house_id": house, "channel_id": 900} for house in sorted(self.bound)]

    async def list_auction_lot_messages(self, guild_id):
        return [dict(card) for card in self.cards.values()]

    async def remember_auction_lot_message(self, *, auction_id, guild_id, house_id, channel_id, message_id):
        self.cards[int(auction_id)] = {"auction_id": auction_id, "guild_id": guild_id, "house_id": house_id,
                                       "channel_id": channel_id, "message_id": message_id}

    async def forget_auction_lot_message(self, auction_id):
        self.cards.pop(int(auction_id), None)

    async def get_character(self, user_id):
        return None


def _lot(auction_id: int, house: str, **extra) -> dict:
    lot = {"auction_id": auction_id, "house_id": house, "item_id": "qi_pill", "quantity": 1,
           "currency_id": "low_spirit_stone", "starting_bid": 10, "current_bid": 0, "active": 1,
           "ends_at": 2_000_000_000, "seller_user_id": None, "seller_npc_name": ""}
    lot.update(extra)
    return lot


class TheTickCardsWhatTheWorldListed(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.feed = _feed()
        self.house = next(iter(sorted(self.feed.WORLD.auction_houses)))
        self.channel = FakeChannel()

    async def _sync(self, db):
        async def resolve(guild, channel_id):
            return self.channel
        with patch.object(self.feed, "DB", db), patch.object(self.feed, "_resolve_text_channel", resolve):
            return await self.feed.sync_lots(FakeGuild())

    async def test_an_npc_consignment_gets_a_card_naming_the_npc(self):
        db = FakeDB([_lot(7, self.house, seller_npc_name="Digger Wu")], {self.house})
        await self._sync(db)
        self.assertIn(7, db.cards, "an open lot the world listed got no card - the tick only edited cards that existed")
        card = self.channel.messages[db.cards[7]["message_id"]].embed
        seller = next(value for name, value, _inline in card.fields if name == "Seller")
        self.assertEqual(seller, "Digger Wu", "an NPC lot's card must name its NPC seller, not 'None'")

    async def test_a_second_tick_posts_nothing_new(self):
        db = FakeDB([_lot(7, self.house, seller_npc_name="Digger Wu")], {self.house})
        await self._sync(db)
        await self._sync(db)
        self.assertEqual(len(self.channel.messages), 1)

    async def test_a_house_with_no_channel_is_skipped(self):
        db = FakeDB([_lot(7, self.house)], set())
        await self._sync(db)
        self.assertEqual(db.cards, {})

    async def test_a_settled_lot_is_still_struck(self):
        db = FakeDB([_lot(7, self.house, seller_npc_name="Digger Wu")], {self.house})
        await self._sync(db)
        db.lots[7].update(active=0, current_bid=40, merchant_buyer="madam_wen")
        closed = await self._sync(db)
        self.assertEqual(closed, 1)
        self.assertNotIn(7, db.cards)


class ACardOutlivesNeitherItsLotNorItsSeller(unittest.IsolatedAsyncioTestCase):
    """v1.34.0: the card of a lot whose row is gone came down as a record and
    stayed up as a message - "Seller: <name>" on the public floor for good - and
    an erasure or a reset never reached the cards of the lots its person sold."""

    def setUp(self):
        self.feed = _feed()
        self.house = next(iter(sorted(self.feed.WORLD.auction_houses)))
        self.channel = FakeChannel()

    async def _run(self, db, call):
        async def resolve(guild, channel_id):
            return self.channel
        with patch.object(self.feed, "DB", db), patch.object(self.feed, "_resolve_text_channel", resolve):
            return await call()

    async def _carded(self, lots, *, bound=True):
        db = FakeDB(lots, {self.house} if bound else set())
        await self._run(db, lambda: self.feed.sync_lots(FakeGuild()))
        return db

    async def test_a_lot_whose_row_is_gone_loses_its_message_too(self):
        db = await self._carded([_lot(7, self.house, seller_user_id=88)])
        message_id = db.cards[7]["message_id"]
        self.assertIn(message_id, self.channel.messages)
        db.lots.pop(7)
        await self._run(db, lambda: self.feed.sync_lots(FakeGuild()))
        self.assertNotIn(7, db.cards)
        self.assertNotIn(
            message_id, self.channel.messages,
            "sync_lots forgot a vanished lot's message id and left the card standing on the floor, "
            "still naming its seller",
        )

    async def test_the_cards_a_person_sold_or_led_are_read_by_role(self):
        db = await self._carded([
            _lot(1, self.house, seller_user_id=88),
            _lot(2, self.house, seller_user_id=99, current_bidder_user_id=88, current_bid=20),
            _lot(3, self.house, seller_user_id=99),
        ])
        records = await self._run(db, lambda: self.feed.player_lot_cards(FakeGuild(), 88))
        self.assertEqual({int(r["auction_id"]): r["role"] for r in records}, {1: "seller", 2: "bidder"})

    async def test_a_sellers_cards_come_down_and_a_bidders_card_is_brought_up_to_date(self):
        db = await self._carded([
            _lot(1, self.house, seller_user_id=88),
            _lot(2, self.house, seller_user_id=99, current_bidder_user_id=88, current_bid=20),
        ])
        records = await self._run(db, lambda: self.feed.player_lot_cards(FakeGuild(), 88))
        seller_card, bidder_card = db.cards[1]["message_id"], db.cards[2]["message_id"]
        # What the engine did between the read and the settle: the seller's lot
        # is deleted, the bidder's is left with no high bid.
        db.lots.pop(1)
        db.lots[2].update(current_bidder_user_id=None, current_bid=0)
        await self._run(db, lambda: self.feed.settle_player_lot_cards(FakeGuild(), records))
        self.assertNotIn(seller_card, self.channel.messages, "the erased seller's lot card is still on the floor")
        self.assertNotIn(1, db.cards)
        self.assertIn(bidder_card, self.channel.messages, "another seller's lot lost its card")
        card = self.channel.messages[bidder_card].embed
        high = next(value for name, value, _inline in card.fields if name == "High bidder")
        self.assertEqual(high, "None", "the card still names the leaving high bidder")

    async def test_a_floor_that_cannot_be_read_costs_the_cards_not_the_command(self):
        class Broken(FakeDB):
            async def list_auction_lot_messages(self, guild_id):
                raise RuntimeError("engine down")
        records = await self._run(Broken([], set()), lambda: self.feed.player_lot_cards(FakeGuild(), 88))
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()

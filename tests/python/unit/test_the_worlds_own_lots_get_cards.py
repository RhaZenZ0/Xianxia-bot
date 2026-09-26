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
    def __init__(self, message_id: int, embed):
        self.id = message_id
        self.embed = embed

    async def edit(self, *, embed):
        self.embed = embed


class FakeChannel:
    def __init__(self, channel_id: int = 900):
        self.id = channel_id
        self.messages: dict[int, FakeMessage] = {}

    async def send(self, *, embed):
        message = FakeMessage(1000 + len(self.messages), embed)
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
        seller = next(field.value for field in card.fields if field.name == "Seller")
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


if __name__ == "__main__":
    unittest.main()

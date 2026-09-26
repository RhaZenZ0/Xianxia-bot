"""A stall's card follows the stall (v1.7.0).

Each world has a read-only market-stalls channel, and the bot keeps one card
per open stall in it. This drives `stall_feed` against a fake database and a
fake channel: a card is posted when a stall opens, edited in place when what is
on it changes, left alone when nothing did (the tick refreshes every stall, and
re-editing every card every tick would spend Discord's rate limit on nothing),
posted again if somebody deleted it, and taken down when the stall closes - on
the command, or on the next tick when the stall went some other way.
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
        return importlib.import_module("app.bot.stall_feed")


class NotFound(Exception):
    pass


class FakeMessage:
    def __init__(self, channel, message_id, embed):
        self.channel, self.id, self.embed, self.edits = channel, message_id, embed, 0

    async def edit(self, *, embed):
        self.embed = embed
        self.edits += 1

    async def delete(self):
        self.channel.messages.pop(self.id, None)


class FakeChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.messages: dict[int, FakeMessage] = {}
        self._next = channel_id * 100

    async def send(self, *, embed):
        self._next += 1
        message = FakeMessage(self, self._next, embed)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id):
        if message_id not in self.messages:
            raise NotFound(message_id)
        return self.messages[message_id]


class FakeGuild:
    id = 42


class FakeDB:
    def __init__(self):
        self.stalls: dict[int, dict] = {}
        self.cards: dict[int, dict] = {}

    async def list_player_stalls(self):
        return [dict(s, listings=[dict(l) for l in s["listings"]]) for s in self.stalls.values()]

    async def list_stall_cards(self, guild_id):
        return [dict(c) for c in self.cards.values()]

    async def remember_stall_card(self, *, guild_id, user_id, channel_id, message_id):
        self.cards[int(user_id)] = {"guild_id": guild_id, "user_id": user_id, "channel_id": channel_id, "message_id": message_id}

    async def forget_stall_card(self, guild_id, user_id):
        self.cards.pop(int(user_id), None)


class StallCards(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.feed = _feed()
        self.db = FakeDB()
        self.mortal = FakeChannel(7)
        self.spiritual = FakeChannel(8)
        self.channels = {"Mortal World": self.mortal, "Spiritual World": self.spiritual}

    async def _run(self, coro_fn, *args):
        channels = self.channels

        async def stall_channel(guild, world):
            return channels.get(world)

        async def resolve(guild, channel_id):
            return next((c for c in channels.values() if c.id == int(channel_id)), None)

        fake_discord_error = type("HTTPException", (Exception,), {})
        with patch.object(self.feed, "DB", self.db), patch.object(self.feed, "stall_channel", stall_channel), \
                patch.object(self.feed, "_resolve_text_channel", resolve), \
                patch.object(self.feed.discord, "HTTPException", (fake_discord_error, NotFound)):
            return await coro_fn(FakeGuild(), *args)

    def _open(self, uid=5, city="Greenriver Town", listings=None):
        self.db.stalls[uid] = {"user_id": uid, "city": city, "name": "Lin's Table", "currency_id": "low_spirit_stone",
                               "owner_name": "Lin Mei", "listings": list(listings or [])}

    def _text(self, message) -> str:
        embed = message.embed
        return "\n".join([embed.title or "", embed.description or ""] + [f.value for f in embed.fields])

    async def test_a_card_is_posted_in_the_stalls_world_and_names_the_grade(self):
        self._open(listings=[{"listing_id": 12, "item_id": "qi_pill@high", "quantity": 2, "unit_price": 90}])
        await self._run(self.feed.refresh_stall, 5)
        self.assertEqual(len(self.mortal.messages), 1, "the stall stands in the Mortal World, so its card goes there")
        self.assertEqual(self.spiritual.messages, {})
        text = self._text(next(iter(self.mortal.messages.values())))
        self.assertIn("Lin's Table", text)
        self.assertIn("(High)", text, "a graded listing is named at its grade on the card")
        self.assertIn("#12", text)

    async def test_a_change_edits_the_same_card(self):
        self._open(listings=[{"listing_id": 12, "item_id": "qi_pill", "quantity": 2, "unit_price": 9}])
        await self._run(self.feed.refresh_stall, 5)
        self.db.stalls[5]["listings"][0]["quantity"] = 1
        await self._run(self.feed.refresh_stall, 5)
        self.assertEqual(len(self.mortal.messages), 1, "a changed stall was posted twice rather than edited")
        message = next(iter(self.mortal.messages.values()))
        self.assertIn("×1", self._text(message))

    async def test_the_tick_leaves_an_unchanged_card_alone(self):
        self._open(listings=[{"listing_id": 12, "item_id": "qi_pill", "quantity": 2, "unit_price": 9}])
        await self._run(self.feed.sync_stalls)
        message = next(iter(self.mortal.messages.values()))
        await self._run(self.feed.sync_stalls)
        await self._run(self.feed.sync_stalls)
        self.assertEqual(message.edits, 0, "the tick re-edited a card that said the same thing")
        self.db.stalls[5]["listings"][0]["quantity"] = 1  # the town bought one on the tick
        await self._run(self.feed.sync_stalls)
        self.assertEqual(message.edits, 1)

    async def test_a_card_deleted_by_hand_is_posted_again(self):
        self._open()
        await self._run(self.feed.refresh_stall, 5)
        self.mortal.messages.clear()
        await self._run(self.feed.refresh_stall, 5)
        self.assertEqual(len(self.mortal.messages), 1)

    async def test_a_closed_stall_takes_its_card_down(self):
        self._open()
        await self._run(self.feed.refresh_stall, 5)
        del self.db.stalls[5]
        await self._run(self.feed.refresh_stall, 5)
        self.assertEqual(self.mortal.messages, {})
        self.assertEqual(self.db.cards, {})

    async def test_the_tick_takes_down_a_stall_that_went_some_other_way(self):
        self._open()
        await self._run(self.feed.sync_stalls)
        del self.db.stalls[5]  # an erasure's cascade, a GM - not a command this module saw
        removed = await self._run(self.feed.sync_stalls)
        self.assertEqual(removed, 1)
        self.assertEqual(self.mortal.messages, {})

    async def test_a_stall_in_a_world_with_no_channel_gets_no_card(self):
        self._open(city="Spirit Jade Capital")
        del self.channels["Spiritual World"]
        await self._run(self.feed.refresh_stall, 5)
        self.assertEqual(self.db.cards, {})
        self.assertEqual(self.mortal.messages, {}, "a stall's card went into another world's market")


if __name__ == "__main__":
    unittest.main()

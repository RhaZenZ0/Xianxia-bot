"""An autonomous event whose thread cannot be opened leaves no intro behind (v1.34.0).

`sweep_events_without_a_scene` retries, every few minutes, every active event
that has no `event_threads` row. `spawn_system_event_thread` posts the scene
intro and then opens the thread on it; when the thread raises (the bot lacks
Create Public Threads in that channel) it returned None with the intro still
posted, so the sweep re-posted a fresh "AUTONOMOUS WORLD EVENT" intro on every
retry for the event's whole life. The intro is taken down when its thread
fails, so a retry leaves no residue; and a failure to post the intro at all
still returns None without touching anything.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _forbidden() -> discord.Forbidden:
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")


class FakeThread:
    id, mention = 77, "<#77>"

    async def send(self, *args, **kwargs):
        return None


class FakeMessage:
    def __init__(self, fail_thread: bool):
        self.id, self.fail_thread, self.deleted = 55, fail_thread, 0

    async def create_thread(self, **kwargs):
        if self.fail_thread:
            raise _forbidden()
        return FakeThread()

    async def delete(self):
        self.deleted += 1


class FakeChannel:
    def __init__(self, *, fail_thread: bool = False, fail_send: bool = False):
        self.id, self.fail_thread, self.fail_send, self.sent = 44, fail_thread, fail_send, []

    async def send(self, content, **kwargs):
        if self.fail_send:
            raise _forbidden()
        message = FakeMessage(self.fail_thread)
        self.sent.append(message)
        return message


class FakeDB:
    def __init__(self):
        self.registered = []

    async def get_server_config(self, guild_id):
        return {}

    async def get_world_event(self, event_key):
        return {"event_key": event_key, "event_type": "demon_invasion", "location": "Qingyun Village"}

    async def register_event_thread(self, **kwargs):
        self.registered.append(kwargs)


class AFailedEventThreadLeavesNoIntro(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, ENV):
            self.scene = importlib.import_module("app.bot.ui.event_scene")

    def _spawn(self, channel):
        db = FakeDB()

        async def location(event_key):
            return "Qingyun Village"

        async def parent(guild, location):
            return channel

        async def feed(guild, location):
            return None

        with patch.object(self.scene, "DB", db), \
                patch.object(self.scene, "_event_scene_location", location), \
                patch.object(self.scene, "event_scene_parent", parent), \
                patch.object(self.scene, "world_event_channel", feed):
            result = asyncio.run(self.scene.spawn_system_event_thread(
                SimpleNamespace(id=1), title="Demon Invasion", announcement="x",
                event_type="demon_invasion", expires_at=0.0, event_key="e1",
            ))
        return result, db

    def test_the_reader_works_a_thread_that_opens_is_registered(self):
        channel = FakeChannel()
        result, db = self._spawn(channel)
        self.assertIsNotNone(result)
        self.assertEqual(len(db.registered), 1)
        self.assertEqual(channel.sent[0].deleted, 0, "a thread that opened lost its intro")

    def test_a_thread_that_fails_takes_its_intro_down(self):
        channel = FakeChannel(fail_thread=True)
        result, db = self._spawn(channel)
        self.assertIsNone(result)
        self.assertEqual(db.registered, [])
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(
            channel.sent[0].deleted, 1,
            "the intro of an event whose thread failed to open is still posted: the sweep that "
            "retries events without a scene every few minutes will post it again on every retry",
        )

    def test_an_intro_that_cannot_be_posted_touches_nothing(self):
        result, db = self._spawn(FakeChannel(fail_send=True))
        self.assertIsNone(result)
        self.assertEqual(db.registered, [])


if __name__ == "__main__":
    unittest.main()

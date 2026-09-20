"""The bot tells a server what changed, once (v1.0.0-rc.59).

Every release's notes were already written in the form a player can read -
`VERSIONS.md`'s changelog, one entry per release - and nothing had ever shown
them to anybody. `#updates` is where they go now.

What this holds is the three rules that keep it from being annoying: it is
idempotent across restarts, a fresh install is told nothing, and an unbound
channel does not burn the announcement - each **driven**, in
`TheAnnouncementHappensOnce`, rather than asserted in a sentence. The first
version of this file said exactly the paragraph above and tested only the
changelog parser and the Discord chunker, so removing the marker write left it
green. That is the fault the release is about, in the gate written for it.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

SAMPLE = """# History

## Changelog

**1.0.0** (rc.59) gives the server an order.

A second paragraph of the same entry.

**1.0.0** (rc.58) wires the numbers that reached no rule.

Its own second paragraph.

**0.40.0** the one before the ones.
"""


def _module():
    with patch.dict(os.environ, ENV):
        import importlib

        return importlib.import_module("app.bot.admin.release_notes")


class TheNotesAreReadOffTheChangelog(unittest.TestCase):
    def setUp(self):
        self.notes = _module()

    def test_an_entry_is_every_paragraph_until_the_next_release(self):
        entry = self.notes.release_notes_for("1.0.0-rc.59", source=SAMPLE)
        self.assertIn("gives the server an order", entry)
        self.assertIn("A second paragraph of the same entry.", entry)
        self.assertNotIn("rc.58", entry, "an entry bled into the one below it")

    def test_a_version_with_no_rc_takes_the_newest_entry_for_it(self):
        """A development checkout has no `RELEASE_TAG`, so `INSTALLED_VERSION`
        is the bare `1.0.0` - and the tree in hand is the newest entry."""
        entry = self.notes.release_notes_for("1.0.0", source=SAMPLE)
        self.assertIn("rc.59", entry)

    def test_a_release_the_changelog_does_not_carry_is_none(self):
        self.assertIsNone(self.notes.release_notes_for("1.0.0-rc.1000", source=SAMPLE))
        self.assertIsNone(self.notes.release_notes_for("9.9.9", source=SAMPLE))

    def test_the_shipped_changelog_parses(self):
        """Against the real file, not the fixture: a parser that only works on
        a sample is a parser that has never met the thing it parses."""
        entry = self.notes.release_notes_for("1.0.0")
        self.assertIsNotNone(entry, "the shipped VERSIONS.md yields no entry at all")
        self.assertGreater(len(entry), 400)
        self.assertTrue(entry.startswith("**1.0.0**"))


class TheNotesFitInDiscord(unittest.TestCase):
    def setUp(self):
        self.notes = _module()

    def test_every_chunk_is_under_the_limit(self):
        entry = self.notes.release_notes_for("1.0.0")
        chunks = self.notes.chunk_for_discord(entry)
        self.assertGreater(len(chunks), 1, "the real entry is long enough to need splitting")
        for chunk in chunks:
            self.assertLessEqual(len(chunk), self.notes.MESSAGE_LIMIT)

    def test_nothing_is_lost_in_the_split(self):
        """Rejoined with a blank line, because each chunk is its own Discord
        message and the paragraph break between them is what a message
        boundary already is. Joining with nothing welds the last word of one
        chunk to the first of the next, which is how this test failed on its
        first run and is a fact about the join, not about the chunker."""
        entry = self.notes.release_notes_for("1.0.0")
        rejoined = "\n\n".join(self.notes.chunk_for_discord(entry)).split()
        self.assertEqual(rejoined, entry.split(), "the split dropped or reordered words")

    def test_a_paragraph_longer_than_the_limit_is_still_split(self):
        long_entry = "**1.0.0** (rc.1) " + ("word " * 2000)
        for chunk in self.notes.chunk_for_discord(long_entry):
            self.assertLessEqual(len(chunk), self.notes.MESSAGE_LIMIT)

    def test_the_limit_leaves_room_for_the_header(self):
        """A chunk is posted with a `## v…` header on the first message, so
        the limit must sit under Discord's 2000 with room for it."""
        self.assertLess(self.notes.MESSAGE_LIMIT, 2000)


class FakeTextChannel:
    """Stands in for `discord.TextChannel`, which `announce_release_if_new`
    tests with `isinstance` before it will post."""

    name = "updates"

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, content: str) -> None:
        self.sent.append(content)


class FakeGuild:
    def __init__(self, channel=None, guild_id: int = 42):
        self.id = guild_id
        self._channel = channel

    def get_channel(self, channel_id: int):
        return self._channel


class FakeDB:
    def __init__(self, config: dict):
        self.config = dict(config)
        self.writes: list[tuple[int, str]] = []

    async def get_server_config(self, guild_id: int) -> dict:
        return dict(self.config)

    async def set_announced_release(self, guild_id: int, version: str) -> None:
        self.writes.append((guild_id, version))
        self.config["announced_release"] = version


class TheAnnouncementHappensOnce(unittest.IsolatedAsyncioTestCase):
    """The three rules the module exists to keep, driven rather than described.

    The first version of this file tested the parser and the chunker and never
    called `announce_release_if_new` at all, while its own docstring claimed it
    held all three - so deleting the marker write left the suite green. That is
    the fault this release is about, found in the gate written for it, and only
    the drill said so.
    """

    RUNNING = "1.0.0-rc.59"

    def setUp(self):
        self.notes = _module()

    def _patched(self, db, *, channel=None):
        """`DB`, the running version and the changelog, all pinned.

        `discord` is replaced wholesale because the isinstance check is the
        one thing standing between a fake channel and the post.
        """
        import contextlib
        import types

        fake_discord = types.SimpleNamespace(
            TextChannel=FakeTextChannel,
            Forbidden=Exception,
            HTTPException=Exception,
            Guild=FakeGuild,
        )
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(self.notes, "DB", db))
        stack.enter_context(patch.object(self.notes, "discord", fake_discord))
        stack.enter_context(patch.object(self.notes, "INSTALLED_VERSION", self.RUNNING))
        stack.enter_context(patch.object(self.notes, "release_notes_for", lambda v, **k: "The notes."))
        return stack

    async def test_it_posts_once_and_never_again(self):
        """The row is the memory: the second boot is a no-op because the
        first wrote what it announced, not because anything remembered."""
        channel = FakeTextChannel()
        db = FakeDB({"announced_release": "1.0.0-rc.58", "updates_channel_id": 7})
        guild = FakeGuild(channel)
        with self._patched(db):
            first = await self.notes.announce_release_if_new(guild)
            second = await self.notes.announce_release_if_new(guild)
        self.assertEqual(first, self.RUNNING)
        self.assertIsNone(second, "a restart announced the same release twice")
        self.assertEqual(db.writes, [(42, self.RUNNING)], "the marker was not written")
        self.assertEqual(len(channel.sent), 1)
        self.assertIn("The notes.", channel.sent[0])

    async def test_a_fresh_install_is_told_nothing(self):
        """A NULL marker records where we are and says nothing: a server being
        set up today does not want forty paragraphs of history."""
        channel = FakeTextChannel()
        db = FakeDB({"announced_release": None, "updates_channel_id": 7})
        with self._patched(db):
            announced = await self.notes.announce_release_if_new(FakeGuild(channel))
        self.assertIsNone(announced)
        self.assertEqual(channel.sent, [])
        self.assertEqual(db.writes, [(42, self.RUNNING)], "a fresh install must still record where it is")

    async def test_an_unbound_channel_does_not_burn_the_announcement(self):
        """Bind `#updates` a week later and the notes still arrive, which they
        cannot if the marker moved while there was nowhere to post."""
        db = FakeDB({"announced_release": "1.0.0-rc.58", "updates_channel_id": None})
        with self._patched(db):
            announced = await self.notes.announce_release_if_new(FakeGuild(None))
        self.assertIsNone(announced)
        self.assertEqual(db.writes, [], "the marker advanced with nowhere to post")

    async def test_a_channel_that_is_bound_but_gone_is_the_same(self):
        """A deleted channel resolves to None, which is the unbound case with
        a stale id in the row."""
        db = FakeDB({"announced_release": "1.0.0-rc.58", "updates_channel_id": 7})
        with self._patched(db):
            announced = await self.notes.announce_release_if_new(FakeGuild(None))
        self.assertIsNone(announced)
        self.assertEqual(db.writes, [])

    async def test_a_release_with_no_notes_leaves_the_marker_alone(self):
        """So a corrected VERSIONS.md still gets its chance."""
        channel = FakeTextChannel()
        db = FakeDB({"announced_release": "1.0.0-rc.58", "updates_channel_id": 7})
        with self._patched(db) as stack:
            stack.enter_context(patch.object(self.notes, "release_notes_for", lambda v, **k: None))
            announced = await self.notes.announce_release_if_new(FakeGuild(channel))
        self.assertIsNone(announced)
        self.assertEqual(channel.sent, [])
        self.assertEqual(db.writes, [])

    async def test_a_long_entry_arrives_as_several_messages_headed_once(self):
        channel = FakeTextChannel()
        db = FakeDB({"announced_release": "1.0.0-rc.58", "updates_channel_id": 7})
        long_notes = "\n\n".join(f"Paragraph {i}. " + "word " * 300 for i in range(4))
        with self._patched(db) as stack:
            stack.enter_context(patch.object(self.notes, "release_notes_for", lambda v, **k: long_notes))
            await self.notes.announce_release_if_new(FakeGuild(channel))
        self.assertGreater(len(channel.sent), 1)
        self.assertEqual([c for c in channel.sent if c.startswith("## ")], channel.sent[:1],
                         "the header belongs on the first message and nowhere else")
        for message in channel.sent:
            self.assertLessEqual(len(message), 2000, "Discord refuses a message over 2000 characters")


if __name__ == "__main__":
    unittest.main()

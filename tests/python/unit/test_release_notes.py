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

GAP = """# History

## Changelog

**1.0.10** names whoever is actually standing there.

**1.0.9** introduces the game a realm at a time.

**1.0.8** gives you back the people in front of you.

**1.0.7** gives every world its own age.

**1.0.6** makes one promise true in the engine too.

**1.0.5** stops a quest being lost to a failure in drawing the reply.

**1.0.0** is the first release with no suffix on its tag.

**1.0.0** (rc.59) gives the server an order.
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

    def _patched(self, db, *, running=None, source=SAMPLE):
        """`DB`, the running version and the changelog, all pinned.

        `discord` is replaced wholesale because the isinstance check is the
        one thing standing between a fake channel and the post.

        The changelog is pinned as a **file**, not by patching the reader.
        rc.59's version of this stubbed `release_notes_for` with a lambda, so
        the parser and the walk beneath it were never driven from here at all -
        and v1.0.11 replaced the reader with `releases_between` under a green
        suite. A fixture that stands in for the thing being changed cannot fail
        the way production fails.
        """
        import contextlib
        import tempfile
        import types
        from pathlib import Path

        fake_discord = types.SimpleNamespace(
            TextChannel=FakeTextChannel,
            Forbidden=Exception,
            HTTPException=Exception,
            Guild=FakeGuild,
        )
        stack = contextlib.ExitStack()
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "VERSIONS.md"
        tmp.write_text(source, encoding="utf-8")
        stack.enter_context(patch.object(self.notes, "DB", db))
        stack.enter_context(patch.object(self.notes, "discord", fake_discord))
        stack.enter_context(patch.object(self.notes, "INSTALLED_VERSION", running or self.RUNNING))
        stack.enter_context(patch.object(self.notes, "VERSIONS_FILE", tmp))
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
        self.assertIn("gives the server an order", channel.sent[0])

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
        with self._patched(db, source=SAMPLE.replace("**1.0.0** (rc.59)", "**0.39.0** (rc.59)")):
            announced = await self.notes.announce_release_if_new(FakeGuild(channel))
        self.assertIsNone(announced)
        self.assertEqual(channel.sent, [])
        self.assertEqual(db.writes, [])

    async def test_a_long_entry_still_arrives_as_one_short_message(self):
        """The point of the short post. rc.59 shipped this posting the entry
        whole - 3,801 characters across three messages for rc.58 - which is
        written for an operator reading `VERSIONS.md`, not for somebody
        glancing at a channel."""
        channel = FakeTextChannel()
        db = FakeDB({"announced_release": "1.0.0-rc.58", "updates_channel_id": 7})
        long_notes = "**1.0.0** (rc.59) does one thing. " + "\n\n".join(
            f"Paragraph {i}. " + "word " * 300 for i in range(4))
        with self._patched(db, source=f"# History\n\n## Changelog\n\n{long_notes}\n"):
            await self.notes.announce_release_if_new(FakeGuild(channel))
        self.assertEqual(len(channel.sent), 1, "a whole entry reached the channel again")
        post = channel.sent[0]
        self.assertLess(len(post), 400, post)
        self.assertIn("does one thing.", post)
        self.assertNotIn("Paragraph 1", post, "the body of the entry does not belong in the channel")
        self.assertIn("Full notes:", post)


class NoReleaseIsJumpedOver(unittest.IsolatedAsyncioTestCase):
    """A skipped version is told, not stepped across (v1.0.11).

    rc.59 compared the marker to the running version for **equality** and
    fetched that one entry, so a server upgrading 1.0.5 -> 1.0.8 was told about
    1.0.8 and never about 1.0.6 or 1.0.7: the marker jumped straight across and
    nothing recorded that two releases went past unmentioned. Neither of the
    two neighbouring behaviours that *do* work covered it - a missing entry and
    an unbound channel both leave the marker alone for a later chance, and a
    skipped version is in neither category, because it was never looked up.

    "Posts each missed release exactly once" is precisely the kind of claim
    rc.59's own version of this file asserted in prose and never drove, so it
    is driven here, including through a send that fails halfway.
    """

    RUNNING = "1.0.8"

    setUp = TheAnnouncementHappensOnce.setUp
    _patched = TheAnnouncementHappensOnce._patched

    def test_a_release_sorts_as_integers_and_below_its_candidates(self):
        """`1.0.10` is newer than `1.0.9`, and `1.0.0-rc.59` is older than
        `1.0.0` - the rule `playtest_checklist.py` learned in v1.0.1, where
        sorting versions as text inherited the wrong checklist's ticks."""
        key = self.notes.version_key
        order = ["1.0.0-rc.9", "1.0.0-rc.59", "1.0.0", "1.0.1", "1.0.9", "1.0.10"]
        self.assertEqual(sorted(order, key=key), order)

    def test_the_window_is_open_below_and_closed_above(self):
        found = [v for v, _ in self.notes.releases_between("1.0.5", "1.0.8", source=GAP)]
        self.assertEqual(found, ["1.0.6", "1.0.7", "1.0.8"], (
            "the walk must skip what the guild was already told about and stop at the release "
            "actually running - never announce a version this tree is not"))

    def test_a_marker_the_changelog_predates_yields_the_whole_window(self):
        """Being told too much once is recoverable; being told nothing is the
        fault the walk exists for."""
        found = [v for v, _ in self.notes.releases_between("0.1.0", "1.0.6", source=GAP)]
        self.assertEqual(found, ["1.0.0-rc.59", "1.0.0", "1.0.5", "1.0.6"])

    async def test_every_skipped_release_is_posted_oldest_first(self):
        channel = FakeTextChannel()
        db = FakeDB({"announced_release": "1.0.5", "updates_channel_id": 7})
        with self._patched(db, source=GAP):
            announced = await self.notes.announce_release_if_new(FakeGuild(channel))
        self.assertEqual(announced, "1.0.8")
        self.assertEqual(len(channel.sent), 3, (
            "a server upgrading 1.0.5 -> 1.0.8 must hear about 1.0.6 and 1.0.7 too; before "
            f"v1.0.11 the marker jumped straight across: {channel.sent}"))
        for index, version in enumerate(("1.0.6", "1.0.7", "1.0.8")):
            self.assertIn(f"v{version}", channel.sent[index], "the gap was not posted oldest first")
        self.assertEqual(db.writes, [(42, "1.0.8")], "the marker is written once, at the end")

    async def test_a_send_that_fails_halfway_does_not_mark_what_it_never_posted(self):
        """"Exactly once" has to survive a partial failure or it is only a
        claim about the happy path."""

        class FailsAfterOne(FakeTextChannel):
            async def send(self, content: str) -> None:
                if len(self.sent) >= 1:
                    raise RuntimeError("Discord said no")
                await super().send(content)

        channel = FailsAfterOne()
        db = FakeDB({"announced_release": "1.0.5", "updates_channel_id": 7})
        with self._patched(db, source=GAP):
            announced = await self.notes.announce_release_if_new(FakeGuild(channel))
        self.assertEqual(announced, "1.0.6")
        self.assertEqual(db.writes, [(42, "1.0.6")], (
            "the marker must stop at the last release actually posted - past it and 1.0.7 and "
            "1.0.8 look announced while nobody was told; short of it and 1.0.6 is posted twice"))

    async def test_a_long_gap_is_capped_and_says_so(self):
        channel = FakeTextChannel()
        db = FakeDB({"announced_release": "1.0.0-rc.59", "updates_channel_id": 7})
        with self._patched(db, running="1.0.10", source=GAP):
            with patch.object(self.notes, "MAX_ANNOUNCED_RELEASES", 3):
                announced = await self.notes.announce_release_if_new(FakeGuild(channel))
        self.assertEqual(announced, "1.0.10")
        self.assertEqual(len(channel.sent), 4, "three posts and the line that says what is missing")
        self.assertIn("not repeated here", channel.sent[0], (
            "a capped catch-up drops releases silently; the count has to be said, the way a fresh "
            "install is spared forty paragraphs on purpose rather than by accident"))
        for index, version in enumerate(("1.0.8", "1.0.9", "1.0.10")):
            self.assertIn(f"v{version}", channel.sent[index + 1])

    async def test_a_downgrade_announces_nothing(self):
        """An older running version yields an empty window rather than
        re-announcing everything the guild has already read."""
        channel = FakeTextChannel()
        db = FakeDB({"announced_release": "1.0.10", "updates_channel_id": 7})
        with self._patched(db, running="1.0.7", source=GAP):
            announced = await self.notes.announce_release_if_new(FakeGuild(channel))
        self.assertIsNone(announced)
        self.assertEqual(channel.sent, [])
        self.assertEqual(db.writes, [])


class TheHeadlineIsDerivedNeverAuthored(unittest.TestCase):
    """One sentence, taken off the entry that already exists.

    A second short blurb per release would be a second statement of the same
    thing, free to drift - the `sync_world_catalog` fault. Every entry in the
    changelog opens the same way, a version stamp then a verb, which is the
    structure this reads.
    """

    def setUp(self):
        self.notes = _module()

    def test_the_stamp_comes_off_and_the_sentence_survives(self):
        entry = "**1.0.0** (rc.59) makes the server readable, and gives a release\nsomewhere to announce itself.\n\nThe rest."
        self.assertEqual(
            self.notes.release_headline(entry),
            "makes the server readable, and gives a release somewhere to announce itself.")

    def test_a_version_without_an_rc_reads_the_same_way(self):
        entry = "**1.0.0** is the first release with no suffix on its tag.\n\nMore."
        self.assertEqual(self.notes.release_headline(entry),
                         "is the first release with no suffix on its tag.")

    def test_it_does_not_break_the_sentence_on_a_version_number(self):
        """`rc.59` and `1.0.0` both carry full stops. A naive split on '.'
        would end the headline at 'makes rc.' and print a fragment."""
        entry = "**1.0.0** (rc.57) makes rc.56 installable. It could not bootstrap a fresh database.\n\nMore."
        self.assertEqual(self.notes.release_headline(entry), "makes rc.56 installable.")

    def test_the_post_names_the_release_and_links_the_notes(self):
        post = self.notes.release_post("1.0.0", "**1.0.0** does a thing.\n\nAt length.")
        self.assertIn("**Xianxia RP v1.0.0** does a thing.", post)
        self.assertIn("/releases/tag/v1.0.0>", post)
        self.assertNotIn("At length", post)

    def test_every_shipped_entry_yields_a_headline_that_fits_one_message(self):
        """Against the real changelog and the archived rcs, not a fixture."""
        import re
        from tests.support import PROJECT_ROOT
        text = (PROJECT_ROOT / "VERSIONS.md").read_text(encoding="utf-8")
        text += (PROJECT_ROOT / "docs" / "history" / "CHANGELOG_1_0_0_RCS.md").read_text(encoding="utf-8")
        entries = [e for e in re.split(r"(?m)^(?=\*\*1\.0\.0\*\*)", text)[1:]]
        self.assertGreater(len(entries), 50, "the changelog and its archive should both be found")
        for entry in entries:
            stamp = entry.splitlines()[0][:40]
            with self.subTest(entry=stamp):
                head = self.notes.release_headline(entry)
                self.assertTrue(head.endswith("."), f"{stamp}: headline is not a sentence: {head!r}")
                self.assertLess(len(head), 400, stamp)
                self.assertFalse(head.startswith("**"), f"{stamp}: the stamp survived")


if __name__ == "__main__":
    unittest.main()

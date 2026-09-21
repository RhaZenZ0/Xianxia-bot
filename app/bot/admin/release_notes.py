"""The bot tells a server what changed when it changes (v1.0.0-rc.59).

Every release's notes were already written, in one place, in the form a player
can read: `VERSIONS.md`'s changelog, one paragraph-block per release, which
`test_release_version.py` already holds to the stamped version. Nothing had
ever shown them to anybody. A GM who upgraded had to go and read the file, and
the rc.21-to-rc.57 notes had to be pasted by hand into `#world-events`, the
in-fiction global feed a world-reset notice uses.

`#updates` is where they go, and the bot posts them itself - as **one short
message**, not the entry whole. rc.59 shipped this posting the whole thing,
which for rc.58 was 3,801 characters across three messages: that is written
for an operator reading `VERSIONS.md`, not for somebody glancing at a channel.
What the channel gets is the entry's opening sentence and a link to the rest,
and the sentence is **derived, never authored twice** - see `release_headline`.

Three rules hold the posting itself:

- **The row is the memory.** `server_config.announced_release` is the release
  this guild has already been told about, so the announcement is idempotent
  across restarts by construction rather than by a flag somebody has to reset.
  It is the same thing `(user_id, quest_key)` does for the beginner path.
- **A fresh install is told nothing.** A guild whose marker is NULL records the
  running release silently: a server being set up today does not want forty
  paragraphs of history, and the first release it is *told* about should be the
  first one that actually changes under it.
- **An unbound channel is not an error, and does not advance the marker.** Bind
  `#updates` a week later and the notes still arrive.
- **A skipped release is told, not jumped over** (v1.0.11). rc.59 compared the
  marker to the running version for *equality* and fetched that one entry, so a
  server upgrading 1.0.5 -> 1.0.8 was told about 1.0.8 and never about 1.0.6 or
  1.0.7 - the marker jumped straight across and nothing recorded that two
  releases went past unmentioned. `releases_between` walks the gap instead.
  The other three rules above are what keep that safe: a NULL marker still
  records silently, and a gap whose newest end this tree does not carry still
  advances nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

import discord

from ...version import INSTALLED_VERSION, RELEASE_VERSION
from ..runtime import DB, SETTINGS, log

VERSIONS_FILE = Path(__file__).resolve().parents[3] / "VERSIONS.md"

# Discord refuses a message over 2000 characters, and a release paragraph is
# routinely longer: rc.58's is about 2,500.
MESSAGE_LIMIT = 1900

# How many releases a single catch-up will post. A server that has been away a
# long time gets the newest of them and one line saying how many it is not
# being shown: the alternative is a channel with thirty messages in it, which
# is the "forty paragraphs of history" a fresh install is spared for the same
# reason.
MAX_ANNOUNCED_RELEASES = 8

_ENTRY = re.compile(r"^\*\*(?P<version>\d+\.\d+(?:\.\d+)?)\*\*\s*(?:\((?P<rc>rc\.\d+)\))?", re.M)


def _release_tag(version: str) -> tuple[str, str]:
    """`1.0.0-rc.59` -> `("1.0.0", "rc.59")`; `1.0.0` -> `("1.0.0", "")`."""
    base, _, suffix = version.partition("-")
    return base, suffix


def release_notes_for(version: str, *, source: str | None = None) -> str | None:
    """The changelog entry for `version`, as written, or None.

    An entry runs from its `**1.0.0** (rc.N)` header to the next such header -
    several paragraphs, which is how they are written and how they read.

    A version with no rc suffix (a development checkout, where `RELEASE_TAG`
    does not exist) matches the newest entry for that version, because that is
    what the tree in hand is.
    """
    try:
        text = source if source is not None else VERSIONS_FILE.read_text(encoding="utf-8")
    except OSError:
        log.warning("Could not read %s for release notes", VERSIONS_FILE)
        return None

    base, suffix = _release_tag(version)
    matches = list(_ENTRY.finditer(text))
    for index, match in enumerate(matches):
        if match.group("version") != base:
            continue
        if suffix and (match.group("rc") or "") != suffix:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        entry = text[match.start():end].strip()
        return entry or None
    return None


def chunk_for_discord(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split on blank lines first, then on lines, then hard.

    Paragraph boundaries are the seams a reader already sees, so a split there
    is invisible; a hard cut mid-word is the last resort and not the first.
    """
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(block[:cut].rstrip())
            block = block[cut:].lstrip("\n")
        current = block
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def release_headline(entry: str) -> str:
    """The entry's opening sentence, with its `**1.0.0** (rc.N)` stamp removed.

    Every entry in the changelog is written the same way - a version stamp,
    then a verb: *"**1.0.0** (rc.59) makes the server readable, and gives a
    release somewhere to announce itself."* The stamp is the subject of that
    sentence, which is what lets `release_post` set the release's name in bold
    and have the rest read exactly as written. **This is derived, never
    authored twice**: a second short blurb per release would be a second
    statement of the same thing, free to drift from the changelog the way
    `sync_world_catalog` drifted from the catalogue it claimed to sync.
    """
    paragraph = entry.split("\n\n")[0].replace("\n", " ").strip()
    body = re.sub(r"^\*\*[\d.]+\*\*\s*(?:\(rc\.\d+\)\s*)?", "", paragraph)
    # A sentence ends at a full stop after a word, a digit or a closing
    # backtick/paren - never at the dot inside `rc.59` or `1.0.0`.
    end = re.search(r"(?<=[a-z0-9)`])\.(?=\s|$)", body)
    return body[: end.start() + 1] if end else body


def version_key(version: str) -> tuple[int, ...]:
    """Sortable, and `1.0.10` is newer than `1.0.9` (v1.0.11).

    Versions sort as integers, never as text - the rule `playtest_checklist.py`
    learned in v1.0.1, where `v1.0.10` sorted before `v1.0.9` and the generator
    inherited the wrong checklist's ticks.

    A release candidate sorts **below** the release it is a candidate for, so
    `1.0.0-rc.59 < 1.0.0 < 1.0.1`. That is what the trailing sentinel is: an
    entry with no rc suffix is the final one of its base, so it takes a number
    no candidate can reach.
    """
    base, suffix = _release_tag(version)
    parts = tuple(int(piece) for piece in re.findall(r"\d+", base))
    rc = re.search(r"rc\.(\d+)", suffix)
    return parts + (int(rc.group(1)) if rc else 1 << 30,)


def releases_between(seen: str, running: str, *, source: str | None = None) -> list[tuple[str, str]]:
    """Every changelog entry after `seen` and up to `running`, oldest first.

    `[(version, entry)]`, where `version` is spelled the way the entry stamps
    itself - `1.0.0-rc.59` for a candidate, `1.0.1` for a release - so the post
    and the marker name the same thing the changelog does.

    An entry outside the window is skipped rather than clamped, and an unknown
    `seen` (a marker from a tree this one does not carry) yields everything up
    to `running`: being told too much once is recoverable, and being told
    nothing is the fault this walk exists for.
    """
    try:
        text = source if source is not None else VERSIONS_FILE.read_text(encoding="utf-8")
    except OSError:
        log.warning("Could not read %s for release notes", VERSIONS_FILE)
        return []

    low, high = version_key(seen), version_key(running)
    found: list[tuple[tuple[int, ...], str, str]] = []
    matches = list(_ENTRY.finditer(text))
    for index, match in enumerate(matches):
        rc = match.group("rc") or ""
        label = f"{match.group('version')}-{rc}" if rc else match.group("version")
        key = version_key(label)
        if not (low < key <= high):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        entry = text[match.start():end].strip()
        if entry:
            found.append((key, label, entry))
    found.sort(key=lambda row: row[0])
    return [(label, entry) for _, label, entry in found]


def release_post(version: str, entry: str) -> str:
    """What `#updates` actually gets: one short message, not the whole entry.

    rc.59 shipped this posting the changelog entry whole - 3,801 characters
    across three messages for rc.58 - which is written for an operator reading
    `VERSIONS.md`, not for somebody glancing at a channel. The full notes are
    one click away in the release itself.
    """
    link = f"https://github.com/{SETTINGS.update_repository}/releases/tag/v{version}"
    return f"📣 **Xianxia RP v{version}** {release_headline(entry)}\n-# Full notes: <{link}>"


async def announce_release_if_new(guild: discord.Guild) -> str | None:
    """Post every release this guild has not been told about, oldest first.

    Returns the newest version actually announced, or None when there was
    nothing to do - which is the common case and is not a failure.
    """
    config = await DB.get_server_config(guild.id)
    running = INSTALLED_VERSION or RELEASE_VERSION
    seen = str(config.get("announced_release") or "").strip()
    if seen == running:
        return None

    if not seen:
        # First boot against this guild: record where we are and say nothing.
        await DB.set_announced_release(guild.id, running)
        return None

    channel_id = config.get("updates_channel_id")
    if not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return None

    pending = releases_between(seen, running)
    if not pending or pending[-1][0] != running:
        # A release whose notes this tree does not carry is not worth a post,
        # and is certainly not worth pretending: the marker stays where it is
        # so a corrected VERSIONS.md still gets its chance. That is also what
        # holds a *downgrade* still - a running version older than the marker
        # yields an empty window rather than a re-announcement.
        log.warning("No changelog entry for %s; not announcing", running)
        return None

    posts = [(version, release_post(version, entry)) for version, entry in pending]
    older = 0
    if len(posts) > MAX_ANNOUNCED_RELEASES:
        older = len(posts) - MAX_ANNOUNCED_RELEASES
        posts = posts[-MAX_ANNOUNCED_RELEASES:]

    announced: str | None = None
    try:
        if older:
            # Said rather than silently dropped, which is the whole finding:
            # a server that has been away a year gets the cap's worth of
            # sentences and one line saying how much it is not being shown.
            await channel.send(
                f"-# {older} earlier release{'s' if older != 1 else ''} since v{seen} "
                f"{'are' if older != 1 else 'is'} not repeated here - full history: "
                f"<https://github.com/{SETTINGS.update_repository}/releases>")
        for version, post in posts:
            # `chunk_for_discord` still guards the send: a headline is one short
            # message in every entry written so far, but nothing structural stops
            # somebody writing a first sentence longer than Discord will accept.
            for chunk in chunk_for_discord(post):
                await channel.send(chunk)
            announced = version
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not post release notes for %s in #%s", running, channel.name)

    if announced:
        # The marker moves to the last release actually posted, never past it.
        # A send that fails halfway through a gap must not make the releases it
        # never reached look announced - "exactly once" has to survive a partial
        # failure or it is only a claim about the happy path.
        await DB.set_announced_release(guild.id, announced)
    return announced

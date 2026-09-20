"""The bot tells a server what changed when it changes (v1.0.0-rc.59).

Every release's notes were already written, in one place, in the form a player
can read: `VERSIONS.md`'s changelog, one paragraph-block per release, which
`test_release_version.py` already holds to the stamped version. Nothing had
ever shown them to anybody. A GM who upgraded had to go and read the file, and
the rc.21-to-rc.57 notes had to be pasted by hand into `#world-events`, the
in-fiction global feed a world-reset notice uses.

`#updates` is where they go, and the bot posts them itself. Three rules hold it:

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
"""
from __future__ import annotations

import re
from pathlib import Path

import discord

from ...version import INSTALLED_VERSION, RELEASE_VERSION
from ..runtime import DB, log

VERSIONS_FILE = Path(__file__).resolve().parents[3] / "VERSIONS.md"

# Discord refuses a message over 2000 characters, and a release paragraph is
# routinely longer: rc.58's is about 2,500.
MESSAGE_LIMIT = 1900

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


async def announce_release_if_new(guild: discord.Guild) -> str | None:
    """Post this release's notes in `#updates` if the guild has not seen them.

    Returns the version announced, or None when there was nothing to do -
    which is the common case and is not a failure.
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

    notes = release_notes_for(running)
    if not notes:
        # A release whose notes this tree does not carry is not worth a post,
        # and is certainly not worth pretending: the marker stays where it is
        # so a corrected VERSIONS.md still gets its chance.
        log.warning("No changelog entry for %s; not announcing", running)
        return None

    header = f"## 📣 v{running}\n"
    try:
        for index, chunk in enumerate(chunk_for_discord(notes)):
            await channel.send(f"{header}{chunk}" if index == 0 else chunk)
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not post release notes for %s in #%s", running, channel.name)
        return None

    await DB.set_announced_release(guild.id, running)
    return running

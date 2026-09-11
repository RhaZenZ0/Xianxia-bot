"""The #bugs forum channel: guidelines post, tag sync, open reports.

Phase 6 of the main.py split (v0.19.41, docs/history/MAIN_SPLIT_PLAN.md). Reads
runtime and discord; never main.py, never the other admin modules.
Definition order is the order these had in main.py.
"""
from __future__ import annotations

from typing import Any

import discord

from ..runtime import DB, log

BUGS_CHANNEL_NAME = "bugs"


BUGS_GUIDELINES_KEY = "bugs-guidelines"


# Forum-post guidelines shown to anyone starting a new #bugs post. Stored as the forum
# channel's own topic (Discord shows a channel's topic as its "post guidelines" for
# forum channels) - GM-customizable the same way as the channel_messages feature, just
# applied by editing the channel's topic instead of sending/editing a message, since
# ForumChannel has no send() of its own.
DEFAULT_BUGS_GUIDELINES = (
    "🐛 Create a new post here for anything that looks broken — a command erroring, "
    "wrong numbers, a stuck scene, or a message that doesn't match what happened.\n\n"
    "Please include: what you did (the exact command/action), what you expected, what "
    "actually happened, and your character name. A screenshot helps but isn't required.\n\n"
    "One bug per post, please — it's much easier to track and fix that way. The GM team "
    "reads every post here and tags it as it's triaged."
)


BUGS_FORUM_TAGS: tuple[tuple[str, str], ...] = (
    ("Open", "🔴"),
    ("Investigating", "🔎"),
    ("Fixed", "✅"),
    ("Can't Reproduce", "❔"),
    ("Duplicate", "♻️"),
)


def missing_bugs_forum_tags(channel: discord.ForumChannel) -> list[str]:
    """Names from BUGS_FORUM_TAGS that this forum does not actually offer.

    Only newly created forums got the tag set; an existing #bugs channel adopted
    by setup kept whatever tags it already had (usually none) while the dashboard
    still advertised the full list, so GMs were told they could file a report
    under "Investigating" when Discord offered no such tag.
    """
    present = {str(tag.name).casefold() for tag in (getattr(channel, "available_tags", None) or ())}
    return [name for name, _emoji in BUGS_FORUM_TAGS if name.casefold() not in present]


async def sync_bugs_forum_tags(channel: discord.ForumChannel) -> list[str]:
    """Add any missing required tag to an existing forum, keeping the GM's own tags.

    Discord caps a forum at 20 tags, so this stops rather than clobbering custom
    tags if there is no room. Returns the tag names actually added.
    """
    missing = missing_bugs_forum_tags(channel)
    if not missing:
        return []
    existing = list(getattr(channel, "available_tags", None) or ())
    room = max(0, 20 - len(existing))
    if room <= 0:
        log.warning("#%s already has 20 forum tags; not adding %s", BUGS_CHANNEL_NAME, ", ".join(missing))
        return []
    wanted = dict(BUGS_FORUM_TAGS)
    added = missing[:room]
    try:
        await channel.edit(
            available_tags=existing + [discord.ForumTag(name=name, emoji=wanted.get(name)) for name in added],
            reason="Xianxia RP bug-report forum tag sync",
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not sync #%s forum tags", BUGS_CHANNEL_NAME)
        return []
    return added


async def ensure_bugs_forum_channel(
    guild: discord.Guild, *, category_name: str = "📜 Xianxia RP", create_missing: bool = False,
) -> tuple[discord.ForumChannel | None, str | None]:
    """Validate/bind (and, when create_missing, create) the #bugs forum channel where
    players report issues as individual forum posts. Mirrors ensure_base_xianxia_channels's
    resolve-by-id -> resolve-by-name -> create fallback, but for a discord.ForumChannel
    instead of a TextChannel (a forum channel has no send() of its own - posting means
    creating a thread), and keeps the forum's guidelines/topic in sync with whatever the
    GM has saved (defaulting to DEFAULT_BUGS_GUIDELINES the first time). Returns
    (channel_or_None, warning_or_None) so callers can surface a create failure without
    raising - matches every other base-channel/realm-hub helper's error handling.
    """
    cfg = await DB.get_server_config(guild.id)
    channel_id = cfg.get("bugs_channel_id")
    channel: discord.ForumChannel | None = None
    if channel_id:
        resolved = guild.get_channel(int(channel_id))
        if resolved is None:
            try:
                resolved = await guild.fetch_channel(int(channel_id))
            except discord.NotFound:
                resolved = None
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not resolve configured bugs channel %s", channel_id)
                resolved = None
        if isinstance(resolved, discord.ForumChannel):
            channel = resolved
    if channel is None:
        existing = discord.utils.get(guild.channels, name=BUGS_CHANNEL_NAME)
        if isinstance(existing, discord.ForumChannel):
            channel = existing

    warning: str | None = None
    me = guild.me
    can_create = create_missing and bool(me) and me.guild_permissions.manage_channels
    if channel is None and can_create:
        category = next((item for item in guild.categories if item.name == category_name), None)
        try:
            channel = await guild.create_forum(
                BUGS_CHANNEL_NAME, category=category, topic=DEFAULT_BUGS_GUIDELINES[:1024],
                available_tags=[discord.ForumTag(name=name, emoji=emoji) for name, emoji in BUGS_FORUM_TAGS],
                reason="Xianxia RP bug-report forum setup",
            )
        except discord.HTTPException:
            log.exception("Could not create #%s forum channel", BUGS_CHANNEL_NAME)
            warning = (
                f"Could not create **#{BUGS_CHANNEL_NAME}** — check the bot's Manage Channels "
                "permission (forum channels also need Discord's Community feature enabled on some servers)."
            )
    elif channel is None:
        warning = f"Missing **#{BUGS_CHANNEL_NAME}**. Create it as a forum channel from the admin dashboard; the bot will not provision channels."

    if channel is not None:
        await DB.set_bugs_channel_id(guild.id, channel.id)
        await sync_bugs_forum_tags(channel)
        stored = await DB.get_channel_messages(guild.id)
        guidelines = (stored.get(BUGS_GUIDELINES_KEY) or {}).get("content") or DEFAULT_BUGS_GUIDELINES
        text = guidelines.strip()[:1024]
        if channel.topic != text:
            try:
                await channel.edit(topic=text, reason="Xianxia RP bug-report forum guidelines sync")
            except discord.HTTPException:
                log.exception("Could not update #%s forum guidelines", BUGS_CHANNEL_NAME)
        if not (stored.get(BUGS_GUIDELINES_KEY) or {}).get("content"):
            await DB.set_channel_message(guild.id, BUGS_GUIDELINES_KEY, content=DEFAULT_BUGS_GUIDELINES, message_id=None)
    return channel, warning


async def bugs_forum_reports(guild: discord.Guild, channel: discord.ForumChannel, *, limit: int = 20) -> list[dict[str, Any]]:
    """Live-read recent #bugs forum posts straight from Discord (bug reports are
    player-created threads, not bot-tracked rows, so there's nothing to store in SQLite -
    this is how the dashboard 'gets info from it')."""
    combined: dict[int, discord.Thread] = {thread.id: thread for thread in channel.threads}
    try:
        async for thread in channel.archived_threads(limit=limit):
            combined.setdefault(thread.id, thread)
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not read archived threads for #%s", BUGS_CHANNEL_NAME)
    ordered = sorted(combined.values(), key=lambda t: t.id, reverse=True)[:limit]
    reports: list[dict[str, Any]] = []
    for thread in ordered:
        owner = guild.get_member(thread.owner_id) if thread.owner_id else None
        reports.append({
            "id": thread.id,
            "title": thread.name,
            "author_id": thread.owner_id,
            "author_name": str(owner) if owner else (str(thread.owner_id) if thread.owner_id else "Unknown"),
            "created_at": thread.created_at.timestamp() if thread.created_at else None,
            "message_count": thread.message_count,
            "archived": thread.archived,
            "locked": thread.locked,
            "tags": [tag.name for tag in getattr(thread, "applied_tags", [])],
            "url": f"https://discord.com/channels/{guild.id}/{thread.id}",
        })
    return reports



"""Managed guild channels and the messages the bot keeps in them: the base
channel specs and bindings, the #xianxia-info guide, and the per-channel
message state machine (default / custom / disabled).

Phase 6 of the main.py split (v0.19.41, docs/MAIN_SPLIT_PLAN.md). Reads
channels, runtime, app.version and discord; never main.py, never the other
admin modules. Definition order is the order these had in main.py.

clear_managed_channel_messages is NOT here although it sits with this code
in main.py: it re-runs the complete server setup, which lives in
server_setup.py, which imports this module - so it moved with the setup
code instead. That is the one cycle in this region.
"""
from __future__ import annotations

from typing import Any, Mapping

import discord
from discord import app_commands

from ...version import RELEASE_VERSION
from ..channels import _resolve_text_channel
from ..runtime import DB, log

BASE_CHANNEL_SETUP_CHOICES = [
    app_commands.Choice(name="Validate / bind existing base Xianxia channels", value="bind"),
    app_commands.Choice(name="Show base-channel status", value="status"),
]


BASE_CHANNEL_SPECS = {
    "world-events": "Global cultivation-world announcements, disasters, invasions and major events.",
    "event-scenes": "Event scene anchors and public roleplay threads created by the Xianxia bot.",
    "player-homes": "Read-only anchor for persistent private player-owned location and sect-residence threads.",
    "bot-logs": "Private operational and administrator-action logs for the Xianxia bot.",
    "begin-here": "New cultivators begin here with /begin before entering the wider cultivation world.",
    "xianxia-info": "Read-only game guide, onboarding and system information maintained by the Xianxia bot.",
    "expeditions": "Read-only anchor for private player expedition threads; normal roleplay happens inside the private threads, not this channel.",
}


READ_ONLY_BASE_CHANNELS = {"xianxia-info", "expeditions", "player-homes"}


def _base_channel_bindings(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "world-events": cfg.get("announcement_channel_id"),
        "event-scenes": cfg.get("event_scene_channel_id"),
        "player-homes": cfg.get("home_scene_channel_id"),
        "bot-logs": cfg.get("log_channel_id"),
        "begin-here": cfg.get("begin_channel_id"),
        "xianxia-info": cfg.get("info_channel_id"),
        "expeditions": cfg.get("exploration_channel_id"),
    }


def _xianxia_info_guide_text() -> str:
    return (
        f"📖 **Xianxia Realm Guide — v{RELEASE_VERSION}**\n"
        "This channel is **read-only** and is **not** a game location. Use the guide menu below for detailed help.\n\n"
        "🌱 **Start** — `/begin` in `#begin-here`\n"
        "🧭 **Explore** — private expedition journal under `#expeditions`\n"
        "🏯 **Sects** — discovery → recommendation → entrance trial → sect life\n"
        "🏡 **Properties** — persistent private player/sect locations under `#player-homes`\n"
        "🧑 **Dashboard** — `/me` for scene, cultivation, relationships and quests\n"
        "📜 **Quests** — `/quests` for generic objective-driven story progress\n"
        "🔒 Unknown locations, future worlds and higher-world NPCs remain hidden until legitimately unlocked."
    )


XIANXIA_INFO_PAGES: dict[str, tuple[str, str]] = {
    "getting_started": ("🌱 Getting Started", "Use `/begin` in `#begin-here`, then `/me` to see your current state. Exploration happens in your private expedition thread; main realm-capital channels remain shared social spaces."),
    "character": ("🧬 Character & Cultivation", "Your family, spiritual root, cultivation path, realms, resources, Karma, Fate, Dao Heart and effects remain canonical game state. The AI router narrates results but cannot change mechanics."),
    "exploration": ("🧭 Exploration & Scenes", "The scene engine separates **physical location** from **active scene**. Wilderness uses your expedition journal. Player properties and sect abodes use persistent private threads. Main cities remain shared channels."),
    "sects": ("🏯 Sects", "Discover a sect route, speak with affiliated NPCs, earn recommendations, take a sect-specific trial, and join only after a canonical success. Membership can grant a private sect residence."),
    "properties": ("🏡 Player-Owned Locations", "Properties are real database-backed locations with private threads, facilities and guest permissions. Guests must be invited and physically reach the entrance before gaining access."),
    "relationships": ("🤝 NPC Relationships", "Persistent NPC state tracks trust, respect, fear, affection, debt, grudge and encounter history. Narration may describe those relationships but never owns the underlying numbers."),
    "quests": ("📜 Quests", "The generic quest engine supports reusable objectives such as explore, talk, investigate, collect, craft, travel and sect milestones. `/quests` shows available and active quests."),
    "admin": ("🧰 Server Administration", "Use `/admin → Server → Setup` for Setup Server, Repair Server, Check Permissions, Show Configuration, Sync Realm Roles and Rebuild Info Guide."),
}


class XianxiaInfoSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose a Xianxia guide topic…",
            min_values=1, max_values=1, custom_id="xianxia:v07:info:topic",
            options=[discord.SelectOption(label=title.split(" ",1)[1] if " " in title else title, value=key, emoji=title.split(" ",1)[0]) for key,(title,_) in XIANXIA_INFO_PAGES.items()],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        topic = self.values[0]
        # #xianxia-info is one persistent, shared message with a single
        # timeout=None View (custom_id-based) - every player who opens this
        # dropdown sees the exact same option list, so a non-admin option
        # cannot be hidden from the menu itself. The "admin" topic is gated
        # here instead: anyone can see it listed, but only a real server
        # administrator can actually open it.
        if topic == "admin":
            member = interaction.user
            if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
                await interaction.response.send_message(
                    "🔒 This topic is for server administrators only.", ephemeral=True
                )
                return
        title, body = XIANXIA_INFO_PAGES.get(topic, ("📖 Xianxia Guide", "No guide page is available."))
        # Every guide reply is private to the person who opened the dropdown -
        # this used to post publicly into the channel for every topic, which is
        # both noisy and, for the admin topic, exposed GM instructions to the
        # whole server.
        await interaction.response.send_message(f"**{title}**\n{body}", ephemeral=True)


class XianxiaInfoView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(XianxiaInfoSelect())


CHANNEL_MESSAGE_KEYS: tuple[str, ...] = (
    "world-events", "event-scenes", "player-homes", "bot-logs", "begin-here", "expeditions",
    "realm:Mortal World", "realm:Spiritual World", "realm:Immortal World", "realm:Celestial World",
)


CHANNEL_MESSAGE_LABELS: dict[str, str] = {
    "world-events": "#world-events",
    "event-scenes": "#event-scenes",
    "player-homes": "#player-homes",
    "bot-logs": "#bot-logs",
    "begin-here": "#begin-here",
    "expeditions": "#expeditions",
    "realm:Mortal World": "Azure Crown Imperial City (Mortal World)",
    "realm:Spiritual World": "Spirit Jade Capital (Spiritual World)",
    "realm:Immortal World": "Nine-Heavens Immortal Court (Immortal World)",
    "realm:Celestial World": "Celestial Mandate Palace (Celestial World)",
}


# GM-authored default text for every channel-message slot. These ship as sensible
# defaults so the feature is useful the moment it's enabled; GMs can rewrite any of
# them from the dashboard's Discord Setup tab and the bot will edit the same
# message in place rather than posting a duplicate (mirrors ensure_xianxia_info_guide).
DEFAULT_CHANNEL_MESSAGES: dict[str, str] = {
    "world-events": (
        "🌍 **World Events**\n"
        "Great happenings ripple out from here — dynastic wars, sect conflicts, tribulations that "
        "split the sky, and the rise and fall of powers across every realm. The Xianxia bot posts "
        "world-shaking news in this channel automatically; feel free to react and discuss what you "
        "read, but roleplay itself belongs in your own scenes and threads, not here."
    ),
    "event-scenes": (
        "🎭 **Event Scenes**\n"
        "When the world calls for a shared, public scene — a market day, a tournament, a sect "
        "gathering, a battle at the gates — the Xianxia bot opens a thread for it right here. Jump "
        "into any open thread to roleplay the event live alongside other cultivators. Threads "
        "archive automatically once their scene concludes."
    ),
    "player-homes": (
        "🏡 **Player Homes**\n"
        "This channel is a **read-only anchor** for every player-owned and sect-owned location in "
        "the world — cave abodes, manors, sect grounds and more. It doesn't carry roleplay itself; "
        "instead, each property gets its own private thread the moment it's built, visible only to "
        "its owner and invited guests. Look for your thread once you've claimed or built a home."
    ),
    "bot-logs": (
        "🛠️ **Bot Logs**\n"
        "This is the Xianxia bot's private operations channel — administrator actions, errors, and "
        "behind-the-scenes diagnostics land here. It's for staff only and has no bearing on the "
        "story; check it if something in the game seems to be misbehaving."
    ),
    "begin-here": (
        "🌱 **Begin Here**\n"
        "Every journey starts with a single step onto the cultivation path. Run `/begin` right in "
        "this channel to create your character — choose your background, awaken your spiritual "
        "root, and step into the world for the first time. Once you're in, check `#xianxia-info` "
        "for a full guide, or dive straight into your starting scene."
    ),
    "expeditions": (
        "🧭 **Expeditions**\n"
        "This channel is a **read-only anchor** for private expedition journals. Wilderness "
        "exploration, foraging, secret realms and wandering encounters all happen inside your own "
        "personal expedition thread, not in this channel directly — the bot creates one for you "
        "automatically the first time you venture out. Look for your thread here once you've set off."
    ),
    "realm:Mortal World": (
        "🏯 **Azure Crown Imperial City**\n"
        "The great meeting city of the Mortal World — where markets bustle, sect envoys trade "
        "favors, clans posture for standing, and duels of reputation are settled in public view. "
        "This channel is shared, open roleplay: any cultivator who has reached the Mortal World may "
        "walk these streets and speak freely. Come here to trade, scheme, forge alliances, or "
        "simply be seen."
    ),
    "realm:Spiritual World": (
        "💎 **Spirit Jade Capital**\n"
        "The ascended meeting city of the Spiritual World — home to storied sects, ancient "
        "bloodline families, and markets where spirit stones change hands by the sackful. This "
        "channel is shared, open roleplay for any cultivator who has broken through into the "
        "Spiritual World. Trade rare materials, court political favor, or cross paths with rivals "
        "from every corner of the realm."
    ),
    "realm:Immortal World": (
        "⛩️ **Nine-Heavens Immortal Court**\n"
        "The immortal meeting court of the Immortal World — where law-bound clans debate the "
        "boundaries of formation and edict, and politics carries the weight of centuries. This "
        "channel is shared, open roleplay for cultivators who have ascended into the Immortal "
        "World. Petition the court, trade in immortal-grade resources, or navigate the currents of "
        "power that shape the higher realms."
    ),
    "realm:Celestial World": (
        "👑 **Celestial Mandate Palace**\n"
        "The sovereign palace of the Celestial World — where heavenly factions hold court, mandates "
        "are issued and contested, and only the mightiest cultivators in existence are received. "
        "This channel is shared, open roleplay for those who have reached the Celestial World. Here, "
        "every word and every alliance can shift the balance of the world itself."
    ),
}


CHANNEL_MESSAGE_DEFAULT = "default"


CHANNEL_MESSAGE_CUSTOM = "custom"


CHANNEL_MESSAGE_DISABLED = "disabled"


def channel_message_state(stored: Mapping[str, Any], channel_key: str) -> str:
    """Classify a channel-message slot into its three genuinely distinct states.

    There are three, not two, and conflating the first and third is a real bug:

      no stored row       -> "default"  : nothing configured, post the built-in text
      stored row, text    -> "custom"   : the GM wrote their own message
      stored row, ""      -> "disabled" : the GM cleared it on purpose, post nothing

    The obvious `row.get("content") or DEFAULT_CHANNEL_MESSAGES[key]` collapses
    "disabled" into "default", because an intentionally empty string is falsy.
    That made clearing a message look like it worked - the Discord message was
    deleted and "" was stored - and then silently undid itself: the dashboard
    redisplayed the default, and the next Full Setup/Repair reposted it.
    """
    row = stored.get(channel_key)
    if not isinstance(row, Mapping) or "content" not in row or row.get("content") is None:
        return CHANNEL_MESSAGE_DEFAULT
    return CHANNEL_MESSAGE_CUSTOM if str(row["content"]).strip() else CHANNEL_MESSAGE_DISABLED


def resolve_channel_message_content(stored: Mapping[str, Any], channel_key: str) -> str:
    """Effective text for a slot: the default, the GM's text, or "" when disabled."""
    state = channel_message_state(stored, channel_key)
    if state == CHANNEL_MESSAGE_DEFAULT:
        return DEFAULT_CHANNEL_MESSAGES.get(channel_key, "")
    if state == CHANNEL_MESSAGE_DISABLED:
        return ""
    return str((stored.get(channel_key) or {}).get("content") or "")


async def _resolve_channel_message_target(guild: discord.Guild, channel_key: str) -> discord.TextChannel | None:
    if channel_key.startswith("realm:"):
        world = channel_key.split(":", 1)[1]
        rows = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
        row = rows.get(world)
        if not row:
            return None
        return await _resolve_text_channel(guild, row.get("channel_id"))
    cfg = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, _base_channel_bindings(cfg).get(channel_key))


async def ensure_channel_message(guild: discord.Guild, channel_key: str, content: str) -> discord.Message | None:
    """Keep one bot-managed GM-authored message per channel slot, edited in place
    instead of duplicated on every save (same pattern as ensure_xianxia_info_guide).
    An empty/whitespace-only content deletes any existing message for that slot.
    """
    text = content.strip()
    stored = await DB.get_channel_messages(guild.id)
    message_id = (stored.get(channel_key) or {}).get("message_id")
    channel = await _resolve_channel_message_target(guild, channel_key)
    if channel is None:
        # Persist the GM's edit even with nothing to post it to. Returning early
        # here used to throw the save away, so clearing a message while its
        # channel was unbound left the old custom text stored and the message
        # came back on the next Full Setup/Repair. The caller still sees None and
        # reports the slot as not-yet-posted; ensure_all_channel_messages applies
        # it once the channel exists.
        await DB.set_channel_message(guild.id, channel_key, content=text, message_id=message_id)
        return None
    message: discord.Message | None = None
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            message = None
    try:
        if not text:
            if message is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    pass
            await DB.set_channel_message(guild.id, channel_key, content="", message_id=None)
            return None
        if message is None:
            message = await channel.send(text)
        elif message.content != text:
            await message.edit(content=text)
        await DB.set_channel_message(guild.id, channel_key, content=text, message_id=message.id)
        return message
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create/update channel message for %s", channel_key)
        return None


async def ensure_all_channel_messages(guild: discord.Guild) -> dict[str, int | None]:
    """Apply every channel-message slot's current (custom, default or disabled) content.
    Called from Full Setup/Repair so newly-bound channels immediately get their
    GM-authored message without a separate dashboard click. A slot the GM cleared
    stays cleared: Repair must not resurrect a message that was deliberately removed.
    """
    stored = await DB.get_channel_messages(guild.id)
    results: dict[str, int | None] = {}
    for key in CHANNEL_MESSAGE_KEYS:
        # Deliberately three-state: a slot the GM cleared resolves to "" and is
        # left alone here instead of being repopulated with the default.
        content = resolve_channel_message_content(stored, key)
        message = await ensure_channel_message(guild, key, content)
        results[key] = message.id if message else None
    return results


async def ensure_xianxia_info_guide(guild: discord.Guild, channel: discord.TextChannel) -> discord.Message | None:
    """Keep one bot-managed read-only guide message instead of duplicating it on every repair."""
    cfg = await DB.get_server_config(guild.id)
    message_id = cfg.get("info_message_id")
    message: discord.Message | None = None
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            message = None
    try:
        if message is None:
            message = await channel.send(_xianxia_info_guide_text(), view=XianxiaInfoView())
            await DB.set_info_message_id(guild.id, message.id)
        elif message.content != _xianxia_info_guide_text():
            await message.edit(content=_xianxia_info_guide_text(), view=XianxiaInfoView())
        return message
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create/update Xianxia info guide")
        return None


async def ensure_base_xianxia_channels(
    guild: discord.Guild, *, category_name: str = "📜 Xianxia RP", create_missing: bool = False,
) -> dict[str, Any]:
    """Validate, bind and (when create_missing) create the base Xianxia channels.

    create_missing defaults to False so the /admin Discord slash-command path stays
    validate-only - "Discord channel/category provisioning is admin-dashboard owned"
    means only the web GM dashboard's Full Setup/Repair actions pass True here.
    Whatever this resolves for a channel - pre-existing, name-matched, or freshly
    created - has its binding persisted immediately (mirroring
    ensure_realm_hub_channels), so the dashboard's own status readout reflects it
    without a separate manual "Save Channel Bindings" click.
    """
    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    category = next((item for item in guild.categories if item.name == category_name), None)
    channels: dict[str, discord.TextChannel] = {}
    created: list[str] = []
    warnings: list[str] = []
    me = guild.me
    can_create = create_missing and bool(me) and me.guild_permissions.manage_channels

    if can_create and category is None:
        try:
            category = await guild.create_category(category_name, reason="Xianxia RP base channel setup")
        except discord.HTTPException:
            log.exception("Could not create base category %s", category_name)

    for name in BASE_CHANNEL_SPECS:
        configured = await _resolve_text_channel(guild, bindings.get(name))
        channel = configured or next((item for item in guild.text_channels if item.name == name), None)
        if channel is None and can_create:
            try:
                overwrites = (
                    {guild.default_role: discord.PermissionOverwrite(send_messages=False)}
                    if name in READ_ONLY_BASE_CHANNELS else {}
                )
                channel = await guild.create_text_channel(
                    name, category=category, topic=BASE_CHANNEL_SPECS[name][:1024],
                    overwrites=overwrites, reason="Xianxia RP base channel setup",
                )
                created.append(name)
            except discord.HTTPException:
                log.exception("Could not create base channel #%s", name)
                warnings.append(
                    f"Could not create **#{name}** — check the bot's Manage Channels permission and any Discord channel limits."
                )
                continue
        if channel is None:
            warnings.append(
                f"Missing **#{name}**. Create/bind it from the admin dashboard; the bot will not provision channels."
            )
            continue
        channels[name] = channel

    if channels.get("world-events") and channels.get("event-scenes"):
        def _bound_id(key: str) -> int | None:
            resolved = channels.get(key)
            return resolved.id if resolved is not None else bindings.get(key)

        await DB.set_server_channels(
            guild.id,
            announcement_channel_id=channels["world-events"].id,
            event_scene_channel_id=channels["event-scenes"].id,
            home_scene_channel_id=_bound_id("player-homes"),
            log_channel_id=_bound_id("bot-logs"),
            begin_channel_id=_bound_id("begin-here"),
            info_channel_id=_bound_id("xianxia-info"),
            exploration_channel_id=_bound_id("expeditions"),
        )
    elif channels or bindings.get("world-events") or bindings.get("event-scenes"):
        warnings.append(
            "Could not save channel bindings: both **#world-events** and **#event-scenes** must exist first."
        )

    info_channel = channels.get("xianxia-info")
    if info_channel is not None:
        await ensure_xianxia_info_guide(guild, info_channel)

    return {
        "category": category,
        "channels": channels,
        "created": created,
        "repaired": [],
        "warnings": warnings,
        "dashboard_owned": True,
    }


# Channels safe to fully wipe (delete + recreate empty) for a "fresh look". Deliberately
# excludes "player-homes", "expeditions" and "event-scenes": those three anchor players'
# LIVE private/property/event threads (cave abodes, sect abodes, expedition journals,
# world-event scenes - see Database.all_managed_thread_ids), and deleting the anchor
# channel deletes every thread under it too, orphaning the database rows that still point
# at those thread ids. A cosmetic message wipe is not worth destroying that game state, so
# those three are left untouched here even though they're base Xianxia channels.
CHANNEL_WIPE_KEYS: tuple[str, ...] = ("world-events", "bot-logs", "begin-here", "xianxia-info")



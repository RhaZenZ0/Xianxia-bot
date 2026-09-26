"""Managed guild channels and the messages the bot keeps in them: the base
channel specs and bindings, the #xianxia-info guide, and the per-channel
message state machine (default / custom / disabled).

Phase 6 of the main.py split (v0.19.41, docs/history/MAIN_SPLIT_PLAN.md). Reads
channels, runtime, app.version and discord; never main.py, never the other
admin modules. Definition order is the order these had in main.py.

clear_managed_channel_messages is NOT here although it sits with this code
in main.py: it re-runs the complete server setup, which lives in
server_setup.py, which imports this module - so it moved with the setup
code instead. That is the one cycle in this region.
"""
from __future__ import annotations

from typing import Any, Mapping, NamedTuple

import discord
from discord import app_commands

from ...version import RELEASE_VERSION
from ..channels import _resolve_text_channel, merge_overwrite
from ..runtime import DB, log

BASE_CHANNEL_SETUP_CHOICES = [
    app_commands.Choice(name="Validate / bind existing base Xianxia channels", value="bind"),
    app_commands.Choice(name="Show base-channel status", value="status"),
]


# The buckets a base channel can sit in. `server_setup.py` owns the category
# *names* and hands them in; these are only the keys both sides agree on.
CATEGORY_START = "start"
CATEGORY_ANNOUNCE = "announce"
CATEGORY_WORLD = "world"
CATEGORY_FEEDBACK = "feedback"
CATEGORY_ADMIN = "admin"


class BaseChannel(NamedTuple):
    """What a base channel is for, and which category it belongs in
    (v1.0.0-rc.59).

    The category used to be one argument to `ensure_base_xianxia_channels`
    and every base channel shared it. It is per channel now, and it lives
    *here*, beside the topic, rather than in a parallel dict - two statements
    of where a channel belongs are two statements free to disagree.

    `category` is a bucket key, not the category's name. The names are
    `SERVER_*CATEGORY` in `server_setup.py`, which imports this module and so
    cannot be imported back (the cycle this file's own docstring describes);
    the caller passes the mapping in. The string is therefore stated once,
    where teardown can also see it.
    """

    topic: str
    category: str


BASE_CHANNEL_SPECS: dict[str, BaseChannel] = {
    "begin-here": BaseChannel(
        "New cultivators begin here with /begin before entering the wider cultivation world.",
        CATEGORY_START),
    "xianxia-info": BaseChannel(
        "Read-only game guide, onboarding and system information maintained by the Xianxia bot.",
        CATEGORY_START),
    "world-events": BaseChannel(
        "Global cultivation-world announcements that belong to no single world. Each world's own news is in 🌠 World Events.",
        CATEGORY_ANNOUNCE),
    "updates": BaseChannel(
        "Read-only release notes. The bot posts each release's changelog here as it arrives; the full history is in VERSIONS.md.",
        CATEGORY_ANNOUNCE),
    "player-homes": BaseChannel(
        "Read-only anchor for persistent private player-owned location and sect-residence threads.",
        CATEGORY_WORLD),
    "expeditions": BaseChannel(
        "Read-only anchor for private player expedition threads; normal roleplay happens inside the private threads, not this channel.",
        CATEGORY_WORLD),
    "playtest": BaseChannel(
        "The playtest board: one post per hub page. React ✅ if it works, ❌ if it fails, 💡 if you want it changed - and say what in a reply.",
        CATEGORY_FEEDBACK),
    "bot-logs": BaseChannel(
        "Private operational and administrator-action logs for the Xianxia bot.",
        CATEGORY_ADMIN),
}


# `#event-scenes` was the ninth and is retired (v1.0.0-rc.59): an event's
# roleplay thread anchors in its own world's feed now, which is where rc.52
# already sent the announcement. It is absent from the specs above, so nothing
# creates one or requires one - and deliberately still present in
# `_base_channel_bindings` below, because teardown builds its targets from that
# dict and a server that already has the channel must still be able to lose it.
RETIRED_BASE_CHANNELS = {"event-scenes"}


READ_ONLY_BASE_CHANNELS = {"xianxia-info", "expeditions", "player-homes", "updates"}


def _base_channel_bindings(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "world-events": cfg.get("announcement_channel_id"),
        "event-scenes": cfg.get("event_scene_channel_id"),
        "player-homes": cfg.get("home_scene_channel_id"),
        "bot-logs": cfg.get("log_channel_id"),
        "begin-here": cfg.get("begin_channel_id"),
        "xianxia-info": cfg.get("info_channel_id"),
        "expeditions": cfg.get("exploration_channel_id"),
        "playtest": cfg.get("playtest_channel_id"),
        "updates": cfg.get("updates_channel_id"),
    }


def _xianxia_info_guide_text() -> str:
    return (
        f"📖 **Xianxia Realm Guide — v{RELEASE_VERSION}**\n"
        "This channel is **read-only** and is **not** a game location. Pick a topic below for detail.\n\n"
        "🌱 **Start** — `/begin` in `#begin-here`; your household gives you your first quest\n"
        "🧭 **Everything** — `/menu` opens every hub from anywhere\n"
        "🗺️ **Where you are** — `/world` for the place, the roads and what is happening\n"
        "🧑 **Who you are** — `/character` for the sheet, afflictions, standing and soul\n"
        "📜 **Quests** — `/quests`; most are handed to you by the people who give them\n"
              "🌠 **News** — one events channel per world, under **World Events**; events open their scenes there too\n"
        "🔒 Unknown places, higher worlds and the NPCs in them stay hidden until you reach them."
    )


XIANXIA_INFO_PAGES: dict[str, tuple[str, str]] = {
    "getting_started": ("🌱 Getting Started", "Run `/begin` in `#begin-here`. You are born into one of thirteen households: it teaches you a trade, hands you an heirloom, and gives you the first quest of a chain that walks you out of the door, through the town and the road, and home again. Follow it. `/menu` opens every hub from anywhere, and `/cooldowns` says what is ready and where each ready thing is done."),
    "server_layout": ("🗺️ The Server", "Nine categories, in the order you read them. **🚪 Start Here** is `#begin-here` and this guide. **📣 Announcements** is `#world-events` for anything global and `#updates` for what changed in the last release. **🌌 Realm Capitals** holds one meeting city per world. **🌠 World Events** holds one news channel per world, and an event's scene and its thread now open in the same place. **🏮 Auction Houses** holds the live lot feeds. **🧺 Market Stalls** holds one channel per world with a live card for every cultivator's stall there. **🗺️ Cultivation World** holds the read-only anchors your private threads hang from. **🛠️ Feedback** is `#playtest` and `#bugs`. **🔒 Admin** is the operator's. A capital is visible only while you are standing in it; a world's news, its scenes, its auction floor and its market stalls are visible once you have reached that world at all."),
    "character": ("🧬 Character & Cultivation", "Your household, spiritual root, physique, path, realm, resources, karma, fate and Dao heart are canonical game state. Two ladders run in parallel — qi cultivation and body tempering — and Stage 9 of either opens the optional Perfection path. The AI narrates what has already happened; it cannot change a mechanic, grant a reward, or decide an outcome."),
    "exploration": ("🧭 Exploration & Scenes", "**Where you stand** and **what scene you are in** are separate. Wilderness travel, exploration, foraging and hunting happen in your own private expedition thread under `#expeditions`. Properties and sect abodes use persistent private threads under `#player-homes`. A realm capital is a shared channel you can only see while you are in the city."),
    "world_events": ("🌠 World Events", "The world produces events on its own, and players trigger them by exploring. Each is announced in **its own world's** news channel with a link to its scene thread, and each carries a **site**: a finite number of beasts, herbs, veins, relics and tasks that deplete as people work them. Travel to the place the notice names to take part. What you are *handed* is banded by realm — a new cultivator is not offered a Dragon — but anything the world spawns on its own, you can walk into."),
    "sects": ("🏯 Sects", "Discover a route, speak with affiliated NPCs, earn a recommendation, pass a sect-specific trial, and join only on a canonical success. Membership brings contribution points, a rank, a private residence, and a side in whatever war the sect is in."),
    "professions": ("⚒️ Crafts & Professions", "Four trades: Forging, Alchemy, Inscription, Formation. Your household teaches you one, and the head of the house can qualify you in all four, once, at home. Recipes come from method slips sold in the halls (`/learn`) and from passing a hall keeper's **examination** at the rank you currently hold. Gather with `/alchemy forage` and on the hunt rather than buying everything."),
    "properties": ("🏡 Player-Owned Locations", "Properties are real database-backed locations with private threads, facilities and guest permissions. A guest must be invited **and** physically reach the entrance before they gain access. Your birth household is a place worth returning to: contribute to its treasury, be tutored again, run its errands, and cultivate at its hearth."),
    "relationships": ("🤝 NPC Relationships", "Persistent NPC state tracks trust, respect, fear, affection, debt, grudge and encounter history. The world's people live on their own — they travel, court, marry, have children, feud, commit crimes, go missing and die — whether or not anybody is watching. Narration may describe a relationship; it never owns the numbers."),
    "quests": ("📜 Quests", "Most quests are **handed to you**: by your household, by a hall keeper offering an examination, by a commission giver in a city, or by finishing the quest before. `/quests` shows what you hold, and what the Quest Forge has approved as open to anyone. Objectives advance off things you actually did — explore, talk, cultivate, travel, win a fight, craft, trade, gather, come home."),
    "admin": ("🧰 Server Administration", "`/admin → Server → Setup` runs Setup Server, Repair Server, Check Permissions, Show Configuration, Sync Realm Roles and Rebuild Info Guide. Repair also creates any missing category or channel and moves existing ones into the category they belong in. Channel layout, the GM dashboard and every audited lever live in the web console."),
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
    # v1.0.0-rc.52: the per-world events channels, resolved through
    # `world_event_channels` the way `realm:` keys resolve through
    # `realm_hub_channels`. A prefixed key needed no new mechanism.
    "world-events:Mortal World", "world-events:Spiritual World",
    "world-events:Immortal World", "world-events:Celestial World",
    # v1.7.0: the per-world market-stalls channels, resolved through
    # `stall_channels` the same way.
    "stalls:Mortal World", "stalls:Spiritual World", "stalls:Immortal World", "stalls:Celestial World",
    # v1.7.0: `#updates` had a default blurb since rc.59 and no slot here, so
    # nothing ever posted it - every other reader of the default walks this
    # tuple. A default no slot names is text nobody sees.
    "updates",
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
    "world-events:Mortal World": "#mortal-world-events",
    "world-events:Spiritual World": "#spiritual-world-events",
    "world-events:Immortal World": "#immortal-world-events",
    "world-events:Celestial World": "#celestial-world-events",
    "stalls:Mortal World": "#mortal-world-stalls",
    "stalls:Spiritual World": "#spiritual-world-stalls",
    "stalls:Immortal World": "#immortal-world-stalls",
    "stalls:Celestial World": "#celestial-world-stalls",
    "updates": "#updates",
}


# GM-authored default text for every channel-message slot. These ship as sensible
# defaults so the feature is useful the moment it's enabled; GMs can rewrite any of
# them from the dashboard's Discord Setup tab and the bot will edit the same
# message in place rather than posting a duplicate (mirrors ensure_xianxia_info_guide).
DEFAULT_CHANNEL_MESSAGES: dict[str, str] = {
    "world-events": (
        "\U0001f30d **World Events — the global feed**\n"
        "Anything that touches every world at once lands here: the doubled-gift weekends, "
        "server-wide notices, and a world reset when one comes. **News about one world goes to "
        "that world's own channel** under **\U0001f320 World Events** — `#mortal-world-events`, "
        "`#spiritual-world-events`, `#immortal-world-events`, `#celestial-world-events` — and you "
        "see each of those once you have reached that world.\n"
        "React and discuss freely; roleplay itself belongs in your scenes and threads, not here."
    ),
    "updates": (
        "\U0001f4e3 **Updates \u2014 what changed**\n"
        "The bot posts each release's notes here the first time it boots on that release, "
        "straight out of `VERSIONS.md`. It never posts the same release twice, and a server set "
        "up today starts from the next one rather than the whole history.\n"
        "Read-only. Questions and bug reports belong in \U0001f6e0\ufe0f **Feedback**."
    ),
    "event-scenes": (
        "\U0001f3ad **Event Scenes \u2014 retired**\n"
        "An event's scene and its roleplay thread now open in **that world's own channel** under "
        "\U0001f320 **World Events**, where its announcement already went \u2014 one place per "
        "event instead of two. Nothing opens here any more.\n"
        "The channel is kept because this server already had it: Teardown removes it, and a new "
        "server is never given one."
    ),
    "player-homes": (
        "\U0001f3e1 **Player Homes**\n"
        "A **read-only anchor** for every player-owned and sect-owned place in the world — cave "
        "abodes, manors, homesteads and sect residences. No roleplay happens in the channel "
        "itself: each property gets its own private thread the moment it is built, visible to its "
        "owner and to guests who have been invited *and* have physically reached the entrance. "
        "Look for your thread once you have claimed or built a home."
    ),
    "bot-logs": (
        "\U0001f6e0\ufe0f **Bot Logs**\n"
        "The bot's private operations channel — administrator actions, failures and diagnostics. "
        "Staff only, and it has no bearing on the story. Every GM action that changes the world is "
        "also written to the audit log the dashboard reads, so this channel is the quick view, not "
        "the record."
    ),
    "begin-here": (
        "\U0001f331 **Begin Here**\n"
        "Every journey starts with one step onto the cultivation path. Run `/begin` in this "
        "channel to create your character — your birth household, your spiritual root, and the "
        "trade your family keeps. You will be given your first quest by the people who raised you; "
        "follow it. `/menu` opens every hub from anywhere, and `#xianxia-info` has the full guide."
    ),
    "expeditions": (
        "\U0001f9ed **Expeditions**\n"
        "A **read-only anchor** for private expedition journals. Wilderness travel, exploration, "
        "foraging, hunting, secret realms and wandering encounters all happen inside your own "
        "thread, opened automatically the first time you venture out — so one cultivator's journey "
        "never buries another's. Look for your thread here once you have set off."
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
    "world-events:Mortal World": (
        "\U0001f30f **Mortal World — news**\n"
        "What the Mortal World does to itself, as it happens: disasters and invasions, sect and "
        "clan wars, disappearances, discoveries, and the rise and fall of its powers. Each notice "
        "links to the scene thread where you can take part — **travel to the place it names** and "
        "you can work the site yourself. A scene holds a finite number of beasts, herbs, veins and "
        "tasks, so getting there first is worth something.\n"
        "You see this channel because you have reached the Mortal World. Discussion is welcome; "
        "roleplay belongs in the scene thread."
    ),
    "world-events:Spiritual World": (
        "\U0001f48e **Spiritual World — news**\n"
        "Upheavals among the ascended sects and the ancient bloodline families, and every event "
        "the Spiritual World opens up. Each notice links to its scene thread; travel to the place "
        "it names to take part. Events here run at the severity this world carries — heavier than "
        "the Mortal World's, and worth more to whoever reaches them.\n"
        "You see this channel because you have broken through into the Spiritual World."
    ),
    "world-events:Immortal World": (
        "\u26e9\ufe0f **Immortal World — news**\n"
        "Immortal clans, law formations, and the events that shake the Nine-Heavens court. Each "
        "notice links to its scene thread; travel to the place it names to take part. What opens "
        "up here is immortal-grade, and so is what it costs to survive.\n"
        "You see this channel because you have ascended into the Immortal World."
    ),
    "world-events:Celestial World": (
        "\U0001f451 **Celestial World — news**\n"
        "Sovereign courts, heavenly factions, and the events that move them. Each notice links to "
        "its scene thread; travel to the place it names to take part. At this height an event can "
        "shift the balance of the world itself.\n"
        "You see this channel because you have reached the Celestial World."
    ),
    "stalls:Mortal World": (
        "\U0001f9fa **Mortal World — market stalls**\n"
        "One card for every stall a cultivator keeps in a city of the Mortal World, kept current by the "
        "bot: what is laid on it, at what price, and who keeps it. Buy from anywhere with "
        "**/economy → Market Stalls → Buy** \u2014 goods from a stall farther off cost a "
        "little more a road, and a High-grade pill or finer is sold here and on the auction floor, "
        "never at a shop. Keep your own with **Market Stalls → Open** once you reach "
        "Foundation Establishment.\n"
        "Read-only: the cards are the channel."
    ),
    "stalls:Spiritual World": (
        "\U0001f9fa **Spiritual World — market stalls**\n"
        "One card for every stall a cultivator keeps in a city of the Spiritual World, kept current by the "
        "bot: what is laid on it, at what price, and who keeps it. Buy from anywhere with "
        "**/economy → Market Stalls → Buy** \u2014 goods from a stall farther off cost a "
        "little more a road, and a High-grade pill or finer is sold here and on the auction floor, "
        "never at a shop. Keep your own with **Market Stalls → Open** once you reach "
        "Foundation Establishment.\n"
        "Read-only: the cards are the channel."
    ),
    "stalls:Immortal World": (
        "\U0001f9fa **Immortal World — market stalls**\n"
        "One card for every stall a cultivator keeps in a city of the Immortal World, kept current by the "
        "bot: what is laid on it, at what price, and who keeps it. Buy from anywhere with "
        "**/economy → Market Stalls → Buy** \u2014 goods from a stall farther off cost a "
        "little more a road, and a High-grade pill or finer is sold here and on the auction floor, "
        "never at a shop. Keep your own with **Market Stalls → Open** once you reach "
        "Foundation Establishment.\n"
        "Read-only: the cards are the channel."
    ),
    "stalls:Celestial World": (
        "\U0001f9fa **Celestial World — market stalls**\n"
        "One card for every stall a cultivator keeps in a city of the Celestial World, kept current by the "
        "bot: what is laid on it, at what price, and who keeps it. Buy from anywhere with "
        "**/economy → Market Stalls → Buy** \u2014 goods from a stall farther off cost a "
        "little more a road, and a High-grade pill or finer is sold here and on the auction floor, "
        "never at a shop. Keep your own with **Market Stalls → Open** once you reach "
        "Foundation Establishment.\n"
        "Read-only: the cards are the channel."
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
    if channel_key.startswith("world-events:"):
        world = channel_key.split(":", 1)[1]
        rows = {str(row["world_name"]): row for row in await DB.get_world_event_channels(guild.id)}
        row = rows.get(world)
        if not row:
            return None
        return await _resolve_text_channel(guild, row.get("channel_id"))
    if channel_key.startswith("stalls:"):
        world = channel_key.split(":", 1)[1]
        rows = {str(row["world_name"]): row for row in await DB.get_stall_channels(guild.id)}
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
    guild: discord.Guild, *, categories: Mapping[str, str], create_missing: bool = False,
) -> dict[str, Any]:
    """Validate, bind and (when create_missing) create the base Xianxia channels.

    create_missing defaults to False so the /admin Discord slash-command path stays
    validate-only - "Discord channel/category provisioning is admin-dashboard owned"
    means only the web GM dashboard's Full Setup/Repair actions pass True here.
    Whatever this resolves for a channel - pre-existing, name-matched, or freshly
    created - has its binding persisted immediately (mirroring
    ensure_realm_hub_channels), so the dashboard's own status readout reflects it
    without a separate manual "Save Channel Bindings" click.

    **It repairs an existing channel now (v1.0.0-rc.59), and that is the whole
    finding.** Until this release the category and the read-only overwrite were
    computed only inside `if channel is None and can_create:` - so a channel
    that already existed, whether pre-existing, name-matched or bound by a GM,
    got neither, ever. `#xianxia-info`, `#expeditions` and `#player-homes` were
    read-only only on a server where the bot had created them; the guide text a
    few hundred lines above claimed Repair "moves existing ones into the
    category they belong in", which was true of auction channels (rc.51) and
    world feeds (rc.52) and false here; and `repaired` was returned as a
    hardcoded empty list, so the audit row and the slash reply had shown an
    empty field since the function was written. A layout change written that
    way reaches a fresh guild and no server anybody is running.

    `categories` maps a `BaseChannel.category` bucket to the category's name.
    It is passed in rather than read here because the names are
    `SERVER_*CATEGORY` in `server_setup.py`, which imports this module.
    """
    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    channels: dict[str, discord.TextChannel] = {}
    created: list[str] = []
    repaired: list[str] = []
    warnings: list[str] = []
    me = guild.me
    can_create = create_missing and bool(me) and me.guild_permissions.manage_channels
    resolved_categories: dict[str, discord.CategoryChannel | None] = {}

    async def _category(bucket: str) -> discord.CategoryChannel | None:
        if bucket in resolved_categories:
            return resolved_categories[bucket]
        wanted = categories.get(bucket)
        found = next((item for item in guild.categories if item.name == wanted), None) if wanted else None
        if found is None and wanted and can_create:
            try:
                found = await guild.create_category(wanted, reason="Xianxia RP base channel setup")
            except discord.HTTPException:
                log.exception("Could not create base category %s", wanted)
        resolved_categories[bucket] = found
        return found

    for name, spec in BASE_CHANNEL_SPECS.items():
        category = await _category(spec.category)
        configured = await _resolve_text_channel(guild, bindings.get(name))
        channel = configured or next((item for item in guild.text_channels if item.name == name), None)
        if channel is None and can_create:
            try:
                # The bot allows itself before it denies anybody (rc.52): an
                # `@everyone` deny binds a bot that is not Administrator, and
                # `#updates` is where it posts the release notes (v1.7.1).
                overwrites: dict[Any, discord.PermissionOverwrite] = {}
                if name in READ_ONLY_BASE_CHANNELS:
                    if guild.me is not None:
                        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
                    overwrites[guild.default_role] = discord.PermissionOverwrite(send_messages=False)
                channel = await guild.create_text_channel(
                    name, category=category, topic=spec.topic[:1024],
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
        if name in created or not can_create:
            continue
        # rc.51's shape, applied to the family that never had it. No `moved`
        # set is needed: one channel per key here, where forty-eight auction
        # houses share nine floors.
        if category is not None and channel.category_id != category.id:
            try:
                await channel.edit(category=category, reason="Xianxia RP base channel setup")
                repaired.append(name)
            except discord.HTTPException:
                log.exception("Could not move #%s into %s", name, category.name)
        if name in READ_ONLY_BASE_CHANNELS:
            # Merged, never replaced (v1.0.11): `set_permissions` with bare
            # kwargs writes a fresh overwrite, which on `#expeditions` and
            # `#player-homes` would drop the cultivator gate's
            # `view_channel=False`. And the bot allows itself first (rc.52),
            # or a bot without Administrator cannot post in its own channel.
            try:
                changed = False
                if guild.me is not None:
                    changed = await merge_overwrite(channel, guild.me, reason="Xianxia RP base channel setup",
                                                    view_channel=True, send_messages=True)
                changed = await merge_overwrite(channel, guild.default_role, reason="Xianxia RP base channel setup",
                                                send_messages=False) or changed
                if changed and name not in repaired:
                    repaired.append(name)
            except discord.HTTPException:
                log.exception("Could not re-lock #%s", name)

    # `#world-events` alone (v1.0.0-rc.59). This used to want `#event-scenes`
    # too, and persisted *nothing at all* without it - so retiring that channel
    # would have silently stopped every binding being saved.
    if channels.get("world-events"):
        def _bound_id(key: str) -> int | None:
            resolved = channels.get(key)
            return resolved.id if resolved is not None else bindings.get(key)

        await DB.set_server_channels(
            guild.id,
            announcement_channel_id=channels["world-events"].id,
            # Retired, and deliberately still written: a server that bound one
            # before rc.59 keeps the binding, so teardown can still delete the
            # channel and `_event_scene_parent` can still fall back to it.
            event_scene_channel_id=_bound_id("event-scenes"),
            home_scene_channel_id=_bound_id("player-homes"),
            log_channel_id=_bound_id("bot-logs"),
            begin_channel_id=_bound_id("begin-here"),
            info_channel_id=_bound_id("xianxia-info"),
            exploration_channel_id=_bound_id("expeditions"),
            playtest_channel_id=_bound_id("playtest"),
            updates_channel_id=_bound_id("updates"),
        )
    elif channels or bindings.get("world-events"):
        warnings.append(
            "Could not save channel bindings: **#world-events** must exist first."
        )

    info_channel = channels.get("xianxia-info")
    if info_channel is not None:
        await ensure_xianxia_info_guide(guild, info_channel)

    return {
        "categories": {bucket: cat for bucket, cat in resolved_categories.items() if cat is not None},
        "channels": channels,
        "created": created,
        # Was a hardcoded `[]` until v1.0.0-rc.59, which is why the audit row
        # and the slash reply have always shown an empty Repaired field.
        "repaired": repaired,
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



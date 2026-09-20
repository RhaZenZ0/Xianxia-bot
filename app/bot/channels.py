"""Guild channels, roles and thread lookup: resolving configured channels,
realm-hub channels/roles, the server log.

Phase 4 of the main.py split (v0.19.39, docs/history/MAIN_SPLIT_PLAN.md).
_resolve_text_channel has 17 call sites and post_server_log is what the hubs
report failures through, so this sits below every command module and below
threads.py. Reads runtime, app.realm_hubs and discord; never main.py.
Definition order is the order these had in main.py.
"""
from __future__ import annotations

from typing import Any

import discord

from ..rules.realm_hubs import REALM_HUBS, REALM_HUB_MEMBER_PERMISSIONS, realm_hub_visibility, realm_presence_role_name
from .runtime import DB, SETTINGS, WORLD, _realm_access_role_name, chunk_text, log

def _event_archive_minutes() -> int:
    allowed = {60, 1440, 4320, 10080}
    value = SETTINGS.event_thread_auto_archive_minutes
    return value if value in allowed else 1440


async def _resolve_text_channel(guild: discord.Guild, channel_id: int | None) -> discord.TextChannel | None:
    if not channel_id:
        return None
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.NotFound:
            # A configured channel may have been deleted or belong to an older
            # server layout. Treat that as a stale binding rather than an
            # application error; the setup/repair commands can replace it.
            log.warning("Configured text channel %s no longer exists; treating binding as stale", channel_id)
            return None
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not resolve text channel %s", channel_id)
            return None
    return channel if isinstance(channel, discord.TextChannel) else None


async def _ensure_realm_presence_roles(guild: discord.Guild) -> dict[str, discord.Role]:
    """One "Xianxia • <capital>" role per realm hub (v0.21.6): held only while
    a cultivator stands in that capital, and the only role the capital's
    channel allows. Created with no guild permissions and not mentionable."""
    roles: dict[str, discord.Role] = {}
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return roles
    for world_name in REALM_HUBS:
        role_name = realm_presence_role_name(world_name)
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            try:
                role = await guild.create_role(
                    name=role_name, mentionable=False, permissions=discord.Permissions.none(),
                    reason="Xianxia realm-capital presence (visible only while in the city)",
                )
            except discord.HTTPException:
                log.exception("Could not create realm presence role for %s", world_name)
                continue
        roles[world_name] = role
    return roles


async def _ensure_realm_access_roles(guild: discord.Guild) -> dict[str, discord.Role]:
    roles: dict[str, discord.Role] = {}
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return roles
    for world_name in REALM_HUBS:
        role_name = _realm_access_role_name(world_name)
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            try:
                role = await guild.create_role(
                    name=role_name, mentionable=False, reason="Xianxia realm-world visibility gate"
                )
            except discord.HTTPException:
                log.exception("Could not create realm access role for %s", world_name)
                continue
        roles[world_name] = role
    return roles


async def ensure_realm_hub_overwrites(
    guild: discord.Guild, channel: discord.TextChannel, role: discord.Role | None, *, stale_roles: list[discord.Role] | None = None,
) -> str:
    """Make one realm hub visible to the cultivators standing in it and to nobody else.

    ``role`` is the capital's presence role ("Xianxia • <capital>", v0.21.6).
    @everyone is denied View Channel; the presence role gets the full member
    set in REALM_HUB_MEMBER_PERMISSIONS; the bot keeps an explicit allow for
    itself; and any ``stale_roles`` (the realm-access roles that gated these
    channels in v0.21.2-v0.21.5, earned by cultivation rather than by being
    there) lose their overwrite, so an unlocked-but-absent cultivator no
    longer sees the room.

    Returns "hidden" when nothing needed changing, "gated" when this call
    changed it, "no-role" when the presence role does not exist yet (nothing
    is changed: denying @everyone without an allow would lock every player
    out), or "failed". Idempotent: an already-gated hub makes no API call.
    """
    if role is None:
        return "no-role"
    overwrites = channel.overwrites or {}
    state = realm_hub_visibility(channel, role, guild.default_role)
    me_view = getattr(overwrites.get(guild.me), "view_channel", None) if guild.me else True
    member_ow = overwrites.get(role)
    member_ok = state["role_view"] is True and all(getattr(member_ow, perm, None) is True for perm in REALM_HUB_MEMBER_PERMISSIONS)
    stale = [r for r in (stale_roles or []) if r in overwrites]
    if state["hidden"] and member_ok and me_view is True and not stale:
        return "hidden"
    try:
        # The bot allows *itself* first, and the order is the fix (v1.0.0-rc.52).
        # A channel overwrite applies to the bot like anyone else unless it is
        # Administrator, so denying @everyone View Channel first takes the bot's
        # own access to the channel away - and every set_permissions call after
        # it is refused 403 Missing Access. The channel is then left denied to
        # @everyone with no allow for the presence role: invisible to the very
        # players it exists for, while `realm_hub_visibility` reports it as
        # "VISIBLE TO ALL" because the role allow it looks for was never
        # written. Two wrong answers from one ordering.
        #
        # Nothing caught it because every server this ran on gave the bot
        # Administrator, which skips channel overwrites entirely. The Discord
        # harness found it the first time it was allowed to reach this path
        # (v1.0.0-rc.52 drives Full Setup over the control plane), because
        # SimCord deliberately grants the bot everything *except* administrator.
        if guild.me is not None and me_view is not True:
            await channel.set_permissions(guild.me, view_channel=True, send_messages=True, manage_messages=True, read_message_history=True, reason="Xianxia realm-capital visibility gate")
        if state["everyone_view"] is not False:
            await channel.set_permissions(guild.default_role, view_channel=False, reason="Xianxia realm-capital visibility gate")
        if not member_ok:
            await channel.set_permissions(role, reason="Xianxia realm-capital visibility gate", **REALM_HUB_MEMBER_PERMISSIONS)
        for old in stale:
            await channel.set_permissions(old, overwrite=None, reason="Xianxia: capitals are gated by presence, not by realm")
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not gate realm hub #%s behind %s", channel.name, role.name)
        return "failed"
    return "gated"


async def ensure_realm_hub_channels(
    guild: discord.Guild, *, category_name: str = "🌌 Realm Capitals", create_missing: bool = False,
) -> list[dict[str, Any]]:
    """Bind existing realm-capital channels and, when create_missing, create any
    that are missing. Discord layout is admin-dashboard owned - the /admin slash
    command leaves create_missing at its default False; only the web dashboard's
    Full Setup/Repair actions opt in.
    """
    existing = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
    category = next((item for item in guild.categories if item.name == category_name), None)
    me = guild.me
    can_create = create_missing and bool(me) and me.guild_permissions.manage_channels
    # The visibility gate is applied on the same dashboard-owned path that
    # provisions channels (Setup/Repair, create_missing=True); the /admin
    # slash path stays validate-only. Roles are created here too so a hub
    # can be gated the moment it exists rather than after a separate sync.
    access_roles = await _ensure_realm_access_roles(guild) if can_create else {}
    roles = await _ensure_realm_presence_roles(guild) if can_create else {}
    if can_create and category is None:
        try:
            category = await guild.create_category(category_name, reason="Xianxia RP realm-capital setup")
        except discord.HTTPException:
            log.exception("Could not create realm-capital category %s", category_name)

    for world, hub in REALM_HUBS.items():
        row = existing.get(world)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        if not isinstance(channel, discord.TextChannel):
            channel = next(
                (item for item in guild.text_channels if item.name == str(hub["channel_name"])),
                None,
            )
        if channel is None and can_create:
            try:
                channel = await guild.create_text_channel(
                    str(hub["channel_name"]), category=category, topic=str(hub.get("topic") or "")[:1024],
                    reason="Xianxia RP realm-capital setup",
                )
            except discord.HTTPException:
                log.exception("Could not create realm-capital channel #%s", hub["channel_name"])
        if channel is None:
            continue
        # The re-parent rc.51 gave the auction floors and rc.52 the world
        # feeds, arriving here in v1.0.0-rc.59: a capital that already existed
        # was bound where it lay and stayed there for ever, so the category
        # split could never have reached a server anybody was running.
        if can_create and category is not None and channel.category_id != category.id:
            try:
                await channel.edit(category=category, reason="Xianxia RP realm-capital setup")
            except discord.HTTPException:
                log.exception("Could not move #%s into %s", channel.name, category_name)
        if can_create:
            stale = [r for w, r in access_roles.items() if w == world]
            await ensure_realm_hub_overwrites(guild, channel, roles.get(world), stale_roles=stale)
        await DB.set_realm_hub_channel(
            guild_id=guild.id,
            world_name=world,
            location=str(hub["location"]),
            channel_id=channel.id,
            category_id=channel.category_id if channel.category_id is not None else (category.id if category else None),
        )
    return await DB.get_realm_hub_channels(guild.id)


def auction_house_channel_name(house_id: str, house: dict[str, Any]) -> str:
    """The channel a house's lots are posted in: content names it
    (`channel_name`), falling back to the house id."""
    return str(house.get("channel_name") or f"{house_id.replace('_', '-')}-auctions")[:100]


async def ensure_auction_house_channels(
    guild: discord.Guild, *, category_name: str = "🏮 Auction Houses", create_missing: bool = False,
) -> list[dict[str, Any]]:
    """Bind existing live-auction channels and, when create_missing, create any
    that are missing (v0.33.1). A grand house (a capital's) has a channel of
    its own; the local floors of a world share one, named in content - so a
    world of twelve cities is one channel, not twelve. Every house is bound to
    the channel its content names, visible to the cultivators who can reach
    that world - the same access role that gates it - and to nobody else. Like
    the capitals, the /admin slash path only binds; the dashboard's
    Setup/Repair is what creates.

    v1.0.0-rc.51: these nine channels used to be created beside the four realm
    capitals, in the category named for them. They have their own now - and a
    channel that already exists is *re-parented*, not merely rebound, because
    `category=` is only read on creation, so without that a deployed server
    would keep its auction channels under the capitals for ever and the change
    would reach a fresh guild only.
    """
    existing = {str(row["house_id"]): row for row in await DB.get_auction_house_channels(guild.id)}
    category = next((item for item in guild.categories if item.name == category_name), None)
    me = guild.me
    can_create = create_missing and bool(me) and me.guild_permissions.manage_channels
    access_roles = await _ensure_realm_access_roles(guild) if can_create else {}
    if can_create and category is None:
        try:
            category = await guild.create_category(category_name, reason="Xianxia RP auction-house setup")
        except discord.HTTPException:
            log.exception("Could not create category %s", category_name)

    # Forty-eight houses share nine channels, and `channel.category_id` is read
    # from the cache, which a gateway event updates after the edit returns - so
    # without this the move would be re-issued for every house sharing a floor.
    moved: set[int] = set()
    for house_id, house in WORLD.auction_houses.items():
        name = auction_house_channel_name(house_id, house)
        interior = WORLD.locations.get(str(house.get("location"))) or {}
        world = str(interior.get("world") or "Mortal World")
        row = existing.get(house_id)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        if not isinstance(channel, discord.TextChannel):
            channel = next((item for item in guild.text_channels if item.name == name), None)
        if channel is None and can_create:
            if str(house.get("size") or "grand") == "local":
                topic = f"Live lots from every local auction floor of the {world} — listed, bid on and struck as it happens. Bid with /economy → Auction House → Bid."
            else:
                topic = f"Live lots at {house.get('name', house_id)} — listed, bid on and struck as it happens. Bid with /economy → Auction House → Bid."
            try:
                channel = await guild.create_text_channel(
                    name, category=category, topic=topic[:1024], reason="Xianxia RP auction-house setup",
                )
            except discord.HTTPException:
                log.exception("Could not create auction channel #%s", name)
        if channel is None:
            continue
        if can_create and category is not None and channel.id not in moved and channel.category_id != category.id:
            moved.add(channel.id)
            try:
                await channel.edit(category=category, reason="Xianxia RP auction-house setup")
            except discord.HTTPException:
                log.exception("Could not move #%s into %s", channel.name, category_name)
        if can_create and access_roles.get(world) is not None:
            await ensure_realm_hub_overwrites(guild, channel, access_roles.get(world))
        await DB.set_auction_house_channel(
            guild_id=guild.id, house_id=house_id, location=str(house.get("location") or ""), channel_id=channel.id,
            category_id=channel.category_id if channel.category_id is not None else (category.id if category else None),
        )
    return await DB.get_auction_house_channels(guild.id)


async def ensure_world_event_channels(
    guild: discord.Guild, *, category_name: str = "\U0001f320 World Events", create_missing: bool = False,
) -> list[dict[str, Any]]:
    """One world-events channel per world (v1.0.0-rc.52), created beside the
    capitals and the auction floors and gated the same way the floors are.

    `world-events` carried all four worlds: a Demon Invasion in the Celestial
    World and a caravan over the bank in a Mortal village in one feed, in front
    of everybody, whatever they could reach. The base channel stays - it is the
    *global* feed now, and the fallback for anything with no world (see
    `world_event_channel` below) - and a located event goes to its own world's.

    Gated by the realm **access** role, not the presence role: a world's news is
    for everyone who has reached that world, not only whoever happens to be
    standing in its capital this minute. Like the capitals and the floors, the
    /admin slash path only binds; the dashboard's Setup/Repair is what creates,
    and a channel that already exists somewhere else is re-parented rather than
    merely rebound.
    """
    existing = {str(row["world_name"]): row for row in await DB.get_world_event_channels(guild.id)}
    category = next((item for item in guild.categories if item.name == category_name), None)
    me = guild.me
    can_create = create_missing and bool(me) and me.guild_permissions.manage_channels
    access_roles = await _ensure_realm_access_roles(guild) if can_create else {}
    if can_create and category is None:
        try:
            category = await guild.create_category(category_name, reason="Xianxia RP world-events setup")
        except discord.HTTPException:
            log.exception("Could not create category %s", category_name)

    for world, hub in REALM_HUBS.items():
        name = str(hub["events_channel_name"])
        row = existing.get(world)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        if not isinstance(channel, discord.TextChannel):
            channel = next((item for item in guild.text_channels if item.name == name), None)
        if channel is None and can_create:
            try:
                channel = await guild.create_text_channel(
                    name, category=category, topic=str(hub.get("events_topic") or "")[:1024],
                    reason="Xianxia RP world-events setup",
                )
            except discord.HTTPException:
                log.exception("Could not create world-events channel #%s", name)
        if channel is None:
            continue
        if can_create and category is not None and channel.category_id != category.id:
            try:
                await channel.edit(category=category, reason="Xianxia RP world-events setup")
            except discord.HTTPException:
                log.exception("Could not move #%s into %s", channel.name, category_name)
        if can_create and access_roles.get(world) is not None:
            await ensure_realm_hub_overwrites(guild, channel, access_roles.get(world))
        await DB.set_world_event_channel(
            guild_id=guild.id, world_name=world, channel_id=channel.id,
            category_id=channel.category_id if channel.category_id is not None else (category.id if category else None),
        )
    return await DB.get_world_event_channels(guild.id)


def world_of_location(location: str | None) -> str | None:
    """Which of the four worlds a place belongs to, or None when nothing knows.

    Deliberately **not** the `or "Mortal World"` default the rest of the tree
    uses. A private residence (`birth_family:<id>`), an inner world
    (`personal_world:<uid>`), an abode or a literal "Unknown" is not in the
    location catalogue, and defaulting those to the Mortal World would file
    somebody's household news as that world's public news. Here the honest
    answer is "no world", which routes to the global feed.
    """
    key = str(location or "").strip()
    if not key:
        return None
    world = str((WORLD.locations.get(key) or {}).get("world") or "").strip()
    return world if world in REALM_HUBS else None


async def world_event_channel(
    guild: discord.Guild | None, location: str | None,
) -> discord.TextChannel | None:
    """The channel an announcement about `location` belongs in.

    Its world's channel when the catalogue places it in one and that channel is
    bound; otherwise the global `world-events` channel, which is what every
    announcement used before v1.0.0-rc.52 and what the world-less ones - the
    weekend gift, a GM's world-reset notice, the dashboard's test post - still
    use. So the worst case is the channel this already went to.
    """
    if guild is None:
        return None
    world = world_of_location(location)
    if world is not None:
        for row in await DB.get_world_event_channels(guild.id):
            if str(row["world_name"]) == world:
                channel = await _resolve_text_channel(guild, row["channel_id"])
                if channel is not None:
                    return channel
                break
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("announcement_channel_id"))


async def event_scene_parent(
    guild: discord.Guild | None, location: str | None,
) -> discord.TextChannel | None:
    """Where an event's scene message and its roleplay thread are anchored
    (v1.0.0-rc.59).

    It is the world's own feed now - the channel rc.52 already sent the
    announcement to. Until this release an event used two channels: the
    announcement went to its world's feed and the thread was anchored in
    `#event-scenes`, a ninth base channel whose whole job was to be somewhere
    for the thread to hang. `#event-scenes` is retired, and a server that still
    has one bound is the fallback here so nothing breaks mid-upgrade.

    `world_event_channel` already falls back to the global feed, so the only
    way to reach the retired channel is a server with neither a world feed nor
    an announcement channel bound - which is a server that has not been set up.
    """
    channel = await world_event_channel(guild, location)
    if channel is not None:
        return channel
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("event_scene_channel_id"))


async def event_channels(interaction: discord.Interaction) -> tuple[discord.TextChannel | None, discord.TextChannel | None]:
    guild = interaction.guild
    if guild is None:
        return None, None

    config = await DB.get_server_config(guild.id)
    announcement_id = config.get("announcement_channel_id")
    scene_id = config.get("event_scene_channel_id")

    announcement_channel = await _resolve_text_channel(guild, announcement_id)
    scene_channel = await _resolve_text_channel(guild, scene_id)
    return announcement_channel, scene_channel


async def home_scene_channel(interaction: discord.Interaction) -> discord.TextChannel | None:
    guild = interaction.guild
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    channel_id = (config.get("home_scene_channel_id") or config.get("event_scene_channel_id")
                  or config.get("announcement_channel_id"))
    return await _resolve_text_channel(guild, channel_id)


async def exploration_scene_channel(interaction: discord.Interaction) -> discord.TextChannel | None:
    guild = interaction.guild
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("exploration_channel_id"))


async def configured_info_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("info_channel_id"))


async def _get_thread(guild: discord.Guild, thread_id: int | None) -> discord.Thread | None:
    if not thread_id:
        return None
    existing = guild.get_thread(int(thread_id))
    if existing is not None:
        try:
            if existing.archived:
                await existing.edit(archived=False, reason="Resume Xianxia private scene")
        except discord.HTTPException:
            pass
        return existing
    try:
        fetched = await guild.fetch_channel(int(thread_id))
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return None
    if not isinstance(fetched, discord.Thread):
        return None
    try:
        if fetched.archived:
            await fetched.edit(archived=False, reason="Resume Xianxia private scene")
    except discord.HTTPException:
        pass
    return fetched


async def send_long_to_thread(thread: discord.Thread, text: str) -> None:
    for chunk in chunk_text(text):
        await thread.send(chunk)


async def configured_log_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("log_channel_id"))


async def configured_begin_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("begin_channel_id"))


async def post_server_log(guild: discord.Guild | None, title: str, detail: str) -> None:
    """Best-effort operational Discord log without exposing full tracebacks or secrets."""
    channel = await configured_log_channel(guild)
    if channel is None:
        return
    body = str(detail).strip()
    if len(body) > 1600:
        body = body[:1597] + "..."
    try:
        await channel.send(f"🧾 **{str(title)[:200]}**\n{body}")
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not post to configured Discord log channel")




# Moved here in split phase 8 (v0.19.43): used by the creation and event-scene
# UI modules and by the battle views, so it has to sit below all of them.
async def _report_game_ui_error(
    interaction: discord.Interaction,
    error: Exception,
    *,
    where: str,
    ephemeral: bool = False,
) -> None:
    log.error(
        "Discord UI callback failed: %s", where,
        exc_info=(type(error), error, error.__traceback__),
    )
    await post_server_log(
        interaction.guild,
        "Discord UI callback failed",
        f"{where} — {type(error).__name__}: {str(error)[:900]}",
    )
    text = "❌ This interface hit an unexpected error. No extra UI-side game rule was applied."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(text, ephemeral=ephemeral)
    except discord.HTTPException:
        log.exception("Could not deliver Discord UI failure response")



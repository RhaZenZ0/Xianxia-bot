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
        if state["everyone_view"] is not False:
            await channel.set_permissions(guild.default_role, view_channel=False, reason="Xianxia realm-capital visibility gate")
        if not member_ok:
            await channel.set_permissions(role, reason="Xianxia realm-capital visibility gate", **REALM_HUB_MEMBER_PERMISSIONS)
        if guild.me is not None and me_view is not True:
            await channel.set_permissions(guild.me, view_channel=True, send_messages=True, manage_messages=True, read_message_history=True, reason="Xianxia realm-capital visibility gate")
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
    guild: discord.Guild, *, category_name: str = "🌌 Realm Capitals", create_missing: bool = False,
) -> list[dict[str, Any]]:
    """Bind existing live-auction channels and, when create_missing, create any
    that are missing (v0.33.1). A grand house (a capital's) has a channel of
    its own; the local floors of a world share one, named in content - so a
    world of twelve cities is one channel, not twelve. Every house is bound to
    the channel its content names, beside the realm capitals, visible to the
    cultivators who can reach that world - the same access role that gates it
    - and to nobody else. Like the capitals, the /admin slash path only binds;
    the dashboard's Setup/Repair is what creates.
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
        if can_create and access_roles.get(world) is not None:
            await ensure_realm_hub_overwrites(guild, channel, access_roles.get(world))
        await DB.set_auction_house_channel(
            guild_id=guild.id, house_id=house_id, location=str(house.get("location") or ""), channel_id=channel.id,
            category_id=channel.category_id if channel.category_id is not None else (category.id if category else None),
        )
    return await DB.get_auction_house_channels(guild.id)


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
    channel_id = config.get("home_scene_channel_id") or config.get("event_scene_channel_id")
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



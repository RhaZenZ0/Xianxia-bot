"""Guild channels, roles and thread lookup: resolving configured channels,
realm-hub channels/roles, the server log.

Phase 4 of the main.py split (v0.19.39, docs/MAIN_SPLIT_PLAN.md).
_resolve_text_channel has 17 call sites and post_server_log is what the hubs
report failures through, so this sits below every command module and below
threads.py. Reads runtime, app.realm_hubs and discord; never main.py.
Definition order is the order these had in main.py.
"""
from __future__ import annotations

from typing import Any

import discord

from ..rules.realm_hubs import REALM_HUBS
from .runtime import DB, SETTINGS, _realm_access_role_name, chunk_text, log

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
        await DB.set_realm_hub_channel(
            guild_id=guild.id,
            world_name=world,
            location=str(hub["location"]),
            channel_id=channel.id,
            category_id=channel.category_id if channel.category_id is not None else (category.id if category else None),
        )
    return await DB.get_realm_hub_channels(guild.id)


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



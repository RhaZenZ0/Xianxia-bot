"""Persistent player threads: expeditions, birth-family households, sect
abodes, private abodes, and finding the private scene a thread belongs to.

Phase 4 of the main.py split (v0.19.39, docs/MAIN_SPLIT_PLAN.md). These are
the helpers family.py and sect.py had to reach back into main.py for with
call-time imports; with this module below them those hooks are gone. Reads
runtime, channels, formatting, app.sect_recruitment and discord; never
main.py. Definition order is the order these had in main.py.
"""
from __future__ import annotations

from typing import Any

import discord

from ..rules.sect_recruitment import recruitment_definition
from .channels import (
    _event_archive_minutes,
    _get_thread,
    _resolve_text_channel,
    exploration_scene_channel,
    home_scene_channel,
)
from .formatting import player_property_emoji, player_property_facility_lines
from .runtime import DB, WORLD, character_location_display, log, player_property_label, private_location_exit

def _expedition_thread_intro(*, name: str, location_display: str, location: object) -> str:
    """Opening message for a player's private expedition journal.

    The closing line is location-aware on purpose. A character standing inside a
    private location cannot explore or hunt, so telling them to "use Explore" is
    an instruction that is guaranteed to fail - the same trap that made every new
    cultivator think the game was broken. When they are indoors, name the way out
    instead, and name the specific one for where they actually are.
    """
    lines = [
        f"🧭 **{name} — Private Expedition Journal**",
        f"Current location: **{location_display}**",
        "",
        "This scene is yours: only you, invited administrators and the bot. Exploration results "
        "and guided Scene Actions are written here, so the thread builds into a continuing "
        "record of where you went and what it cost you.",
        "",
    ]
    exit_route = private_location_exit(location)
    if exit_route is not None:
        command, description = exit_route
        lines.append(
            f"Right now you're inside {description}, where exploring and hunting are unavailable. "
            f"Step out with {command} first — then **/world → Explore** to search wherever you land, "
            "or **/action** for a guided in-scene action."
        )
    else:
        lines.append(
            f"Start with **/world → Explore** to search **{location_display}**, "
            "or **/action** for a guided in-scene action."
        )
    return "\n".join(lines)


async def ensure_expedition_thread(interaction: discord.Interaction, character: dict[str, Any]) -> discord.Thread | None:
    """Create/recover one private expedition journal per player and guild."""
    guild = interaction.guild
    if guild is None:
        return None
    row = await DB.get_expedition_thread(guild.id, interaction.user.id)
    if row:
        thread = await _get_thread(guild, row.get("thread_id"))
        if thread is not None:
            try:
                await thread.add_user(interaction.user)
            except (discord.Forbidden, discord.HTTPException):
                pass
            await DB.update_expedition_location(guild.id, interaction.user.id, str(character.get("location") or "Unknown"))
            return thread
    parent = await exploration_scene_channel(interaction)
    if parent is None:
        return None
    try:
        thread = await parent.create_thread(
            name=(f"🧭 {character.get('name', interaction.user.display_name)} • Expedition")[:100],
            type=discord.ChannelType.private_thread,
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Private Xianxia expedition journal for {interaction.user}",
        )
        await thread.add_user(interaction.user)
        await DB.set_expedition_thread(
            guild.id, interaction.user.id, thread_id=thread.id,
            parent_channel_id=parent.id, last_location=str(character.get("location") or "Unknown"),
        )
        await thread.send(
            _expedition_thread_intro(
                name=str(character.get("name", interaction.user.display_name)),
                location_display=await character_location_display(character),
                location=character.get("location"),
            )
        )
        return thread
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create private expedition thread for user %s", interaction.user.id)
        return None


async def open_expedition_thread_after_exit(interaction: discord.Interaction) -> None:
    """Open the personal expedition journal after stepping back into the world.

    Reported by a player: after leaving the birth household there was no personal
    thread until they happened to run /explore, so the room their own scenes get
    written into simply did not exist yet. Every entry in PRIVATE_LOCATION_EXITS
    has the same shape - household, sect residence, own property, personal world -
    so all of them do this, rather than fixing the one that was reported.

    Three deliberate choices:

    * The character is RE-READ. Each caller fetched its copy before the engine
      moved them, so using it would stamp the journal with the location they just
      walked out of.
    * If they are still inside a private location, this exit stepped into another
      one and the world journal is not the right room yet.
    * This runs AFTER the caller has already confirmed the move, and never
      raises. The exit is committed canonically at that point; a thread that
      cannot be created is worth a quiet log, not a failed action or a stalled
      interaction.
    """
    if interaction.guild is None:
        return
    try:
        character = await DB.get_character(interaction.user.id)
        if not character:
            return
        if private_location_exit(character.get("location")) is not None:
            return
        thread = await ensure_expedition_thread(interaction, dict(character))
        if thread is None:
            return
        await interaction.followup.send(
            f"🧭 Your expedition journal is open in {thread.mention} — your private "
            "scenes, exploration and Scene Actions are written there.",
            ephemeral=False,
        )
    except Exception:
        log.warning(
            "Could not open the expedition journal after a private-location exit",
            exc_info=True,
        )


async def ensure_birth_family_household_thread(
    interaction: discord.Interaction, family: dict[str, Any]
) -> discord.Thread | None:
    """Create/recover one shared private Discord scene for a canonical birth household."""
    guild = interaction.guild
    if guild is None:
        return None
    family_id = int(family.get("family_id") or 0)
    if family_id <= 0:
        return None
    row = await DB.get_birth_family_household_thread(guild.id, family_id)
    if row:
        thread = await _get_thread(guild, row.get("thread_id"))
        if thread is not None:
            try:
                await thread.add_user(interaction.user)
            except (discord.Forbidden, discord.HTTPException):
                pass
            return thread
    parent = await home_scene_channel(interaction)
    if parent is None:
        return None
    family_name = str(family.get("family_name") or "Birth Family")
    base_location = str(family.get("location") or "Unknown")
    try:
        thread = await parent.create_thread(
            name=(f"🏠 {family_name} Household")[:100],
            type=discord.ChannelType.private_thread,
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Shared Xianxia birth household for family {family_id}",
        )
        await thread.add_user(interaction.user)
        await DB.set_birth_family_household_thread(
            guild.id, family_id, thread_id=thread.id, parent_channel_id=parent.id
        )
        await thread.send(
            f"🏠 **{family_name} — Shared Household**\n"
            f"Home region: **{base_location}**\n\n"
            "**You are inside the house.** Exploring, hunting and travel need the open world, "
            f"so they will refuse until you step out — **/family → Leave** puts you in **{base_location}**, "
            "and **/family → Enter** brings you back whenever you like.\n\n"
            "This scene is shared by every player born into this household. While more than one of you "
            "is inside, you can talk, roleplay and target each other with guided Scene Actions (**/action**). "
            "Nothing here is lost when you leave."
        )
        return thread
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create shared birth-household thread for family %s", family_id)
        return None


def _sect_abode_name(character_name: str, sect_name: str, rank_name: str = "Outer Disciple") -> str:
    rank = str(rank_name or "Disciple")
    if "Ancestor" in rank or "Master" in rank:
        residence = "Peak Residence"
    elif "Core" in rank:
        residence = "Inner Peak Pavilion"
    elif "Inner" in rank:
        residence = "Spirit Courtyard"
    else:
        residence = "Disciple Courtyard"
    return f"{character_name}'s {residence}"


async def ensure_sect_abode_record(user_id: int, character: dict[str, Any], membership: dict[str, Any]) -> dict[str, Any]:
    sect_name = str(membership["sect_name"])
    rec = recruitment_definition(WORLD.sects, sect_name) or {}
    base_location = str(rec.get("location") or character.get("location") or "Unknown")
    return await DB.ensure_sect_abode(
        user_id,
        sect_name=sect_name,
        name=_sect_abode_name(str(character.get("name") or "Cultivator"), sect_name, str(membership.get("rank_name") or "Disciple")),
        base_location=base_location,
    )


async def ensure_sect_abode_thread_for(
    guild: discord.Guild, member: discord.Member | discord.User, abode: dict[str, Any]
) -> discord.Thread | None:
    thread = await _get_thread(guild, abode.get("thread_id"))
    if thread is not None:
        try:
            await thread.add_user(member)
        except (discord.Forbidden, discord.HTTPException):
            pass
        return thread
    cfg = await DB.get_server_config(guild.id)
    parent = await _resolve_text_channel(guild, cfg.get("home_scene_channel_id") or cfg.get("event_scene_channel_id"))
    if parent is None:
        return None
    try:
        thread = await parent.create_thread(
            name=(f"🏯 {abode['name']}")[:100],
            type=discord.ChannelType.private_thread,
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Private sect abode for {member}",
        )
        await thread.add_user(member)
        await DB.set_sect_abode_thread(int(member.id), thread_id=thread.id, thread_channel_id=parent.id)
        await thread.send(
            f"🏯 **{abode['name']} — Sect Abode**\n"
            f"Sect: **{abode['sect_name']}**\n"
            f"Assigned to: **{getattr(member, 'display_name', str(member))}**\n"
            f"Sect gate: **{abode['base_location']}**\n\n"
            "This is your persistent private residence inside the sect. Cultivation RP, study, alchemy, "
            "private meetings and guided Scene Actions can continue here without cluttering a public city channel."
        )
        return thread
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create private sect-abode thread for user %s", member.id)
        return None


async def _private_scene_for_thread(guild: discord.Guild, thread_id: int, user_id: int, character: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    expedition = await DB.get_expedition_thread_by_thread(thread_id)
    if expedition and int(expedition.get("user_id", 0)) == int(user_id):
        current_location = str(character.get("location") or "")
        if not current_location.startswith(("abode:", "sect_abode:", "personal_world:", "birth_family:")):
            return "expedition", expedition
    household = await DB.get_birth_family_household_thread_by_thread(thread_id)
    if household:
        family = await DB.get_birth_family(user_id)
        expected_location = f"birth_family:{int(household.get('family_id') or 0)}"
        if family and int(family.get("family_id") or 0) == int(household.get("family_id") or 0) and str(character.get("location") or "") == expected_location:
            return "birth_family_household", household
    abode = await DB.get_abode_by_thread(thread_id)
    if abode and str(character.get("location") or "") == str(abode.get("location_key") or ""):
        if await DB.can_access_abode(int(abode["user_id"]), int(user_id)):
            return "player_abode", abode
    sect_abode = await DB.get_sect_abode_by_thread(thread_id)
    if sect_abode and int(sect_abode.get("user_id", 0)) == int(user_id) and str(character.get("location") or "") == str(sect_abode.get("location_key") or ""):
        return "sect_abode", sect_abode
    return None


async def active_private_location_thread(interaction: discord.Interaction, character: dict[str, Any]) -> discord.Thread | None:
    guild = interaction.guild
    if guild is None:
        return None
    location = str(character.get("location") or "")
    if location.startswith("birth_family:"):
        family = await DB.get_birth_family(interaction.user.id)
        if family and location == f"birth_family:{int(family.get('family_id') or 0)}":
            return await ensure_birth_family_household_thread(interaction, family)
    if location.startswith("abode:"):
        abode = await DB.get_abode_by_location(location)
        if abode:
            thread = await _get_thread(guild, abode.get("thread_id"))
            if thread is not None and await DB.can_access_abode(int(abode["user_id"]), interaction.user.id):
                try: await thread.add_user(interaction.user)
                except (discord.Forbidden, discord.HTTPException): pass
                return thread
    if location.startswith("sect_abode:"):
        abode = await DB.get_sect_abode_by_location(location)
        if abode and int(abode.get("user_id", 0)) == interaction.user.id:
            return await ensure_sect_abode_thread_for(guild, interaction.user, abode)
    return None


async def ensure_abode_thread(interaction: discord.Interaction, abode: dict) -> discord.Thread | None:
    if interaction.guild is None:
        return None
    thread_id = abode.get("thread_id")
    if thread_id:
        existing = interaction.guild.get_thread(int(thread_id))
        if existing is not None:
            try:
                await existing.add_user(interaction.user)
            except (discord.Forbidden, discord.HTTPException):
                pass
            return existing
        try:
            fetched = await interaction.guild.fetch_channel(int(thread_id))
            if isinstance(fetched, discord.Thread):
                try:
                    await fetched.add_user(interaction.user)
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return fetched
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
    channel = await home_scene_channel(interaction)
    if channel is None:
        return None
    property_label = player_property_label(abode)
    emoji = player_property_emoji(abode)
    try:
        thread = await channel.create_thread(
            name=(f"{emoji} {abode['name']}")[:100],
            type=discord.ChannelType.private_thread,
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Private player-owned location for {interaction.user}",
        )
        await thread.add_user(interaction.user)
        await DB.set_abode_thread(
            interaction.user.id, thread_id=thread.id, thread_channel_id=channel.id
        )
        facilities = " • ".join(player_property_facility_lines(abode)) or "No developed facilities yet"
        await thread.send(
            f"{emoji} **{abode['name']} — {property_label}**\n"
            f"Owner: **{interaction.user.display_name}**\n"
            f"Entrance: **{abode.get('base_location','Unknown')}**\n"
            f"Facilities: {facilities}\n\n"
            "This private thread is the persistent scene for this player-owned location. "
            "Invited cultivators can be granted or revoked with **/abode → Invite** and **/abode → Revoke**.\n"
            "Physical travel still matters: guests must reach the property's entrance before they can enter. "
            "Use this scene for cultivation, crafting, beasts, family scenes, commerce, meetings and guided Scene Actions."
        )
        return thread
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create private player-property thread for user %s", interaction.user.id)
        return None



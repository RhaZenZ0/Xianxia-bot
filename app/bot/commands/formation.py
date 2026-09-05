"""The /formation hub: party combat formations and stance management."""

from __future__ import annotations

import discord
from discord import app_commands

from ...advanced_runtime import FORMATION_POSITIONS, FORMATION_STANCES
from ...game_engine import GameEngineError
from ..registry import registered_group_command
from ..runtime import (
    DB,
    ENGINE,
    current_world_time,
    require_character,
    reply_long,
    serialized_user_action,
)

formation_group = app_commands.Group(
    name="formation",
    description="Assign party combat positions and formation stances",
)


@registered_group_command(formation_group, name="create", description="Create a named combat formation for your active party")
@serialized_user_action
async def formation_create(interaction: discord.Interaction, name: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "formation.create",
            interaction.user.id,
            {"name": name},
            action_id=f"discord:{interaction.id}:formation.create",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    await interaction.followup.send(f"🧿 Formation `#{result.get('formation_id')}` created.", ephemeral=False)


@registered_group_command(formation_group, name="assign", description="Assign one party member to Vanguard, Core, Flank or Support")
@app_commands.choices(position=[app_commands.Choice(name=x.title(), value=x) for x in FORMATION_POSITIONS])
@serialized_user_action
async def formation_assign(interaction: discord.Interaction, formation_id: int, member: discord.Member, position: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "formation.assign",
            interaction.user.id,
            {"formation_id": int(formation_id), "target_user_id": member.id, "position": position.value},
            action_id=f"discord:{interaction.id}:formation.assign",
        )
        _ = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    await interaction.followup.send(f"🧿 Assigned {member.mention} to **{position.value}**.", ephemeral=False)


@registered_group_command(formation_group, name="activate", description="Activate a formation and choose its combat stance")
@app_commands.choices(stance=[app_commands.Choice(name=x.title(), value=x) for x in FORMATION_STANCES])
@serialized_user_action
async def formation_activate(interaction: discord.Interaction, formation_id: int, stance: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "formation.activate",
            interaction.user.id,
            {"formation_id": int(formation_id), "stance": stance.value},
            action_id=f"discord:{interaction.id}:formation.activate",
        )
        _ = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    await interaction.followup.send(f"🧿 Formation `#{formation_id}` activated in **{stance.value}** stance.", ephemeral=False)


@registered_group_command(formation_group, name="stance", description="Change the active formation stance between combat rounds")
@app_commands.choices(stance=[app_commands.Choice(name=x.title(), value=x) for x in FORMATION_STANCES])
@serialized_user_action
async def formation_stance(interaction: discord.Interaction, stance: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "formation.stance",
            interaction.user.id,
            {"stance": stance.value},
            action_id=f"discord:{interaction.id}:formation.stance",
        )
        _ = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    await interaction.followup.send(f"🧿 Active formation stance changed to **{stance.value}**.", ephemeral=False)


@registered_group_command(formation_group, name="status", description="Inspect your party formation, positions and remaining cohesion")
async def formation_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    party = await DB.get_party(interaction.user.id)
    if not party:
        await interaction.response.send_message("You are not in an active party.", ephemeral=False)
        return
    rows = await DB.get_formations(int(party['party_id']))
    if not rows:
        await interaction.response.send_message("Your party has no formations.", ephemeral=False)
        return
    lines = [f"☯️ **Party Formations — {party['name']}**"]
    for formation in rows:
        assignments = ", ".join(f"{position['position'].title()}: <@{position['user_id']}>" for position in formation['positions']) or "unassigned"
        lines.append(f"\n{'⭐' if formation['active'] else '▫️'} `#{formation['formation_id']}` **{formation['name']}** • {formation['stance']} • cohesion **{formation['cohesion']}%**\n{assignments}")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)

"""The /boss and /hunter hubs: boss encounters and bounty-hunter pursuers."""

from __future__ import annotations

import discord
from discord import app_commands

from ...rules.advanced_runtime import BOSS_TEMPLATES
from ...ops.game_engine import GameEngineError
from ..registry import registered_group_command
from ..runtime import (
    DB,
    ENGINE,
    WORLD,
    current_world_time,
    require_character,
    reply_long,
    serialized_user_action,
)


boss_group = app_commands.Group(
    name="boss",
    description="Run persistent multi-phase party boss encounters",
)

hunter_group = app_commands.Group(
    name="hunter",
    description="Respond to autonomous bounty-hunter pursuits",
)


async def law_technique_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    needle = current.casefold().strip()
    out: list[app_commands.Choice[str]] = []
    for tid, definition in WORLD.law_system.get("techniques", {}).items():
        name = str(definition.get("name", tid))
        if not needle or needle in name.casefold() or needle in tid.casefold():
            out.append(app_commands.Choice(name=name[:100], value=tid[:100]))
    return out[:25]


@registered_group_command(boss_group, name="list", description="List persistent multi-phase bosses and their required locations")
async def boss_list(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    lines = ["👹 **Boss Encounters**"]
    for key, boss in BOSS_TEMPLATES.items():
        lines.append(f"\n`{key}` — **{boss['name']}** • {boss['location']} • {len(boss['phases'])} phases • base HP {boss['max_hp']}")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(boss_group, name="start", description="Party leader starts a persistent multi-phase boss encounter")
@serialized_user_action
async def boss_start(interaction: discord.Interaction, boss: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "boss.start",
            interaction.user.id,
            {"template_key": boss},
            action_id=f"discord:{interaction.id}:boss.start",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    await interaction.followup.send(
        f"👹 **Boss Encounter #{result.get('encounter_id')} — {result.get('boss_name', 'Boss')}** begins with **{result.get('boss_hp', 0)}/{result.get('boss_hp_max', 0)} HP**.",
        ephemeral=False,
    )


@registered_group_command(boss_group, name="status", description="View the active party boss phase, HP, raid vitality and round")
async def boss_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    encounter = await DB.get_boss_encounter(user_id=interaction.user.id)
    if not encounter:
        await interaction.response.send_message("Your party has no active boss encounter.", ephemeral=False)
        return
    lines = [
        f"👹 **#{encounter['encounter_id']} {encounter['boss_name']}** • Round **{encounter['round_index']}**",
        f"Boss HP **{encounter['boss_hp']}/{encounter['boss_hp_max']}** • Phase **{encounter['phase'].get('name', 'Unknown')}**",
    ]
    for participant in encounter['participants']:
        lines.append(f"• <@{participant['user_id']}> — **{participant['vitality']}/{participant['vitality_max']}** • {participant['status']} • damage {participant['total_damage']}")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(boss_group, name="act", description="Take your once-per-round raid action; boss retaliates after the full party acts")
@app_commands.choices(style=[app_commands.Choice(name="Attack", value="attack"), app_commands.Choice(name="Technique", value="technique"), app_commands.Choice(name="Defend", value="defend"), app_commands.Choice(name="Support", value="support")])
@app_commands.autocomplete(technique=law_technique_autocomplete)
@serialized_user_action
async def boss_act(interaction: discord.Interaction, style: app_commands.Choice[str], technique: str = "") -> None:
    if not await require_character(interaction):
        return
    encounter = await DB.get_boss_encounter(user_id=interaction.user.id)
    if not encounter:
        await interaction.response.send_message("Your party has no active boss encounter.", ephemeral=False)
        return
    if style.value == "technique" and not technique.strip():
        await interaction.response.send_message("Choose which unlocked Law technique to use.", ephemeral=False)
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "boss.act",
            interaction.user.id,
            {
                "encounter_id": int(encounter['encounter_id']),
                "style": style.value,
                "technique": technique.strip(),
                "version": int(encounter['version']),
            },
            action_id=f"discord:{interaction.id}:boss.act",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    events = "\n".join(f"• {event}" for event in list(result.get('events') or [])[:12])
    await interaction.response.send_message(
        f"👹 **Boss #{result.get('encounter_id', encounter['encounter_id'])}** • {result.get('status', 'active')} • Round {result.get('round_index', '?')} • HP **{result.get('boss_hp', '?')}/{result.get('boss_hp_max', '?')}**\n{events}",
        ephemeral=False,
    )


@registered_group_command(boss_group, name="claim", description="Claim your reward from a completed boss encounter")
@serialized_user_action
async def boss_claim(interaction: discord.Interaction, encounter_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "boss.claim",
            interaction.user.id,
            {"id": int(encounter_id)},
            action_id=f"discord:{interaction.id}:boss.claim",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    await interaction.followup.send(
        f"🏆 Claimed **{result.get('currency_amount', 0)} Low Spirit Stones** and **{WORLD.item_name(str(result.get('item_id', '')))} x{result.get('item_quantity', 0)}**.",
        ephemeral=False,
    )


@registered_group_command(hunter_group, name="status", description="View the autonomous bounty hunter currently tracking this incarnation")
async def hunter_status(interaction: discord.Interaction) -> None:
    if not await require_character(interaction):
        return
    pursuit = await DB.get_bounty_hunter_pursuit(user_id=interaction.user.id)
    if not pursuit:
        await interaction.response.send_message("🎯 No autonomous bounty hunter is actively pursuing you.", ephemeral=False)
        return
    await interaction.response.send_message(
        f"🎯 **Pursuit #{pursuit['pursuit_id']} — {pursuit['hunter_name']}**\n"
        f"Jurisdiction **{pursuit['jurisdiction']}** • bounty **{pursuit['amount']}** • hunter power **{pursuit['hunter_power']}**\n"
        f"Status **{pursuit['status']}** • pressure **{pursuit['pressure']}%** • escape **{pursuit['escape_progress']}%** • capture **{pursuit['capture_progress']}%**",
        ephemeral=False,
    )


@registered_group_command(hunter_group, name="act", description="Evade, fight or surrender to an active bounty hunter")
@app_commands.choices(action=[app_commands.Choice(name="Evade", value="evade"), app_commands.Choice(name="Fight", value="fight"), app_commands.Choice(name="Surrender", value="surrender")])
@serialized_user_action
async def hunter_act(interaction: discord.Interaction, pursuit_id: int, action: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "bounty_hunter.act",
            interaction.user.id,
            {"pursuit_id": int(pursuit_id), "action": action.value},
            action_id=f"discord:{interaction.id}:bounty_hunter.act",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    await interaction.followup.send(
        f"🎯 **{result.get('hunter_name', 'Hunter')}** • status **{result.get('status', 'active')}** • pressure {result.get('pressure', 0)}% • escape {result.get('escape_progress', 0)}% • capture {result.get('capture_progress', 0)}%",
        ephemeral=False,
    )

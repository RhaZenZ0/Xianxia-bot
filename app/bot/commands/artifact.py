"""The /artifact hub: persistent artifact bonds and awakening."""

from __future__ import annotations

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ...rules.progression_systems import profession_rank
from ..registry import registered_group_command
from ..runtime import DB, ENGINE, WORLD, require_character, reply_long, serialized_user_action


artifact_group = app_commands.Group(
    name="artifact",
    description="Bond, awaken and inspect personal artifacts",
)


@registered_group_command(artifact_group, name="status", description="View artifacts bonded to this incarnation")
async def artifact_status(interaction: discord.Interaction) -> None:
    character = await require_character(interaction)
    if not character:
        return
    rows = await DB.get_artifact_bonds(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🗡️ You have not formed a persistent artifact bond yet.", ephemeral=False)
        return
    lines = [f"🗡️ **Artifact Bonds — {character['name']}**"]
    for row in rows:
        spirit = f" • Spirit **{row['spirit_name']}**" if row.get("spirit_name") else ""
        lines.append(
            f"\n**{WORLD.item_name(row['item_id'])}** • Bond {row['bond_level']}/10 • "
            f"Resonance **{row['resonance']}%** • {'Awakened' if row['awakened'] else 'Dormant'}{spirit}"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(artifact_group, name="bond", description="Deepen a bond with a carried item")
@serialized_user_action
async def artifact_bond(interaction: discord.Interaction, item: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "artifact.bond",
            interaction.user.id,
            {"item_id": item},
            action_id=f"discord:{interaction.id}:artifact.bond",
        )
    except GameEngineError as exc:
        await interaction.followup.send(str(exc), ephemeral=False)
        return

    resolved = dict(envelope.get("result") or {})
    artifact = dict(resolved.get("artifact") or {})
    artifact_progress = dict(resolved.get("profession_progress") or {})
    await interaction.followup.send(
        f"🗡️ Bond with **{WORLD.item_name(item)}** deepens to **{artifact['bond_level']}**. "
        f"Resonance **{artifact['resonance']}%**.\n"
        f"🔨 Artifact Refining: **{profession_rank(int(artifact_progress.get('level', 0)))}** "
        f"Lv.{int(artifact_progress.get('level', 0))}.",
        ephemeral=False,
    )


@registered_group_command(artifact_group, name="awaken", description="Awaken a sufficiently bonded artifact spirit")
@serialized_user_action
async def artifact_awaken(interaction: discord.Interaction, item: str, spirit_name: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "artifact.awaken",
            interaction.user.id,
            {"item_id": item, "spirit_name": spirit_name},
            action_id=f"discord:{interaction.id}:artifact.awaken",
        )
    except GameEngineError as exc:
        await interaction.followup.send(str(exc), ephemeral=False)
        return
    resolved = dict(envelope.get("result") or {})
    artifact = dict(resolved.get("artifact") or {})
    artifact_progress = dict(resolved.get("profession_progress") or {})
    await interaction.followup.send(
        f"✨ **{WORLD.item_name(item)} awakens.** Its spirit answers to **{artifact['spirit_name']}** "
        f"at **{artifact['resonance']}% resonance**. Awakened artifacts contribute to battle checks.\n"
        f"🔮 Artifact Refining: **{profession_rank(int(artifact_progress.get('level', 0)))}** "
        f"Lv.{int(artifact_progress.get('level', 0))}.",
        ephemeral=False,
    )

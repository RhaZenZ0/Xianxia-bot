"""The /beast hub: contracted spirit beasts and tameable wild beasts."""

from __future__ import annotations

from types import SimpleNamespace

import discord
from discord import app_commands

from ..formatting import roll_line
from ..registry import registered_group_command
from ..runtime import (
    DB,
    ENGINE,
    WORLD,
    character_location_display,
    current_world_time,
    require_character,
    reply_long,
    serialized_user_action,
)
from ...ops.game_engine import GameEngineError
from ...rules.progression_systems import profession_rank


beast_group = app_commands.Group(
    name="beast",
    description="Manage contracted spirit beasts and active companions",
)


@registered_group_command(beast_group, name="status", description="View your contracted spirit beasts")
async def beast_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_spirit_beasts(interaction.user.id)
    if not rows:
        await interaction.response.send_message(
            "🐾 You have no spirit-beast contract. Beast Binders can earn equality-contract opportunities from overwhelming **/world → Hunt** successes.",
            ephemeral=False,
        )
        return
    lines = [f"🐉 **Spirit Beasts — {c['name']}**"]
    for row in rows:
        lines.append(
            f"\n{'⭐ ' if row.get('active') else ''}**#{row['beast_id']} {row['name']}** ({row['species']})\n"
            f"Rank **{row['rank']}** • {row['element']} • Loyalty **{row['loyalty']}** • Evolution **{row['evolution_stage']}** • Contract **{row['contract_type']}**"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(beast_group, name="encounters", description="View subdued wild beasts currently available for taming")
async def beast_encounters(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    rows = await DB.get_wild_beast_encounters(
        interaction.user.id,
        game_minute=wt.total_minutes,
        location=str(c.get("location", "")),
    )
    if not rows:
        await interaction.response.send_message(
            "🐾 No subdued wild beast is waiting here. Strong **/world → Hunt** victories can create taming opportunities.",
            ephemeral=False,
        )
        return
    lines = [f"🪢 **Taming Opportunities — {await character_location_display(c)}**"]
    for row in rows:
        remaining = max(0, int(row["expires_game_minute"]) - wt.total_minutes)
        lines.append(
            f"\n`#{row['encounter_id']}` **{row['species']}** • Rank {row['rank']} • {row['element']} • "
            f"{row['temperament']} • taming TN **{row['taming_tn']}** • leaves in **{remaining} game minutes**"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(beast_group, name="tame", description="Attempt a consensual spirit-beast bond with a subdued wild beast")
@serialized_user_action
async def beast_tame(interaction: discord.Interaction, encounter_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "beast.tame",
            interaction.user.id,
            {"encounter_id": int(encounter_id)},
            action_id=f"discord:{interaction.id}:beast.tame",
        )
    except GameEngineError as exc:
        await interaction.followup.send(str(exc), ephemeral=False)
        return

    resolved = dict(envelope.get("result") or {})
    encounter = dict(resolved.get("encounter") or {})
    result = SimpleNamespace(**resolved)
    species = str(encounter.get("species") or "spirit beast")
    if bool(resolved.get("success")):
        beast = dict(resolved.get("beast") or {})
        beast_progress = dict(resolved.get("profession_progress") or {})
        active_line = " It becomes your active companion." if beast.get("active") else ""
        await interaction.followup.send(
            f"🐉 **Spirit-Beast Bond — {species}**\n{roll_line(result)}\n"
            f"The beast accepts an **equality contract** at loyalty **{beast.get('loyalty', 30)}**.{active_line}\n"
            f"🪢 Beast Taming: **{profession_rank(int(beast_progress.get('level', 0)))}** Lv.{int(beast_progress.get('level', 0))}."
        )
    else:
        await interaction.followup.send(
            f"🐾 **Taming Failed — {species}**\n{roll_line(result)}\n"
            "The beast rejects the bond and escapes. No contract is forced."
        )


@registered_group_command(beast_group, name="feed", description="Feed a contracted beast Spirit Herbs or Beast Cores to strengthen loyalty")
@app_commands.choices(
    food=[
        app_commands.Choice(name="Spirit Herb", value="spirit_herb"),
        app_commands.Choice(name="Low Beast Core", value="beast_core"),
    ]
)
@serialized_user_action
async def beast_feed(interaction: discord.Interaction, beast_id: int, food: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "beast.feed",
            interaction.user.id,
            {"beast_id": int(beast_id), "food": food.value},
            action_id=f"discord:{interaction.id}:beast.feed",
        )
    except GameEngineError as exc:
        await interaction.followup.send(str(exc), ephemeral=False)
        return

    updated = dict(envelope.get("result") or {})
    await interaction.followup.send(
        f"🐉 **{updated['name']}** accepts the **{WORLD.item_name(food.value)}**. "
        f"Loyalty rises to **{updated['loyalty']}** and intelligence to **{updated['intelligence']}**.",
        ephemeral=False,
    )


@registered_group_command(beast_group, name="train", description="Train a contracted beast to raise loyalty and intelligence")
@serialized_user_action
async def beast_train(interaction: discord.Interaction, beast_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "beast.train",
            interaction.user.id,
            {"beast_id": int(beast_id)},
            action_id=f"discord:{interaction.id}:beast.train",
        )
    except GameEngineError as exc:
        await interaction.followup.send(str(exc), ephemeral=False)
        return

    resolved = dict(envelope.get("result") or {})
    row = dict(resolved.get("beast") or {})
    beast_progress = dict(resolved.get("profession_progress") or {})
    pen_level = int(resolved.get("beast_pen_level", 0))
    context_bonus = int(resolved.get("context_bonus", 0))
    pen_note = f" • Spirit Beast Pen Lv.{pen_level} training bonus +{context_bonus}" if pen_level else ""
    await interaction.followup.send(
        f"🐉 **{row['name']}** completes a training cycle. Loyalty **{row['loyalty']}**, "
        f"intelligence **{row['intelligence']}**.{pen_note}\n"
        f"🪢 Beast Taming: **{profession_rank(int(beast_progress.get('level', 0)))}** "
        f"Lv.{int(beast_progress.get('level', 0))}.",
        ephemeral=False,
    )


@registered_group_command(beast_group, name="evolve", description="Attempt a bloodline/evolution step once loyalty is sufficient")
@serialized_user_action
async def beast_evolve(interaction: discord.Interaction, beast_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "beast.evolve",
            interaction.user.id,
            {"beast_id": int(beast_id)},
            action_id=f"discord:{interaction.id}:beast.evolve",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Evolution failed: {exc}", ephemeral=False)
        return
    resolved = dict(envelope.get("result") or {})
    row = dict(resolved.get("beast") or {})
    beast_progress = dict(resolved.get("profession_progress") or {})
    await interaction.followup.send(
        f"🧬 **{row['name']} evolves.** Evolution Stage **{row['evolution_stage']}**, Rank **{row['rank']}**. The strain reduces loyalty to **{row['loyalty']}**.\n"
        f"🪢 Beast Taming: **{profession_rank(int(beast_progress.get('level', 0)))}** Lv.{int(beast_progress.get('level', 0))}.",
        ephemeral=False,
    )


@registered_group_command(beast_group, name="active", description="Choose the spirit beast that supports you in battle")
@serialized_user_action
async def beast_active(interaction: discord.Interaction, beast_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        await ENGINE.authoritative_action(
            "beast.active",
            interaction.user.id,
            {"beast_id": int(beast_id)},
            action_id=f"discord:{interaction.id}:beast.active",
        )
    except GameEngineError as exc:
        await interaction.followup.send(str(exc), ephemeral=False)
        return
    await interaction.followup.send(
        "🐉 Active companion changed. Its rank, evolution and loyalty now contribute to normal battle exchanges.",
        ephemeral=False,
    )

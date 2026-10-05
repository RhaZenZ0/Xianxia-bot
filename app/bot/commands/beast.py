"""The /beast hub: contracted spirit beasts and tameable wild beasts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import discord
from discord import app_commands

from ..formatting import roll_line
from ..hubs import HubDynamicOption, register_hub_option_hint, register_hub_option_provider
from ..registry import registered_group_command
from ..runtime import (
    _explain_engine_error,
    DB,
    ENGINE,
    WORLD,
    character_location_display,
    current_world_time,
    log,
    require_character,
    reply_long,
    serialized_user_action,
)
from ...ops.game_engine import GameEngineError
from ...rules.advanced_runtime import companion_bonus
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
            "🐾 You have no spirit-beast contract. Beast Binders can earn equality-contract opportunities from overwhelming **/world → Act → Hunt** successes.",
            ephemeral=False,
        )
        return
    lines = [f"🐉 **Spirit Beasts — {c['name']}**"]
    for row in rows:
        lines.append(
            f"\n{'⭐ ' if row.get('active') else ''}**#{row['beast_id']} {row['name']}** ({row['species']})\n"
            f"Rank **{row['rank']}** • {row['element']} • Loyalty **{row['loyalty']}** • Evolution **{row['evolution_stage']}** • Contract **{row['contract_type']}**\n"
            f"{_companion_line(row)}"
        )
    lines.append(
        "\n-# A beast's bonus is half its rank, plus its evolution stage, plus one for every 40 loyalty, plus two for every tenth rank. "
        "Only the ⭐ active beast fights beside you, and only in one-on-one battles, not boss raids."
    )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


def _companion_line(row: Any) -> str:
    """What one beast adds in a fight (v1.7.5): the engine's
    `combatCompanionBonus`, twinned in `companion_bonus`."""
    bonus = companion_bonus(row.get("rank"), row.get("evolution_stage"), row.get("loyalty"))
    when = "while active" if row.get("active") else "if made active"
    return f"🐾 **{bonus:+d}** to your attack, flee and defence rolls {when} (1v1 battles)"


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
            "🐾 No subdued wild beast is waiting here. Strong **/world → Act → Hunt** victories can create taming opportunities.",
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
    lines.append(
        "\n-# Taming rolls 2d10 + spirit + presence + half your will against the TN, "
        "+4 for a Beast Binder, +1 for each beast already bonded (up to 4) and + your Beast Taming rank."
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
        await interaction.followup.send(_explain_engine_error(exc), ephemeral=False)
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
            f"🪢 Beast Taming: **{profession_rank(int(beast_progress.get('level', 0)), 'Beast Taming')}** Lv.{int(beast_progress.get('level', 0))}."
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
        await interaction.followup.send(_explain_engine_error(exc), ephemeral=False)
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
        await interaction.followup.send(_explain_engine_error(exc), ephemeral=False)
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
        f"🪢 Beast Taming: **{profession_rank(int(beast_progress.get('level', 0)), 'Beast Taming')}** "
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
        await interaction.followup.send(f"Evolution failed: {_explain_engine_error(exc)}", ephemeral=False)
        return
    resolved = dict(envelope.get("result") or {})
    row = dict(resolved.get("beast") or {})
    beast_progress = dict(resolved.get("profession_progress") or {})
    await interaction.followup.send(
        f"🧬 **{row['name']} evolves.** Evolution Stage **{row['evolution_stage']}**, Rank **{row['rank']}**. The strain reduces loyalty to **{row['loyalty']}**.\n"
        f"🪢 Beast Taming: **{profession_rank(int(beast_progress.get('level', 0)), 'Beast Taming')}** Lv.{int(beast_progress.get('level', 0))}.",
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
        envelope = await ENGINE.authoritative_action(
            "beast.active",
            interaction.user.id,
            {"beast_id": int(beast_id)},
            action_id=f"discord:{interaction.id}:beast.active",
        )
    except GameEngineError as exc:
        await interaction.followup.send(_explain_engine_error(exc), ephemeral=False)
        return
    # The engine answers with the beast's own row; the bonus is read off it.
    row = dict((envelope or {}).get("result") or {})
    chosen = " Its rank, evolution and loyalty now contribute to one-on-one battles."
    if row.get("rank") is not None:
        bonus = companion_bonus(row.get("rank"), row.get("evolution_stage"), row.get("loyalty"))
        chosen = f" **{row.get('name') or 'Your beast'}** now adds **{bonus:+d}** to your attack, flee and defence rolls in one-on-one battles."
    await interaction.followup.send(f"🐉 Active companion changed.{chosen}", ephemeral=False)


# Every beast leaf asked for a number (v1.11.0): `/beast tame` wanted an
# encounter id and feed, train, evolve and active a beast id, each read off
# another page and typed by hand. Reported as "Tame ask for id when taming and
# each time you feed them". Each id is a picker now - on the panel through the
# hub option providers, and on the slash command through autocomplete over the
# same rows - so the number is still what the engine is sent and a player never
# has to know it.


def _beast_option(row: Any) -> HubDynamicOption:
    bonus = companion_bonus(row.get("rank"), row.get("evolution_stage"), row.get("loyalty"))
    return HubDynamicOption(
        label=f"{row['name']} ({row['species']})"[:100],
        value=int(row["beast_id"]),
        description=(f"Rank {row['rank']} • loyalty {row['loyalty']} • evolution {row['evolution_stage']} "
                     f"• {bonus:+d} in battle{' • active' if row.get('active') else ''}")[:100],
        emoji="⭐" if row.get("active") else "🐉",
    )


async def _beast_rows(interaction: discord.Interaction) -> list[Any]:
    try:
        return list(await DB.get_spirit_beasts(interaction.user.id))
    except Exception:
        log.warning("Could not load spirit beasts for a picker", exc_info=True)
        return []


async def beast_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    """Every contracted beast, the active one first."""
    rows = sorted(await _beast_rows(interaction), key=lambda row: (not row.get("active"), int(row["beast_id"])))
    return [_beast_option(row) for row in rows[:25]]


async def beast_inactive_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    """Only a beast that is not already the active companion."""
    rows = [row for row in await _beast_rows(interaction) if not row.get("active")]
    return [_beast_option(row) for row in rows[:25]]


async def encounter_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    """The subdued wild beasts waiting where the player stands."""
    try:
        c = await DB.get_character(interaction.user.id)
        if not c:
            return []
        wt = await current_world_time()
        rows = list(await DB.get_wild_beast_encounters(
            interaction.user.id, game_minute=wt.total_minutes, location=str(c.get("location", ""))))
    except Exception:
        log.warning("Could not load taming opportunities for a picker", exc_info=True)
        return []
    return [
        HubDynamicOption(
            label=f"{row['species']} • Rank {row['rank']}"[:100],
            value=int(row["encounter_id"]),
            description=(f"{row['element']} • {row['temperament']} • taming TN {row['taming_tn']} "
                         f"• leaves in {max(0, int(row['expires_game_minute']) - wt.total_minutes)} game min")[:100],
            emoji="🪢",
        )
        for row in rows[:25]
    ]


def _choices(options: list[HubDynamicOption], current: str) -> list[app_commands.Choice[int]]:
    needle = current.casefold().strip()
    return [
        app_commands.Choice(name=option.label[:100], value=int(option.value))
        for option in options
        if not needle or needle in option.label.casefold() or needle == str(option.value)
    ][:25]


async def beast_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    return _choices(await beast_hub_options(interaction, current), current)


async def beast_inactive_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    return _choices(await beast_inactive_hub_options(interaction, current), current)


async def encounter_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    return _choices(await encounter_hub_options(interaction, current), current)


beast_tame.autocomplete("encounter_id")(encounter_autocomplete)
for _command in (beast_feed, beast_train, beast_evolve):
    _command.autocomplete("beast_id")(beast_autocomplete)
beast_active.autocomplete("beast_id")(beast_inactive_autocomplete)

register_hub_option_provider(beast_tame, "encounter_id", encounter_hub_options)
for _command in (beast_feed, beast_train, beast_evolve):
    register_hub_option_provider(_command, "beast_id", beast_hub_options)
register_hub_option_provider(beast_active, "beast_id", beast_inactive_hub_options)

register_hub_option_hint(
    beast_tame,
    "encounter_id",
    "No subdued wild beast is waiting where you stand. An overwhelming **/hunt** victory can leave one to tame, "
    "and it waits only a while.",
)
for _command in (beast_feed, beast_train, beast_evolve):
    register_hub_option_hint(
        _command,
        "beast_id",
        "You have no contracted spirit beast yet. Tame one with **/beast → Companions → Tame** after a strong **/hunt**.",
    )
register_hub_option_hint(
    beast_active,
    "beast_id",
    "There is no other beast to make active: your only companion is already fighting beside you, or you have none yet - "
    "see them on **/beast → Companions → Status**.",
)

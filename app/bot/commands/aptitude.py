"""The /aptitude hub: spiritual roots, ancestral bloodlines and physiques.

Split phase 9a (v0.19.44, docs/history/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import discord
from discord import app_commands

from ...rules.aptitudes import bloodline_definition, root_compatibility
from ...ops.game_engine import GameEngineError
from ..formatting import roll_line
from ..registry import registered_group_command
from ..runtime import _explain_engine_error, DB, ENGINE, SETTINGS, WORLD, current_world_time, reply_long, require_character, serialized_user_action

aptitude_group = app_commands.Group(
    name="aptitude",
    description="Spiritual roots, ancestral bloodlines and special physiques",
)


APTITUDE_TARGET_CHOICES = [
    app_commands.Choice(name="Spiritual Root", value="root"),
    app_commands.Choice(name="Bloodline", value="bloodline"),
    app_commands.Choice(name="Physique", value="physique"),
]


AWAKEN_TARGET_CHOICES = [
    app_commands.Choice(name="Bloodline", value="bloodline"),
    app_commands.Choice(name="Physique", value="physique"),
]


def _root_summary(root: dict, character: dict) -> str:
    elements = "/".join(str(value) for value in root.get("elements", [character.get("spiritual_root", "Mortal Root")]))
    mutation_key = str(root.get("mutation") or "")
    mutation = WORLD.spiritual_root_system.get("mutations", {}).get(mutation_key, {}).get("name", mutation_key or "None")
    compatibility = root_compatibility(
        root.get("elements", []), str(character.get("path", "")), WORLD.spiritual_root_system, mutation_key,
    )
    return (
        f"🌿 **Spiritual Root**\n"
        f"Grade: **{root.get('grade','Common')}** • Elements: **{elements}**\n"
        f"Purity: **{int(root.get('purity',50))}%** • Stability: **{int(root.get('stability',100))}%**\n"
        f"Path compatibility: **{compatibility}%** • Refinement: **{int(root.get('refinement_progress',0))}%**\n"
        f"Mutation: **{mutation}**"
    )


def _bloodline_summary(bloodline: dict | None) -> str:
    if not bloodline:
        return "🩸 **Bloodline**\nNo recognized ancestral bloodline is carried by this incarnation."
    _, definition = bloodline_definition(bloodline, WORLD.bloodlines)
    stage = int(bloodline.get("evolution_stage", 0))
    evolutions = list(definition.get("evolutions", []))
    stage_name = evolutions[min(len(evolutions), max(1, stage)) - 1].get("name") if evolutions and stage else "Dormant Lineage"
    techniques = list(bloodline.get("unlocked_techniques") or [])
    return (
        f"🩸 **{bloodline.get('name','Ancestral Bloodline')}**\n"
        f"State: **{str(bloodline.get('state','dormant')).title()}** • Evolution: **{stage_name}**\n"
        f"Affinity: **{bloodline.get('affinity','None')}** • Purity: **{int(bloodline.get('purity',0))}%**\n"
        f"Progress: **{int(bloodline.get('progress',0))}%** • Rejection: **{int(bloodline.get('rejection',0))}%**\n"
        f"Mutation: **{bloodline.get('mutation') or 'None'}**\n"
        f"Ancestral techniques: **{', '.join(str(x) for x in techniques) if techniques else 'None awakened'}**"
    )


def _physique_summary(physique: dict | None) -> str:
    physique = physique or {}
    definition = WORLD.physiques.get(str(physique.get("physique_id", "ordinary_mortal_body")), {})
    stage = int(physique.get("evolution_stage", 0))
    evolutions = list(definition.get("evolutions", []))
    stage_name = evolutions[min(len(evolutions), max(1, stage)) - 1].get("name") if evolutions and stage else "Unawakened"
    return (
        f"💠 **{physique.get('name','Ordinary Mortal Body')}**\n"
        f"State: **{str(physique.get('state','ordinary')).title()}** • Evolution: **{stage_name}**\n"
        f"Progress: **{int(physique.get('progress',0))}%** • Stability: **{int(physique.get('stability',100))}%** • "
        f"Instability: **{int(physique.get('instability',0))}%**\n"
        f"Advantage: {definition.get('advantage','No innate special-body advantage.')}\n"
        f"Drawback: {definition.get('drawback','No innate special-body burden.')}"
    )


@registered_group_command(aptitude_group, name="status", description="Show your root, bloodline and physique together")
async def aptitude_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    bundle = await DB.get_aptitudes(interaction.user.id)
    await reply_long(
        interaction,
        "\n\n".join([
            _root_summary(bundle.get("root") or {}, c),
            _bloodline_summary(bundle.get("bloodline")),
            _physique_summary(bundle.get("physique")),
        ]),
        ephemeral=False,
    )


@registered_group_command(aptitude_group, name="root", description="Inspect spiritual-root grade, purity, elements and compatibility")
async def aptitude_root(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    bundle = await DB.get_aptitudes(interaction.user.id)
    await interaction.response.send_message(_root_summary(bundle.get("root") or {}, c), ephemeral=False)


@registered_group_command(aptitude_group, name="bloodline", description="Inspect bloodline awakening, purity, rejection and techniques")
async def aptitude_bloodline(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    bundle = await DB.get_aptitudes(interaction.user.id)
    await interaction.response.send_message(_bloodline_summary(bundle.get("bloodline")), ephemeral=False)


@registered_group_command(aptitude_group, name="physique", description="Inspect special-physique progression, advantages and drawbacks")
async def aptitude_physique(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    bundle = await DB.get_aptitudes(interaction.user.id)
    await interaction.response.send_message(_physique_summary(bundle.get("physique")), ephemeral=False)


@registered_group_command(aptitude_group, name="temper", description="Spend cultivation essence to progress an innate aptitude")
@app_commands.choices(target=APTITUDE_TARGET_CHOICES)
@serialized_user_action
async def aptitude_temper(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "aptitude.temper",
            interaction.user.id,
            {
                "target": target.value,
                
                "cooldown_seconds": max(300, SETTINGS.cultivate_cooldown_minutes * 60),
            },
            action_id=f"discord:{interaction.id}:aptitude.temper:{target.value}",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    bundle = dict(result.get("aptitudes") or {})
    record = dict(bundle.get(target.value) or {})
    progress_key = "refinement_progress" if target.value == "root" else "progress"
    progress = int(record.get(progress_key, 0))
    awarded = int(result.get("awarded", 0))
    cost = int(result.get("cost", 0))
    await interaction.followup.send(
        f"🔥 **{target.name} Tempering**\nSpent **{cost}** essence and gained **+{awarded}%** progress.\n"
        f"Progress: **{progress}% / 100%**"
        + ("\n✨ The aptitude is ready for its next awakening/evolution attempt." if progress >= 100 else "")
    )


@registered_group_command(aptitude_group, name="awaken", description="Attempt to awaken a prepared bloodline or physique")
@app_commands.choices(target=AWAKEN_TARGET_CHOICES)
@serialized_user_action
async def aptitude_awaken(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "aptitude.awaken",
            interaction.user.id,
            {"target": target.value},
            action_id=f"discord:{interaction.id}:aptitude.awaken:{target.value}",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    state = str(result.get("state") or "unknown")
    await interaction.followup.send(
        f"✨ **{target.name} Awakening**\n{roll_line(roll)}\n"
        + (
            f"The {target.name.lower()} awakens successfully. State: **{state.title()}**."
            if bool(getattr(roll, "success", False))
            else "Awakening failed. The persistent backlash has been recorded; use **/cultivation → Aptitudes → harmonize** before rejection or instability becomes severe."
        )
    )


@registered_group_command(aptitude_group, name="evolve", description="Attempt the next grade or ancestral evolution")
@app_commands.choices(target=APTITUDE_TARGET_CHOICES)
@serialized_user_action
async def aptitude_evolve(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "aptitude.evolve",
            interaction.user.id,
            {"target": target.value},
            action_id=f"discord:{interaction.id}:aptitude.evolve:{target.value}",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    outcome = dict(result.get("outcome") or {})
    if target.value == "root":
        outcome_text = f"Root grade: **{outcome.get('grade', 'Unknown')}** • Stability: **{int(outcome.get('stability', 0))}%**"
    elif target.value == "bloodline":
        outcome_text = (
            f"Stage: **{int(outcome.get('stage', 0))}** • Purity: **{int(outcome.get('purity', 0))}%** • "
            f"Rejection: **{int(outcome.get('rejection', 0))}%**"
        )
    else:
        outcome_text = (
            f"Stage: **{int(outcome.get('stage', 0))}** • Stability: **{int(outcome.get('stability', 0))}%** • "
            f"Instability: **{int(outcome.get('instability', 0))}%**"
        )
    await interaction.followup.send(
        f"🌌 **{target.name} Evolution**\n{roll_line(roll)}\n{outcome_text}\n"
        + ("The evolution succeeds." if bool(getattr(roll, "success", False)) else "The failure caused a persistent setback that must be harmonized or overcome.")
    )


@registered_group_command(aptitude_group, name="harmonize", description="Spend essence to reduce rejection or instability and restore stability")
@app_commands.choices(target=APTITUDE_TARGET_CHOICES)
@serialized_user_action
async def aptitude_harmonize(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "aptitude.harmonize",
            interaction.user.id,
            {
                "target": target.value,
                
                "cooldown_seconds": max(300, SETTINGS.cultivate_cooldown_minutes * 60),
            },
            action_id=f"discord:{interaction.id}:aptitude.harmonize:{target.value}",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    bundle = dict(result.get("aptitudes") or {})
    amount = int(result.get("amount", 0))
    cost = int(result.get("cost", 0))
    summary = (
        _root_summary(dict(bundle.get("root") or {}), c)
        if target.value == "root"
        else _bloodline_summary(bundle.get("bloodline"))
        if target.value == "bloodline"
        else _physique_summary(dict(bundle.get("physique") or {}))
    )
    await reply_long(
        interaction,
        f"☯️ Harmonization spent **{cost}** essence and restored **{amount}** points.\n\n{summary}",
        ephemeral=False,
    )



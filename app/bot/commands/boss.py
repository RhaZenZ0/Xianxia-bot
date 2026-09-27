"""The /boss and /hunter hubs: boss encounters and bounty-hunter pursuers."""

from __future__ import annotations

import re
from typing import Any

import discord
from discord import app_commands

from ...rules.advanced_runtime import BOSS_TEMPLATES, boss_encounter_phase, boss_lair
from ...rules.battle import vitality_bar
from ...ops.game_engine import GameEngineError
from ..cards import Card, card_view
from ..hubs import register_hub_option_hint
from ..registry import registered_group_command
from ..runtime import (
    _explain_engine_error,
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


_RAID_COLOURS = {"active": 0x8E44AD, "victory": 0x2ECC71, "defeat": 0xE74C3C}
_RAID_TITLES = {"active": "👹", "victory": "🏆", "defeat": "💀"}


def _mention_participants(text: str, participants: list[dict[str, Any]]) -> str:
    """The engine names a struck raider by user id; the card names them."""
    for participant in participants:
        uid = str(participant.get("user_id") or "")
        if uid:
            text = re.sub(rf"\b{uid}\b", f"<@{uid}>", text)
    return text


def raid_card(encounter: dict[str, Any], *, events: list[str] | None = None, note: str = "") -> Card:
    """The raid drawn as one card (v1.8.3): the boss's health, its phase,
    the round, and every raider's vitality and whether they have acted.

    Every number is read off the encounter row the engine wrote and the
    template it was started from; nothing here decides anything.
    """
    status = str(encounter.get("status") or "active")
    participants = list(encounter.get("participants") or [])
    template = BOSS_TEMPLATES.get(str(encounter.get("template_key")), {})
    phases = list(template.get("phases") or [])
    phase_index = min(max(0, int(encounter.get("phase_index") or 0)), max(0, len(phases) - 1))
    round_index = int(encounter.get("round_index") or 1)

    lines = [_mention_participants(str(event), participants) for event in list(events or [])[:12]]
    if note:
        lines.append(note)
    if not lines:
        lines.append(
            {
                "victory": "The boss has fallen. Every raider has a reward waiting.",
                "defeat": "The party has fallen. The boss keeps its lair.",
            }.get(status, "Each raider acts once a round; the boss answers when the whole party has acted.")
        )
    embed = Card(
        title=f"{_RAID_TITLES.get(status, '👹')} Raid #{encounter.get('encounter_id')} — {encounter.get('boss_name', 'Boss')}",
        description="\n".join(f"• {line}" if events else line for line in lines)[:4000],
        colour=_RAID_COLOURS.get(status, _RAID_COLOURS["active"]),
    )
    embed.add_field(
        name="Boss",
        value=vitality_bar(int(encounter.get("boss_hp") or 0), int(encounter.get("boss_hp_max") or 0)),
        inline=False,
    )
    if phases:
        phase = boss_encounter_phase(encounter)
        threshold = float(phase.get("threshold") or 0)
        shift = f"\n-# shifts below {round(threshold * 100)}% health" if status == "active" and threshold > 0 else ""
        embed.add_field(
            name="Phase",
            value=f"**{phase.get('name', 'Unknown')}** • {phase_index + 1}/{len(phases)}{shift}",
            inline=True,
        )
    embed.add_field(name="Round", value=f"**{round_index}**", inline=True)
    embed.add_field(name="Lair", value=str(encounter.get("location") or "Unknown"), inline=True)

    party: list[str] = []
    for participant in participants:
        if str(participant.get("status")) == "knocked_out":
            state = "💀 down"
        elif status != "active":
            state = "standing"
        elif int(participant.get("acted_round") or 0) >= round_index:
            state = "🛡️ guarding" if int(participant.get("guard") or 0) else "✅ acted"
        else:
            state = "⏳ to act"
        party.append(
            f"<@{participant.get('user_id')}> • {state} • dealt **{int(participant.get('total_damage') or 0)}**\n"
            f"{vitality_bar(int(participant.get('vitality') or 0), int(participant.get('vitality_max') or 0), width=8)}"
        )
    if party:
        shown: list[str] = []
        for entry in party:
            if len("\n".join([*shown, entry])) > 980:
                break
            shown.append(entry)
        if len(shown) < len(party):
            shown.append(f"-# and {len(party) - len(shown)} more raiders")
        embed.add_field(name=f"Raid Party ({len(participants)})", value="\n".join(shown), inline=False)

    if status == "victory":
        embed.set_footer(text=f"Claim your reward: /boss claim encounter_id:{encounter.get('encounter_id')}")
    elif status == "active":
        embed.set_footer(text="Act: Attack • Technique • Defend • Support — once each round")
    return embed


async def _send_raid_card(interaction: discord.Interaction, card: Card) -> None:
    # The party is named by mention; in an embed a mention never pinged, and
    # in a text display it would, so the card says who without calling them.
    quiet = discord.AllowedMentions.none()
    if interaction.response.is_done():
        await interaction.followup.send(view=card_view(card), allowed_mentions=quiet, ephemeral=False)
    else:
        await interaction.response.send_message(view=card_view(card), allowed_mentions=quiet, ephemeral=False)


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
        lair, realm_id = boss_lair(boss, WORLD.secret_realms)
        where = lair
        if realm_id:
            # A secret floor (v1.3.0): fought at the realm's entrance by a
            # party whose leader has walked the realm to its last room.
            where = f"{lair} — the floor beneath the {boss['location']}, open once you have walked that realm to its end"
        lines.append(
            f"\n`{key}` — **{boss['name']}** • {where} • every member at **{WORLD.realm_name(int(boss['realm_index']))}** or above"
            f" • {len(boss['phases'])} phases • base HP {boss['max_hp']}"
        )
    lines.append(
        "\n-# The party leader starts a raid with the whole party standing at the lair. "
        "Alone, just press Start: a party of one is formed for the raid and closed when it ends, "
        "and a boss fought alone has less health than one a party faces."
    )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(boss_group, name="start", description="Start a boss raid - as your party's leader, or alone")
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
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    # A lone cultivator is given a party of one for the raid (v1.7.8); the
    # engine says so, and closes it when the raid ends.
    alone = (
        "You face it alone: a party of one was formed for this raid and closes when the raid ends."
        if result.get("solo_party") else ""
    )
    encounter = await DB.get_boss_encounter(encounter_id=int(result.get("encounter_id") or 0)) if result.get("encounter_id") else None
    if not encounter:
        encounter = {**result, "boss_name": result.get("boss_name", "Boss"), "status": "active"}
    await _send_raid_card(interaction, raid_card(encounter, note=alone))


@boss_start.autocomplete("boss")
async def boss_template_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Every boss, the ones at the player's location first (v0.33.0). The
    engine refuses a boss fought from the wrong place; the label says where
    each one is so the refusal is never the first time a player learns it."""
    c = await DB.get_character(interaction.user.id)
    here = str(c.get("location") or "") if c else ""
    needle = current.casefold().strip()
    choices: list[tuple[int, str, app_commands.Choice[str]]] = []
    for key, boss in BOSS_TEMPLATES.items():
        name = str(boss.get("name", key))
        location = boss_lair(boss, WORLD.secret_realms)[0]
        if needle and needle not in name.casefold() and needle not in key.casefold() and needle not in location.casefold():
            continue
        marker = "here" if location == here else location
        realm = WORLD.realm_name(int(boss.get("realm_index") or 0))
        choices.append((0 if location == here else 1, name, app_commands.Choice(name=f"{name} — {marker}, {realm}+"[:100], value=key[:100])))
    return [choice for _, _, choice in sorted(choices, key=lambda row: row[:2])][:25]


register_hub_option_hint(
    boss_start,
    "boss",
    "No boss encounter is defined in the world content. **/combat → Boss Raids → List** shows the catalogue when there is one.",
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
    await _send_raid_card(interaction, raid_card(encounter))


@registered_group_command(boss_group, name="act", description="Take your once-per-round raid action; boss retaliates after the full party acts")
@app_commands.choices(style=[app_commands.Choice(name="Attack", value="attack"), app_commands.Choice(name="Technique", value="technique"), app_commands.Choice(name="Defend", value="guard"), app_commands.Choice(name="Support", value="support")])
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
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    # The card is drawn from the row the action just wrote, read by id
    # because a finished raid is no longer the party's active one.
    after = await DB.get_boss_encounter(encounter_id=int(result.get("encounter_id") or encounter["encounter_id"]))
    card = {**encounter, **result, **(after or {})}
    card["status"] = str(result.get("status") or card.get("status") or "active")
    await _send_raid_card(interaction, raid_card(card, events=[str(e) for e in list(result.get("events") or [])]))


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
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
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
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    # The trail is the engine's word for what the hunter is following - what
    # the cultivator is still carrying that carries somebody's mark. It is read
    # off the action result rather than computed here: the rule is Go's.
    trail = f"\nTrail **{int(result.get('trail') or 0)}%** — {result.get('trail_word', '')}" if result.get("trail_word") else ""
    await interaction.followup.send(
        f"🎯 **{result.get('hunter_name', 'Hunter')}** • status **{result.get('status', 'active')}** • pressure {result.get('pressure', 0)}% • escape {result.get('escape_progress', 0)}% • capture {result.get('capture_progress', 0)}%"
        + trail,
        ephemeral=False,
    )

"""The /equipment hub: durable gear, loadout state, repairs and bind flow.

This split keeps the command-group registration in the bot bootstrap while moving
all equipment-specific handlers into their own module.
"""

from __future__ import annotations

from collections.abc import Mapping

import discord
from discord import app_commands

from ...rules.advanced_runtime import (
    describe_equipment,
    describe_equipment_in_full,
    equipment_definition,
    equipment_totals_line,
)
from ...ops.game_engine import GameEngineError
from ..hubs import HubDynamicOption, register_hub_option_hint, register_hub_option_provider
from ..registry import registered_group_command
from ..runtime import (
    _explain_engine_error,
    DB,
    ENGINE,
    WORLD,
    current_world_time,
    log,
    require_character,
    reply_long,
    serialized_user_action,
)


equipment_group = app_commands.Group(
    name="equipment",
    description="Bind, equip, repair and inspect durable combat equipment",
)


def _gear_name(item_id: object) -> str:
    """A piece of gear's name at its grade ("Spirit-Iron Sword (High)")."""
    named = str(WORLD.item_definition(str(item_id)).get("name") or "")
    return named or str(equipment_definition(item_id).get("name") or WORLD.item_name(str(item_id)))


@registered_group_command(equipment_group, name="status", description="Inspect your persistent equipment loadout and durability")
async def equipment_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_equipment(interaction.user.id)
    if not rows:
        await interaction.response.send_message(
            "🛡️ No equipment has been bound yet. Use **/items → Equipment → Bind** on a supported carried item.",
            ephemeral=False,
        )
        return
    bonus = dict(await ENGINE.action("equipment.power", interaction.user.id, {}) or {})
    # The totals are the engine's own answer; the line only says what they do.
    # Spirit is carried by every piece and read by no rule, so it is not shown.
    totals = equipment_totals_line(bonus) or "No bonuses from what you have equipped."
    lines = [f"🛡️ **Equipment — {c['name']}**", f"**In a fight** (equipped gear):\n{totals}"]
    for row in rows:
        # Every piece says what it is and what its passive does (v1.9.1), not
        # only its numbers.
        description = describe_equipment_in_full(
            row["item_id"],
            description=str(WORLD.item_definition(str(row["item_id"])).get("description") or ""),
            quality=row["quality"],
            durability=row["durability"],
            max_durability=row["max_durability"],
            ladder=WORLD.item_grades,
        )
        lines.append(
            f"\n{'✅' if row['equipped'] else '▫️'} `#{row['equipment_id']}` **{_gear_name(row['item_id'])}** • {row['slot']} • quality {row['quality']}%"
            f"\n{description}"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(equipment_group, name="bind", description="Convert one carried equipment item into a persistent durable instance")
@serialized_user_action
async def equipment_bind(interaction: discord.Interaction, item: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "equipment.bind",
            interaction.user.id,
            {"item_id": item},
            action_id=f"discord:{interaction.id}:equipment.bind",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await interaction.followup.send(
        f"🧷 Bound **{WORLD.item_name(item)}** as equipment `#{result.get('equipment_id')}`.",
        ephemeral=False,
    )


@registered_group_command(equipment_group, name="equip", description="Equip a bound item; another item in the same slot is automatically unequipped")
@serialized_user_action
async def equipment_equip(interaction: discord.Interaction, equipment_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "equipment.equip",
            interaction.user.id,
            {"id": int(equipment_id)},
            action_id=f"discord:{interaction.id}:equipment.equip",
        )
        _ = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await interaction.followup.send(f"⚔️ Equipped {await _equipment_label(interaction.user.id, int(equipment_id))}.", ephemeral=False)


@registered_group_command(equipment_group, name="unequip", description="Remove a bound item from your active combat loadout")
@serialized_user_action
async def equipment_unequip(interaction: discord.Interaction, equipment_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "equipment.unequip",
            interaction.user.id,
            {"id": int(equipment_id)},
            action_id=f"discord:{interaction.id}:equipment.unequip",
        )
        _ = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await interaction.followup.send(f"🎒 Unequipped {await _equipment_label(interaction.user.id, int(equipment_id))}.", ephemeral=False)


@registered_group_command(equipment_group, name="repair", description="Restore equipment durability using Spirit Iron")
@serialized_user_action
async def equipment_repair(interaction: discord.Interaction, equipment_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    _ = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "equipment.repair",
            interaction.user.id,
            {"id": int(equipment_id)},
            action_id=f"discord:{interaction.id}:equipment.repair",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await interaction.followup.send(
        f"🔧 Repaired {await _equipment_label(interaction.user.id, int(equipment_id))} for **{int(result.get('repair_cost', 0))} Spirit Iron**.",
        ephemeral=False,
    )


# ---------------------------------------------------------------------------
# Equipment live options
# ---------------------------------------------------------------------------
# The player-facing hub providers live here rather than in the main startup file so
# the equipment group stays self-contained and easier to move in future refactors.
def _equipment_option(row: Mapping[str, object]) -> HubDynamicOption:
    name = _gear_name(row["item_id"])
    worn = int(row["max_durability"]) - int(row["durability"])
    description = describe_equipment(
        row["item_id"],
        quality=row["quality"],
        durability=row["durability"],
        max_durability=row["max_durability"],
        ladder=WORLD.item_grades,
    )
    return HubDynamicOption(
        label=f"{name} • {row['slot']}"[:100],
        value=int(row["equipment_id"]),
        description=f"#{row['equipment_id']} • {description}"[:100],
        emoji="✅" if row["equipped"] else ("🔧" if worn else "▫️"),
    )


async def _equipment_options(interaction: discord.Interaction, *, equipped: bool | None, damaged_first: bool = False) -> list[HubDynamicOption]:
    try:
        rows = list(await DB.get_equipment(interaction.user.id))
    except Exception:
        log.warning("Could not load equipment options", exc_info=True)
        return []
    if equipped is not None:
        rows = [row for row in rows if bool(row["equipped"]) is equipped]
    if damaged_first:
        rows.sort(key=lambda row: int(row["durability"]) - int(row["max_durability"]))
    return [_equipment_option(row) for row in rows[:25]]


async def equipment_equip_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    """Only unequipped items: equipping what is already equipped is a no-op."""
    return await _equipment_options(interaction, equipped=False)


async def equipment_unequip_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    return await _equipment_options(interaction, equipped=True)


async def equipment_repair_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    """Everything, most damaged first — that is what a player is looking for."""
    return await _equipment_options(interaction, equipped=None, damaged_first=True)


async def equipment_bind_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    """Carried items that are actually bindable equipment, with quantities."""
    try:
        inventory = await DB.get_inventory(interaction.user.id)
    except Exception:
        log.warning("Could not load inventory for bind options", exc_info=True)
        return []
    options: list[HubDynamicOption] = []
    for item_id, quantity in sorted(dict(inventory or {}).items()):
        if int(quantity or 0) <= 0:
            continue
        definition = equipment_definition(item_id)
        if not definition:
            continue
        description = describe_equipment(item_id, ladder=WORLD.item_grades)
        options.append(
            HubDynamicOption(
                label=f"{_gear_name(item_id)} x{int(quantity)}"[:100],
                value=str(item_id),
                description=f"{definition.get('slot', 'gear')} • {description}"[:100],
                emoji="🧷",
            )
        )
    return options[:25]


register_hub_option_provider(equipment_bind, "item", equipment_bind_hub_options)
register_hub_option_provider(equipment_equip, "equipment_id", equipment_equip_hub_options)
register_hub_option_provider(equipment_unequip, "equipment_id", equipment_unequip_hub_options)
register_hub_option_provider(equipment_repair, "equipment_id", equipment_repair_hub_options)

register_hub_option_hint(
    equipment_bind,
    "item",
    "You are not carrying anything that can be bound as equipment. Weapons, armour, "
    "boots and accessories can be bound — buy one with **/economy → Local Market → Buy**, or forge one with "
    "**/craft → General Crafting → Craft**.",
)
register_hub_option_hint(
    equipment_equip,
    "equipment_id",
    "Equip works on **bound** equipment, not on carried items. Run **Bind** on a carried "
    "weapon or armour first — or everything you have bound is already equipped.",
)
register_hub_option_hint(
    equipment_unequip,
    "equipment_id",
    "Nothing is equipped right now, so there is nothing to remove.",
)
register_hub_option_hint(
    equipment_repair,
    "equipment_id",
    "You have no bound equipment to repair. Run **Bind** on a carried item first.",
)


async def _equipment_label(user_id: int, equipment_id: int) -> str:
    """Equipped item #7 tells a player nothing. Name it when we can."""
    try:
        for row in await DB.get_equipment(user_id):
            if int(row["equipment_id"]) == int(equipment_id):
                return f"**{_gear_name(row['item_id'])}** `#{equipment_id}`"
    except Exception:
        log.warning("Could not resolve equipment label", exc_info=True)
    return f"item `#{equipment_id}`"

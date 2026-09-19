"""/admin player karma and grants, /admin sect, /admin world advancetime,
/admin server maintenance.

Phase 7 of the main.py split (v0.19.42, docs/history/MAIN_SPLIT_PLAN.md). Everything
here reads core, runtime, services, pickers and app.*; never main.py. The
three /admin world commands that open or close event scenes (events,
spawnrealm, closeevent) are NOT here: they read spawn_event_thread and the bot
instance, which stay in main.py until phase 8. Definition order is the order
these had in main.py.
"""
from __future__ import annotations

import time
from typing import Any

import discord
from discord import app_commands

from ...rules.advanced_catalog import GRADES
from ...rules.advanced_runtime import EQUIPMENT_DEFINITIONS
from ...rules.birthfamily import karma_label
from ...rules.game import World
from ...ops.game_engine import GameEngineError
from ...rules.worldtime import from_game_minutes
from ..formatting import human_duration
from ..hubs import HubDynamicOption, register_hub_option_provider
from ..pickers import auction_currency_autocomplete
from .. import maintenance
from ..registry import registered_group_command
from ..runtime import DB, ENGINE, SETTINGS, WORLD, _explain_engine_error, current_world_time, log, reply_long
from ..services import QUEST_FORGE, QUESTS, SIM
from ...ai.quest_forge import store_draft
from ...rules.quests import validate_quest_definition
from ..threads import ensure_sect_abode_record, ensure_sect_abode_thread_for
from ..bot import bot
from ..ui.event_scene import spawn_event_thread
from .core import admin_player_group, admin_sect_group, admin_server_group, admin_world_group, audit_admin, require_admin

@registered_group_command(admin_player_group, name="karma",description="Adjust a cultivator's canonical Good/Evil karma score")
async def admin_karma(interaction:discord.Interaction,member:discord.Member,amount:app_commands.Range[int,-1000,1000],reason:str="GM/world decision")->None:
    if not await require_admin(interaction):return
    if not await DB.get_character(member.id):
        await interaction.response.send_message("That member has no cultivation character.",ephemeral=False);return
    before_c = await DB.get_character(member.id)
    karma_result = dict(
        await ENGINE.action(
            "admin.player.karma",
            interaction.user.id,
            {"user_id": member.id, "delta": int(amount), "reason": reason[:200]},
        )
        or {}
    )
    score = int(karma_result.get("karma_score", 0))
    await audit_admin(
        interaction,
        "player.karma",
        target=f"user:{member.id}",
        before={"karma": int((before_c or {}).get("karma_score", 0))},
        after={"karma": score},
        reason=reason,
        database_log=False,
    )
    await interaction.response.send_message(f"☯️ {member.mention}: karma changed by **{int(amount):+d}** → **{score:+d} ({karma_label(score)})**.",ephemeral=False)


@registered_group_command(admin_sect_group, name="setsect", description="Assign a cultivator to a sect using the canonical hierarchy")
async def admin_setsect(
    interaction: discord.Interaction,
    member: discord.Member,
    sect_name: str,
    rank_name: str = "Outer Disciple",
) -> None:
    if not await require_admin(interaction):
        return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member must create a character first with /begin.", ephemeral=False)
        return
    rank = WORLD.sect_rank(rank_name)
    if not rank:
        await interaction.response.send_message(
            "Unknown rank. Use the canonical ladder: " + ", ".join(r["name"] for r in WORLD.sect_system.get("ranks", [])),
            ephemeral=False,
        )
        return
    old_membership = await DB.get_sect_membership(member.id)
    try:
        await ENGINE.action("admin.player.set_sect", interaction.user.id, {
            "user_id": member.id, "sect_name": sect_name,
            "rank_name": str(rank["name"]), "rank_level": int(rank["level"]),
            "reason": "discord admin",
        })
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    abode = await ensure_sect_abode_record(member.id, c, await DB.get_sect_membership(member.id) or {"sect_name": sect_name, "rank_name": str(rank["name"])})
    abode_thread = await ensure_sect_abode_thread_for(interaction.guild, member, abode) if interaction.guild else None
    await audit_admin(interaction, "sect.assign", target=f"user:{member.id}", before=old_membership or {}, after={"sect_name": sect_name, "rank_name": str(rank["name"]), "rank_level": int(rank["level"])}, database_log=False)
    await interaction.response.send_message(
        f"✅ **{c['name']}** is now recorded in **{sect_name}** as **{rank['name']}** (rank level {rank['level']})."
        + (f"\n🏯 Sect abode: {abode_thread.mention}" if abode_thread else ""),
        ephemeral=False,
    )


async def admin_sect_name_hub_options(
    interaction: discord.Interaction, current: str
) -> list[HubDynamicOption]:
    needle = current.casefold().strip()
    options: list[HubDynamicOption] = []
    for sect_name, data in dict(WORLD.data.get("sects", {})).items():
        label = str(sect_name)
        searchable = f"{label} {data.get('alignment','')} {data.get('specialty','')}".casefold()
        if needle and needle not in searchable:
            continue
        details = " • ".join(
            part for part in (str(data.get("alignment") or ""), str(data.get("specialty") or "")) if part
        )
        options.append(HubDynamicOption(label=label[:100], value=label, description=details[:100], emoji="🏯"))
    return options[:25]


register_hub_option_provider(admin_setsect, "sect_name", admin_sect_name_hub_options)


@registered_group_command(admin_sect_group, name="removesect", description="Remove a cultivator from their recorded sect and clear their lineage links")
async def admin_removesect(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await require_admin(interaction):
        return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member has no character.", ephemeral=False)
        return
    old_membership = await DB.get_sect_membership(member.id)
    try:
        await ENGINE.action("admin.player.set_sect", interaction.user.id, {
            "user_id": member.id, "remove": True, "reason": "discord admin",
        })
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await audit_admin(interaction, "sect.remove", target=f"user:{member.id}", before=old_membership or {}, database_log=False)
    await interaction.response.send_message(
        f"✅ Removed **{c['name']}** from their recorded sect and cleared attached lineage links.", ephemeral=False
    )


@registered_group_command(admin_sect_group, name="setmaster", description="Set one player character as another character's Shifu")
async def admin_setmaster(
    interaction: discord.Interaction, disciple: discord.Member, master: discord.Member
) -> None:
    if not await require_admin(interaction):
        return
    dc = await DB.get_character(disciple.id)
    mc = await DB.get_character(master.id)
    if not dc or not mc:
        await interaction.response.send_message("Both members must have cultivation characters.", ephemeral=False)
        return
    try:
        await ENGINE.action("admin.player.set_master", interaction.user.id, {
            "disciple_user_id": disciple.id, "master_user_id": master.id, "reason": "discord admin",
        })
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await audit_admin(interaction, "sect.setmaster", target=f"user:{disciple.id}", after={"master_user_id": master.id}, database_log=False)
    await interaction.response.send_message(
        f"✅ **{mc['name']}** is now the recorded **Master** of **{dc['name']}**.", ephemeral=False
    )


@registered_group_command(admin_sect_group, name="clearmaster", description="Remove a cultivator's direct master relationship")
async def admin_clearmaster(interaction: discord.Interaction, disciple: discord.Member) -> None:
    if not await require_admin(interaction):
        return
    c = await DB.get_character(disciple.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False)
        return
    try:
        await ENGINE.action("admin.player.set_master", interaction.user.id, {
            "disciple_user_id": disciple.id, "clear": True, "reason": "discord admin",
        })
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await audit_admin(interaction, "sect.clearmaster", target=f"user:{disciple.id}", database_log=False)
    await interaction.response.send_message(
        f"✅ Cleared the direct Master relationship for **{c['name']}**.", ephemeral=False
    )


async def sect_rank_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip();out=[]
    for rank in WORLD.sect_system.get("ranks",[]):
        name=str(rank.get("name","Disciple"))
        if not needle or needle in name.casefold():out.append(app_commands.Choice(name=name[:100],value=name[:100]))
    return out[:25]


register_hub_option_provider(admin_setsect, "rank_name", sect_rank_autocomplete)


@registered_group_command(admin_sect_group, name="sectrank",description="Promote or demote a recorded sect member within the rigid hierarchy")
@app_commands.autocomplete(rank=sect_rank_autocomplete)
async def admin_sect_rank(interaction:discord.Interaction,member:discord.Member,rank:str)->None:
    if not await require_admin(interaction):return
    membership=await DB.get_sect_membership(member.id)
    if not membership:
        await interaction.response.send_message("That cultivator is not in a recorded sect.",ephemeral=False);return
    rank_def=WORLD.sect_rank(rank)
    if not rank_def:
        await interaction.response.send_message("Unknown canonical sect rank.",ephemeral=False);return
    before_rank = await DB.get_sect_membership(member.id)
    try:
        await ENGINE.action("admin.player.set_sect_rank", interaction.user.id, {
            "user_id": member.id, "rank_name": str(rank_def['name']),
            "rank_level": int(rank_def['level']), "reason": "discord admin",
        })
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await audit_admin(interaction, "sect.rank", target=f"user:{member.id}", before=before_rank or {}, after={"rank_name": str(rank_def['name']), "rank_level": int(rank_def['level'])}, database_log=False)
    await interaction.response.send_message(f"✅ {member.mention} is now **{rank_def['name']}** (level {rank_def['level']}).",ephemeral=False)


@registered_group_command(admin_sect_group, name="masterattention",description="Adjust how much attention a master currently gives a disciple")
async def admin_master_attention(interaction:discord.Interaction,disciple:discord.Member,amount:app_commands.Range[int,-100,100])->None:
    if not await require_admin(interaction):return
    try:
        result = dict(await ENGINE.action("admin.player.master_attention", interaction.user.id, {
            "disciple_user_id": disciple.id, "delta": int(amount), "reason": "discord admin",
        }) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    value = int(result.get("attention", 0))
    await audit_admin(interaction, "sect.masterattention", target=f"user:{disciple.id}", after={"attention": value, "delta": int(amount)}, database_log=False)
    await interaction.response.send_message(f"✅ Master attention for {disciple.mention}: **{value}**.",ephemeral=False)


@registered_group_command(admin_player_group, name="grantstorage",description="Grant or replace a cultivator's spatial storage container")
@app_commands.choices(grade=[app_commands.Choice(name=grade, value=grade) for grade in GRADES])
async def admin_grant_storage(
    interaction:discord.Interaction,member:discord.Member,name:str,grade:app_commands.Choice[str]|None=None,
    slots:app_commands.Range[int,1,5000]=80,living_space:bool=False
)->None:
    if not await require_admin(interaction):return
    grade_name = grade.value if grade else "Earth"
    try:
        result = dict(await ENGINE.action("admin.player.grant_storage", interaction.user.id, {
            "user_id": member.id, "container_id": name.casefold().replace(' ', '_'), "name": name,
            "grade": grade_name, "slot_capacity": int(slots), "living_space": living_space,
            "reason": "discord admin",
        }) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    # The engine refuses to shrink a container below what is already inside it,
    # so the granted capacity is not always the one that was asked for. Report
    # what the cultivator actually has.
    granted = int(result.get("slot_capacity", slots))
    await audit_admin(interaction, "player.grantstorage", target=f"user:{member.id}", after={"name": name, "grade": grade_name, "slots": granted, "living_space": living_space}, database_log=False)
    floor_note = f" (raised from {slots} to fit what is already stored)" if granted != int(slots) else ""
    await interaction.response.send_message(f"✅ Granted **{name}** ({grade_name}, {granted} stacks{floor_note}, living space: {living_space}) to {member.mention}.",ephemeral=False)


@registered_group_command(admin_player_group, name="grantcurrency",description="Grant cultivation currency for events, testing or GM rewards")
@app_commands.autocomplete(currency=auction_currency_autocomplete)
async def admin_grant_currency(interaction:discord.Interaction,member:discord.Member,currency:str,amount:app_commands.Range[int,1,2000000000])->None:
    if not await require_admin(interaction):return
    if currency not in WORLD.currencies:
        await interaction.response.send_message("Unknown currency.",ephemeral=False);return
    if not await DB.get_character(member.id):
        await interaction.response.send_message("That member has no cultivation character.",ephemeral=False);return
    currency_result = dict(
        await ENGINE.action(
            "admin.player.grant_currency",
            interaction.user.id,
            {
                "user_id": member.id,
                "currency_id": currency,
                "amount": int(amount),
                "reason": "discord admin",
            },
        )
        or {}
    )
    balance = int(currency_result.get("balance", 0))
    await audit_admin(
        interaction,
        "player.grantcurrency",
        target=f"user:{member.id}",
        after={"currency": currency, "amount": int(amount), "balance": balance},
        database_log=False,
    )
    await interaction.response.send_message(f"✅ Granted **{amount:,} {WORLD.currency_name(currency)}**. New balance: **{balance:,}**.",ephemeral=False)


ADMIN_GRANT_KIND_CHOICES = [
    app_commands.Choice(name="Item", value="item"),
    app_commands.Choice(name="Currency", value="currency"),
]


async def admin_grant_target_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    kind = str(getattr(interaction.namespace, "kind", "item") or "item")
    needle = current.casefold().strip()
    if kind == "currency":
        return await auction_currency_autocomplete(interaction, current)
    options: list[app_commands.Choice[str]] = []
    for item_id, item in WORLD.items.items():
        name = str(item.get("name", item_id))
        if needle and needle not in item_id.casefold() and needle not in name.casefold():
            continue
        options.append(app_commands.Choice(name=name[:100], value=str(item_id)[:100]))
    return options[:25]


@registered_group_command(
    admin_player_group,
    name="grant",
    description="Grant an item or currency to a player's character",
)
@app_commands.choices(kind=ADMIN_GRANT_KIND_CHOICES)
@app_commands.autocomplete(target=admin_grant_target_autocomplete)
async def admin_grant(
    interaction: discord.Interaction,
    member: discord.Member,
    kind: app_commands.Choice[str],
    target: str,
    amount: app_commands.Range[int, 1, 2000000000],
    reason: str = "GM grant",
) -> None:
    if not await require_admin(interaction):
        return
    if not await DB.get_character(member.id):
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False)
        return
    operation = "admin.player.grant_currency" if kind.value == "currency" else "admin.player.adjust_item"
    payload: dict[str, Any] = {
        "user_id": member.id,
        "reason": reason[:200],
    }
    if kind.value == "currency":
        if target not in WORLD.currencies:
            await interaction.response.send_message("Unknown currency.", ephemeral=False)
            return
        payload.update({"currency_id": target, "amount": int(amount)})
    else:
        if target not in WORLD.items:
            await interaction.response.send_message("Unknown item.", ephemeral=False)
            return
        # v0.19.32: one-of-a-kind reward equipment (the Bugslayer Sword) is
        # flagged "unique" in EQUIPMENT_DEFINITIONS. The Go engine's
        # adjust_item is a plain signed delta and knows nothing about
        # uniqueness, so the guard lives here at the only grant entry point.
        definition = EQUIPMENT_DEFINITIONS.get(target, {})
        if definition.get("unique"):
            if int(amount) != 1:
                await interaction.response.send_message(
                    f"**{WORLD.item_name(target)}** is unique and can only be granted one at a time.",
                    ephemeral=False,
                )
                return
            inventory = await DB.get_inventory(member.id)
            equipment = await DB.get_equipment(member.id)
            already_owned = int(inventory.get(target, 0) or 0) > 0 or any(
                str(row.get("item_id")) == target for row in equipment
            )
            if already_owned:
                await interaction.response.send_message(
                    f"⚔️ {member.mention} already owns the unique **{WORLD.item_name(target)}**.",
                    ephemeral=False,
                )
                return
        payload.update({"item_id": target, "quantity": int(amount)})
    try:
        result = dict(await ENGINE.action(operation, interaction.user.id, payload) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    label = WORLD.currency_name(target) if kind.value == "currency" else WORLD.item_name(target)
    balance = result.get("balance") if kind.value == "currency" else result.get("quantity")
    suffix = "balance" if kind.value == "currency" else "carried"
    # The Go handler audits itself (admin_audit_log); this is the Discord-side
    # audit trail, mirroring grantcurrency/grantstorage.
    await audit_admin(
        interaction,
        "player.grant",
        target=f"user:{member.id}",
        after={"kind": kind.value, "target": target, "amount": int(amount), suffix: int(balance or 0)},
        reason=reason[:200],
        database_log=False,
    )
    passive_name = str(EQUIPMENT_DEFINITIONS.get(target, {}).get("passive_name") or "").strip()
    passive_line = f"\n✨ Passive: **{passive_name}**" if kind.value == "item" and passive_name else ""
    await interaction.response.send_message(
        f"✅ Granted **{int(amount):,} {label}** to {member.mention}. New {suffix}: **{int(balance or 0):,}**.{passive_line}",
        ephemeral=False,
    )


@registered_group_command(admin_world_group, name="advancetime",description="Advance the canonical in-world clock")
async def admin_advance_time(
    interaction: discord.Interaction,
    minutes: app_commands.Range[int, 1, 525600],
    scale: app_commands.Range[int, 0, 60] | None = None,
) -> None:
    """Move the clock, and only change its rate when asked to (v1.0.0-rc.39).

    This used to send `SETTINGS.world_time_scale` on every call, so a GM who
    had slowed or frozen the world on the dashboard had that undone by the next
    unrelated `/admin world advancetime`. The engine leaves the stored scale
    alone when the payload carries none; `scale: 0` freezes the world.
    """
    if not await require_admin(interaction):return
    payload: dict[str, Any] = {"minutes": int(minutes), "reason": "discord admin"}
    if scale is not None:
        payload["scale"] = int(scale)
    time_result = dict(await ENGINE.action("admin.world.advance_time", interaction.user.id, payload) or {})
    new_minute = int(time_result.get("game_minute", 0))
    after: dict[str, Any] = {"minutes": int(minutes), "new_game_minute": new_minute}
    if scale is not None:
        after["scale"] = int(scale)
    await audit_admin(
        interaction,
        "world.advancetime",
        target="world_clock",
        after=after,
        database_log=False,
    )
    wt=from_game_minutes(new_minute)
    rate = ""
    if scale == 0:
        rate = "\nThe world clock is now **stopped**."
    elif scale is not None:
        rate = f"\nRate: **{int(scale)} game minutes per real minute**."
    await interaction.response.send_message(f"🕰️ Advanced world time by **{minutes:,} minutes**.\nNow: **{wt.display}**{rate}",ephemeral=False)


@registered_group_command(admin_server_group, name="lockdown",
                         description="Close the world for maintenance, or open it again")
async def admin_lockdown(
    interaction: discord.Interaction,
    enabled: bool,
    reason: app_commands.Range[str, 0, 300] | None = None,
) -> None:
    """Bolt the doors while the server is updated (v1.0.0-rc.41).

    Named `lockdown` rather than `maintenance` because `/admin server
    maintenance` already exists and is a different thing entirely: cleanup,
    VACUUM and the content resync, none of which stop anybody playing.

    The reason is shown to players verbatim in every refusal, so it is worth
    writing as a sentence they would want to read.
    """
    if not await require_admin(interaction):
        return
    before = await DB.get_maintenance_mode()
    result = dict(
        await ENGINE.action(
            "admin.server.maintenance_mode",
            interaction.user.id,
            {"enabled": bool(enabled), "reason": str(reason or "")},
        )
        or {}
    )
    # The engine is the source of truth and has just written it; taking the
    # result straight into this process's cache means the very next command
    # is refused (or allowed) without waiting for a TTL to lapse.
    maintenance.remember(result)
    await audit_admin(
        interaction,
        "server.lockdown",
        target="maintenance_mode",
        before={"enabled": bool(before.get("enabled"))},
        after={"enabled": bool(result.get("enabled"))},
        database_log=False,
    )
    if result.get("enabled"):
        note = f"\n> {result.get('reason')}" if result.get("reason") else ""
        await interaction.response.send_message(
            "🔧 **The world is closed.** Players are refused at every door — slash commands, "
            "hub panels and typed lines — and the scheduled world tick has stood down. "
            f"Administrators are unaffected.{note}",
            ephemeral=False,
        )
        return
    await interaction.response.send_message(
        "✅ **The world is open again.** Players may act, and the world tick resumes "
        "from where it stood down.",
        ephemeral=False,
    )


MAINTENANCE_CHOICES=[
    app_commands.Choice(name="Cleanup expired data",value="cleanup"),
    app_commands.Choice(name="Vacuum database",value="vacuum"),
    app_commands.Choice(name="Sync world catalog",value="sync"),
]


@registered_group_command(admin_server_group, name="maintenance",description="Run safe database/content maintenance")
@app_commands.choices(action=MAINTENANCE_CHOICES)
async def admin_maintenance(interaction:discord.Interaction,action:app_commands.Choice[str])->None:
    if not await require_admin(interaction):return
    await interaction.response.defer(ephemeral=False)
    if action.value=="sync":
        # Until schema 51 this wrote `WORLD.data` - this process's in-memory
        # copy, parsed at import - and then reported that it had resynced
        # from world.json, which it had not: no edit to the file could reach
        # the database without a restart. It re-reads the file now, on both
        # sides. Since v1.0.0-rc.40 the catalogue itself is entirely the
        # engine's: `admin.content.reload` runs the hash-gated apply into
        # content_* and puts the audit row down, and Python's half of the
        # re-read is the map the file implies - a territory node per location
        # - which was never a mirror of anything. (The running WORLD is left
        # alone either way: a hot swap of the dict 347 call sites read is not
        # a maintenance action.) The message says what is and is not live,
        # rather than what the operator hoped.
        fresh = World(WORLD.content_path)
        await DB.seed_world_territories(fresh.data)
        try:
            reload = dict(await ENGINE.action("admin.content.reload", interaction.user.id, {"reason": "admin maintenance sync"}) or {})
        except GameEngineError as exc:
            await interaction.followup.send(
                "⚠️ the territory map was reseeded from a fresh read of world.json, but the engine refused to reload its content tables: "
                + _explain_engine_error(exc), ephemeral=False)
            return
        counts = {str(k): int(v) for k, v in dict(reload.get("counts") or {}).items()}
        verdict = "changed - the tables were rewritten" if reload.get("applied") else ("skipped - the content tables are not migrated" if reload.get("skipped") else "unchanged since the last apply")
        await interaction.followup.send(
            f"✅ world.json re-read from disk. Engine content hash `{str(reload.get('hash') or '')[:12]}`: {verdict}"
            + (f" ({sum(counts.values()):,} rows across {len(counts)} tables)" if counts else "") + ".\n"
            "Go-owned rules and the content tables serve the new file now; this bot's in-process presentation applies it at its next restart.",
            ephemeral=False)
        return
    if action.value=="vacuum":
        await DB.vacuum();await interaction.followup.send("✅ SQLite VACUUM completed.",ephemeral=False);return
    wt=await current_world_time(); counts=await DB.maintenance_cleanup(wt.total_minutes)
    await interaction.followup.send(
        "🧹 **Maintenance cleanup complete**\n"+"\n".join(f"• {k}: {v} rows" for k,v in counts.items()),ephemeral=False
    )




# ---------------------------------------------------------------------------
# /admin world events, spawnrealm, closeevent - moved here in split phase 8
# (v0.19.43), once spawn_event_thread (ui/event_scene.py) and the bot instance
# (bot.py) were importable from below.
# ---------------------------------------------------------------------------
@registered_group_command(admin_world_group, name="events", description="List categorized active world events with keys and locations")
async def admin_events(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    events = await DB.get_active_world_events()
    if not events:
        await interaction.response.send_message("No server-wide events are active.", ephemeral=False)
        return
    now = time.time()
    lines = ["🌌 **Active Admin Event List**"]
    for event in events:
        thread_text = f"<#{event['thread_id']}>" if event.get("thread_id") else "none"
        category=str(event.get("payload",{}).get("category") or event["event_type"])
        lines.append(
            f"\n\n**{event['title']}**\n"
            f"Key: `{event['event_key']}`\n"
            f"Type: `{event['event_type']}` • Category: **{category}** • Location: **{event['location']}**\n"
            f"Closes in {human_duration(int(event['ends_at'] - now))}\n"
            f"Thread: {thread_text}"
        )
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_group_command(admin_world_group, name="spawnrealm", description="Open a secret realm immediately for testing or GM events")
async def admin_spawnrealm(interaction: discord.Interaction, realm: str) -> None:
    if not await require_admin(interaction):
        return
    realm_id = None
    wanted = realm.strip().casefold()
    for rid, info in WORLD.secret_realms.items():
        if wanted in {rid.casefold(), str(info.get("name", "")).casefold()}:
            realm_id = rid
            break
    if realm_id is None:
        await interaction.response.send_message(
            "Unknown secret realm. Try one of: " + ", ".join(r["name"] for r in WORLD.secret_realms.values()),
            ephemeral=False,
        )
        return

    info = WORLD.secret_realms[realm_id]
    # The engine mints the event key and the closing time: both are derived
    # from when the spawn actually commits, which Python does not know.
    try:
        result = dict(await ENGINE.action("admin.world.spawn_realm", interaction.user.id, {
            "realm_id": realm_id,
            "title": info["name"],
            "location": info["location"],
            "open_hours": int(info.get("open_hours", 8)),
            "reason": "discord admin",
        }) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    event_key = str(result.get("event_key", ""))
    ends_at = float(result.get("ends_at", 0.0))
    await audit_admin(interaction, "event.spawnrealm", target=event_key, after={"realm_id": realm_id, "ends_at": ends_at}, database_log=False)
    await interaction.response.defer(ephemeral=False)
    thread = await spawn_event_thread(
        interaction,
        title=info["name"],
        event_type="secret_realm",
        event_key=event_key,
        expires_at=ends_at,
        announcement=(
            f"🌀 **SECRET REALM OPENED — {info['name']}**\n"
            f"📍 **{info['location']}**\n{info['description']}\n\n"
            f"Opened by an administrator. The entrance closes <t:{int(ends_at)}:R>."
        ),
    )
    await interaction.followup.send(
        f"✅ Opened **{info['name']}**. " + (f"Scene: {thread.mention}" if thread else "The realm opened, but no thread could be created."),
        ephemeral=False,
    )


@admin_spawnrealm.autocomplete("realm")
async def admin_spawnrealm_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    needle = current.casefold().strip()
    choices = []
    for rid, info in WORLD.secret_realms.items():
        name = str(info["name"])
        if not needle or needle in name.casefold() or needle in rid.casefold():
            choices.append(app_commands.Choice(name=name[:100], value=rid[:100]))
    return choices[:25]


@registered_group_command(admin_world_group, name="closeevent", description="Close an active event/secret realm immediately")
async def admin_closeevent(interaction: discord.Interaction, event_key: str) -> None:
    if not await require_admin(interaction):
        return
    record = await DB.get_event_thread_by_key(event_key.strip())
    if not record:
        await interaction.response.send_message(
            "No active event thread was found for that key. Use **/admin → World → Events** first.", ephemeral=False
        )
        return
    await interaction.response.defer(ephemeral=False)
    await bot.close_event_scene(record, manual=True)
    await audit_admin(interaction, "event.close", target=event_key.strip(), before={"title": record.get("title"), "event_type": record.get("event_type")})
    await interaction.followup.send(f"🔒 Closed **{record['title']}** and archived its thread.", ephemeral=False)


@admin_closeevent.autocomplete("event_key")
async def admin_closeevent_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    events = await DB.get_active_world_events()
    needle = current.casefold().strip()
    choices: list[app_commands.Choice[str]] = []
    for event in events:
        if not event.get("thread_id"):
            continue
        key = str(event["event_key"])
        label = f"{event['title']} — {event['event_type']}"
        if not needle or needle in key.casefold() or needle in label.casefold():
            choices.append(app_commands.Choice(name=label[:100], value=key[:100]))
    return choices[:25]


async def admin_closeevent_hub_options(
    interaction: discord.Interaction, current: str
) -> list[HubDynamicOption]:
    """Rich live dropdown entries for Admin → World → Close Event."""
    events = await DB.get_active_world_events()
    needle = current.casefold().strip()
    now = time.time()
    options: list[HubDynamicOption] = []
    for event in events:
        if not event.get("thread_id"):
            continue
        key = str(event["event_key"])
        title = str(event["title"])
        event_type = str(event["event_type"])
        location = str(event.get("location") or "Unknown location")
        searchable = f"{title} {event_type} {location} {key}".casefold()
        if needle and needle not in searchable:
            continue
        remaining = max(0, int(float(event["ends_at"]) - now))
        options.append(
            HubDynamicOption(
                label=title[:100],
                value=key,
                description=f"{event_type} • {location} • closes in {human_duration(remaining)}"[:100],
                emoji="🌌" if event_type != "secret_realm" else "🌀",
            )
        )
    return options[:25]


register_hub_option_provider(admin_closeevent, "event_key", admin_closeevent_hub_options)


# ---------------------------------------------------------------------------
# Quest Forge (v0.20.6): draft a quest from a story, approve or discard it.
# ---------------------------------------------------------------------------
def _quest_draft_embed(row: dict[str, Any]) -> discord.Embed:
    status = str(row.get("status", "draft"))
    colour = {"draft": 0xC9A227, "approved": 0x2E8B57, "retired": 0x777777, "discarded": 0x8B2E2E}.get(status, 0x777777)
    embed = discord.Embed(title=f"📜 {row['title']}", description=str(row.get("description", ""))[:1500], colour=colour)
    objectives = "\n".join(
        f"▫️ {obj.get('label', obj.get('id'))} ×{int(obj.get('count', 1))}"
        + (f" — `{obj['target']}`" if obj.get("target") else "")
        for obj in row.get("objectives") or []
    ) or "—"
    embed.add_field(name="Objectives", value=objectives[:1000], inline=False)
    rewards = row.get("rewards") or {}
    parts = []
    if rewards.get("insight_xp"):
        parts.append(f"✨ {rewards['insight_xp']} Insight XP")
    if rewards.get("spirit_stones"):
        parts.append(f"🪙 {rewards['spirit_stones']} spirit stones")
    for item_id, qty in dict(rewards.get("items") or {}).items():
        parts.append(f"🎁 {item_id} ×{qty}")
    embed.add_field(name="Rewards", value=", ".join(parts) or "none", inline=False)
    # v0.24.0: Approve and Discard are still the whole vocabulary here, and that
    # is now a deliberate limit rather than the only one there is. A draft that
    # is nearly right no longer has to be thrown away and re-rolled - it can be
    # fixed on the Quests page - so the embed says where, instead of leaving a
    # GM to guess that discarding is their only option.
    embed.add_field(
        name="Not quite right?",
        value="Open **Quests** in the GM dashboard to edit the objectives, targets and rewards, then approve it there.",
        inline=False,
    )
    embed.set_footer(text=f"{row['quest_key']} • {status} • {row.get('origin', '')} • {row.get('model') or 'procedural'}")
    return embed


class QuestDraftReviewView(discord.ui.View):
    """Approve / Discard for one draft; admin-gated like every /admin surface."""

    def __init__(self, quest_key: str) -> None:
        super().__init__(timeout=900)
        self.quest_key = quest_key

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await require_admin(interaction)

    async def _set(self, interaction: discord.Interaction, status: str, verb: str) -> None:
        row = await DB.get_quest_definition(self.quest_key)
        if row is None:
            await interaction.response.send_message("That draft no longer exists.", ephemeral=False)
            return
        await DB.set_quest_definition_status(self.quest_key, status, reviewed_by=interaction.user.id)
        await QUESTS.catalog(refresh=True)
        await audit_admin(interaction, f"quest.{status}", target=self.quest_key, before={"status": row.get("status")}, after={"status": status})
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"{verb} **{row['title']}** (`{self.quest_key}`).", embed=_quest_draft_embed({**row, "status": status}), view=self)

    @discord.ui.button(label="Approve — players can accept it", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set(interaction, "approved", "✅ Approved")

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.danger)
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set(interaction, "discarded", "🗑️ Discarded")


@registered_group_command(admin_world_group, name="questforge", description="Draft a quest from a story; you approve it before players see it")
@app_commands.describe(story="What happened, or what should happen - a few sentences. Locations and NPCs by name help.")
async def admin_questforge(interaction: discord.Interaction, story: str) -> None:
    if not await require_admin(interaction):
        return
    story = story.strip()
    if len(story) < 12:
        await interaction.response.send_message("Give the Forge a little more story to work with (a sentence or two).", ephemeral=False)
        return
    await interaction.response.defer(ephemeral=False)
    result = await QUEST_FORGE.draft(story, source_key=f"gm:{interaction.user.id}")
    if result.definition is None:
        await interaction.followup.send("The Forge could not produce a valid quest:\n- " + "\n- ".join(result.errors[:8]), ephemeral=False)
        return
    row = await store_draft(DB, result, story=story, origin="gm_prompt", created_by=interaction.user.id)
    await audit_admin(interaction, "quest.forge", target=row["quest_key"], after={"model": result.model, "procedural": result.procedural})
    note = ""
    if result.procedural:
        note = "\n_(The model was unavailable or kept producing an invalid draft" + (f": {result.errors[0]}" if result.errors else "") + "; this is the procedural draft.)_"
    await interaction.followup.send(
        f"Draft ready. Approve to put it in every cultivator's **/quests**, or discard it.{note}",
        embed=_quest_draft_embed(row), view=QuestDraftReviewView(row["quest_key"]), ephemeral=False,
    )


@registered_group_command(admin_world_group, name="quests", description="Review forged quest drafts; retire an approved one")
@app_commands.describe(retire="Quest key of an approved forged quest to retire (optional)")
async def admin_quests(interaction: discord.Interaction, retire: str = "") -> None:
    if not await require_admin(interaction):
        return
    if retire.strip():
        row = await DB.get_quest_definition(retire.strip())
        if row is None or row.get("status") != "approved":
            await interaction.response.send_message("No approved forged quest has that key.", ephemeral=False)
            return
        await DB.set_quest_definition_status(row["quest_key"], "retired", reviewed_by=interaction.user.id)
        await QUESTS.catalog(refresh=True)
        await audit_admin(interaction, "quest.retired", target=row["quest_key"], before={"status": "approved"}, after={"status": "retired"})
        await interaction.response.send_message(f"📕 Retired **{row['title']}**; cultivators who already hold it keep it.", ephemeral=False)
        return
    drafts = await DB.list_quest_definitions("draft")
    approved = await DB.list_quest_definitions("approved")
    if not drafts and not approved:
        await interaction.response.send_message("No forged quests yet. Draft one with **/admin → World → Questforge**.", ephemeral=False)
        return
    lines = []
    if approved:
        # "live in /quests" was true of every approved row until v1.0.0-rc.46,
        # and is now true of a Forge draft and false of a roster's quest - the
        # journal offers only what nothing hands over. A GM workbench that says
        # the wrong one of those is how a GM concludes a feature is broken, so
        # each row says which, and the offerable ones sort first: the fifteen
        # shown were otherwise about to be sixty-odd seeded rows deep.
        ordered = sorted(approved, key=lambda r: (QUESTS.handed_over(r), str(r["quest_key"])))
        lines.append("**Approved**\n" + "\n".join(
            f"• `{r['quest_key']}` {r['title']}" + (" — handed over by its roster" if QUESTS.handed_over(r) else "")
            for r in ordered[:15]))
        lines.append("-# Rows with no note are offered in **/quests**; the rest arrive when their "
                     "roster hands them over (the beginner path, a household errand, an ascension, "
                     "a trade's examination).")
    await interaction.response.send_message("\n".join(lines) or "No approved forged quests.", ephemeral=False)
    for row in drafts[:5]:
        await interaction.followup.send(embed=_quest_draft_embed(row), view=QuestDraftReviewView(row["quest_key"]), ephemeral=False)
    if len(drafts) > 5:
        await interaction.followup.send(f"…and {len(drafts) - 5} more drafts; review these first and run the command again.", ephemeral=False)


@admin_quests.autocomplete("retire")
async def admin_quests_retire_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    needle = current.casefold().strip()
    rows = await DB.list_quest_definitions("approved")
    return [
        app_commands.Choice(name=f"{r['title']} ({r['quest_key']})"[:100], value=str(r["quest_key"]))
        for r in rows if not needle or needle in str(r["title"]).casefold() or needle in str(r["quest_key"])
    ][:25]


@registered_group_command(
    admin_player_group,
    name="erase",
    description="Permanently erase everything this bot holds about a person (data-protection request)",
)
async def admin_erase(
    interaction: discord.Interaction,
    member: discord.Member,
    confirm: str,
    reason: str = "data protection request",
) -> None:
    """Honour a player's request to have their data deleted.

    This is the command behind the promise in `docs/PRIVACY.md`, and it is the
    only one here that cannot be undone - `admin.undo_last` reverses a grant by
    replaying its before/after, and there is no before to replay for a hundred
    deleted rows. So it takes the same typed confirmation the destructive
    server-setup actions take rather than a button: a GM who meant to type
    `/admin player inspect` does not accidentally type ERASE as well.

    What is erased, what is anonymised and what is kept is the engine's to
    decide (`go_core/internal/game/privacy_actions.go`); this only asks, and
    reads the receipt back.
    """
    if not await require_admin(interaction):
        return
    if confirm.strip().upper() != "ERASE":
        await interaction.response.send_message(
            "❌ Not erased. This cannot be undone, so it needs `confirm: ERASE` typed exactly.",
            ephemeral=False,
        )
        return
    if member.id == interaction.user.id:
        # The engine refuses this too; catching it here costs a round trip and
        # says why in words rather than as an engine error.
        await interaction.response.send_message(
            "❌ You cannot erase yourself through the console — it would take the audit trail's "
            "author with it. Stop the bot and use `./reset_database.sh` if you really mean it.",
            ephemeral=False,
        )
        return
    # Deferred: the erasure walks every table in the schema, which is longer
    # than Discord's three seconds on a NAS disk.
    await interaction.response.defer()
    try:
        result = dict(
            await ENGINE.action(
                "admin.player.erase",
                interaction.user.id,
                {"user_id": member.id, "reason": reason},
            )
            or {}
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    deleted = int(result.get("rows_deleted") or 0)
    anonymised = int(result.get("rows_anonymised") or 0)
    tables = int(result.get("tables_touched") or 0)
    if not result.get("had_character") and deleted == 0 and anonymised == 0:
        await interaction.followup.send(
            f"✅ Nothing to erase — this bot held no data for {member.mention}.", ephemeral=False
        )
        return
    lines = [
        f"✅ Erased everything held about {member.mention}.",
        f"**{deleted}** rows deleted and **{anonymised}** anonymised across **{tables}** tables.",
        "-# Shared world state (history, a founded sect or family, authored quests) keeps its row "
        "and loses the link. The audit log keeps its record that this was done.",
    ]
    await interaction.followup.send("\n".join(lines), ephemeral=False)
    # audit_admin's own row is the Discord-side mirror; the engine has already
    # written the authoritative one inside the same transaction as the deletes.
    await audit_admin(
        interaction,
        "player.erase",
        target=f"user:{member.id}",
        before={"had_character": bool(result.get("had_character"))},
        after={"rows_deleted": deleted, "rows_anonymised": anonymised},
        reason=reason,
        database_log=False,
    )

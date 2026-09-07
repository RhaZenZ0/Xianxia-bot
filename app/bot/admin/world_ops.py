"""/admin player karma and grants, /admin sect, /admin world advancetime,
/admin server maintenance.

Phase 7 of the main.py split (v0.19.42, docs/MAIN_SPLIT_PLAN.md). Everything
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

from ...rules.advanced_runtime import EQUIPMENT_DEFINITIONS
from ...rules.birthfamily import karma_label
from ...ops.game_engine import GameEngineError
from ...rules.worldtime import from_game_minutes
from ..formatting import human_duration
from ..hubs import HubDynamicOption, register_hub_option_provider
from ..pickers import auction_currency_autocomplete
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
async def admin_grant_storage(
    interaction:discord.Interaction,member:discord.Member,name:str,grade:str="Earth",
    slots:app_commands.Range[int,1,5000]=80,living_space:bool=False
)->None:
    if not await require_admin(interaction):return
    try:
        result = dict(await ENGINE.action("admin.player.grant_storage", interaction.user.id, {
            "user_id": member.id, "container_id": name.casefold().replace(' ', '_'), "name": name,
            "grade": grade, "slot_capacity": int(slots), "living_space": living_space,
            "reason": "discord admin",
        }) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    # The engine refuses to shrink a container below what is already inside it,
    # so the granted capacity is not always the one that was asked for. Report
    # what the cultivator actually has.
    granted = int(result.get("slot_capacity", slots))
    await audit_admin(interaction, "player.grantstorage", target=f"user:{member.id}", after={"name": name, "grade": grade, "slots": granted, "living_space": living_space}, database_log=False)
    floor_note = f" (raised from {slots} to fit what is already stored)" if granted != int(slots) else ""
    await interaction.response.send_message(f"✅ Granted **{name}** ({grade}, {granted} stacks{floor_note}, living space: {living_space}) to {member.mention}.",ephemeral=False)


@registered_group_command(admin_player_group, name="grantcurrency",description="Grant cultivation currency for events, testing or GM rewards")
@app_commands.autocomplete(currency=auction_currency_autocomplete)
async def admin_grant_currency(interaction:discord.Interaction,member:discord.Member,currency:str,amount:app_commands.Range[int,1,2000000000])->None:
    if not await require_admin(interaction):return
    if currency not in WORLD.currencies:
        await interaction.response.send_message("Unknown currency.",ephemeral=False);return
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
async def admin_advance_time(interaction:discord.Interaction,minutes:app_commands.Range[int,1,525600])->None:
    if not await require_admin(interaction):return
    time_result = dict(
        await ENGINE.action(
            "admin.world.advance_time",
            interaction.user.id,
            {
                "minutes": int(minutes),
                "scale": SETTINGS.world_time_scale,
                "reason": "discord admin",
            },
        )
        or {}
    )
    new_minute = int(time_result.get("game_minute", 0))
    await audit_admin(
        interaction,
        "world.advancetime",
        target="world_clock",
        after={"minutes": int(minutes), "new_game_minute": new_minute},
        database_log=False,
    )
    wt=from_game_minutes(new_minute)
    await interaction.response.send_message(f"🕰️ Advanced world time by **{minutes:,} minutes**.\nNow: **{wt.display}**",ephemeral=False)


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
        await DB.sync_world_catalog(WORLD.data)
        await interaction.followup.send("✅ Locations, NPCs and recipes were resynced from world.json into SQLite.",ephemeral=False);return
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
            "No active event thread was found for that key. Use **/admin world events** first.", ephemeral=False
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
        await interaction.response.send_message("No forged quests yet. Draft one with **/admin world questforge**.", ephemeral=False)
        return
    lines = []
    if approved:
        lines.append("**Approved (live in /quests)**\n" + "\n".join(f"• `{r['quest_key']}` {r['title']}" for r in approved[:15]))
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

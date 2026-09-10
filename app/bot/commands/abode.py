"""Player property: /abode, /array, /innerworld and spatialkey.

Split phase 9b (v0.19.45, docs/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ..formatting import player_property_emoji, player_property_facility_lines, player_property_unbuilt
from ..pickers import usable_item_autocomplete
from ..registry import registered_group_command, registered_root_command
from ..runtime import _explain_engine_error, DB, ENGINE, WORLD, current_world_time, player_property_label, reply_long, require_character, serialized_user_action
from ..services import GUILD, PLAYER_PROPERTY_FACILITY_KEYS, PLAYER_PROPERTY_FACILITY_LABELS
from ..threads import ensure_abode_thread, open_expedition_thread_after_exit

# ---------- Player-owned locations / homes ----------
abode_group=app_commands.Group(name="abode",description="Your one home: found it, then build and raise its facilities")


ABODE_FACILITIES=[
    app_commands.Choice(name=PLAYER_PROPERTY_FACILITY_LABELS.get(x,x.replace('_',' ').title()),value=x)
    for x in PLAYER_PROPERTY_FACILITY_KEYS
][:25]


@registered_group_command(abode_group, name="establish",description="Found your one home at the current normal location; build its facilities with Upgrade")
@serialized_user_action
async def abode_establish(interaction:discord.Interaction,name:str)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    # One home, one shape (v0.30.1): the engine founds the homestead - a
    # cultivation chamber and a storeroom - and everything else is built
    # with /abode → Upgrade. There is no type to choose at the door.
    try:
        envelope=await ENGINE.authoritative_action("abode.establish",interaction.user.id,{"name":name},action_id=f"discord:{interaction.id}:abode.establish")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    unbuilt=player_property_unbuilt(result)
    await interaction.followup.send(
        f"🏡 **{result.get('name',name)}** founded at **{result.get('base_location','your location')}**: a cultivation chamber and a storeroom.\n"
        + (f"Not yet built: {', '.join(unbuilt)}. Use **/abode → Upgrade** to build one." if unbuilt else ""),
        ephemeral=False,
    )


@registered_group_command(abode_group, name="status",description="Inspect your player-owned property, facilities and guest access")
async def abode_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    a=await DB.get_abode(interaction.user.id)
    if not a:
        await interaction.response.send_message("You do not own a player property yet. Use **/abode → Establish**.",ephemeral=False);return
    guests=await DB.get_abode_guests(interaction.user.id)
    thread_text=f"<#{a['thread_id']}>" if a.get('thread_id') else "not created"
    facilities=" • ".join(player_property_facility_lines(a)) or "No developed facilities"
    unbuilt=player_property_unbuilt(a)
    await interaction.response.send_message(
        f"{player_property_emoji(a)} **{a['name']} — {player_property_label(a)}**\n"
        f"Entrance: **{a['base_location']}** • Grade: **{a['grade']}**\n"
        f"Facilities: {facilities}\n"
        + (f"Not yet built: {', '.join(unbuilt)}\n" if unbuilt else "")
        + f"Invited guests: **{len(guests)}**\n"
        f"Private location thread: {thread_text}\n\n"
        "The Discord thread is the scene for the property; the world location remains authoritative for entering and leaving.",
        ephemeral=False,
    )


@registered_group_command(abode_group, name="thread",description="Create or recover the private Discord thread for your player-owned property")
async def abode_thread_command(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    a=await DB.get_abode(interaction.user.id)
    if not a:
        await interaction.response.send_message("You do not own a player property.",ephemeral=False);return
    thread=await ensure_abode_thread(interaction,a)
    if thread:
        await interaction.response.send_message(f"{player_property_emoji(a)} Private property scene: {thread.mention}",ephemeral=False)
    else:
        await interaction.response.send_message("Could not create a private property thread. Run **/admin → Server → Setup Server** and grant Create Private Threads / Send in Threads.",ephemeral=False)


@registered_group_command(abode_group, name="enter",description="Enter your player-owned property from its physical entrance location")
@serialized_user_action
async def abode_enter(interaction:discord.Interaction)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.enter",interaction.user.id,{},action_id=f"discord:{interaction.id}:abode.enter")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🏡 You enter **{result.get('name','your property')}**.",ephemeral=False)


@registered_group_command(abode_group, name="visit",description="Enter another player's property if they invited you and you reached its entrance")
@serialized_user_action
async def abode_visit(interaction:discord.Interaction,owner:discord.Member)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.visit",interaction.user.id,{"owner_user_id":owner.id},action_id=f"discord:{interaction.id}:abode.visit")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🏡 You visit **{result.get('name','the property')}**.",ephemeral=False)


@registered_group_command(abode_group, name="leave",description="Leave the current player-owned property and return to its entrance")
@serialized_user_action
async def abode_leave(interaction:discord.Interaction)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:abode.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🚪 You leave the property for **{result.get('outside','outside')}**.",ephemeral=False)
    await open_expedition_thread_after_exit(interaction)


@registered_group_command(abode_group, name="invite",description="Invite another cultivator to your player-owned property")
async def abode_invite(interaction:discord.Interaction,member:discord.Member)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.invite",interaction.user.id,{"guest_user_id":member.id},action_id=f"discord:{interaction.id}:abode.invite")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🔑 {member.mention} may now enter your property.",ephemeral=False)


@registered_group_command(abode_group, name="revoke",description="Revoke a guest's access to your player-owned property")
@serialized_user_action
async def abode_revoke(interaction:discord.Interaction,member:discord.Member)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.revoke",interaction.user.id,{"guest_user_id":member.id},action_id=f"discord:{interaction.id}:abode.revoke")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🔒 Property access revoked for {member.mention}.",ephemeral=False)


@registered_group_command(abode_group, name="guests",description="List cultivators currently invited to your player-owned property")
async def abode_guests(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    a=await DB.get_abode(interaction.user.id)
    if not a:
        await interaction.response.send_message("You do not own a player property.",ephemeral=False);return
    rows=await DB.get_abode_guests(interaction.user.id)
    if not rows:
        await interaction.response.send_message(f"🔐 **{a['name']}** has no invited guests.",ephemeral=False);return
    lines=[f"🔐 **Guest Access — {a['name']}**"]
    for row in rows[:25]:
        lines.append(f"• <@{int(row['guest_user_id'])}> — {row.get('character_name') or 'Cultivator'}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@registered_group_command(abode_group, name="upgrade",description="Build a facility your home lacks, or raise one it has")
@app_commands.choices(facility=ABODE_FACILITIES)
@serialized_user_action
async def abode_upgrade(interaction:discord.Interaction,facility:app_commands.Choice[str])->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.upgrade",interaction.user.id,{"facility":facility.value},action_id=f"discord:{interaction.id}:abode.upgrade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    label=PLAYER_PROPERTY_FACILITY_LABELS.get(facility.value,facility.value.replace('_',' ').title())
    level=int(result.get('level',0) or 0)
    cost=f" for **{result.get('cost','?')}** {WORLD.currency_name(str(result.get('currency','')))}" if result.get('cost') is not None else ""
    if level<=1:
        await interaction.followup.send(f"🏡 You build a **{label}**{cost}.",ephemeral=False)
    else:
        await interaction.followup.send(f"🏡 **{label}** raised to level **{level}**{cost}.",ephemeral=False)


@registered_group_command(abode_group, name="focus",description="Use a developed property facility for a temporary specialization effect or scene benefit")
@app_commands.choices(facility=ABODE_FACILITIES)
@serialized_user_action
async def abode_focus(interaction:discord.Interaction,facility:app_commands.Choice[str])->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.focus",interaction.user.id,{"facility":facility.value},action_id=f"discord:{interaction.id}:abode.focus")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🏡 You focus within the **{facility.value}** facility.",ephemeral=False)


# ---------- Teleportation arrays / spatial keys ----------
array_group=app_commands.Group(name="array",description="Use public teleportation formations")


@registered_group_command(array_group, name="list",description="List teleportation arrays available from your current location")
async def array_list(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    options=[d for d in WORLD.teleport_arrays.values() if d.get('from')==c['location']]
    if not options: await interaction.response.send_message("No public teleportation array is anchored at this location.",ephemeral=False);return
    lines=[f"🌀 **Teleportation Arrays — {c['location']}**"]
    for d in options: lines.append(f"• **{d['name']}** → {d['to']} • {d['cost']} {WORLD.currency_name(str(d['currency']))}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


async def array_destination_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    c=await DB.get_character(interaction.user.id); needle=current.casefold().strip(); out=[]
    if c:
        for aid,d in WORLD.teleport_arrays.items():
            if d.get('from')==c['location'] and (not needle or needle in str(d['to']).casefold() or needle in str(d['name']).casefold()): out.append(app_commands.Choice(name=f"{d['name']} → {d['to']}"[:100],value=aid[:100]))
    return out[:25]


@registered_group_command(array_group, name="use",description="Travel through a public teleportation array")
@app_commands.autocomplete(array=array_destination_autocomplete)
@serialized_user_action
async def array_use(interaction:discord.Interaction,array:str)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("array.use",interaction.user.id,{"array_id":array},action_id=f"discord:{interaction.id}:array.use"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    await interaction.followup.send(f"🌀 The formation ignites and folds the route beneath you. You arrive at **{r.get('location',r.get('destination','your destination'))}**.",ephemeral=False)


@registered_root_command(name="spatialkey",description="Use a spatial key/token to open its linked secret dimension",guild=GUILD)
@app_commands.autocomplete(item=usable_item_autocomplete)
@serialized_user_action
async def spatial_key_command(interaction:discord.Interaction,item:str)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("spatial_key.use",interaction.user.id,{"item_id":item},action_id=f"discord:{interaction.id}:spatial_key.use"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    await interaction.followup.send(f"🗝️ The key tears open a temporary entrance to **{r.get('realm_name',r.get('realm_id','a secret realm'))}**.",ephemeral=False)


# ---------- Personal world creation ----------
innerworld_group=app_commands.Group(name="innerworld",description="Create and define a stabilized personal world at the peak of Space Law")


@registered_group_command(innerworld_group, name="create",description="Stabilize your own personal world")
@serialized_user_action
async def innerworld_create(interaction:discord.Interaction,name:str)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("personal_world.create",interaction.user.id,{"name":name},action_id=f"discord:{interaction.id}:personal_world.create")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🌌 Personal world **{result.get('name',name)}** created.",ephemeral=False)


@registered_group_command(innerworld_group, name="status",description="Inspect your stabilized personal world")
async def innerworld_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    pw=await DB.get_personal_world(interaction.user.id)
    if not pw: await interaction.response.send_message("You have no stabilized personal world.",ephemeral=False);return
    rules='\n'.join(f"• **{k}:** {v}" for k,v in pw.get('laws',{}).items()) or 'No explicit local laws defined yet.'
    await reply_long(interaction,f"🌌 **{pw['name']}**\nStability: **{pw['stability']}**\nAccess: {pw['access_mode']}\n\n**Local Laws**\n{rules}",ephemeral=False)


@registered_group_command(innerworld_group, name="setrule",description="Define or refine one physical/conceptual rule inside your personal world")
@serialized_user_action
async def innerworld_setrule(interaction:discord.Interaction,rule:str,definition:str)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("personal_world.set_rule",interaction.user.id,{"rule":rule,"definition":definition},action_id=f"discord:{interaction.id}:personal_world.set_rule")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🌌 Inner-world rule **{rule}** updated.",ephemeral=False)


@registered_group_command(innerworld_group, name="enter",description="Enter your personal world")
@serialized_user_action
async def innerworld_enter(interaction:discord.Interaction)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("personal_world.enter",interaction.user.id,{},action_id=f"discord:{interaction.id}:personal_world.enter")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send("🌌 You enter your personal world.",ephemeral=False)


@registered_group_command(innerworld_group, name="leave",description="Leave your personal world and return to Greenriver Town")
@serialized_user_action
async def innerworld_leave(interaction:discord.Interaction)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("personal_world.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:personal_world.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"🚪 You leave the personal world for **{result.get('outside','outside')}**.",ephemeral=False)
    await open_expedition_thread_after_exit(interaction)



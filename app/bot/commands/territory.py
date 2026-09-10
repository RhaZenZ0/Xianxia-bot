"""/territory, /war, /caravan and /party: persistent territory conflicts, trade caravans and voluntary parties.

Split phase 9a (v0.19.44, docs/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ..locations import location_autocomplete
from ..registry import registered_group_command
from ..runtime import carried_item_autocomplete, _explain_engine_error, DB, ENGINE, WORLD, current_world_time, reply_long, require_character, serialized_user_action

# ---------- Advanced branch forward-port: beasts, artifacts, territory, caravans, parties, PvP, social state ----------
territory_group = app_commands.Group(name="territory", description="Inspect or contest persistent territory control")


war_group = app_commands.Group(name="war", description="Inspect active sect and territory conflicts")


caravan_group = app_commands.Group(name="caravan", description="Dispatch and inspect persistent trade caravans")


party_group = app_commands.Group(name="party", description="Create voluntary cultivation parties")


@registered_group_command(territory_group, name="status", description="Inspect persistent control and resource state at your location")
async def territory_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_territories(str(c.get('location')))
    if not rows:
        await interaction.response.send_message("No persistent territory node exists here.",ephemeral=False);return
    t=rows[0]
    await interaction.response.send_message(
        f"🏯 **{t['name']}**\nController: **{t['controller_type']}:{t['controller_key'] or 'unclaimed'}** • Resource **{t['resource_type']}**\nProsperity **{t['prosperity']}** • Defense **{t['defense']}** • Unrest **{t['unrest']}**",ephemeral=False)


@registered_group_command(territory_group, name="claim", description="Claim an unheld territory for your sect or begin a territorial war")
@serialized_user_action
async def territory_claim(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_territories(str(c.get('location')))
    if not rows:
        await interaction.response.send_message("No claimable territory node exists here.",ephemeral=False);return
    t=rows[0]; wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("territory.claim",interaction.user.id,{"territory_key":str(t['territory_key'])},action_id=f"discord:{interaction.id}:territory.claim"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    if r.get('war_id'): text=f"⚔️ **Territorial War #{r['war_id']}** begins for **{t['name']}**."
    else: text=f"🏯 **{r.get('sect_name','Your sect')}** establishes a recognized claim over **{t['name']}**."
    await interaction.response.send_message(text,ephemeral=False)


@registered_group_command(war_group, name="status", description="View siege, armies, morale and occupation state for territorial wars")
async def war_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_territory_wars(active_only=False)
    if not rows:
        await interaction.response.send_message("⚔️ No formal territorial war has been recorded.",ephemeral=False);return
    lines=["⚔️ **Territory Wars**"]
    for w in rows[:20]:
        op=w.get('operations') or {}
        occupation=f" • occupation until {op.get('occupation_until_game_minute')}" if int(op.get('occupation_until_game_minute') or 0)>0 else ""
        lines.append(f"\n`#{w['war_id']}` **{w['attacker_key']}** vs **{w['defender_key']}** for **{w['territory_key']}** • **{w['status']}**\nSiege **{op.get('siege_progress',0)}%** • morale A/D **{op.get('attacker_morale',100)}/{op.get('defender_morale',100)}** • forces A/D **{op.get('attacker_force',0)}/{op.get('defender_force',0)}**{occupation}"+(f" • winner **{op.get('winner_key')}**" if op.get('winner_key') else ""))
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(war_group, name="act", description="Contribute cultivation power to a siege, defense, sabotage or counterattack")
@app_commands.choices(tactic=[app_commands.Choice(name=x.title(),value=x) for x in ("assault","siege","sabotage","fortify","repel")])
@serialized_user_action
async def war_act(interaction: discord.Interaction, war_id: int, tactic: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("war.act",interaction.user.id,{"war_id":int(war_id),"tactic":tactic.value},action_id=f"discord:{interaction.id}:war.act"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    op=dict(r.get('operations') or r)
    text=f"⚔️ **War #{war_id} — {tactic.name}**\nSiege **{op.get('siege_progress',0)}%** • morale A/D **{op.get('attacker_morale',100)}/{op.get('defender_morale',100)}** • forces A/D **{op.get('attacker_force',0)}/{op.get('defender_force',0)}**"
    if r.get('status') and r.get('status')!='active': text+=f"\n🏯 War resolved: **{op.get('winner_key','unknown')}**."
    await interaction.followup.send(text,ephemeral=False)


@registered_group_command(caravan_group, name="dispatch", description="Send goods with optional escorts, smuggling and destination tax exposure")
@app_commands.autocomplete(destination=location_autocomplete)
@app_commands.autocomplete(item=carried_item_autocomplete)
@serialized_user_action
async def caravan_dispatch(interaction: discord.Interaction, destination: str, item: str, quantity: app_commands.Range[int,1,50]=1, escort: app_commands.Range[int,0,20]=0, smuggle: bool=False) -> None:
    await interaction.response.defer(ephemeral=False)
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("caravan.dispatch",interaction.user.id,{"destination":destination,"item_id":item,"quantity":int(quantity),"escort":int(escort),"smuggle":bool(smuggle)},action_id=f"discord:{interaction.id}:caravan.dispatch"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    await interaction.followup.send(f"🐫 Caravan **#{r.get('caravan_id')}** dispatched to **{destination}** carrying **{WORLD.item_name(item)} x{quantity}**.",ephemeral=False)


@registered_group_command(caravan_group, name="status", description="Settle due caravans and inspect escorts, interception, smuggling, tax and losses")
async def caravan_status(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "caravan.settle", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:caravan.settle",
        )
        arrived=list(dict(envelope.get("result") or {}).get("resolved") or [])
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    rows=[x for x in await DB.get_caravans() if x.get('owner_type')=='player' and str(x.get('owner_key'))==str(interaction.user.id)]
    if not rows:
        await interaction.followup.send("🐫 You have not dispatched a caravan.",ephemeral=False);return
    lines=[f"🐫 **Caravans — {c['name']}**"]
    if arrived: lines.append(f"\n✅ **{len(arrived)} caravan(s) resolved on this check.**")
    for row in rows[:15]:
        remaining=max(0,int(row['arrive_game_minute'])-wt.total_minutes)
        details=f"escort {int(row.get('escort_strength') or 0)} • {'smuggling' if int(row.get('smuggling') or 0) else f'tax {int(row.get("tax_rate") or 0)}%'}"
        if row['status']=='traveling': details+=f" • {remaining} game minutes"
        else: details+=f" • payout {int(row.get('payout_final') or 0)} • toll {int(row.get('toll_paid') or 0)} • losses {int((row.get('losses') or {}).get('percent',0))}%"
        lines.append(f"\n`#{row['caravan_id']}` {row['origin']} → **{row['destination']}** • **{row['status']}** • {details}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(caravan_group, name="events", description="Inspect the route event history for one caravan")
async def caravan_events(interaction: discord.Interaction, caravan_id: int) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=[x for x in await DB.get_caravans() if int(x['caravan_id'])==int(caravan_id) and x.get('owner_type')=='player' and str(x.get('owner_key'))==str(interaction.user.id)]
    if not rows:
        await interaction.response.send_message("That is not one of your caravans.",ephemeral=False);return
    events=await DB.get_caravan_events(caravan_id)
    lines=[f"🐫 **Caravan #{caravan_id} Route Events**"]
    for e in events: lines.append(f"\n• **{e['event_type']}** at game minute {e['game_minute']} — {e.get('detail',{})}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(party_group, name="create", description="Create a voluntary cultivation party")
@serialized_user_action
async def party_create(interaction: discord.Interaction, name: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("party.create",interaction.user.id,{"name":name},action_id=f"discord:{interaction.id}:party.create")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(f"👥 Party **{result.get('name',name)}** created.",ephemeral=False)


@registered_group_command(party_group, name="join", description="Join another cultivator's active party voluntarily")
@serialized_user_action
async def party_join(interaction: discord.Interaction, leader: discord.Member) -> None:
    if not await require_character(interaction):return
    target=await DB.get_party(leader.id)
    if not target:
        await interaction.response.send_message("That cultivator does not lead an active party.",ephemeral=False);return
    try: await ENGINE.authoritative_action("party.join",interaction.user.id,{"party_id":int(target['party_id'])},action_id=f"discord:{interaction.id}:party.join")
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    await interaction.response.send_message(f"👥 You joined {leader.mention}'s party.",ephemeral=False)


@registered_group_command(party_group, name="status", description="View your active cultivation party")
async def party_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    party=await DB.get_party(interaction.user.id)
    if not party:
        await interaction.response.send_message("You are not in an active party.",ephemeral=False);return
    members="\n".join(f"• <@{m['user_id']}> — {m['role']}" for m in party['members'])
    await interaction.response.send_message(f"👥 **{party['name']}** (`#{party['party_id']}`)\n{members}",ephemeral=False)


@registered_group_command(party_group, name="leave", description="Leave your current cultivation party")
@serialized_user_action
async def party_leave(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("party.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:party.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send("👋 You left your active party.",ephemeral=False)



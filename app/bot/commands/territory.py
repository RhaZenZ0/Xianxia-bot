"""/territory, /war, /caravan and /party: persistent territory conflicts, trade caravans and voluntary parties.

Split phase 9a (v0.19.44, docs/history/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ..hubs import HubDynamicOption, register_hub_option_hint, register_hub_option_provider
from ..locations import location_autocomplete
from ..registry import registered_group_command
from ..runtime import carried_item_autocomplete, _explain_engine_error, DB, ENGINE, WORLD, current_world_time, log, reply_long, require_character, serialized_user_action
from ..war_feed import refresh_war

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
    t=rows[0]
    try:
        e=await ENGINE.authoritative_action("territory.claim",interaction.user.id,{"territory_key":str(t['territory_key'])},action_id=f"discord:{interaction.id}:territory.claim"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    if r.get('war_id'):
        verb="comes back for" if r.get('retake') else "moves on"
        text=(f"⚔️ **Territorial War #{r['war_id']}** begins: **{r.get('attacker_key')}** {verb} **{t['name']}**, "
              f"held by **{r.get('defender_key')}**. Fight with **/sect → War → Act**; the war front of this world carries its card.")
    else: text=f"🏯 **{r.get('sect_name','Your sect')}** establishes a recognized claim over **{t['name']}**."
    await interaction.response.send_message(text,ephemeral=False)
    # The war front's card follows the war (v1.24.0), after the engine agreed.
    if r.get('war_id'):
        wt=await current_world_time()
        await refresh_war(interaction.guild,int(r['war_id']),game_minute=wt.total_minutes)


def _days_left(until: Any, now: int) -> int:
    return max(0, -(-(int(until or 0)-int(now)) // 1440))


@registered_group_command(war_group, name="status", description="View siege, armies, morale and occupation state for territorial wars")
async def war_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_territory_wars(active_only=False)
    if not rows:
        await interaction.response.send_message("⚔️ No formal territorial war has been recorded.",ephemeral=False);return
    wt=await current_world_time()
    # Which wars are this cultivator's to fight is the engine's to say
    # (`war.fronts`), never restated here.
    mine:dict[int,dict[str,Any]]={}
    try:
        fronts=dict(await ENGINE.action("war.fronts",interaction.user.id,{}) or {})
        mine={int(f['war_id']):dict(f) for f in list(fronts.get('wars') or [])}
    except GameEngineError:
        pass
    rows=sorted(rows,key=lambda w:(int(w['war_id']) not in mine, str(w.get('status'))!='active', -int(w['war_id'])))
    lines=["⚔️ **Territory Wars**"]
    for w in rows[:20]:
        op=w.get('operations') or {}
        ground=w.get('territory_name') or w['territory_key']
        front=mine.get(int(w['war_id']))
        tag=""
        if front: tag=f" • **you fight for {front.get('fights_for')}**"+(" as an ally" if front.get('ally') else "")
        until=int(op.get('occupation_until_game_minute') or 0)
        occupation=f" • occupied {_days_left(until,wt.total_minutes)} more day(s)" if until>wt.total_minutes and op.get('resolution')=='attacker_occupation' else ""
        lines.append(f"\n`#{w['war_id']}` **{w['attacker_key']}** vs **{w['defender_key']}** for **{ground}** • **{w['status']}**{tag}\nSiege **{op.get('siege_progress',0)}%** • walls **{int(w.get('territory_defense') or 0)}** • morale A/D **{op.get('attacker_morale',100)}/{op.get('defender_morale',100)}** • forces A/D **{op.get('attacker_force',0)}/{op.get('defender_force',0)}**{occupation}"+(f" • winner **{op.get('winner_key')}**" if op.get('winner_key') else ""))
        for ally in (w.get('allies') or []):
            beside=w['attacker_key'] if ally.get('side')=='attacker' else w['defender_key']
            lines.append(f"   🤝 **{ally.get('sect_name')}** fights beside {beside}")
        for a in (w.get('recent_actions') or []):
            who=f"<@{a['user_id']}>" if a.get('user_id') else (str(a.get('sect_name') or '') or "the field")
            lines.append(
                f"   ↳ {who} — **{str(a.get('tactic','')).title()}** for {a.get('side')} "
                f"(power {int(a.get('power') or 0)}, siege {int(a.get('siege_delta') or 0):+d}, "
                f"morale {int(a.get('morale_delta') or 0):+d})")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


async def _war_fronts(interaction: discord.Interaction) -> list[dict[str, Any]]:
    """The engine's answer to which wars this cultivator may fight in, and on
    which side (`war.fronts`), so no picker offers a war the act would refuse
    (rc.46). Empty when the engine cannot be asked."""
    try:
        fronts=dict(await ENGINE.action("war.fronts",interaction.user.id,{}) or {})
    except Exception:
        log.warning("Could not read the war fronts for a picker", exc_info=True)
        return []
    return [dict(f) for f in list(fronts.get('wars') or [])]


def _front_option(f: dict[str, Any]) -> HubDynamicOption:
    ally=" (ally)" if f.get('ally') else ""
    return HubDynamicOption(
        label=f"#{int(f['war_id'])} {f.get('territory_name') or f.get('territory_key')} — for {f.get('fights_for')}{ally}"[:100],
        value=int(f['war_id']),
        description=(f"{f.get('attacker_key')} vs {f.get('defender_key')} • siege {int(f.get('siege_progress') or 0)}% "
                     f"• walls {int(f.get('territory_defense') or 0)} • {int(f.get('points_left') or 0)} pts left")[:100],
        emoji="⚔️",
    )


async def war_front_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    """The active wars this cultivator may fight in, and on which side."""
    return [_front_option(f) for f in (await _war_fronts(interaction))[:25]]


async def war_front_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    needle=current.casefold().strip()
    return [app_commands.Choice(name=o.label[:100],value=int(o.value)) for o in await war_front_hub_options(interaction,current)
            if not needle or needle in o.label.casefold() or needle==str(o.value)][:25]


@registered_group_command(war_group, name="act", description="Contribute cultivation power to a siege, defense, sabotage or counterattack")
@app_commands.choices(tactic=[app_commands.Choice(name=x.title(),value=x) for x in ("assault","siege","sabotage","fortify","repel")])
@serialized_user_action
async def war_act(interaction: discord.Interaction, war_id: int, tactic: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):return
    try:
        e=await ENGINE.authoritative_action("war.act",interaction.user.id,{"war_id":int(war_id),"tactic":tactic.value},action_id=f"discord:{interaction.id}:war.act"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    await interaction.followup.send(war_act_text(int(war_id),tactic.name,r),ephemeral=False)
    # The war front's card follows the war (v1.24.0), after the engine agreed.
    wt=await current_world_time()
    await refresh_war(interaction.guild,int(war_id),game_minute=wt.total_minutes)


def war_act_text(war_id: int, tactic: str, r: dict[str, Any]) -> str:
    """What one act did, every number the engine's (v1.24.0)."""
    op=dict(r.get('operations') or r)
    side=f" for **{r.get('fights_for')}**"+(" as an ally" if r.get('ally') else "") if r.get('fights_for') else ""
    lines=[f"⚔️ **War #{war_id} — {tactic}**{side}",
           f"Siege **{op.get('siege_progress',0)}%** • walls **{int(r.get('territory_defense') or 0)}** • morale A/D **{op.get('attacker_morale',100)}/{op.get('defender_morale',100)}** • forces A/D **{op.get('attacker_force',0)}/{op.get('defender_force',0)}**"]
    if r.get('ally_joined'):
        lines.append(f"🤝 Your sect has joined this war beside **{r.get('fights_for')}**; the other side will remember it.")
    points=int(r.get('points') or 0)
    lines.append(f"🏯 +**{points}** sect contribution." if points else "🏯 You have earned all the contribution this war pays for fighting; a win still pays.")
    if r.get('status') and r.get('status')!='active':
        winner=op.get('winner_key','unknown')
        lines.append(f"🏁 War resolved: **{winner}** {'takes the ground' if op.get('resolution')=='attacker_occupation' else 'holds'}.")
        if int(r.get('victory_points') or 0):
            lines.append(f"🎖️ Victory: +**{int(r['victory_points'])}** sect contribution to you, and to every fighter on your side ({int(r.get('victors_paid') or 0)} paid).")
    if r.get('promoted'):
        lines.append(f"⬆️ Promoted to **{r['promoted']}**.")
    return "\n".join(lines)


async def war_peace_hub_options(interaction: discord.Interaction, current: str) -> list[HubDynamicOption]:
    """The wars this cultivator's own sect is fighting - an ally has no
    standing at the table - read off the same engine answer."""
    return [_front_option(f) for f in (await _war_fronts(interaction)) if not f.get('ally')][:25]


async def war_peace_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    needle=current.casefold().strip()
    return [app_commands.Choice(name=o.label[:100],value=int(o.value)) for o in await war_peace_hub_options(interaction,current)
            if not needle or needle in o.label.casefold() or needle==str(o.value)][:25]


@registered_group_command(war_group, name="peace", description="Sue for peace in a war your sect is fighting; the terms follow the siege")
@serialized_user_action
async def war_peace(interaction: discord.Interaction, war_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):return
    try:
        e=await ENGINE.authoritative_action("war.peace",interaction.user.id,{"war_id":int(war_id)},action_id=f"discord:{interaction.id}:war.peace"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    await interaction.followup.send(war_peace_text(r),ephemeral=False)
    # The war front's card draws the verdict (v1.24.0), after the engine agreed.
    wt=await current_world_time()
    await refresh_war(interaction.guild,int(war_id),game_minute=wt.total_minutes)


def war_peace_text(r: dict[str, Any]) -> str:
    """The terms the engine made, in its own numbers (v1.24.0)."""
    ground=r.get('territory_name') or r.get('territory_key') or 'the ground'
    if r.get('resolution')=='ceded':
        terms=f"**{r.get('defender_key')}** cedes **{ground}** to **{r.get('attacker_key')}**, and may not move on it again for a while"
    else:
        terms=f"**{r.get('defender_key')}** keeps **{ground}**, and **{r.get('attacker_key')}** may not move on it again for a while"
    return (f"🕊️ **War #{int(r.get('war_id') or 0)} ends in peace**, sued for by **{r.get('sued_by')}** with the siege at "
            f"**{int(r.get('siege_progress') or 0)}%**: {terms}. Nobody is paid a victory, and the two sects stand warmer for it.\n"
            f"🏯 It cost you **{int(r.get('cost') or 0)}** sect contribution.")


war_act.autocomplete("war_id")(war_front_autocomplete)
war_peace.autocomplete("war_id")(war_peace_autocomplete)
register_hub_option_provider(war_peace, "war_id", war_peace_hub_options)
register_hub_option_hint(
    war_peace,
    "war_id",
    "Your sect is fighting no war. Peace is sued for by a member of a sect at war, at Deacon or above, once the war is a few days old - an ally has no standing at the table. See every war with **/sect → War → Status**.",
)
register_hub_option_provider(war_act, "war_id", war_front_hub_options)
register_hub_option_hint(
    war_act,
    "war_id",
    "Your sect is in no war you can fight in. A war begins when a sect member stands on a rival's ground and uses **/sect → Territory → Claim**; a sect allied to one side, by a marriage pact or close standing, may fight beside it. See every war with **/sect → War → Status**.",
)


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
        # The inner f-string reused the outer quote character, which is also
        # 3.12-only (PEP 701). Same text, spelled so 3.11 can parse it.
        toll_note='smuggling' if int(row.get('smuggling') or 0) else 'tax '+str(int(row.get('tax_rate') or 0))+'%'
        details=f"escort {int(row.get('escort_strength') or 0)} • {toll_note}"
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



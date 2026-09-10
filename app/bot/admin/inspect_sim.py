"""/admin player inspect/teleport/revive/clearbattle, /admin family, /admin
npc, /admin simulation, /admin server backup and audit.

Phase 7 of the main.py split (v0.19.42, docs/MAIN_SPLIT_PLAN.md). Reads core,
runtime, services, locations, pickers, formatting and app.*; never main.py.
Definition order is the order these had in main.py.
"""
from __future__ import annotations

import time
from typing import Any

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ...simulation import MINUTES_PER_DAY
from ..formatting import human_duration
from ..hubs import register_hub_option_provider
from ..locations import location_autocomplete
from ..pickers import _market_item_matches
from ..registry import registered_group_command
from ..runtime import DB, ENGINE, ROOT, WORLD, current_world_time, log, reply_long
from ..services import SIM
from .core import (
    admin_family_group,
    admin_npc_group,
    admin_player_group,
    admin_server_group,
    admin_sim_group,
    audit_admin,
    require_admin,
)
from .world_ops import admin_sect_name_hub_options

ADMIN_AUTOMATION_CHOICES = [
    app_commands.Choice(name="Event expiry/scene closing", value="event_expiry"),
    app_commands.Choice(name="Auction settlement", value="auction_settlement"),
    app_commands.Choice(name="Unexpected exploration events", value="unexpected_events"),
    app_commands.Choice(name="Autonomous world event spawning", value="autonomous_world_events"),
    app_commands.Choice(name="Automatic maintenance cleanup", value="maintenance_cleanup"),
    app_commands.Choice(name="NPC civilization", value="npc_civilization"),
    app_commands.Choice(name="NPC autonomous lives", value="npc_life"),
    app_commands.Choice(name="Autonomous sect politics", value="sect_politics"),
    app_commands.Choice(name="Dynamic economy", value="dynamic_economy"),
    app_commands.Choice(name="Rotating black markets", value="black_markets"),
    app_commands.Choice(name="Martial clan dynamics", value="clan_dynamics"),
    app_commands.Choice(name="Background cultivation / seclusion", value="background_seclusion"),
]


SIMULATION_SYSTEM_CHOICES = [
    app_commands.Choice(name="All simulation systems", value="all"),
    app_commands.Choice(name="NPC civilization", value="npc_civilization"),
    app_commands.Choice(name="NPC autonomous lives", value="npc_life"),
    app_commands.Choice(name="Autonomous sect politics", value="sect_politics"),
    app_commands.Choice(name="Dynamic economy", value="dynamic_economy"),
    app_commands.Choice(name="Rotating black markets", value="black_markets"),
    app_commands.Choice(name="Martial clan dynamics", value="clan_dynamics"),
    app_commands.Choice(name="Background cultivation / seclusion", value="background_seclusion"),
]


SIMULATION_SINGLE_SYSTEM_CHOICES = SIMULATION_SYSTEM_CHOICES[1:]


BACKUP_ACTIONS = [
    app_commands.Choice(name="Create backup", value="create"),
    app_commands.Choice(name="List backups", value="list"),
    app_commands.Choice(name="Backup status", value="status"),
]


@registered_group_command(admin_player_group, name="inspect", description="Inspect a player's hidden canonical game state")
async def admin_inspect(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await require_admin(interaction): return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False); return
    family = await DB.get_birth_family(member.id)
    sect = await DB.get_sect_membership(member.id)
    battle = await DB.get_active_battle(member.id)
    abode = await DB.get_abode(member.id)
    laws = await DB.get_law_progress(member.id)
    wallet = await DB.get_wallet(member.id)
    inventory = await DB.get_inventory(member.id)
    soul = await DB.get_soul_legacy(member.id)
    reinc = await DB.get_reincarnation_state(member.id)
    seclusion = await DB.get_seclusion(member.id)
    wt = await current_world_time()
    effects = await DB.get_active_effects(member.id, wt.total_minutes)
    lines = [
        f"🛡️ **Admin Inspect — {c['name']}** (`{member.id}`)",
        f"\nLife: **{c.get('life_status','alive')}** • Realm: **{WORLD.realm_name(int(c['realm_index']), c.get('gender'))} Stage {c['phase']}**",
        f"\nBody: **{WORLD.body_realm_name(int(c.get('body_realm_index',0)), c.get('gender'))} Stage {c.get('body_phase',1)}**",
        f"\nLocation: **{c.get('location')}** • Karma: **{int(c.get('karma_score',0)):+d}**",
        f"\nQi/Vitality: **{c.get('qi')}/{c.get('qi_max')} • {c.get('vitality')}/{c.get('vitality_max')}**",
        f"\nFamily: **{family.get('family_name') if family else 'None'}** • Sect: **{sect.get('sect_name') if sect else 'None'}**",
        f"\nBattle: **{battle.get('npc_name') if battle else 'None'}** • Abode: **{abode.get('name') if abode else 'None'}**",
        f"\nSoul incarnation: **{soul.get('incarnation_count',1)}** • Legacy: **{soul.get('legacy_points',0)}**",
        f"\nReincarnation pending: **{'yes' if reinc else 'no'}** • Seclusion: **{seclusion.get('mode').upper() if seclusion else 'none'}** • Active effects: **{len(effects)}** • Laws: **{len(laws)}**",
        f"\nWallet entries: **{len([v for v in wallet.values() if int(v)!=0])}** • Inventory stacks: **{len([v for v in inventory.values() if int(v)>0])}**",
    ]
    await audit_admin(interaction, "player.inspect", target=f"user:{member.id}")
    await interaction.response.send_message("".join(lines), ephemeral=False)


@registered_group_command(admin_player_group, name="teleport", description="Teleport a player to a canonical location")
async def admin_teleport(interaction: discord.Interaction, member: discord.Member, location: str, reason: str = "GM teleport") -> None:
    if not await require_admin(interaction): return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False); return
    loc = await DB.get_location_definition(location)
    if not loc:
        await interaction.response.send_message("Unknown canonical location.", ephemeral=False); return
    before = {"location": c.get("location")}
    await ENGINE.action(
        "admin.player.teleport",
        interaction.user.id,
        {"user_id": member.id, "location": location, "reason": reason},
    )
    await audit_admin(
        interaction,
        "player.teleport",
        target=f"user:{member.id}",
        before=before,
        after={"location": location},
        reason=reason,
        database_log=False,
    )
    await interaction.response.send_message(f"✅ Teleported {member.mention} to **{location}**.", ephemeral=False)


@admin_teleport.autocomplete("location")
async def admin_teleport_location_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    names = await DB.search_catalog("location", current, 25)
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names]


@registered_group_command(admin_player_group, name="revive", description="Revive a dead character and cancel pending Samsara")
async def admin_revive(interaction: discord.Interaction, member: discord.Member, reason: str = "GM intervention") -> None:
    if not await require_admin(interaction): return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False); return
    before = {"life_status": c.get("life_status"), "vitality": c.get("vitality")}
    revive_result = dict(
        await ENGINE.action(
            "admin.player.revive",
            interaction.user.id,
            {"user_id": member.id, "reason": reason},
        )
        or {}
    )
    ok = bool(revive_result)
    if not ok:
        await interaction.response.send_message("Could not revive that character.", ephemeral=False); return
    await audit_admin(
        interaction,
        "player.revive",
        target=f"user:{member.id}",
        before=before,
        after={"life_status": "alive", "vitality": "full"},
        reason=reason,
        database_log=False,
    )
    await interaction.response.send_message(f"✨ Revived {member.mention}. Pending Samsara was cancelled.", ephemeral=False)


@registered_group_command(admin_player_group, name="clearbattle", description="Force-clear a player's active battle state")
async def admin_clearbattle(interaction: discord.Interaction, member: discord.Member, reason: str = "GM recovery") -> None:
    if not await require_admin(interaction): return
    clear_result = dict(
        await ENGINE.action(
            "admin.player.clear_battle",
            interaction.user.id,
            {"user_id": member.id, "reason": reason},
        )
        or {}
    )
    count = int(clear_result.get("cleared", 0))
    await audit_admin(
        interaction,
        "player.clearbattle",
        target=f"user:{member.id}",
        after={"cleared": count},
        reason=reason,
        database_log=False,
    )
    await interaction.response.send_message(f"✅ Cleared **{count}** active battle state(s) for {member.mention}.", ephemeral=False)


@registered_group_command(admin_family_group, name="familyinspect", description="Inspect a player's NPC birth family")
async def admin_familyinspect(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await require_admin(interaction): return
    family = await DB.get_birth_family(member.id)
    if not family:
        await interaction.response.send_message("That member has no recorded birth family.", ephemeral=False); return
    alive = [n for n in family.get("npcs", []) if n.get("status") == "alive"]
    lines = [
        f"🏯 **Admin Family Inspect — {family['family_name']}**",
        f"\nArchetype: `{family.get('archetype')}` • Tier: **{family.get('tier')}** • Status: **{family.get('line_status','active')}**",
        f"\nWealth **{family.get('wealth')}** • Influence **{family.get('influence')}** • Stability **{family.get('stability')}**",
        f"\nHead: **{family.get('head_title')} {family.get('head_name')}** • Location: **{family.get('location')}**",
        f"\nBloodline: **{family.get('bloodline_name','None')}** • Purity **{family.get('bloodline_purity',0)}%** • Affinity **{family.get('bloodline_affinity','None')}**",
        f"\nBranches **{family.get('branch_count',1)}** • Retainers **{family.get('retainer_count',0)}** • Martial alliance **{family.get('confederacy_name','None')}**",
        f"\nRecorded NPC relatives: **{len(family.get('npcs', []))}** • Alive: **{len(alive)}**",
    ]
    history = list(family.get("history", []))[-5:]
    if history:
        lines.append("\n\n**Recent history**\n" + "\n".join(f"• {h}" for h in history))
    await audit_admin(interaction, "family.inspect", target=f"family:{family['family_id']}")
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_group_command(admin_npc_group, name="npcinspect", description="Inspect a canonical NPC including hidden GM-only fields")
async def admin_npcinspect(interaction: discord.Interaction, npc: str) -> None:
    if not await require_admin(interaction): return
    data = await DB.get_npc_definition(npc)
    if not data:
        await interaction.response.send_message("Unknown NPC.", ephemeral=False); return
    lines = [f"🧿 **Admin NPC Inspect — {npc}**"]
    for key in ("title","location","world","realm_index","phase","role","personality","schedule"):
        if key in data:
            lines.append(f"\n**{key.replace('_',' ').title()}:** `{data[key]}`")
    if data.get("hidden_master"):
        lines.append(f"\n\n🔒 **Hidden-master data:** `{data['hidden_master']}`")
    if data.get("fake_hidden_master"):
        lines.append(f"\n\n🎭 **Fake-master data:** `{data['fake_hidden_master']}`")
    sim_state=await SIM.npc_status(npc)
    if sim_state:
        lines.append(f"\n\n⚙️ **Simulation:** location `{sim_state.get('current_location')}` • activity `{sim_state.get('activity')}` • mood `{sim_state.get('mood')}` • wealth {sim_state.get('wealth')} • influence {sim_state.get('influence')} • realm {sim_state.get('realm_index')}/{sim_state.get('phase')}")
        lines.append(f"\n🎯 **Current goal:** {sim_state.get('current_goal') or 'No active goal recorded.'} ({sim_state.get('goal_progress',0)}%)")
        if sim_state.get('recent_event'):
            lines.append(f"\n🧠 **Recent autonomous development:** {sim_state.get('recent_event')}")
    try:
        lifespan = await ENGINE.action("npc.lifespan", 0, {"npc_name": npc})
    except GameEngineError:
        lifespan = None
    if lifespan:
        remaining = lifespan.get("remaining_years")
        ageless = bool(lifespan.get("ageless"))
        lines.append(
            f"\n\n⌛ **Lifespan:** age {lifespan.get('age_years','?')} • "
            f"{'ageless (realm transcends natural lifespan)' if ageless else f'~{remaining} years remaining' if remaining is not None else 'unknown'}"
        )
    await audit_admin(interaction, "npc.inspect", target=f"npc:{npc}")
    await reply_long(interaction, "".join(lines), ephemeral=False)


@admin_npcinspect.autocomplete("npc")
async def admin_npcinspect_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    names = await DB.search_catalog("npc", current, 25)
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names]


@registered_group_command(admin_sim_group, name="toggle", description="Enable/disable an automatic world system")
@app_commands.choices(system=ADMIN_AUTOMATION_CHOICES)
async def admin_automation(interaction: discord.Interaction, system: app_commands.Choice[str], enabled: bool) -> None:
    if not await require_admin(interaction): return
    before = await DB.get_automation_settings()
    automation_result = dict(
        await ENGINE.action(
            "admin.automation.set",
            interaction.user.id,
            {"system": system.value, "enabled": enabled, "reason": "discord admin"},
        )
        or {}
    )
    after = dict(automation_result.get("settings") or {})
    await audit_admin(
        interaction,
        "automation.set",
        target=system.value,
        before={system.value: before.get(system.value)},
        after={system.value: after.get(system.value)},
        database_log=False,
    )
    await interaction.response.send_message(f"⚙️ **{system.name}** is now **{'ON' if enabled else 'OFF'}**.", ephemeral=False)


@registered_group_command(admin_sim_group, name="automation", description="Show all automatic world-system switches")
async def admin_automation_status(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction): return
    settings = await DB.get_automation_settings()
    labels = {c.value:c.name for c in ADMIN_AUTOMATION_CHOICES}
    lines = ["⚙️ **Automation Status**"]
    for key, enabled in settings.items():
        lines.append(f"\n{'✅' if enabled else '⛔'} **{labels.get(key,key)}:** {'ON' if enabled else 'OFF'}")
    lines.append("\n\nSimulation systems are world-time driven, persisted in SQLite, and catch up safely after restarts.")
    await interaction.response.send_message("".join(lines), ephemeral=False)


@registered_group_command(admin_sim_group, name="status", description="Show simulation intervals, lag and completed runs")
async def admin_simulation_status(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction): return
    wt=await current_world_time()
    rows=await SIM.simulation_status()
    if not rows:
        await interaction.response.send_message("No simulation state has been initialized yet.",ephemeral=False);return
    labels={c.value:c.name for c in SIMULATION_SINGLE_SYSTEM_CHOICES}
    lines=[f"🧭 **World Simulation Status**\nCanonical time: **{wt.display}**"]
    for row in rows:
        interval=max(1,int(row['interval_game_minutes']))
        lag=max(0,int(row.get('lag_game_minutes',0)))
        lines.append(f"• **{labels.get(row['system'],row['system'])}** — every {interval/MINUTES_PER_DAY:g} day(s) • lag {lag:,} game min • runs {row['runs']}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@registered_group_command(admin_sim_group, name="run", description="Force one or all world-simulation systems to advance")
@app_commands.choices(system=SIMULATION_SYSTEM_CHOICES)
async def admin_simulation_run(interaction: discord.Interaction, system: app_commands.Choice[str], steps: app_commands.Range[int,1,120]=1) -> None:
    if not await require_admin(interaction): return
    wt=await current_world_time()
    await interaction.response.defer(ephemeral=False)
    run=await SIM.force_run(system.value,int(steps),wt.total_minutes)
    await audit_admin(interaction,"simulation.run",target=system.value,after={"steps":int(steps),"summary":run.summary})
    await interaction.followup.send(f"⚙️ **{system.name}** forced for **{int(steps)}** step(s).\n{run.summary}",ephemeral=False)


@registered_group_command(admin_sim_group, name="interval", description="Set a simulation subsystem interval in world-days")
@app_commands.choices(system=SIMULATION_SINGLE_SYSTEM_CHOICES)
async def admin_simulation_interval(interaction: discord.Interaction, system: app_commands.Choice[str], days: app_commands.Range[int,1,365]) -> None:
    if not await require_admin(interaction): return
    before=await SIM.get_system_state(system.value)
    after=await SIM.set_interval_days(system.value,int(days))
    await audit_admin(interaction,"simulation.interval",target=system.value,before=before or {},after=after)
    await interaction.response.send_message(f"⏱️ **{system.name}** now runs every **{int(days)} world-day(s)**.",ephemeral=False)


@registered_group_command(admin_sim_group, name="region", description="Inspect a civilization region and recent autonomous incidents")
async def admin_simulation_region(interaction: discord.Interaction, location: str) -> None:
    if not await require_admin(interaction): return
    data=await SIM.civilization_status(location)
    if not data:
        await interaction.response.send_message("Unknown or unsimulated region.",ephemeral=False);return
    lines=[f"🏙️ **Admin Region — {location}**",f"Population **{int(data['population']):,}** • prosperity {data['prosperity']} • security {data['security']} • unrest {data['unrest']}",f"Spirit resources {data['spirit_resources']} • food {data['food_supply']} • migration {int(data['migration_pressure']):+d}"]
    for event in list(data.get('events') or [])[:5]: lines.append(f"• {event['event_text']}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@admin_simulation_region.autocomplete("location")
async def admin_simulation_region_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    names=await DB.search_catalog("location",current,25)
    return [app_commands.Choice(name=n[:100],value=n[:100]) for n in names]


@registered_group_command(admin_sim_group, name="npc", description="Inspect a named NPC's autonomous civilization and life state")
async def admin_simulation_npc(interaction: discord.Interaction, npc: str) -> None:
    if not await require_admin(interaction): return
    data=await SIM.npc_status(npc)
    if not data:
        await interaction.response.send_message("That NPC has no simulation state.",ephemeral=False);return
    age=data.get("age_years")
    life=data.get("lifespan_years")
    life_text="ageless" if life is None and int(data.get("realm_index") or 0)>=16 else (f"{float(life):.0f}y ceiling" if life else "unknown")
    injury=str(data.get("injury") or "None")
    relation_lines=[]
    for r in list(data.get("relationships") or [])[:5]:
        other=r['npc_b'] if r['npc_a']==npc else r['npc_a']
        relation_lines.append(f"• {other}: {r['relation_type']} (aff {int(r['affinity']):+d}, trust {int(r['trust']):+d}, grudge {int(r['grudge'])})")
    bond_lines=[]
    for b in list(data.get("discipleship") or [])[:5]:
        bond_lines.append(f"• Disciple: {b['disciple_name']}" if b['master_name']==npc else f"• Master: {b['master_name']}")
    lines=[
        f"🧿 **NPC Simulation — {npc}**",
        f"Location: **{data['current_location']}** (home {data['home_location']})",
        f"Activity: **{data['activity']}** • Faction: **{data['faction']}** • Rank: **{data.get('sect_rank') or 'Independent Cultivator'}**",
        f"Cultivation: **{WORLD.realm_name(int(data['realm_index']))} Stage {data['phase']}**",
        f"Age: **{float(age):.1f}y** • Lifespan: **{life_text}** • Health: **{int(data.get('health') or 0)}/100** • Injury: **{injury}**" if age is not None else f"Health: **{int(data.get('health') or 0)}/100** • Injury: **{injury}**",
        f"Family: **{data.get('relationship_status') or 'single'}**" + (f" • spouse **{data.get('spouse_name')}**" if data.get('spouse_name') else "") + f" • children **{int(data.get('children_count') or 0)}**",
        f"Wealth {data['wealth']} • Influence {data['influence']} • Ambition {data['ambition']} • Career {int(data.get('career_progress') or 0)}%",
    ]
    if relation_lines: lines.append("\n**Relationships**\n"+"\n".join(relation_lines))
    if bond_lines: lines.append("\n**Master / Disciple**\n"+"\n".join(bond_lines))
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@admin_simulation_npc.autocomplete("npc")
async def admin_simulation_npc_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    names=await DB.search_catalog("npc",current,25)
    return [app_commands.Choice(name=n[:100],value=n[:100]) for n in names]


@registered_group_command(admin_sim_group, name="sect", description="Inspect autonomous sect factions, resources and relations")
async def admin_simulation_sect(interaction: discord.Interaction, sect: str) -> None:
    if not await require_admin(interaction): return
    data=await SIM.sect_status(sect)
    if not data:
        await interaction.response.send_message("Unknown or unsimulated sect.",ephemeral=False);return
    lines=[f"🏯 **Sect Simulation — {sect}**",f"Influence **{data['influence']}** • cohesion **{data['cohesion']}/100** • resources **{data['resources']}**",f"Recruitment **{data['recruitment_pressure']}/100** • doctrine **{data['doctrine_pressure']}/100** • policy **{data['leader_policy']}**"]
    if data.get('factions'):
        lines.append("\n**Factions**"); lines.extend(f"• {f['faction_name']} — power {f['power']}% / loyalty {f['loyalty']}" for f in data['factions'])
    if data.get('relations'):
        lines.append("\n**Relations**"); lines.extend(f"• {r['other']} — {r['relation_type']} ({int(r['relation_score']):+d})" for r in data['relations'][:8])
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


register_hub_option_provider(admin_simulation_sect, "sect", admin_sect_name_hub_options)


@registered_group_command(admin_sim_group, name="market", description="Inspect a local dynamic market")
async def admin_simulation_market(interaction: discord.Interaction, location: str, item: str | None=None) -> None:
    if not await require_admin(interaction): return
    if item:
        q=await SIM.market_quote(location,item)
        if not q:
            await interaction.response.send_message("That item has no market quote there.",ephemeral=False);return
        await interaction.response.send_message(f"💹 **{WORLD.item_name(item)} — {location}**\nBase {q['base_price']} • index x{float(q['price_index']):.2f} • buy {q['buy_price']} • sell {q['sell_price']} {WORLD.currency_name(q['currency_id'])}\nSupply {q['supply']} • demand {q['demand']}",ephemeral=False);return
    rows=await SIM.market_rows(location,15)
    if not rows:
        await interaction.response.send_message("No market is simulated at that location.",ephemeral=False);return
    lines=[f"💹 **Admin Market — {location}**"]
    for q in rows: lines.append(f"• {WORLD.item_name(q['item_id'])} — {max(1,int(round(q['base_price']*q['price_index'])))} {WORLD.currency_name(q['currency_id'])} • S{q['supply']}/D{q['demand']} • x{float(q['price_index']):.2f}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@admin_simulation_market.autocomplete("location")
async def admin_simulation_market_location_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await admin_simulation_region_autocomplete(interaction,current)


@admin_simulation_market.autocomplete("item")
async def admin_simulation_market_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await _market_item_matches(current)


@registered_group_command(admin_sim_group, name="clan", description="Inspect mechanical clan branches, retainers and alliances for a player")
async def admin_simulation_clan(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await require_admin(interaction): return
    fam=await DB.get_birth_family(member.id)
    if not fam:
        await interaction.response.send_message("That member has no birth family.",ephemeral=False);return
    data=await SIM.clan_status(int(fam['family_id']))
    lines=[f"🩸 **Clan Simulation — {fam['family_name']}**",f"Bloodline: **{fam.get('bloodline_name','None')}** • purity **{fam.get('bloodline_purity',0)}%**"]
    lines.append(f"Branches: **{len(data['branches'])}** • retainer groups: **{len(data['retainers'])}** • active relations: **{len(data['relations'])}**")
    for branch in data['branches'][:6]: lines.append(f"• Branch: {branch['branch_name']} — {branch['status']} / loyalty {branch['loyalty']}")
    for relation in data['relations'][:6]: lines.append(f"• Relation: {relation['partner_name']} — {relation['relation_type']} ({int(relation['relation_score']):+d})")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(admin_sim_group, name="actions", description="Show recent player actions that changed persistent world state")
async def admin_simulation_actions(interaction: discord.Interaction, limit: app_commands.Range[int,1,50] = 15) -> None:
    if not await require_admin(interaction): return
    rows=await SIM.recent_player_actions(int(limit))
    if not rows:
        await interaction.response.send_message("No player-driven world consequences have been recorded yet.",ephemeral=False);return
    lines=["🌍 **Recent Player-Driven World Changes**"]
    for row in rows:
        impacts=list((row.get('payload') or {}).get('impacts') or [])
        summary="; ".join(str(x) for x in impacts[:3]) if impacts else "recorded without a major faction shift"
        lines.append(f"• <@{row.get('user_id')}> `{row.get('action_type')}` → **{row.get('target_key')}** at **{row.get('location')}** — {summary}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(admin_sim_group, name="world", description="Show a compact canonical world/database status snapshot")
async def admin_simulation_world(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction): return
    snap = await DB.admin_world_snapshot()
    wt = await current_world_time()
    automation = await DB.get_automation_settings()
    text = (
        f"🌍 **Admin World Status**\nWorld time: **{wt.display}**\n"
        f"Characters: **{snap['characters']}** ({snap['alive_characters']} alive / {snap['deceased_characters']} deceased)\n"
        f"Birth families: **{snap['birth_families']}**\nActive battles: **{snap['active_battles']}**\n"
        f"Active auctions: **{snap['active_auctions']}**\nActive world events: **{snap['active_events']}**\n"
        f"Active effects: **{snap['active_effects']}**\n"
        f"Civilization regions: **{snap.get('civilization_regions',0)}** • Simulated named NPCs: **{snap.get('simulated_npcs',0)}**\n"
        f"Sect factions: **{snap.get('sect_factions',0)}** • Market entries: **{snap.get('market_entries',0)}**\n"
        f"Clan branches: **{snap.get('clan_branches',0)}** • Retainer groups: **{snap.get('retainer_groups',0)}** • Clan relations: **{snap.get('clan_relations',0)}**\n"
        f"Active seclusions: **{snap.get('active_seclusions',0)}** • Recorded player world-actions: **{snap.get('world_action_events',0)}**\n"
        f"Persistent conditions: **{snap.get('active_conditions',0)}** • Cleared tribulations: **{snap.get('cleared_tribulations',0)}** • Profession records: **{snap.get('profession_records',0)}**\n"
        f"Open crimes: **{snap.get('open_crimes',0)}** • Active bounties: **{snap.get('active_bounties',0)}** • Active grudges: **{snap.get('active_grudges',0)}**\n"
        f"Automation: **{sum(1 for v in automation.values() if v)}/{len(automation)} enabled**"
    )
    await interaction.response.send_message(text, ephemeral=False)


@registered_group_command(admin_server_group, name="backup", description="Create or inspect safe SQLite backups")
@app_commands.choices(action=BACKUP_ACTIONS)
async def admin_backup(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    if not await require_admin(interaction): return
    backup_dir = ROOT / "data" / "backups"
    if action.value == "create":
        await interaction.response.defer(ephemeral=False)
        path = await DB.create_backup(backup_dir)
        await audit_admin(interaction, "backup.create", target=path.name, after={"path": str(path)})
        await interaction.followup.send(f"💾 Backup created: `{path.name}`", ephemeral=False)
        return
    backups = await DB.list_backups(backup_dir, 20)
    if action.value == "status":
        if not backups:
            await interaction.response.send_message("💾 No backups exist yet.", ephemeral=False); return
        latest = backups[0]
        age = max(0, int(time.time() - float(latest['modified_at'])))
        await interaction.response.send_message(f"💾 Latest backup: `{latest['name']}` • {latest['size']:,} bytes • {human_duration(age)} old", ephemeral=False)
        return
    if not backups:
        await interaction.response.send_message("💾 No backups exist yet.", ephemeral=False); return
    lines = ["💾 **Recent SQLite Backups**"]
    for item in backups:
        lines.append(f"\n• `{item['name']}` — {item['size']:,} bytes")
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_group_command(admin_server_group, name="audit", description="Show recent admin actions")
async def admin_audit_log(interaction: discord.Interaction, limit: app_commands.Range[int,1,50] = 15) -> None:
    if not await require_admin(interaction): return
    rows = await DB.get_admin_audit_log(int(limit))
    if not rows:
        await interaction.response.send_message("No admin audit entries exist yet.", ephemeral=False); return
    lines = ["📜 **Admin Audit Log**"]
    for row in rows:
        when = f"<t:{int(row['created_at'])}:R>"
        lines.append(f"\n• {when} <@{row['admin_user_id']}> — `{row['action']}` → `{row.get('target','')}`" + (f" — {row['reason']}" if row.get('reason') else ""))
    await reply_long(interaction, "".join(lines), ephemeral=False)



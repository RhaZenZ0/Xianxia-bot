"""Law, manuals, conditions, professions and crime.

Split phase 9d (v0.19.47, docs/history/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order (`worldrules`, which sat between the manual and
condition blocks, stays with the sense domain). Reads battle.py for the
panel and the technique executor; nothing reads back.
"""
from __future__ import annotations

from types import SimpleNamespace

import discord
from discord import app_commands

from ...rules.effects import normalize_effect_payload
from ...ops.game_engine import GameEngineError
from ...rules.progression_systems import condition_definition, profession_rank, profession_xp_needed
from ..character_state import current_effect_modifiers
from ..formatting import roll_line
from ..registry import registered_group_command
from ..runtime import _explain_engine_error, DB, ENGINE, WORLD, current_world_time, reply_long, require_character, respond, serialized_user_action
from .battle import _battle_panel, _execute_battle_law_technique


# ---------- Law / Dao cultivation ----------
law_group = app_commands.Group(name="law", description="Comprehend and wield the Laws that govern reality")

async def law_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip(); out=[]
    for law_id,data in WORLD.law_system.get("laws",{}).items():
        name=str(data.get("name",law_id))
        if not needle or needle in law_id.casefold() or needle in name.casefold(): out.append(app_commands.Choice(name=name[:100],value=law_id[:100]))
    return out[:25]

@registered_group_command(law_group, name="status",description="View your Law and Dao comprehension")
async def law_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_law_progress(interaction.user.id); daos=await DB.get_dao_progress(interaction.user.id)
    if not rows: await interaction.response.send_message("You have not begun comprehending a Law. Use **/cultivation → Path → Comprehend** when your realm is sufficient.",ephemeral=False); return
    lines=[f"⚖️ **Law Comprehension — {c['name']}**"]
    for row in rows[:12]:
        d=WORLD.law_definition(str(row['law_id'])) or {}; stage=WORLD.law_stage(int(row['comprehension']))
        lines.append(f"• **{d.get('name',row['law_id'])}** — {row['comprehension']}% • **{stage['name']}** • {d.get('category','Law')}")
    if daos:
        lines.append("\n☯️ **Dao Reconstruction**")
        for row in daos[:8]: lines.append(f"• {row['dao_id']}: **{row['progress']}%**")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)

@registered_group_command(law_group, name="comprehend",description="Meditate on a Law and increase genuine comprehension")
@app_commands.autocomplete(law=law_autocomplete)
@serialized_user_action
async def law_comprehend(interaction:discord.Interaction,law:str,spend_insight:bool=False)->None:
    """`spend_insight` (v1.0.0-rc.4) puts Insight XP into the comprehension:
    the engine charges it and adds its bonus to the check."""
    c=await require_character(interaction)
    if not c:return
    definition=WORLD.law_definition(law)
    if not definition:
        await interaction.response.send_message("Unknown Law.",ephemeral=False);return
    # Settle time-based effects; Go owns eligibility, affinity, legacy bonus,
    # cooldown, RNG, comprehension/Dao gains, and memory awakening.
    _,_,wt=await current_effect_modifiers(interaction.user.id)
    try:
        envelope=await ENGINE.authoritative_action(
            "law.comprehend",interaction.user.id,
            {"law":law,"spend_insight":bool(spend_insight)},
            action_id=f"discord:{interaction.id}:law.comprehend:{law}",
        )
    except GameEngineError as exc:
        message=str(exc)
        if "Insight XP" in message:
            message+=" Insight XP comes from exploring, quests, battles and the Refine stance (**/cultivation → Cultivate → Stance**)."
        await interaction.response.send_message(f"Law comprehension could not resolve: {message}",ephemeral=False);return
    result=dict(envelope.get("result") or {})
    roll=SimpleNamespace(**dict(result.get("roll") or {}))
    legacy_note=f"\n☸️ Soul Legacy Law Echo: **+{int(result.get('legacy_bonus',0))}** to the comprehension check." if int(result.get('legacy_bonus',0)) else ""
    if int(result.get('insight_spent',0)):
        legacy_note+=f"\n💡 You put **{int(result['insight_spent'])} Insight XP** into it: **+{int(result.get('insight_bonus',0))}** to the check."
    await interaction.response.send_message(
        f"⚖️ **{result.get('name',definition['name'])}**\n{roll_line(roll)}{legacy_note}\n"
        f"Comprehension **+{int(result.get('gain',0))}%** → **{int(result.get('comprehension',0))}%**\n"
        f"Stage: **{result.get('stage_name','Unawakened')}**\nDao reconstruction +{int(result.get('dao_gain',0))}%."
    )

async def law_technique_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip();out=[]
    for tid,d in WORLD.law_system.get('techniques',{}).items():
        name=str(d.get('name',tid))
        if not needle or needle in name.casefold() or needle in tid.casefold():out.append(app_commands.Choice(name=name[:100],value=tid[:100]))
    return out[:25]

@registered_group_command(law_group, name="technique",description="Use an unlocked Law technique in the current battle or scene")
@app_commands.autocomplete(technique=law_technique_autocomplete)
@serialized_user_action
async def law_technique_command(interaction:discord.Interaction,technique:str)->None:
    c=await require_character(interaction)
    if not c:return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False)
    t=WORLD.law_technique(technique)
    if not t: await respond(interaction, "Unknown Law technique.",ephemeral=False);return
    rows=await DB.get_law_progress(interaction.user.id,str(t['law'])); comp=int(rows[0]['comprehension']) if rows else 0; stage=WORLD.law_stage(comp)
    if int(stage['index'])<int(t.get('requires_stage',1)) or int(c['realm_index'])<int(t.get('min_realm_index',0)):
        await respond(interaction, f"You have not met the requirements for **{t['name']}**. Needed: Law stage {t.get('requires_stage')} and realm **{WORLD.realm_name(int(t.get('min_realm_index',0)))}**.",ephemeral=False);return
    if technique=='world_collapse':
        pw=await DB.get_personal_world(interaction.user.id)
        if not pw: await respond(interaction, "World Collapse requires a stabilized personal world.",ephemeral=False);return
    battle=await DB.get_active_battle(interaction.user.id)
    if technique in {'spatial_lockdown','spatial_strangulation'} and not battle:
        await respond(interaction, "That control technique currently requires an active battle target.",ephemeral=False);return
    if battle:
        result=await _execute_battle_law_technique(interaction,battle,technique)
        updated=await DB.get_battle(int(battle['battle_id']),user_id=interaction.user.id,active_only=True)
        if not updated:
            await respond(interaction, "⌛ This battle has already ended.",ephemeral=False);return
        c=await DB.get_character(interaction.user.id) or c
        embed,view=await _battle_panel(interaction.user.id,c,updated,result_text=result)
        await respond(interaction, embed=embed,view=view);return
    # Out of battle the technique is an engine action too, as of v0.23.0: the
    # requirement checks above and the effect write below used to sit on the
    # same side of the boundary, so nothing but this file decided whether a
    # player qualified. The engine re-checks them and owns the write.
    try:
        await ENGINE.authoritative_action(
            "law.technique", interaction.user.id, {"technique": technique},
            action_id=f"discord:{interaction.id}:law.technique:{technique}",
        )
    except GameEngineError as exc:
        await respond(interaction, f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await respond(interaction, f"🌌 **{t['name']}** manifests.\n{t.get('description','')}")

# ---------- Manuals / forbidden cultivation ----------
manual_group = app_commands.Group(name="manual", description="Study cultivation manuals and use learned techniques")


def _mastery_name(index:int)->str:
    levels=list(WORLD.technique_system.get("mastery_levels", ["Learned","Practiced","Proficient","Mastered","Perfected"]))
    return str(levels[max(0,min(len(levels)-1,int(index)))]) if levels else f"Mastery {index}"


async def manual_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip(); inv=await DB.get_inventory(interaction.user.id); learned={str(r['manual_id']) for r in await DB.get_manuals(interaction.user.id)}; out=[]
    for mid,m in WORLD.manuals.items():
        iid=str(m.get('item_id',''))
        if int(inv.get(iid,0))<=0 and mid not in learned: continue
        name=str(m.get('name',mid))
        if not needle or needle in name.casefold() or needle in mid.casefold(): out.append(app_commands.Choice(name=name[:100],value=mid[:100]))
    return out[:25]


async def learned_manual_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    """The methods you have actually opened, best grade first - the ones you
    can practise (v1.0.0-rc.6)."""
    needle=current.casefold().strip(); learned={str(r['manual_id']) for r in await DB.get_manuals(interaction.user.id)}
    order={g:i for i,g in enumerate(("Dao","Immortal","Heaven","Spirit","Earth","Mortal"))}
    rows=[]
    for mid in learned:
        m=WORLD.manual_definition(mid) or {}
        name=str(m.get('name',mid)); grade=str(m.get('grade','Unknown'))
        if needle and needle not in name.casefold() and needle not in mid.casefold(): continue
        rows.append((order.get(grade,99),name,app_commands.Choice(name=f"{name} • {grade}"[:100],value=mid[:100])))
    rows.sort(key=lambda r:(r[0],r[1]))
    return [r[2] for r in rows[:25]]


async def learned_technique_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip(); rows={str(r['manual_id']):r for r in await DB.get_manuals(interaction.user.id)}; out=[]
    for tid,t in WORLD.techniques.items():
        row=rows.get(str(t.get('manual')))
        if not row or int(row.get('mastery',0))<int(t.get('min_mastery',0)): continue
        name=str(t.get('name',tid))
        if not needle or needle in name.casefold() or needle in tid.casefold(): out.append(app_commands.Choice(name=name[:100],value=tid[:100]))
    return out[:25]


@registered_group_command(manual_group, name="list",description="View cultivation manuals you have learned")
async def manual_list(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_manuals(interaction.user.id)
    if not rows:
        await interaction.response.send_message("You have not learned a cultivation manual yet. Acquire a manual item, then use **/cultivation → Arts → Study**.",ephemeral=False);return
    lines=[f"📚 **Cultivation Manuals — {c['name']}**"]
    for row in rows:
        m=WORLD.manual_definition(str(row['manual_id'])) or {}
        unlocked=[]
        for tid in m.get('techniques',[]):
            t=WORLD.technique_definition(str(tid)) or {}
            if int(row.get('mastery',0))>=int(t.get('min_mastery',0)): unlocked.append(str(t.get('name',tid)))
        lines.append(f"\n**{m.get('name',row['manual_id'])}** • {m.get('alignment','Unknown')} • {m.get('grade','Unknown')}\nMastery: **{_mastery_name(int(row.get('mastery',0)))}** • Practice {row.get('practice',0)}\nTechniques: {', '.join(unlocked) if unlocked else 'None unlocked'}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(manual_group, name="study",description="Learn or practice a cultivation manual you possess")
@app_commands.autocomplete(manual=manual_autocomplete)
@serialized_user_action
async def manual_study(interaction:discord.Interaction,manual:str)->None:
    c=await require_character(interaction)
    if not c:return
    m=WORLD.manual_definition(manual)
    if not m:
        await interaction.response.send_message("Unknown cultivation manual.",ephemeral=False);return
    if int(c.get('realm_index',0))<int(m.get('min_realm_index',0)):
        await interaction.response.send_message(f"Your cultivation cannot safely comprehend **{m['name']}** yet. Required realm: **{WORLD.realm_name(int(m.get('min_realm_index',0)))}**.",ephemeral=False);return
    inv=await DB.get_inventory(interaction.user.id); iid=str(m.get('item_id',''))
    learned={str(r['manual_id']):r for r in await DB.get_manuals(interaction.user.id)}
    if manual not in learned and int(inv.get(iid,0))<=0:
        await interaction.response.send_message(f"You do not possess **{WORLD.item_name(iid)}**.",ephemeral=False);return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "manual.study",interaction.user.id,
            {"manual_id":manual,"cooldown_seconds":45*60},
            action_id=f"discord:{interaction.id}:manual.study",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {});state=dict(resolved.get("state") or {})
    first=bool(resolved.get("first_study"));forbidden=bool(resolved.get("forbidden"));karma_note=""
    if first and forbidden:
        karma_note=f"\n☯️ Merely accepting the inheritance leaves a faint karmic stain: **{int(resolved.get('karma_score',0)):+d}**."
    unlocked=[]
    for tid in m.get('techniques',[]):
        t=WORLD.technique_definition(str(tid)) or {}
        if int(state.get('mastery',0))>=int(t.get('min_mastery',0)): unlocked.append(str(t.get('name',tid)))
    await interaction.response.send_message(f"📖 **{m['name']}**\n{m.get('description','')}\nMastery: **{_mastery_name(int(state.get('mastery',0)))}** • Practice {state.get('practice',0)}\nUnlocked: **{', '.join(unlocked) if unlocked else 'none yet'}**{karma_note}",ephemeral=False)


@registered_group_command(manual_group, name="practise",description="Choose the manual you cultivate by; its grade speeds every session")
@app_commands.autocomplete(manual=learned_manual_autocomplete)
@serialized_user_action
async def manual_practise(interaction:discord.Interaction,manual:str)->None:
    """The art you practise (v1.0.0-rc.6). The engine keeps the choice and
    applies its grade and your mastery to every gathering session."""
    await interaction.response.defer(ephemeral=False)
    c=await require_character(interaction)
    if not c:return
    try:
        envelope=await ENGINE.authoritative_action(
            "cultivation.manual",interaction.user.id,
            {"manual_id":manual},
            action_id=f"discord:{interaction.id}:cultivation.manual",
        )
    except GameEngineError as exc:
        message=str(exc)
        if "has not been learned" in message:
            message+=" Study it first with **/cultivation → Arts → Study**."
        await interaction.followup.send(f"❌ {message}",ephemeral=False);return
    result=dict(envelope.get("result") or {})
    previous=str(result.get("previous_manual") or "")
    note=f"\nYou set aside **{previous}**." if previous and result.get("changed") else ""
    await interaction.followup.send(
        f"📖 **{c['name']} circulates the {result.get('manual_name') or manual}.**\n"
        f"Grade **{result.get('manual_grade') or 'Unknown'}** • mastery **{_mastery_name(int(result.get('mastery',0)))}** — "
        f"every session gathers **x{float(result.get('manual_mult',1)):.2f}**.{note}",
        ephemeral=False,
    )


@registered_group_command(manual_group, name="technique",description="Use a learned manual technique in your active battle")
@app_commands.autocomplete(technique=learned_technique_autocomplete)
@serialized_user_action
async def manual_technique(interaction:discord.Interaction,technique:str)->None:
    c=await require_character(interaction)
    if not c:return
    t=WORLD.technique_definition(technique)
    if not t:
        await interaction.response.send_message("Unknown manual technique.",ephemeral=False);return
    rows={str(r['manual_id']):r for r in await DB.get_manuals(interaction.user.id)}; row=rows.get(str(t.get('manual')))
    if not row or int(row.get('mastery',0))<int(t.get('min_mastery',0)):
        await interaction.response.send_message(f"You have not mastered **{t['name']}** enough to use it.",ephemeral=False);return
    battle=await DB.get_active_battle(interaction.user.id)
    if not battle:
        await interaction.response.send_message("That technique currently requires an active battle target.",ephemeral=False);return
    if int(battle.get('npc_hp',0))<=0:
        await interaction.response.send_message("The opponent is already defeated. Choose **Spare** or **Kill**.",ephemeral=False);return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "manual.technique",interaction.user.id,
            {"technique_id":technique},
            action_id=f"discord:{interaction.id}:manual.technique",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {})
    qi_cost=int(resolved.get('qi_cost',0));vit_cost=int(resolved.get('vitality_cost',0));damage=int(resolved.get('damage',0));heal=int(resolved.get('heal',0));suppress=int(resolved.get('suppress_turns',0));nhp=int(resolved.get('npc_hp',0))
    crime=dict(resolved.get('crime') or {});social_note=""
    if crime:
        social_note=f"⚖️ Crime record **#{crime.get('crime_id')}** opened with **{crime.get('evidence',0)}% evidence**."
        if crime.get('bounty_id') is not None: social_note += " A bounty was issued."
    updated=await DB.get_battle(int(battle['battle_id']),user_id=interaction.user.id,active_only=True) or {**battle,'npc_hp':nhp}
    c=await DB.get_character(interaction.user.id) or c
    impacts=list(resolved.get('impacts') or [])
    result=[f"🌑 **{t['name']}** — {t.get('description','')}",f"Cost: **{qi_cost} Qi**"+(f" + **{vit_cost} Vitality**" if vit_cost else "")]
    if damage: result.append(f"💥 Damage: **{damage}** • Opponent Vitality: **{nhp}**")
    if heal: result.append(f"🩸 Forced recovery: **+{heal} Vitality**")
    if suppress: result.append(f"⛓️ Suppression: **{suppress} turn(s)**")
    if bool(resolved.get('forbidden')):
        result.append(f"☯️ Karma: **{int(resolved.get('karma_score',0)):+d}**")
        result.append("👁️ The forbidden art was **witnessed**." if bool(resolved.get('witnessed')) else "🌫️ The forbidden art was mostly **concealed**.")
    if impacts: result.extend(["🌍 **World reaction:**",*[f"• {x}" for x in impacts[:6]]])
    if social_note: result.append(social_note)
    if nhp<=0: result.append("🏆 **Opponent defeated.** You must still choose **Spare** or **Kill**.")
    embed,view=await _battle_panel(interaction.user.id,c,updated,result_text="\n".join(result))
    await interaction.response.send_message(embed=embed,view=view)


# ---------- Persistent injuries / deviations ----------
condition_group = app_commands.Group(name="condition", description="Inspect and treat persistent injuries, poisons and cultivation deviations")


async def condition_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    needle = current.casefold().strip()
    rows = await DB.get_conditions(interaction.user.id)
    out: list[app_commands.Choice[str]] = []
    for row in rows:
        name = str(row.get("name") or row.get("condition_key"))
        key = str(row.get("condition_key"))
        if not needle or needle in name.casefold() or needle in key.casefold():
            out.append(app_commands.Choice(name=f"{name} (Severity {row.get('severity',1)})"[:100], value=key[:100]))
    return out[:25]


@registered_group_command(condition_group, name="status", description="View persistent injuries, poisons, qi deviation and heart demons")
async def condition_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_conditions(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🌿 Your body, meridians, dantian and soul have no recorded persistent conditions.", ephemeral=False)
        return
    lines = [f"🩺 **Persistent Conditions — {c['name']}**"]
    for row in rows:
        definition = condition_definition(str(row["condition_key"]))
        item = str(definition.get("treatment_item", "recovery_pill"))
        lines.append(
            f"\n**{row['name']}** • {row['category']} • Severity **{row['severity']}/5**\n"
            f"{definition.get('description','')}\nTreatment resource: **{WORLD.item_name(item)}**"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(condition_group, name="treat", description="Attempt treatment for a persistent condition")
@app_commands.autocomplete(condition=condition_autocomplete)
@serialized_user_action
async def condition_treat(interaction: discord.Interaction, condition: str) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "condition.treat", interaction.user.id,
            {"condition": condition},
            action_id=f"discord:{interaction.id}:condition.treat:{condition}",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Condition treatment could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    if bool(result.get("success")):
        outcome = ("The condition is **resolved** and its mechanical penalties are removed."
                   if bool(result.get("resolved")) else f"Severity falls to **{int(result.get('severity_after',0))}/5**.")
    else:
        outcome = "The treatment fails. The medicine is consumed, but the condition does not worsen."
    await interaction.followup.send(
        f"🩺 **Treat {result.get('name', condition)}**\n{roll_line(roll)}\n{outcome}", ephemeral=False,
    )
















# ---------- Profession progression ----------
profession_group = app_commands.Group(name="profession", description="Track cultivation-profession mastery independent from realm")


@registered_group_command(profession_group, name="status", description="View your crafting and support-profession mastery")
async def profession_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_profession_progress(interaction.user.id)
    if not rows:
        await interaction.response.send_message(
            "🛠️ You have no profession experience yet. Successful or failed **/craft** attempts now build profession mastery.", ephemeral=False
        )
        return
    lines = [f"🛠️ **Profession Mastery — {c['name']}**"]
    for row in rows:
        level = int(row.get("level", 0)); xp = int(row.get("xp", 0))
        lines.append(
            f"\n**{row['profession']} — {profession_rank(level)}** (Level {level})\n"
            f"XP **{xp}/{profession_xp_needed(level)}** • Successes {row.get('successes',0)} • Failures {row.get('failures',0)} • Quality {row.get('quality_points',0)}"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


# ---------- Reputation / crime / witnesses / bounties / grudges ----------
crime_group = app_commands.Group(name="crime", description="Inspect jurisdictional crimes and evidence recorded against your incarnation")




@registered_group_command(crime_group, name="status", description="View open crimes, evidence and whether they generated bounties")
async def crime_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    crimes = await DB.get_crimes(interaction.user.id)
    if not crimes:
        await interaction.response.send_message("⚖️ No open jurisdictional crime record exists for this incarnation.", ephemeral=False)
        return
    lines = [f"⚖️ **Open Crime Records — {c['name']}**"]
    for row in crimes:
        witnesses = await DB.get_witnesses(int(row["crime_id"]))
        lines.append(
            f"\n`#{row['crime_id']}` **{row['crime_type'].replace('_',' ').title()}** • {row['jurisdiction']}\n"
            f"Severity **{row['severity']}/10** • Evidence **{row['evidence']}%** • Witness records **{len(witnesses)}**\n{row.get('description','')}"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(crime_group, name="atone", description="Pay local restitution to resolve one open crime and its bounty")
@serialized_user_action
async def crime_atone(interaction: discord.Interaction, crime_id: int) -> None:
    c=await require_character(interaction)
    if not c:return
    crimes=await DB.get_crimes(interaction.user.id)
    row=next((x for x in crimes if int(x.get("crime_id",0))==int(crime_id)),None)
    if not row:
        await interaction.response.send_message("That open crime record does not exist.",ephemeral=False);return
    if str(c.get("location"))!=str(row.get("jurisdiction")):
        await interaction.response.send_message(
            f"You must return to **{row.get('jurisdiction')}** to negotiate restitution for this jurisdictional record.",ephemeral=False
        );return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "crime.atone",interaction.user.id,
            {"crime_id":int(crime_id)},
            action_id=f"discord:{interaction.id}:crime.atone",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {})
    fine=int(resolved.get("fine",0));currency=str(resolved.get("currency",''));balance=int(resolved.get("balance",0))
    await interaction.response.send_message(
        f"⚖️ Crime **#{crime_id}** is marked **atoned** and any bounty sourced only from it is resolved. "
        f"Paid **{fine} {WORLD.currency_name(currency)}** • remaining balance **{balance}**.",ephemeral=False
    )

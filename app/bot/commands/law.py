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
from ..cards import Card, card_view
from ..character_state import record_quest_progress, announce_quest_progress, current_effect_modifiers
from ..formatting import roll_line
from ..registry import registered_group_command
from ..services import QUESTS
from ..status_cards import _ELEMENT_MARKS
from ..runtime import _explain_engine_error, DB, ENGINE, WORLD, current_world_time, log, reply_long, require_character, respond, serialized_user_action
from .battle import _battle_panel, _execute_battle_law_technique, _execute_battle_manual_technique


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
    if not rows: await interaction.response.send_message("You have not begun comprehending a Law. Use **/cultivation → Laws → Comprehend** when your realm is sufficient.",ephemeral=False); return
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
        message=_explain_engine_error(exc)
        if "Insight XP" in message:
            message+=" Insight XP comes from exploring, quests, battles and the Refine stance (**/cultivation → Cultivate → Stance**)."
        await interaction.response.send_message(f"Law comprehension could not resolve: {message}",ephemeral=False);return
    result=dict(envelope.get("result") or {})
    roll=SimpleNamespace(**dict(result.get("roll") or {}))
    legacy_note=f"\n☸️ Soul Legacy Law Echo: **+{int(result.get('legacy_bonus',0))}** to the comprehension check." if int(result.get('legacy_bonus',0)) else ""
    # v1.13.0: the +3 a root or a path with an affinity for this Law adds was
    # rolled since the Laws were written and printed nowhere.
    if int(result.get('affinity_bonus',0)):
        legacy_note+=f"\n🜂 Your root or path has an affinity for this Law: **+{int(result['affinity_bonus'])}** to the check."
    if int(result.get('insight_spent',0)):
        legacy_note+=f"\n💡 You put **{int(result['insight_spent'])} Insight XP** into it: **+{int(result.get('insight_bonus',0))}** to the check."
    # v1.17.0: the Spiritual World's job. The engine names the ground and its
    # bonus; a Law is read more clearly under the sutra archive's roof.
    if int(result.get('place_bonus',0)):
        legacy_note+=f"\n📜 {result.get('place')}: **+{int(result['place_bonus'])}** to the check."
    # The upper worlds' road asks for a Law read (v1.18.0): recorded once the
    # engine has granted the gain and before the reply, told after (v1.0.5).
    progressed=await record_quest_progress(interaction.user.id,"law_comprehend",target=law,amount=1,game_minute=wt.total_minutes)
    await interaction.response.send_message(
        f"⚖️ **{result.get('name',definition['name'])}**\n{roll_line(roll)}{legacy_note}\n"
        f"Comprehension **+{int(result.get('gain',0))}%** → **{int(result.get('comprehension',0))}%**\n"
        f"Stage: **{result.get('stage_name','Unawakened')}**\nDao reconstruction +{int(result.get('dao_gain',0))}%."
    )
    await announce_quest_progress(interaction,progressed)

async def law_technique_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    """The techniques of the Laws this cultivator has begun to comprehend
    (v1.18.0). Every Law carries techniques now - twenty-five across eleven
    Laws, which is the picker's whole width - so a list of all of them offered
    what the engine refuses (rc.46) and cut off at Discord's twenty-five. A
    cultivator who has begun no Law is shown every technique, because a
    picker that empties is a road nobody learns exists (rc.32)."""
    needle=current.casefold().strip();out=[]
    begun:set[str]=set()
    try:
        begun={str(r.get("law_id")) for r in (await DB.get_law_progress(interaction.user.id) or []) if int(r.get("comprehension") or 0)>0}
    except Exception:  # an unavailable read offers everything, never nothing
        begun=set()
    for tid,d in WORLD.law_system.get('techniques',{}).items():
        if begun and str(d.get('law'))not in begun:continue
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
    if WORLD.law_technique_targets_another(technique) and not battle:
        await respond(interaction, "That control technique currently requires an active battle target.",ephemeral=False);return
    if battle:
        result=await _execute_battle_law_technique(interaction,battle,technique)
        updated=await DB.get_battle(int(battle['battle_id']),user_id=interaction.user.id,active_only=True)
        if not updated:
            await respond(interaction, "⌛ This battle has already ended.",ephemeral=False);return
        c=await DB.get_character(interaction.user.id) or c
        _card,view=await _battle_panel(interaction.user.id,c,updated,result_text=result)
        await respond(interaction, view=view);return
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
    # A technique that manifested is what the road asks for (v1.18.0): recorded
    # after the engine agreed and before the reply, told after (v1.0.5). The
    # in-battle cast records in `_execute_battle_law_technique`, once the roll
    # has landed.
    progressed=await record_quest_progress(interaction.user.id,"law_technique",target=technique,amount=1,game_minute=(await current_world_time()).total_minutes)
    await respond(interaction, f"🌌 **{t['name']}** manifests.\n{t.get('description','')}")
    await announce_quest_progress(interaction,progressed)

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
    # v1.13.0: a method written for your own path gathers and practises faster,
    # so the picker says which ones are.
    try:
        own_path=str((await DB.get_character(interaction.user.id) or {}).get("path") or "")
    except Exception:
        own_path=""
    own_mult=float((WORLD.data.get("path_system") or {}).get("own_manual_gathering_mult") or 1)
    rows=[]
    for mid in learned:
        m=WORLD.manual_definition(mid) or {}
        name=str(m.get('name',mid)); grade=str(m.get('grade','Unknown'))
        if needle and needle not in name.casefold() and needle not in mid.casefold(): continue
        suited=" • your path x"+f"{own_mult:g}" if own_path and str(m.get('path') or '')==own_path else ""
        rows.append((order.get(grade,99),name,app_commands.Choice(name=f"{name} • {grade}{suited}"[:100],value=mid[:100])))
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
        element=str(m.get('element') or '')
        mark=f" • {_ELEMENT_MARKS.get(element,'☯️')} {element} qi" if element else ""
        lines.append(f"\n**{m.get('name',row['manual_id'])}** • {m.get('alignment','Unknown')} • {m.get('grade','Unknown')}{mark}\nMastery: **{_mastery_name(int(row.get('mastery',0)))}** • Practice {row.get('practice',0)}\nTechniques: {', '.join(unlocked) if unlocked else 'None unlocked'}")
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
            {"manual_id":manual},
            action_id=f"discord:{interaction.id}:manual.study",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(_explain_engine_error(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {});state=dict(resolved.get("state") or {})
    first=bool(resolved.get("first_study"));forbidden=bool(resolved.get("forbidden"));karma_note=""
    if first and forbidden:
        karma_note=f"\n☯️ Merely accepting the inheritance leaves a faint karmic stain: **{int(resolved.get('karma_score',0)):+d}**."
    unlocked=[]
    for tid in m.get('techniques',[]):
        t=WORLD.technique_definition(str(tid)) or {}
        if int(state.get('mastery',0))>=int(t.get('min_mastery',0)): unlocked.append(str(t.get('name',tid)))
    await interaction.response.send_message(f"📖 **{m['name']}**\n{m.get('description','')}\nMastery: **{_mastery_name(int(state.get('mastery',0)))}** • Practice {state.get('practice',0)}\nUnlocked: **{', '.join(unlocked) if unlocked else 'none yet'}**{karma_note}",ephemeral=False)


@registered_group_command(manual_group, name="cultivate_by",description="Choose the manual you cultivate by; each session speeds and practises it")
@app_commands.autocomplete(manual=learned_manual_autocomplete)
@serialized_user_action
async def manual_cultivate_by(interaction:discord.Interaction,manual:str)->None:
    """The art you cultivate by (v1.0.0-rc.6). The engine keeps the choice and
    applies its grade and your mastery to every gathering session. It was
    called Practise until v1.11.0 and practised nothing; every session that
    gathers now adds a point of practice to this manual, so the name says
    what the choice is rather than what it used not to do."""
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
        message=_explain_engine_error(exc)
        if "has not been learned" in message:
            message+=" Study it first with **/cultivation → Arts → Study**."
        await interaction.followup.send(f"❌ {message}",ephemeral=False);return
    result=dict(envelope.get("result") or {})
    previous=str(result.get("previous_manual") or "")
    note=f"\nYou set aside **{previous}**." if previous and result.get("changed") else ""
    element=str(result.get('element') or '')
    # v1.0.0-rc.9: the method draws one kind of qi, and the root decides how
    # much of it actually goes in.
    affinity=""
    if element:
        affinity=(f"\n{_ELEMENT_MARKS.get(element,'☯️')} It draws **{element} qi**, which your root finds "
                  f"**{result.get('element_label') or 'indifferent'}**: **x{float(result.get('element_mult',1)):.2f}**."
                  + (f" {result.get('element_note')}" if str(result.get('element_note') or '') else ""))
    await interaction.followup.send(
        f"📖 **{c['name']} circulates the {result.get('manual_name') or manual}.**\n"
        f"Grade **{result.get('manual_grade') or 'Unknown'}** • mastery **{_mastery_name(int(result.get('mastery',0)))}** — "
        f"every session gathers **x{float(result.get('manual_mult',1)):.2f}** and practises it.{affinity}{note}",
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
    text=await _execute_battle_manual_technique(interaction,battle,technique)
    updated=await DB.get_battle(int(battle['battle_id']),user_id=interaction.user.id,active_only=True) or battle
    c=await DB.get_character(interaction.user.id) or c
    _card,view=await _battle_panel(interaction.user.id,c,updated,result_text=text)
    if interaction.response.is_done(): await interaction.followup.send(view=view)
    else: await interaction.response.send_message(view=view)


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
        await interaction.followup.send(f"Condition treatment could not resolve: {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    # A treatment always mends since v1.0.16: the roll decides how much, never
    # whether, so the line reads the severity the engine wrote, not `success`.
    if bool(result.get("resolved")):
        outcome = "The condition is **resolved** and its mechanical penalties are removed."
    else:
        outcome = (f"Severity falls from {int(result.get('severity_before', 0))} "
                   f"to **{int(result.get('severity_after', 0))}/5**.")
    # Which pill went (v1.12.2): any grade of the treatment treats, and the
    # plainest carried is spent first, so the line names the one the engine chose.
    used = str(result.get("treatment_item") or "")
    spent = f"\nSpent: **{WORLD.item_name(used)}**" if used else ""
    await interaction.followup.send(
        f"🩺 **Treat {result.get('name', condition)}**\n{roll_line(roll)}\n{outcome}{spent}", ephemeral=False,
    )
















# ---------- Profession progression ----------
profession_group = app_commands.Group(name="profession", description="Track cultivation-profession mastery independent from realm")


@registered_group_command(profession_group, name="status", description="View your crafting and support-profession mastery")
async def profession_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_profession_progress(interaction.user.id)
    # What a method needs, and whether you are carrying it (v1.0.1). Every
    # piece of this already existed and none of it reached a player:
    # `character_recipes` had four writers and no Python reader, so nothing
    # could say what you had learned; `get_recipe_definition` has always parsed
    # a recipe's `cost` out of `cost_json` and no command read it; and the
    # engine computed the exact shortfall on a failed craft and refused with
    # the bare words "missing materials". A player who bought a slip, learned
    # it, and then could not work out what to buy next was reading the only
    # surface there was.
    known = await DB.get_known_recipes(interaction.user.id)
    carried = await DB.get_inventory(interaction.user.id)
    levels = {str(row["profession"]): int(row.get("level", 0)) for row in rows}
    by_trade: dict[str, list[str]] = {}
    for entry in known:
        definition = await DB.get_recipe_definition(str(entry["recipe"]))
        if not definition:
            continue
        trade = str(definition.get("profession") or "Other")
        cost = dict(definition.get("cost") or {})
        parts = [
            f"{WORLD.item_name(item)} ×{int(qty)} (have {int(carried.get(item, 0))})"
            for item, qty in sorted(cost.items())
        ]
        short = [item for item, qty in cost.items() if int(carried.get(item, 0)) < int(qty)]
        needed = int(definition.get("min_level") or 0)
        under = levels.get(trade, 0) < needed
        mark = "🔴" if under else ("❌" if short else "✅")
        tail = f" • needs Level {needed}" if under else ""
        # What the method's output does (v1.21.0), through the one door.
        outputs = list(dict(definition.get("output") or {}))
        does = WORLD.item_does(outputs[0]) if outputs else ""
        by_trade.setdefault(trade, []).append(
            f"  {mark} **{entry['recipe']}** — {', '.join(parts) or 'no materials'}"
            f" • TN {int(definition.get('tn') or 0)}{tail}"
            + (f"\n    ↳ {does}" if does else "")
        )

    if not rows and not known:
        # The page used to stop here whatever else was true, which meant a
        # cultivator who had bought a slip and read it was told they had no
        # profession experience and shown nothing they had learned.
        await interaction.response.send_message(
            "🛠️ You have no profession experience and know no methods yet. Successful or failed "
            "**/craft** attempts build mastery; slips are sold in most halls of a trade and are read "
            "with **/craft → Profession → Learn**.", ephemeral=False,
        )
        return
    await interaction.response.send_message(view=card_view(_profession_card(c, rows, by_trade, bool(known))))


def _xp_bar(xp: int, needed: int, *, width: int = 10) -> str:
    """How far a trade is toward its next rank, as a bar a glance can read."""
    cap = max(1, int(needed))
    value = max(0, min(cap, int(xp)))
    filled = min(width, round(value * width / cap))
    return f"{'🟧' * filled}{'⬛' * (width - filled)} **{value}/{cap} XP**"


def _profession_card(c: dict, rows: list, by_trade: dict[str, list[str]], knows_methods: bool) -> Card:
    """The profession panel (v1.9.1): one section per trade - its rank, a bar
    toward the next, the record, and every method known in it with what it
    needs against what is carried. Asked for in play as "a panel to check the
    status of your profession"; it had been a wall of text."""
    card = Card(title=f"🛠️ Profession Mastery — {c['name']}", colour=0xE67E22)
    if not rows:
        card.description = "_No craft attempt has been recorded yet — the methods below are what you know._"
    for row in rows:
        level = int(row.get("level", 0)); xp = int(row.get("xp", 0))
        trade = str(row["profession"])
        methods = by_trade.pop(trade, [])
        value = [
            _xp_bar(xp, profession_xp_needed(level)),
            f"✔️ {row.get('successes', 0)} successes • ✖️ {row.get('failures', 0)} failures • ✨ {row.get('quality_points', 0)} quality",
            *(methods or ["  _no method known in this trade yet_"]),
        ]
        card.add_field(name=f"{trade} — {profession_rank(level, trade)} (Level {level})", value="\n".join(value), inline=False)
    # A method in a trade with no progress row yet is still one you know, and
    # leaving it out is how the old page managed to show nothing at all.
    for trade, entries in sorted(by_trade.items()):
        card.add_field(name=f"{trade} — {profession_rank(0, trade)} (Level 0)", value="\n".join(entries), inline=False)
    if knows_methods:
        card.add_field(
            name="Reading this",
            value=("✅ you can make it now • ❌ short of materials • 🔴 your rank is too low\n"
                   "Buy materials at a hall of the trade (**/economy → City Shops → Here**) or gather "
                   "them (**/craft → Alchemy → Forage**). New methods come from slips: **/craft → Profession → Learn**."),
            inline=False,
        )
    else:
        card.add_field(name="Methods", value=("You know no methods yet. Slips are sold in most halls of a trade — buy one and read "
                                             "it with **/craft → Profession → Learn**."), inline=False)
    card.set_footer(text="Craft with /craft • sit a hall's examination with /profession exam")
    return card


PROFESSION_EXAM_CHOICES = [
    app_commands.Choice(name=trade, value=trade)
    for trade in sorted(dict(WORLD.data.get("profession_exams") or {}))
]


@registered_group_command(profession_group, name="exam",
                          description="Sit your trade's examination at a hall of that trade")
@app_commands.choices(profession=PROFESSION_EXAM_CHOICES)
@serialized_user_action
async def profession_exam(interaction: discord.Interaction, profession: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "profession.exam", interaction.user.id, {"profession": profession.value},
            action_id=f"discord:{interaction.id}:profession.exam",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    lines = [
        f"🎓 **{result.get('title') or 'Examination'}** — the {result.get('rank_name','')} examination in "
        f"**{result.get('profession','')}**, at **{result.get('shop') or result.get('location','')}**.",
        f"*{result.get('opening','')}*",
        f"\n**{result.get('examiner','The keeper')}** watches. {roll_line(roll)}",
    ]
    if result.get("fee"):
        lines.append(f"The hall's fee is **{int(result.get('fee',0))} "
                     f"{WORLD.currency_name(str(result.get('currency') or ''))}** (remaining: "
                     f"**{int(result.get('balance',0))}**).")
    if result.get("passed"):
        taught = [str(name) for name in list(result.get("recipes_taught") or [])]
        withheld = [str(name) for name in list(result.get("recipes_withheld") or [])]
        lines.append(f"\n✅ **Passed.** The hall enters you on its roll as a **{result.get('rank_name','')}** "
                     f"of {result.get('profession','')} (+{int(result.get('standing_gain',0))} standing).")
        if taught:
            lines.append("📜 The keeper writes out what a cultivator of that rank is expected to know: "
                         + ", ".join(f"**{name}**" for name in taught) + ".")
        elif not withheld:
            lines.append("📜 You already knew every method of that rank; the certificate is the new part.")
        if withheld:
            # A hall teaches only what its own world can make (v1.3.0); the
            # engine names the rest, and a slip sold where a method can be
            # made, or a hall standing there, is where it is learned.
            world = str(result.get("hall_world") or "this world")
            lines.append(f"📜 Not taught here, because the {world} cannot supply the makings: "
                         + ", ".join(f"**{name}**" for name in withheld)
                         + ". A hall of the trade in a world that can, or its method slip, teaches it.")
    else:
        hours = max(1, int(result.get("retry_game_minutes", 1440)) // 60)
        lines.append(f"\n❌ **Not this time.** The hall will look at you again in about **{hours} hours**.")
    # The quest's objective is the pass: recorded before the reply, told after
    # (v1.0.5).
    progressed: list[dict] = []
    if result.get("passed"):
        progressed = await record_quest_progress(
            interaction.user.id, "profession_exam", game_minute=wt.total_minutes)
    await reply_long(interaction, "\n".join(lines), ephemeral=False)
    await announce_quest_progress(interaction, progressed)


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
        await interaction.response.send_message(_explain_engine_error(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {})
    fine=int(resolved.get("fine",0));currency=str(resolved.get("currency",''));balance=int(resolved.get("balance",0))
    await interaction.response.send_message(
        f"⚖️ Crime **#{crime_id}** is marked **atoned** and any bounty sourced only from it is resolved. "
        f"Paid **{fine} {WORLD.currency_name(currency)}** • remaining balance **{balance}**.",ephemeral=False
    )


# ---------------------------------------------------------------------------
# Flames (v1.10.0)
# ---------------------------------------------------------------------------
# A flame is captured at its world's forge terraces, refined with beast cores
# and ore, and bound to steady an Alchemy or Forging roll; a fully refined
# heavenly flame is what opens the Transcendent grade. Everything below is the
# engine's answer (flames.go): nothing here decides a cost, a bonus or a roll.
flame_group = app_commands.Group(name="flame", description="Capture, refine and bind the flames that steady alchemy and forging")


async def _flame_status(user_id: int) -> dict:
    return dict(await ENGINE.action("flame.status", int(user_id), {}) or {})


def _refinement_bar(level: int, maximum: int) -> str:
    maximum = max(1, int(maximum))
    level = max(0, min(maximum, int(level)))
    return f"{'🔥' * level}{'▫️' * (maximum - level)} **{level}/{maximum}**"


def _flame_card(c: dict, status: dict) -> Card:
    trades = " and ".join(str(t) for t in status.get("trades") or []) or "crafting"
    card = Card(title=f"🔥 Flames — {c['name']}", colour=0xE74C3C,
                description=(f"A bound flame steadies every **{trades}** roll. A fully refined heavenly flame "
                             "lets a crafter at the sixth rank make **Transcendent** work."))
    maximum = int(status.get("max_refinement") or 9)
    for flame in status.get("flames") or []:
        name = str(flame.get("name") or flame.get("flame_id"))
        if flame.get("held"):
            head = f"{'✅ ' if flame.get('bound') else ''}{name}{' — bound' if flame.get('bound') else ''}"
            lines = [_refinement_bar(int(flame.get("refinement") or 0), maximum),
                     f"Adds **+{int(flame.get('bonus') or 0)}** to a craft roll"
                     + (" • opens **Transcendent**" if flame.get("opens_now") else
                        (" • opens Transcendent when fully refined" if flame.get("opens_top_grade") else ""))]
            if flame.get("next_refine_items") is not None:
                cost = WORLD.item_names({str(k): int(v) for k, v in dict(flame["next_refine_items"]).items()})
                lines.append(f"Next refinement: {cost} and some qi")
        else:
            head = f"▫️ {name}"
            lines = [f"Captured at **{flame.get('location')}** ({flame.get('world')}) from "
                     f"{WORLD.realm_name(int(flame.get('min_realm_index') or 0))}",
                     f"+{int(flame.get('base_bonus') or 0)} to +{int(flame.get('max_bonus') or 0)} to a craft roll"
                     + (" • opens Transcendent when fully refined" if flame.get("opens_top_grade") else "")]
        if flame.get("description"):
            lines.append(f"*{flame['description']}*")
        card.add_field(name=head, value="\n".join(lines), inline=False)
    card.set_footer(text="Capture with /flame capture at a forge terrace • refine and bind with /flame refine and /flame bind")
    return card


async def held_flame_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The flames this cultivator holds - the only ones refine and bind accept."""
    try:
        status = await _flame_status(interaction.user.id)
    except Exception:
        log.warning("Could not load flames for a picker", exc_info=True)
        return []
    needle = current.casefold().strip()
    out = []
    for flame in status.get("flames") or []:
        if not flame.get("held"):
            continue
        name = str(flame.get("name") or flame.get("flame_id"))
        if needle and needle not in name.casefold():
            continue
        out.append(app_commands.Choice(name=f"{name} • refinement {int(flame.get('refinement') or 0)}"[:100],
                                       value=str(flame.get("flame_id"))[:100]))
    return out[:25]


@registered_group_command(flame_group, name="status", description="See the flames you hold, what they give, and where the rest are captured")
async def flame_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    try:
        status = await _flame_status(interaction.user.id)
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await interaction.response.send_message(view=card_view(_flame_card(c, status)))


@registered_group_command(flame_group, name="capture", description="Try to capture the flame that burns at this forge terrace")
@serialized_user_action
async def flame_capture(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action("flame.capture", interaction.user.id, {},
                                                     action_id=f"discord:{interaction.id}:flame.capture")
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    name = str(result.get("name") or "the flame")
    # The realm road's second stage asks for the Earth-Heart Fire (v1.16.0):
    # recorded on a capture the engine agreed to, told after the reply.
    progressed = []
    if result.get("success"):
        progressed = await record_quest_progress(
            interaction.user.id, "flame_capture", target=str(result.get("flame_id") or ""),
            game_minute=(await current_world_time()).total_minutes)
    lines = [f"🔥 **Capturing the {name}** — {int(result.get('qi_cost') or 0)} qi spent",
             roll_line(SimpleNamespace(**dict(result.get("roll") or {})))]
    if result.get("success"):
        lines.append(f"✅ The {name} is yours, for good. It adds **+{int(result.get('bonus') or 0)}** to Alchemy and Forging rolls"
                     + (" and is bound." if result.get("bound") else "; bind it with **/craft → Flames → Bind**."))
    else:
        scorched = dict(result.get("scorched") or {})
        lines.append(f"🩸 The flame gets away and burns your meridians — **{scorched.get('name', 'Meridian Damage')}** "
                     f"(severity {int(scorched.get('severity', 1))}). `/condition treat` with a **Jade Life Herb** mends it.")
    await interaction.followup.send("\n".join(lines), ephemeral=False)
    await announce_quest_progress(interaction, progressed)


@registered_group_command(flame_group, name="refine", description="Refine a flame you hold with beast cores, ore and qi")
@app_commands.autocomplete(flame=held_flame_autocomplete)
@serialized_user_action
async def flame_refine(interaction: discord.Interaction, flame: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action("flame.refine", interaction.user.id, {"flame_id": str(flame)},
                                                     action_id=f"discord:{interaction.id}:flame.refine")
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    spent = WORLD.item_names({str(k): int(v) for k, v in dict(result.get("spent_items") or {}).items()})
    lines = [f"🔥 **{result.get('name')}** refined — {_refinement_bar(int(result.get('refinement') or 0), int(result.get('max_refinement') or 9))}",
             f"Spent {spent} and {int(result.get('qi_cost') or 0)} qi. It now adds **+{int(result.get('bonus') or 0)}** to a craft roll."]
    if result.get("opens_top_grade"):
        lines.append("✨ Fully refined: while it is bound, a crafter at the sixth rank can make **Transcendent** work.")
    progressed = await record_quest_progress(interaction.user.id, "flame_refine", game_minute=(await current_world_time()).total_minutes)
    await interaction.followup.send("\n".join(lines), ephemeral=False)
    await announce_quest_progress(interaction, progressed)


@registered_group_command(flame_group, name="bind", description="Bind one of your flames as the one your crafting reads")
@app_commands.autocomplete(flame=held_flame_autocomplete)
@serialized_user_action
async def flame_bind(interaction: discord.Interaction, flame: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action("flame.bind", interaction.user.id, {"flame_id": str(flame)},
                                                     action_id=f"discord:{interaction.id}:flame.bind")
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    await interaction.followup.send(f"✅ **{result.get('name')}** is bound: it adds **+{int(result.get('bonus') or 0)}** "
                                    "to your Alchemy and Forging rolls.", ephemeral=False)


# ---------------------------------------------------------------------------
# The spirit sense (v1.10.0)
# ---------------------------------------------------------------------------
# Built, never captured: Formation and Inscription crafts, meditation and
# scene actions fill it, qi settles each stage, and it steadies Formation and
# Inscription rolls. Fully built, it opens the Transcendent grade for those
# two trades. Every number here is the engine's (spirit_sense.go).
spirit_group = app_commands.Group(name="spirit", description="Build the spirit sense that steadies formation and inscription")


def _spirit_card(c: dict, status: dict) -> Card:
    trades = " and ".join(str(t) for t in status.get("trades") or []) or "those trades"
    stage = int(status.get("stage") or 0)
    maximum = int(status.get("max_stage") or 9)
    card = Card(title=f"🌀 Spirit Sense — {c['name']}", colour=0x8E44AD,
                description=(f"Built, not found: practice fills it and qi settles each stage. It steadies every "
                             f"**{trades}** roll, and fully built it lets a crafter at the sixth rank make **Transcendent** work."))
    card.add_field(name=f"Stage {stage}/{maximum}",
                   value=(f"{'🌀' * stage}{'▫️' * (maximum - stage)}\nAdds **+{int(status.get('bonus') or 0)}** to a {trades} roll"
                          + (" • opens **Transcendent**" if status.get("opens_now") else "")), inline=False)
    if status.get("need") is not None:
        progress, need = int(status.get("progress") or 0), int(status.get("need") or 1)
        filled = min(10, round(progress * 10 / max(1, need)))
        ready = progress >= need
        card.add_field(name="Toward the next stage",
                       value=(f"{'🟪' * filled}{'⬛' * (10 - filled)} **{progress}/{need}**\n"
                              + (f"✅ Ready: **/spirit settle** spends some qi → +{int(status.get('next_bonus') or 0)}"
                                 if ready else f"The next stage adds +{int(status.get('next_bonus') or 0)}; settling it spends some qi")),
                       inline=False)
    gains = dict(status.get("gains") or {})
    card.add_field(name="What builds it",
                   value=(f"🛠️ a {trades} craft **+{int(gains.get('craft') or 0)}** (half on a miss)\n"
                          f"🧘 a meditation **+{int(gains.get('meditation') or 0)}**\n"
                          f"🎭 a successful scene action **+{int(gains.get('scene') or 0)}**, {int(status.get('scene_gains_per_day') or 0)} a world day\n"
                          f"Each gains a little more for your Spirit (+1 per {int(status.get('spirit_divisor') or 4)} Spirit)."),
                   inline=False)
    card.set_footer(text="Settle a full stage with /spirit settle")
    return card


@registered_group_command(spirit_group, name="status", description="See your spirit sense, its stage and what builds it")
async def spirit_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    try:
        status = dict(await ENGINE.action("spirit_sense.status", interaction.user.id, {}) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await interaction.response.send_message(view=card_view(_spirit_card(c, status)))


@registered_group_command(spirit_group, name="settle", description="Settle a full stage of your spirit sense into the next, for qi")
@serialized_user_action
async def spirit_settle(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action("spirit_sense.settle", interaction.user.id, {},
                                                     action_id=f"discord:{interaction.id}:spirit_sense.settle")
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    lines = [f"🌀 **Spirit sense settled — stage {int(result.get('stage') or 0)}/{int(result.get('max_stage') or 9)}** "
             f"for {int(result.get('qi_cost') or 0)} qi. It now adds **+{int(result.get('bonus') or 0)}** to Formation and Inscription rolls."]
    if result.get("opens_top_grade"):
        lines.append("✨ Fully built: a crafter at the sixth rank can make **Transcendent** Formation and Inscription work.")
    progressed = await record_quest_progress(interaction.user.id, "spirit_settle", game_minute=(await current_world_time()).total_minutes)
    await interaction.followup.send("\n".join(lines), ephemeral=False)
    await announce_quest_progress(interaction, progressed)

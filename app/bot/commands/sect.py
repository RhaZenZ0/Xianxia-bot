"""The /sect hub: sect membership, lineage, manor, discipleship and recruitment.

Split stage 3. Stage 1 (v0.19.12-v0.19.14) moved the shared core into
``app/bot/runtime.py``; stage 2 (v0.19.23) moved ``/family`` into
``app/bot/commands/family.py``. Both cost failed deploys, and both are the
reason this module is shaped the way it is:

* **Definition order.** Everything here is a function, a group, or a literal,
  kept in the exact order it had in main.py. ``DefinitionOrderTests`` covers
  this module the same way it covers every other one in ``MODULES``.

* **Annotation resolution through ``functools.wraps``.** ``serialized_user_action``
  lives in runtime.py, and ``wraps`` cannot copy ``__globals__`` - discord.py
  evaluates a wrapped callback's string annotations against runtime.py's
  namespace, not this file's. Every annotation used by a
  ``@serialized_user_action`` handler below (``discord.Interaction``,
  ``app_commands.Choice[str]``, ``int``, ``str``) already resolves there;
  ``WrappingDecoratorAnnotationTests`` enforces it across the package.

The footprint was measured the same way stage 2's was, before anything moved:
29 free names read by this block. Of those, one (``carried_item_autocomplete``)
was promoted to runtime.py rather than duplicated - it is referenced as a bare
``@app_commands.autocomplete(...)`` argument, which evaluates at *module
import* time, not call time, so a deferred import cannot reach it the way the
others below do. Five (``SIM``, ``_known_locations``, ``current_npc_location``,
``ensure_sect_abode_record``, ``ensure_sect_abode_thread_for``) stay in
main.py, where they are used far more than by ``/sect`` alone, and are pulled
in with a call-time ``from ..main import`` inside the one function that needs
each - a module-level import of any of them would be a genuine cycle, since
main.py imports this module at its own module level. ``_sect_recruitment_at_location``
is the mirror image: it is DEFINED here but used once outside this module (in
main.py's `/explore` road-discovery flow), so main.py imports it back, exactly
as it already does for ``sect_group`` itself.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ...game_engine import GameEngineError
from ...sect_manor import (
    MAX_MANOR_FACILITY_LEVEL,
    SECT_MANOR_ESTABLISHMENT_COST,
    SECT_MANOR_FACILITIES,
    manor_benefit_lines,
    manor_upgrade_cost,
)
from ...sect_recruitment import (
    recommendation_modifier,
    recruitment_definition,
    trial_modifier,
    trial_profile,
)
from ..registry import registered_group_command
from ..runtime import (
    DB,
    ENGINE,
    WORLD,
    carried_item_autocomplete,
    character_location_display,
    current_world_time,
    reply_long,
    require_character,
    serialized_user_action,
)


# Used only by /sect -> Form; moves with it rather than staying an orphaned
# constant in main.py.
ADDRESS_STYLE_CHOICES = [
    app_commands.Choice(name="Masculine — Senior Brother / Junior Brother", value="masculine"),
    app_commands.Choice(name="Feminine — Senior Sister / Junior Sister", value="feminine"),
    app_commands.Choice(name="Neutral — Senior / Junior Martial Sibling", value="neutral"),
]


sect_group = app_commands.Group(name="sect", description="Sect membership, lineage, resources, manor, and martial-family titles")
sect_manor_group = app_commands.Group(
    name="manor",
    description="Build and upgrade your sect's shared cultivation manor",
    parent=sect_group,
)
sect_disciple_group = app_commands.Group(
    name="discipleship",
    description="Request, accept and manage player master-disciple bonds",
    parent=sect_group,
)
sect_recruitment_group = app_commands.Group(
    name="recruitment",
    description="Discover sects, earn NPC recommendations and take story-driven entrance trials",
    parent=sect_group,
)
SECT_MANOR_FACILITY_CHOICES = [
    app_commands.Choice(name=str(definition["name"]), value=key)
    for key, definition in SECT_MANOR_FACILITIES.items()
]


def _relationship_label(rel: dict[str, Any] | None, *, show_chinese: bool) -> str:
    if not rel:
        return "Martial Sibling"
    english = rel.get("translation") or "Martial Sibling"
    if show_chinese and rel.get("pinyin") and rel.get("hanzi"):
        return f"{english} ({rel['pinyin']} {rel['hanzi']})"
    return english


async def _build_family_text(user_id: int, *, show_chinese: bool = False) -> str | None:
    c = await DB.get_character(user_id)
    if not c:
        return None
    snap = await DB.get_lineage_snapshot(user_id)
    if not snap:
        return None

    membership = snap.get("membership")
    lines = [f"🌿 **Martial Family — {c['name']}**"]
    if membership:
        lines.append(f"🏯 **{membership['sect_name']}** — {membership['rank_name']}")

    if snap.get("grandmaster"):
        label = "Grandmaster"
        if show_chinese:
            label += " (Shigong/Shiye 师公/师爷)"
        lines.append(f"**{label}:** {snap['grandmaster']['name']}")

    if snap.get("master"):
        label = "Master"
        if show_chinese:
            label += " (Shifu 师父)"
        lines.append(f"**{label}:** {snap['master']['name']}")

    if snap.get("master_siblings"):
        lines.append("\n**Your master's martial siblings:**")
        for row in snap["master_siblings"][:15]:
            rel = await DB.get_address_context(user_id, int(row["user_id"]))
            lines.append(f"• {_relationship_label(rel, show_chinese=show_chinese)}: {row['name']}")

    if snap.get("siblings"):
        lines.append("\n**Your martial siblings:**")
        for row in snap["siblings"][:20]:
            rel = await DB.get_address_context(user_id, int(row["user_id"]))
            lines.append(f"• {_relationship_label(rel, show_chinese=show_chinese)}: {row['name']}")

    if snap.get("disciples"):
        lines.append("\n**Your direct disciples:**")
        for row in snap["disciples"][:20]:
            lines.append(f"• Disciple: {row['name']}")

    if len(lines) <= 2 and not snap.get("master"):
        lines.append("*No master/disciple lineage has been recorded yet.*")
    return "\n".join(lines)



async def _sync_sect_discoveries(user_id: int, character: dict, *, game_minute: int | None = None) -> list[str]:
    """Promote already-discovered recruitment locations into public sect knowledge."""
    from ..main import _known_locations
    if game_minute is None:
        game_minute = (await current_world_time()).total_minutes
    known_locations = await _known_locations(user_id, character)
    newly_known: list[str] = []
    for sect_name, sect_def in WORLD.sects.items():
        rec = recruitment_definition(WORLD.sects, sect_name)
        if not rec:
            continue
        location = str(rec.get("location") or "")
        if location and location in known_locations:
            if await DB.discover_sect(
                user_id, sect_name, game_minute=int(game_minute),
                discovery_kind="recruitment_route", source_key=location,
            ):
                newly_known.append(sect_name)
    return newly_known


async def _known_sect_names(user_id: int, character: dict) -> list[str]:
    await _sync_sect_discoveries(user_id, character)
    rows = await DB.get_discovered_sects(user_id)
    names = [str(row.get("sect_name")) for row in rows if str(row.get("sect_name")) in WORLD.sects]
    return sorted(dict.fromkeys(names))


def _sect_recruitment_at_location(location: str) -> list[str]:
    out: list[str] = []
    for sect_name in WORLD.sects:
        rec = recruitment_definition(WORLD.sects, sect_name)
        if rec and str(rec.get("location") or "") == str(location):
            out.append(sect_name)
    return sorted(out)


async def sect_known_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    needle = current.casefold().strip()
    names = await _known_sect_names(interaction.user.id, c)
    return [
        app_commands.Choice(
            name=f"{name} — {WORLD.sects[name].get('specialty','Sect')}"[:100], value=name[:100]
        )
        for name in names if not needle or needle in name.casefold()
    ][:25]


async def sect_local_trial_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    known = set(await _known_sect_names(interaction.user.id, c))
    needle = current.casefold().strip()
    names = [name for name in _sect_recruitment_at_location(str(c.get("location") or "")) if name in known]
    return [
        app_commands.Choice(name=f"{name} — Entrance Trial"[:100], value=name[:100])
        for name in names if not needle or needle in name.casefold()
    ][:25]


async def sect_recommender_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    from ..main import current_npc_location
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    wt = await current_world_time()
    needle = current.casefold().strip()
    out: list[app_commands.Choice[str]] = []
    for name, npc in WORLD.npcs.items():
        if not bool(npc.get("can_recommend")) or not npc.get("sect_affiliation"):
            continue
        npc_location = await current_npc_location(name, wt.period)
        if npc_location != str(c.get("location") or ""):
            continue
        if needle and needle not in name.casefold() and needle not in str(npc.get("sect_affiliation")).casefold():
            continue
        out.append(app_commands.Choice(
            name=f"{name} — {npc.get('sect_affiliation')}"[:100], value=name[:100]
        ))
    return out[:25]


@registered_group_command(sect_recruitment_group, name="status", description="Show sects, recruitment gates and recommendations your character actually knows")
async def sect_recruitment_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    membership = await DB.get_sect_membership(interaction.user.id)
    wt = await current_world_time()
    newly = await _sync_sect_discoveries(interaction.user.id, c, game_minute=wt.total_minutes)
    known = await _known_sect_names(interaction.user.id, c)
    recommendations = await DB.get_active_sect_recommendations(interaction.user.id)
    lines = ["🏯 **Sect Recruitment Journal**"]
    if membership:
        lines.append(f"Current public sect: **{membership['sect_name']} — {membership['rank_name']}**")
    else:
        lines.append("Current public sect: **Unaffiliated**")
    if newly:
        lines.append(f"🧭 Newly recognized from discovered routes: **{', '.join(newly)}**")
    lines.append("\n**Known sects**")
    if not known:
        lines.append("• None yet. Explore the world, meet sect-affiliated NPCs, and listen for recruitment routes.")
    for sect_name in known:
        sect = WORLD.sects[sect_name]
        rec = recruitment_definition(WORLD.sects, sect_name) or {}
        at_gate = str(c.get("location") or "") == str(rec.get("location") or "")
        lines.append(
            f"• **{sect_name}** • {sect.get('alignment','Unknown')} • {sect.get('specialty','Unknown specialty')}\n"
            f"  Gate: **{rec.get('location','Unknown')}**{' • **You are here**' if at_gate else ''}"
        )
    lines.append("\n**Active NPC recommendations**")
    if recommendations:
        for row in recommendations:
            lines.append(
                f"• **{row['sect_name']}** via **{row['npc_name']}** • entrance bonus **+{int(row.get('bonus',0))}**"
            )
    else:
        lines.append("• None. Speak with a local sect-affiliated NPC before asking them to sponsor you.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_recruitment_group, name="info", description="Inspect the public recruitment story for a sect you have discovered")
@app_commands.autocomplete(sect_name=sect_known_autocomplete)
async def sect_recruitment_info(interaction: discord.Interaction, sect_name: str) -> None:
    c = await require_character(interaction)
    if not c:
        return
    known = set(await _known_sect_names(interaction.user.id, c))
    if sect_name not in known or sect_name not in WORLD.sects:
        await interaction.response.send_message("That sect has not been discovered by this character.", ephemeral=False)
        return
    sect = WORLD.sects[sect_name]
    rec = recruitment_definition(WORLD.sects, sect_name) or {}
    recommendation = await DB.get_active_sect_recommendation(interaction.user.id, sect_name)
    lines = [
        f"🏯 **{sect_name} — Recruitment**",
        f"Alignment: **{sect.get('alignment','Unknown')}**",
        f"Specialty: **{sect.get('specialty','Unknown')}**",
        f"Recruitment gate: **{rec.get('location','Unknown')}**",
        f"Entrance trial: **{rec.get('trial_name','Entrance Examination')}**",
        str(rec.get("description") or "A formal sect entrance examination."),
        f"Examiner: **{rec.get('examiner','Sect Examiner')}**",
    ]
    if recommendation:
        lines.append(
            f"\n📜 **Recommendation:** {recommendation['npc_name']} has sponsored your approach (**+{int(recommendation.get('bonus',0))}** to the trial checks)."
        )
    elif not bool(rec.get("public_route", True)):
        lines.append("\n🌑 This is not a public recruitment route. An affiliated NPC recommendation is normally required to reveal the way.")
    else:
        lines.append("\nA recommendation is optional, but a trusted sponsor can improve the entrance examination.")
    if str(c.get("location") or "") != str(rec.get("location") or ""):
        lines.append("\n🗺️ You must physically travel to the recruitment gate before attempting the trial.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_recruitment_group, name="recommendation", description="Ask a local sect-affiliated NPC to sponsor your entrance attempt")
@app_commands.autocomplete(npc=sect_recommender_autocomplete)
@serialized_user_action
async def sect_recruitment_recommendation(interaction: discord.Interaction, npc: str) -> None:
    from ..main import current_npc_location
    c=await require_character(interaction)
    if not c:return
    npc_data=await DB.get_npc_definition(npc)
    if not npc_data or not bool(npc_data.get('can_recommend')) or not npc_data.get('sect_affiliation'):
        await interaction.response.send_message("That NPC cannot issue a sect recommendation.",ephemeral=False);return
    sect_name=str(npc_data.get('sect_affiliation')); rec=recruitment_definition(WORLD.sects,sect_name)
    if not rec:
        await interaction.response.send_message("That NPC's sect has no public recruitment path configured.",ephemeral=False);return
    wt=await current_world_time(); npc_location=await current_npc_location(npc,wt.period)
    if npc_location!=str(c.get('location') or ''):
        await interaction.response.send_message(f"**{npc}** is currently at **{npc_location or 'an unknown location'}**, not **{await character_location_display(c)}**.",ephemeral=False);return
    memory=await DB.get_npc_memory(interaction.user.id,npc)
    if not memory.strip():
        await interaction.response.send_message(f"Speak with **{npc}** first; a recommendation requires established personal history.",ephemeral=False);return
    family=await DB.get_birth_family(interaction.user.id); reps=await DB.get_reputations(interaction.user.id); rep=next((int(x.get('score',0)) for x in reps if str(x.get('faction_key'))==sect_name),0)
    _,notes=recommendation_modifier(c,faction_reputation=rep,family=family,sect_alignment=str(WORLD.sects[sect_name].get('alignment','Neutral')))
    try:
        e=await ENGINE.authoritative_action("sect.recruitment.recommendation",interaction.user.id,{"npc_name":npc,"sect_name":sect_name,"location":str(rec.get('location') or ''),"details":{"modifier_notes":notes}},action_id=f"discord:{interaction.id}:sect.recruitment.recommendation"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    roll=dict(r.get('roll') or {}); roll_text=f"2d10 {int(roll.get('modifier',0)):+d} = **{int(roll.get('total',0))}** vs TN **{int(roll.get('tn',0))}**"
    tail=f"📜 Recommendation secured: +{int(r.get('recommendation_bonus',0))}." if r.get('success') else "The recommendation was not granted."
    await interaction.response.send_message(f"{roll_text}\n{tail}",ephemeral=False)


@registered_group_command(sect_recruitment_group, name="recommendations", description="List active NPC sect recommendations")
async def sect_recruitment_recommendations(interaction: discord.Interaction) -> None:
    if not await require_character(interaction):
        return
    rows = await DB.get_active_sect_recommendations(interaction.user.id)
    if not rows:
        await interaction.response.send_message("You hold no active sect recommendations.", ephemeral=False)
        return
    lines = ["📜 **Active Sect Recommendations**"]
    for row in rows:
        lines.append(f"• **{row['sect_name']}** — sponsor **{row['npc_name']}** • trial bonus **+{int(row.get('bonus',0))}**")
    lines.append("\nA recommendation is consumed when you take that sect's entrance trial. It does not guarantee admission.")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_group_command(sect_recruitment_group, name="trial", description="Take the story-driven entrance examination at your current sect gate")
@app_commands.autocomplete(sect_name=sect_local_trial_autocomplete)
@serialized_user_action
async def sect_recruitment_trial(interaction: discord.Interaction, sect_name: str) -> None:
    c=await require_character(interaction)
    if not c:return
    if sect_name not in WORLD.sects:
        await interaction.response.send_message("Unknown sect.",ephemeral=False);return
    wt=await current_world_time(); rec=recruitment_definition(WORLD.sects,sect_name); profile=trial_profile(WORLD.sects,sect_name)
    if not rec or not profile:
        await interaction.response.send_message("That sect has no configured entrance trial.",ephemeral=False);return
    recommendation=await DB.get_active_sect_recommendation(interaction.user.id,sect_name); recommendation_bonus=int(recommendation.get('bonus',0)) if recommendation else 0; family=await DB.get_birth_family(interaction.user.id)
    _,primary_notes,rejection=trial_modifier(c,rec,attribute=profile.primary_attribute,family=family,recommendation_bonus=recommendation_bonus)
    _,secondary_notes,rejection2=trial_modifier(c,rec,attribute=profile.secondary_attribute,family=family,recommendation_bonus=recommendation_bonus)
    if rejection or rejection2:
        await interaction.response.send_message(f"🚫 **Entrance refused before examination.** {rejection or rejection2}",ephemeral=False);return
    try:
        e=await ENGINE.authoritative_action("sect.recruitment.trial",interaction.user.id,{"sect_name":sect_name,"examiner":profile.examiner,"location":profile.location,"trial_name":profile.trial_name,"primary_details":primary_notes,"secondary_details":secondary_notes},action_id=f"discord:{interaction.id}:sect.recruitment.trial"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    outcome=str(r.get('outcome','fail')); p=dict(r.get('primary') or {}); q=dict(r.get('secondary') or {})
    await interaction.response.send_message(f"**{profile.trial_name}** — {outcome.replace('_',' ').title()}\nPrimary: **{p.get('total','?')}** vs TN **{p.get('tn','?')}**\nSecondary: **{q.get('total','?')}** vs TN **{q.get('tn','?')}**",ephemeral=False)


@registered_group_command(sect_recruitment_group, name="history", description="Review your recent sect recommendation and entrance-trial history")
async def sect_recruitment_history(interaction: discord.Interaction) -> None:
    if not await require_character(interaction):
        return
    rows = await DB.get_recent_sect_recruitment_attempts(interaction.user.id, limit=12)
    if not rows:
        await interaction.response.send_message("You have no sect recruitment history yet.", ephemeral=False)
        return
    lines = ["📚 **Sect Recruitment History**"]
    for row in rows:
        kind = "Recommendation" if str(row.get("attempt_type")) == "recommendation" else "Entrance Trial"
        result = str(row.get("result", "unknown")).replace("_", " ").title()
        actor = f" • {row.get('npc_name')}" if row.get("npc_name") else ""
        lines.append(f"• **{row.get('sect_name')}** — {kind}: **{result}**{actor}")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_group_command(sect_group, name="form", description="Choose which English martial-sibling titles are used for your character")
@app_commands.choices(style=ADDRESS_STYLE_CHOICES)
async def sect_form(interaction: discord.Interaction, style: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await DB.set_address_style(interaction.user.id, style.value)
    examples = {
        "masculine": "Senior Brother / Junior Brother",
        "feminine": "Senior Sister / Junior Sister",
        "neutral": "Senior / Junior Martial Sibling",
    }
    await interaction.response.send_message(
        f"✅ Your normal English sect address is now **{examples[style.value]}**.",
        ephemeral=False,
    )


@registered_group_command(sect_group, name="status", description="Show sect membership and direct lineage")
async def sect_status(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    target = member or interaction.user
    c = await DB.get_character(target.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False)
        return
    membership = await DB.get_sect_membership(target.id)
    master = await DB.get_master(target.id)
    lines = [f"🏯 **Sect Record — {c['name']}**"]
    if membership:
        lines += [
            f"Sect: **{membership['sect_name']}**",
            f"Rank: **{membership['rank_name']}** (level {membership['rank_level']})",
            f"Contribution points: **{membership.get('contribution_points', 0)}**",
            f"Influence: **{membership.get('influence', 0)}**",
        ]
        snap = await DB.get_lineage_snapshot(target.id)
        if snap.get("person", {}).get("master_attention") is not None and master:
            lines.append(f"Master attention: **{snap['person'].get('master_attention', 0)}**")
    else:
        lines.append("Sect: **Unaffiliated / not recorded**")
    lines.append(f"Master: **{master['name']}**" if master else "Master: *none recorded*")
    style_names = {
        "masculine": "Senior Brother / Junior Brother",
        "feminine": "Senior Sister / Junior Sister",
        "neutral": "Senior / Junior Martial Sibling",
    }
    lines.append(f"Normal address form: **{style_names.get(c.get('address_style', 'neutral'), 'Senior / Junior Martial Sibling')}**")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)




@registered_group_command(sect_disciple_group, name="status", description="View your master, disciples and pending player contracts")
async def sect_discipleship_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    snap = await DB.get_lineage_snapshot(interaction.user.id)
    incoming = await DB.get_disciple_requests(interaction.user.id, incoming=True)
    outgoing = await DB.get_disciple_requests(interaction.user.id, incoming=False)
    lines = [f"🎓 **Master-Disciple Record — {c['name']}**"]
    master = snap.get("master")
    lines.append(f"Master: **{master['name']}**" if master else "Master: *none*")
    disciples = list(snap.get("disciples") or [])
    lines.append("Direct disciples: " + (", ".join(f"**{x['name']}**" for x in disciples[:15]) if disciples else "*none*"))
    if incoming:
        lines.append("\n**Requests awaiting your answer**")
        for row in incoming[:15]:
            lines.append(f"• `#{row['request_id']}` — **{row['other_name']}**, realm {row['other_realm_index']} stage {row['other_phase']}")
    if outgoing:
        lines.append("\n**Requests you have sent**")
        for row in outgoing[:15]:
            lines.append(f"• `#{row['request_id']}` → **{row['other_name']}**")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_disciple_group, name="request", description="Ask a stronger cultivator to formally become your master")
@serialized_user_action
async def sect_discipleship_request(interaction: discord.Interaction, master: discord.Member) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("discipleship.request",interaction.user.id,{"master_user_id":master.id},action_id=f"discord:{interaction.id}:discipleship.request")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}",ephemeral=False); return
    await interaction.followup.send(f"🙏 Discipleship request `#{result.get('request_id')}` sent to {master.mention}.",ephemeral=False)


@registered_group_command(sect_disciple_group, name="accept", description="Accept a pending disciple request addressed to you")
@serialized_user_action
async def sect_discipleship_accept(interaction: discord.Interaction, request_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("discipleship.resolve",interaction.user.id,{"request_id":int(request_id),"accept":True},action_id=f"discord:{interaction.id}:discipleship.resolve")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}",ephemeral=False); return
    await interaction.followup.send("🙏 Discipleship request accepted.",ephemeral=False)


@registered_group_command(sect_disciple_group, name="reject", description="Reject a pending disciple request addressed to you")
@serialized_user_action
async def sect_discipleship_reject(interaction: discord.Interaction, request_id: int) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("discipleship.resolve",interaction.user.id,{"request_id":int(request_id),"accept":False},action_id=f"discord:{interaction.id}:discipleship.resolve")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}",ephemeral=False); return
    await interaction.followup.send("Discipleship request rejected.",ephemeral=False)


@registered_group_command(sect_disciple_group, name="leave", description="Sever your current master-disciple bond")
@serialized_user_action
async def sect_discipleship_leave(interaction: discord.Interaction, confirm: bool = False) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("discipleship.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:discipleship.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}",ephemeral=False); return
    await interaction.followup.send("🧵 Your discipleship bond has been ended.",ephemeral=False)


SECT_ABODE_ACTIONS = [
    app_commands.Choice(name="Status / Open Thread", value="status"),
    app_commands.Choice(name="Enter Sect Abode", value="enter"),
    app_commands.Choice(name="Leave Sect Abode", value="leave"),
]


@registered_group_command(sect_group, name="abode", description="Open, enter or leave the private residence assigned by your public sect")
@app_commands.choices(action=SECT_ABODE_ACTIONS)
@serialized_user_action
async def sect_abode(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    from ..main import ensure_sect_abode_record, ensure_sect_abode_thread_for
    c = await require_character(interaction)
    if not c:
        return
    membership = await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not a public sect member, so no sect abode is assigned.", ephemeral=False)
        return
    abode = await ensure_sect_abode_record(interaction.user.id, c, membership)
    thread = await ensure_sect_abode_thread_for(interaction.guild, interaction.user, abode) if interaction.guild else None
    if action.value == "status":
        await interaction.response.send_message(
            f"🏯 **{abode['name']}**\nSect: **{abode['sect_name']}**\nSect gate: **{abode['base_location']}**\n"
            f"Current location: **{await character_location_display(c)}**\n"
            + (f"Private scene: {thread.mention}" if thread else "⚠️ Private scene thread is unavailable; repair the base channels."),
            ephemeral=False,
        )
        return
    if action.value == "leave":
        if str(c.get("location") or "") != str(abode["location_key"]):
            await interaction.response.send_message("You are not inside your sect abode.", ephemeral=False)
            return
        await DB.set_location(interaction.user.id, str(abode["base_location"]))
        if thread:
            try: await thread.send(f"🚪 **{c['name']}** leaves the sect abode and returns to **{abode['base_location']}**.")
            except discord.HTTPException: pass
        await interaction.response.send_message(f"You leave your sect abode and return to **{abode['base_location']}**.", ephemeral=False)
        return
    if str(c.get("location") or "") != str(abode["base_location"]):
        await interaction.response.send_message(
            f"Travel to the sect gate at **{abode['base_location']}** before entering your assigned residence.", ephemeral=False
        )
        return
    await DB.set_location(interaction.user.id, str(abode["location_key"]))
    if thread:
        try: await thread.send(f"🏯 **{c['name']}** enters **{abode['name']}**. This private thread is now the active residence scene.")
        except discord.HTTPException: pass
    await interaction.response.send_message(
        f"🏯 You enter **{abode['name']}**." + (f" Continue in {thread.mention}." if thread else ""), ephemeral=False
    )


@registered_group_command(sect_group, name="shadow", description="Investigate the hidden Heaven-Devouring Demon Sect and its karma gates")
@app_commands.choices(action=[
    app_commands.Choice(name="Investigate", value="investigate"),
    app_commands.Choice(name="Status", value="status"),
    app_commands.Choice(name="Accept initiation", value="initiate"),
])
@serialized_user_action
async def sect_shadow(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    c=await require_character(interaction)
    if not c:return
    sect_def=WORLD.sects.get("Heaven-Devouring Demon Sect",{})
    karma=int(c.get('karma_score',0)); hidden=await DB.get_hidden_sect_membership(interaction.user.id)
    world_name=WORLD.realm_world(int(c.get('realm_index',0)))
    branch=str((sect_def.get('branches') or {}).get(world_name, 'Unknown Shadow Cell'))
    righteous_enemy=int(sect_def.get('righteous_enemy',50)); observe=int(sect_def.get('karma_observation',-50)); initiate=int(sect_def.get('karma_initiation',-200))
    if hidden and karma>=righteous_enemy and str(hidden.get('status'))!='enemy':
        hidden=await DB.set_hidden_sect_status(interaction.user.id,'enemy',standing_delta=-100)
    if action.value=='status':
        if hidden:
            await interaction.response.send_message(
                f"🌑 **Heaven-Devouring Demon Sect**\nBranch: **{hidden['branch_name']}** • Rank **{hidden['rank_name']}** • Status **{hidden['status']}** • Standing **{hidden['standing']:+d}**\nKarma: **{karma:+d}**. Reaching righteous karma (**+{righteous_enemy}**) turns the hidden sect hostile.",ephemeral=False);return
        if karma<=observe:
            await interaction.response.send_message(f"🌫️ Your karma **{karma:+d}** has attracted unseen observation from **{branch}**, but you are not initiated.",ephemeral=False);return
        await interaction.response.send_message("🌫️ You find rumors and contradictory signs, but no hidden-sect contact reveals itself to this incarnation.",ephemeral=False);return
    if action.value=='investigate':
        if karma>=righteous_enemy:
            await interaction.response.send_message(f"☀️ Your righteous karma **{karma:+d}** marks you as a probable enemy. Shadow messengers avoid open contact; concealed hostility is more likely than recruitment.",ephemeral=False);return
        if karma<=initiate:
            await interaction.response.send_message(f"🌑 **{branch}** stops merely observing you. Your karma **{karma:+d}** satisfies the initiation gate. You may use **/sect → Sect → Shadow** and choose **Accept initiation**.",ephemeral=False);return
        if karma<=observe:
            await interaction.response.send_message(f"👁️ You detect a watcher from **{branch}**. Your karma **{karma:+d}** is dark enough for observation, but initiation requires **{initiate}** or lower.",ephemeral=False);return
        await interaction.response.send_message(f"You uncover only dead drops and false trails. A karma stain of **{observe}** or lower is normally required before the sect takes interest.",ephemeral=False);return
    if hidden and str(hidden.get('status'))=='active':
        await interaction.response.send_message("You are already an active hidden-sect initiate.",ephemeral=False);return
    if karma>initiate:
        await interaction.response.send_message(f"The initiation seal remains cold. Required karma: **{initiate} or lower**; yours is **{karma:+d}**.",ephemeral=False);return
    if karma>=righteous_enemy:
        await interaction.response.send_message("The hidden sect recognizes you as a righteous enemy, not a recruit.",ephemeral=False);return
    wt=await current_world_time(); hidden=await DB.initiate_hidden_sect(interaction.user.id,sect_name="Heaven-Devouring Demon Sect",branch_name=branch,game_minute=wt.total_minutes)
    candidates=[]
    for mid,m in WORLD.manuals.items():
        if str(m.get('alignment','')).casefold()!='demonic': continue
        if str(m.get('path',''))!=str(c.get('path','')): continue
        if int(m.get('min_realm_index',0))<=int(c.get('realm_index',0)): candidates.append((int(m.get('min_realm_index',0)),mid,m))
    granted=None
    if candidates:
        _,mid,m=max(candidates,key=lambda x:(x[0],x[1])); item_id=str(m.get('item_id',''))
        if item_id:
            await DB.add_items(interaction.user.id,{item_id:1}); granted=m
            await DB.record_item_provenance(interaction.user.id,item_id,source_type='hidden_sect_initiation',source_key=branch,ownership_mark='Heaven-Devouring Seal',legal_status='forbidden',tracking_strength=70,game_minute=wt.total_minutes)
    text=f"🌑 You accept the **Heaven-Devouring Demon Sect** initiation in **{branch}**. Hidden rank: **{hidden['rank_name']}**. This affiliation is stored separately from your public sect lineage."
    if granted: text+=f"\n📕 Initiation inheritance: **{granted['name']}** was placed in your inventory; study it with **/cultivation → Manuals & Techniques → Study**."
    await interaction.response.send_message(text,ephemeral=False)


@registered_group_command(sect_group, name="roster", description="Show your sect hierarchy, ranks, contribution and influence")
async def sect_roster(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    membership=await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not recorded as a sect member.",ephemeral=False);return
    roster=await DB.get_sect_roster(str(membership['sect_name']))
    lines=[f"🏯 **{membership['sect_name']} — Hierarchy**"]
    for row in roster[:40]:
        lines.append(
            f"\n**{row['rank_name']}** — {row['name']} • "
            f"{WORLD.realm_name(int(row['realm_index']))} Stage {row['phase']} • "
            f"CP {row.get('contribution_points',0)} • Influence {row.get('influence',0)}"
        )
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(sect_group, name="politics", description="Show current sect influence, master attention and resource pressure")
async def sect_politics(interaction: discord.Interaction) -> None:
    from ..main import SIM
    if not await require_character(interaction):
        return
    membership = await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not a sect member.", ephemeral=False)
        return
    roster = await DB.get_sect_roster(str(membership["sect_name"]))
    treasury = await DB.get_sect_treasury(str(membership["sect_name"]))
    snap = await DB.get_lineage_snapshot(interaction.user.id)
    attention = snap.get("person", {}).get("master_attention", 0)
    sim_politics = await SIM.sect_status(str(membership["sect_name"]))
    lines = [
        f"🏯 **{membership['sect_name']} — Internal Politics**",
        f"Your rank: **{membership['rank_name']}**",
        f"Contribution points: **{membership.get('contribution_points', 0)}**",
        f"Institutional influence: **{membership.get('influence', 0)}**",
        f"Master attention: **{attention if snap.get('master') else 'No recorded master'}**",
    ]
    if sim_politics:
        lines.extend([
            f"Sect influence: **{sim_politics.get('influence',0)}** • Cohesion: **{sim_politics.get('cohesion',0)}/100** • Resources: **{sim_politics.get('resources',0)}**",
            f"Recruitment pressure: **{sim_politics.get('recruitment_pressure',0)}/100** • Doctrine pressure: **{sim_politics.get('doctrine_pressure',0)}/100**",
        ])
        factions=list(sim_politics.get('factions') or [])[:3]
        if factions:
            lines.append("\n**Autonomous internal factions**")
            for faction in factions:
                lines.append(f"• **{faction['faction_name']}** — power {faction['power']}% • loyalty {faction['loyalty']}/100\n  {faction['agenda']}")
        relations=list(sim_politics.get('relations') or [])[:4]
        if relations:
            lines.append("\n**External relations**")
            for relation in relations:
                lines.append(f"• **{relation['other']}** — {str(relation.get('relation_type','neutral')).title()} ({int(relation.get('relation_score',0)):+d}) • treaty {relation.get('treaty_status','none')}")
        events=list(sim_politics.get('events') or [])[:3]
        if events:
            lines.append("\n**Recent political incidents**")
            lines.extend(f"• {event['event_text']}" for event in events)
    lines.append("\n**Most influential members**")
    for row in sorted(roster, key=lambda r: (int(r.get('influence', 0)), int(r.get('rank_level', 0))), reverse=True)[:5]:
        lines.append(f"• {row['name']} — {row['rank_name']} • Influence {row.get('influence', 0)}")
    if treasury:
        scarce = sorted(treasury.items(), key=lambda kv: kv[1])[:5]
        lines.append("\n**Scarce stocked resources**")
        for item_id, qty in scarce:
            lines.append(f"• {WORLD.item_name(item_id)} x{qty}")
    else:
        lines.append("\n**Resource pressure:** the sect treasury is empty.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_group, name="treasury", description="Inspect resources currently available to your sect")
async def sect_treasury(interaction:discord.Interaction)->None:
    from ..main import SIM
    if not await require_character(interaction):return
    membership=await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not a sect member.",ephemeral=False);return
    treasury=await DB.get_sect_treasury(str(membership['sect_name']))
    lines=[f"📦 **{membership['sect_name']} Treasury**",f"Your contribution points: **{membership.get('contribution_points',0)}**"]
    if not treasury: lines.append("*No contributed materials are currently stocked.*")
    else:
        sim_state=await SIM.sect_status(str(membership['sect_name']))
        resources=int((sim_state or {}).get('resources',50))
        pressure_mult=1.60 if resources<25 else 1.35 if resources<50 else 1.20 if resources<80 else 1.00
        lines.append(f"Autonomous resource pressure: **{resources}** • redemption multiplier **x{pressure_mult:.2f}**")
        for item_id,qty in treasury.items():
            cost=max(1,int(round(WORLD.item_sect_value(item_id)*pressure_mult)))
            lines.append(f"• {WORLD.item_name(item_id)} x{qty} — **{cost} CP each**")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(sect_group, name="contribute", description="Donate materials to your sect for contribution points and influence")
@app_commands.autocomplete(item=carried_item_autocomplete)
@serialized_user_action
async def sect_contribute(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999]=1)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("sect.contribute",interaction.user.id,{"item_id":item,"quantity":int(quantity)},action_id=f"discord:{interaction.id}:sect.contribute")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}",ephemeral=False); return
    await interaction.followup.send(f"🏯 Contributed **{WORLD.item_name(item)} x{quantity}** to the sect treasury.",ephemeral=False)


async def sect_treasury_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    membership=await DB.get_sect_membership(interaction.user.id)
    if not membership:return []
    treasury=await DB.get_sect_treasury(str(membership['sect_name']));needle=current.casefold().strip();out=[]
    for item_id,qty in treasury.items():
        name=WORLD.item_name(item_id)
        if not needle or needle in name.casefold() or needle in item_id.casefold():out.append(app_commands.Choice(name=f"{name} x{qty}"[:100],value=item_id[:100]))
    return out[:25]


@registered_group_command(sect_group, name="redeem", description="Exchange contribution points for stocked sect resources")
@app_commands.autocomplete(item=sect_treasury_item_autocomplete)
@serialized_user_action
async def sect_redeem(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999]=1)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("sect.redeem",interaction.user.id,{"item_id":item,"quantity":int(quantity)},action_id=f"discord:{interaction.id}:sect.redeem")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}",ephemeral=False); return
    await interaction.followup.send(f"🏯 Redeemed **{WORLD.item_name(item)} x{quantity}** from the sect treasury.",ephemeral=False)


@registered_group_command(sect_manor_group, name="status", description="Inspect your sect's shared manor, facilities and recent construction")
async def sect_manor_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    membership = await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not a sect member.", ephemeral=False)
        return
    sect_name = str(membership["sect_name"])
    manor = await DB.get_sect_manor(sect_name)
    treasury = await DB.get_sect_treasury(sect_name)
    if not manor:
        lines = [
            f"🏯 **{sect_name} — No Sect Manor Yet**",
            "A Sect Master or Ancestor can establish one at a normal world location once the shared treasury holds the foundation materials.",
            f"Foundation cost: **{WORLD.item_names(SECT_MANOR_ESTABLISHMENT_COST)}**",
            f"Current treasury toward foundation: **{WORLD.item_names({k: min(v, treasury.get(k,0)) for k,v in SECT_MANOR_ESTABLISHMENT_COST.items()})}**",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=False)
        return
    lines = [
        f"🏯 **{manor['name']} — {sect_name}**",
        f"Seat: **{manor['base_location']}**",
        f"Your rank: **{membership['rank_name']}** • Contribution Points: **{membership.get('contribution_points',0)}**",
        "",
        "**Facilities & active benefits**",
    ]
    lines.extend(f"• {line}" for line in manor_benefit_lines(manor))
    lines.append("\n**Next upgrades**")
    for key, definition in SECT_MANOR_FACILITIES.items():
        level = int(manor.get(str(definition["column"]), 0))
        if level >= MAX_MANOR_FACILITY_LEVEL:
            lines.append(f"• **{definition['name']}** — MAX Lv.{MAX_MANOR_FACILITY_LEVEL}")
        else:
            lines.append(f"• **{definition['name']}** Lv.{level} → Lv.{level+1}: {WORLD.item_names(manor_upgrade_cost(key, level))}")
    projects = await DB.get_sect_manor_projects(sect_name, limit=5)
    if projects:
        lines.append("\n**Recent construction**")
        for project in projects:
            if str(project.get("project_type")) == "establish":
                lines.append(f"• Foundation established • {WORLD.item_names(project.get('cost',{}))}")
            else:
                facility = SECT_MANOR_FACILITIES.get(str(project.get("facility_key")), {})
                lines.append(
                    f"• {facility.get('name', project.get('facility_key','Facility'))} "
                    f"Lv.{project.get('from_level',0)} → Lv.{project.get('to_level',0)} • {WORLD.item_names(project.get('cost',{}))}"
                )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_manor_group, name="establish", description="Spend shared treasury materials to establish the sect's one persistent manor")
@serialized_user_action
async def sect_manor_establish(interaction: discord.Interaction, name: str, confirm: bool = False) -> None:
    c=await require_character(interaction)
    if not c:return
    if not confirm:
        await interaction.response.send_message(f"🏯 Establish **{name[:80]}** here? Repeat with **confirm:true** to lay the foundation.",ephemeral=False);return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("sect.manor.establish",interaction.user.id,{"name":name},action_id=f"discord:{interaction.id}:sect.manor.establish"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    established_location = r.get('base_location') or await character_location_display(c)
    await interaction.response.send_message(f"🏯 **{r.get('name',name)}** is established at **{established_location}**.",ephemeral=False)


@registered_group_command(sect_manor_group, name="upgrade", description="Upgrade a shared manor facility using materials from the sect treasury")
@app_commands.choices(facility=SECT_MANOR_FACILITY_CHOICES)
@serialized_user_action
async def sect_manor_upgrade(
    interaction: discord.Interaction, facility: app_commands.Choice[str], confirm: bool = False
) -> None:
    if not await require_character(interaction):return
    if not confirm:
        await interaction.response.send_message(f"🏗️ Upgrade **{facility.name}**? Repeat with **confirm:true** to spend shared treasury materials.",ephemeral=False);return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("sect.manor.upgrade",interaction.user.id,{"facility":facility.value},action_id=f"discord:{interaction.id}:sect.manor.upgrade"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"🏗️ **{facility.name} upgraded to Lv.{r.get('level','?')}**.",ephemeral=False)


@registered_group_command(sect_group, name="address", description="Show the proper English martial-family title for another cultivator")
async def sect_address(
    interaction: discord.Interaction, member: discord.Member, show_chinese: bool = False
) -> None:
    observer = await require_character(interaction)
    if not observer:
        return
    target = await DB.get_character(member.id)
    if not target:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False)
        return
    result = await DB.get_address_context(interaction.user.id, member.id)
    if not result:
        await interaction.response.send_message("No relationship information could be resolved.", ephemeral=False)
        return
    display = result["display_chinese"] if show_chinese else result["display"]
    await interaction.response.send_message(
        f"🪷 **{observer['name']} → {target['name']}**\n{display}", ephemeral=False
    )


@registered_group_command(sect_group, name="family", description="Check your martial-family tree")
async def sect_family(interaction: discord.Interaction, show_chinese: bool = False) -> None:
    c = await require_character(interaction)
    if not c:
        return
    text = await _build_family_text(interaction.user.id, show_chinese=show_chinese)
    if not text:
        await interaction.response.send_message("No sect lineage is recorded for your character.", ephemeral=False)
        return
    await reply_long(interaction, text, ephemeral=False)

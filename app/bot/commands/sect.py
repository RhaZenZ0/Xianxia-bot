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
others below do. Four (``SIM``, ``current_npc_location``,
``ensure_sect_abode_record``, ``ensure_sect_abode_thread_for``) stay in
main.py, where they are used far more than by ``/sect`` alone, and are pulled
in with a call-time ``from ..main import`` inside the one function that needs
each - a module-level import of any of them would be a genuine cycle, since
main.py imports this module at its own module level. ``_sect_recruitment_at_location``
is the mirror image: it is DEFINED here but used once outside this module (in
main.py's `/explore` road-discovery flow), so main.py imports it back, exactly
as it already does for ``sect_group`` itself.

Update, split phases 2-4 (v0.19.34-v0.19.39): all five of those names now live
below main.py (``SIM`` in services.py, the two location helpers in
locations.py, the two sect-abode helpers in threads.py) and are ordinary
module-level imports. This module no longer imports main.py at all.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ...rules.sect import resolve_address
from ...rules.sect_manor import (
    MAX_MANOR_FACILITY_LEVEL,
    SECT_MANOR_ESTABLISHMENT_COST,
    SECT_MANOR_FACILITIES,
    manor_benefit_lines,
    manor_upgrade_cost,
)
from ...rules.sect_recruitment import (
    recommendation_modifier,
    recruitment_definition,
    trial_modifier,
    trial_profile,
)
from ..registry import registered_group_command
from ..locations import DEAD, current_npc_location, npc_whereabouts, npcs_present
from ..character_state import record_quest_progress, announce_quest_progress
from ..formatting import player_property_facility_lines, player_property_unbuilt
from ..services import PLAYER_PROPERTY_FACILITY_LABELS, QUESTS, SIM
from ..threads import ensure_sect_abode_record, ensure_sect_abode_thread_for
from ..runtime import (
    _explain_engine_error,
    DB,
    ENGINE,
    WORLD,
    carried_item_autocomplete,
    character_location_display,
    current_world_time,
    log,
    reply_long,
    require_character,
    respond,
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


async def _address_context(observer_user_id: int, target_user_id: int) -> dict[str, Any] | None:
    """How the observer addresses the target, from two lineage snapshots.

    The snapshot is the repository's; the rule (resolve_address) is the
    rules tier's; putting them together is presentation, so it happens here
    and not in app/database (v0.30.0).
    """
    observer_snapshot = await DB.get_lineage_snapshot(observer_user_id)
    target_snapshot = await DB.get_lineage_snapshot(target_user_id)
    if not observer_snapshot or not target_snapshot:
        return None
    result = resolve_address(
        observer_snapshot["person"],
        target_snapshot["person"],
        observer_membership=observer_snapshot.get("membership"),
        target_membership=target_snapshot.get("membership"),
        observer_master=observer_snapshot.get("master"),
        target_master=target_snapshot.get("master"),
        observer_grandmaster=observer_snapshot.get("grandmaster"),
        observer_master_master=observer_snapshot.get("grandmaster"),
        sibling_rows=observer_snapshot.get("siblings", []),
        master_sibling_rows=observer_snapshot.get("master_siblings", []),
    )
    return {
        "title": result.title,
        "pinyin": result.pinyin,
        "hanzi": result.hanzi,
        "translation": result.translation,
        "reason": result.reason,
        "display": result.display,
        "display_chinese": result.display_chinese,
    }


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
            rel = await _address_context(user_id, int(row["user_id"]))
            lines.append(f"• {_relationship_label(rel, show_chinese=show_chinese)}: {row['name']}")

    if snap.get("siblings"):
        lines.append("\n**Your martial siblings:**")
        for row in snap["siblings"][:20]:
            rel = await _address_context(user_id, int(row["user_id"]))
            lines.append(f"• {_relationship_label(rel, show_chinese=show_chinese)}: {row['name']}")

    if snap.get("disciples"):
        lines.append("\n**Your direct disciples:**")
        for row in snap["disciples"][:20]:
            lines.append(f"• Disciple: {row['name']}")

    if len(lines) <= 2 and not snap.get("master"):
        lines.append("*No master/disciple lineage has been recorded yet.*")
    return "\n".join(lines)



async def _sync_sect_discoveries(user_id: int, character: dict, *, game_minute: int | None = None) -> list[str]:
    """Promote already-discovered recruitment locations into public sect knowledge.

    Which sects that is has been the engine's to say since v1.3.1: it reads the
    gates the cultivator knows (`knownLocationsTx`) and discovers exactly the
    sects standing on them, so nothing here names a sect - a list sent from
    the client could only narrow, and a client could never widen it.
    """
    del game_minute  # the engine stamps its own minute (rc.48)
    del character
    try:
        result = dict(await ENGINE.action("sect.discover", user_id, {
            "discovery_kind": "recruitment_route",
        }) or {})
    except GameEngineError:
        log.exception("Sect discovery reconcile failed for user %s", user_id)
        return []
    return [str(name) for name in (result.get("discovered") or [])]


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
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    wt = await current_world_time()
    here = str(c.get("location") or "")
    needle = current.casefold().strip()
    sponsors: dict[str, str] = {}
    for name, npc in WORLD.npcs.items():
        if not bool(npc.get("can_recommend")) or not npc.get("sect_affiliation"):
            continue
        npc_location = await current_npc_location(name, wt.period)
        if npc_location != here:
            continue
        sponsors[name] = str(npc.get("sect_affiliation"))
    # A recruitment delegation's elder is a sponsor too (v1.1.0), and he is in
    # no catalogue: the engine stamped his sect onto the cast row at spawn.
    # This picker is the only way the hub can ask him - its command takes no
    # argument - so a sponsor missing here is a sponsor nobody can reach.
    try:
        cast = await DB.list_active_event_npcs(here)
    except Exception:
        log.exception("Could not read the event cast at %s", here)
        cast = []
    for row in cast:
        if int(row.get("can_recommend") or 0) and str(row.get("sect_name") or ""):
            sponsors.setdefault(str(row.get("name")), str(row.get("sect_name")))
    out: list[app_commands.Choice[str]] = []
    for name, sect_name in sorted(sponsors.items()):
        if needle and needle not in name.casefold() and needle not in sect_name.casefold():
            continue
        out.append(app_commands.Choice(name=f"{name} — {sect_name}"[:100], value=name[:100]))
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
        # v1.1.0: this used to say "explore the world", and no road reaches a
        # sect gate, so exploring never found one. These are the doors that do.
        lines.append(
            "• None yet. The envoys' hall in a realm capital's temple quarter names every public gate of its world "
            "(**/world → City → Envoys**); a sect's recruitment delegation at a world event can sponsor you "
            "(**/sect → Recruitment → Recommendation**)."
        )
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
        lines.append(
            "• None. A sponsor standing where you are can put their name to you "
            "(**/sect → Recruitment → Recommendation**): **+N on both trial rolls**."
        )
    # A route recognised here is a sect discovered (v1.1.0): the quest that
    # asks for one had no reporter a player could reach. Recorded before the
    # page is sent, told after it (v1.0.5).
    progressed = []
    if newly:
        progressed = await record_quest_progress(interaction.user.id, "sect_discovery", amount=1, game_minute=wt.total_minutes)
    await reply_long(interaction, "\n".join(lines), ephemeral=False)
    await announce_quest_progress(interaction, progressed)


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
    c=await require_character(interaction)
    if not c:return
    npc_data=await DB.get_npc_definition(npc)
    if not npc_data or not bool(npc_data.get('can_recommend')) or not npc_data.get('sect_affiliation'):
        await interaction.response.send_message("That NPC cannot issue a sect recommendation.",ephemeral=False);return
    sect_name=str(npc_data.get('sect_affiliation')); rec=recruitment_definition(WORLD.sects,sect_name)
    if not rec:
        await interaction.response.send_message("That NPC's sect has no public recruitment path configured.",ephemeral=False);return
    wt=await current_world_time(); npc_location=await current_npc_location(npc,wt.period)
    if npc_location==DEAD:
        await interaction.response.send_message(f"**{npc}** is dead and recommends nobody.",ephemeral=False);return
    if npc_location!=str(c.get('location') or ''):
        # The one whereabouts rule (v1.20.2): never a missing sponsor's true
        # position, never a place the player has not found.
        where=await npc_whereabouts(interaction.user.id,c,npc) or "is not here"
        await interaction.response.send_message(f"**{npc}** {where}, not at **{await character_location_display(c)}**.",ephemeral=False);return
    # v1.1.0: a "speak with them first" check stood here and never fired -
    # `get_npc_memory` answers a sentence, never "", for somebody you have not
    # met - and it is gone rather than fixed: asking is the conversation.
    family=await DB.get_birth_family(interaction.user.id); reps=await DB.get_reputations(interaction.user.id); rep=next((int(x.get('score',0)) for x in reps if str(x.get('faction_key'))==sect_name),0)
    _,notes=recommendation_modifier(c,faction_reputation=rep,family=family,sect_alignment=str(WORLD.sects[sect_name].get('alignment','Neutral')))
    # Whom the sponsor speaks for and the gate their word reveals are the
    # engine's (v1.1.0): it used to write whatever `location` this sent onto
    # the travel list, where a road-less place is an instant jump.
    try:
        e=await ENGINE.authoritative_action("sect.recruitment.recommendation",interaction.user.id,{"npc_name":npc,"details":{"modifier_notes":notes}},action_id=f"discord:{interaction.id}:sect.recruitment.recommendation"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    sect_name=str(r.get('sect_name') or sect_name); gate=str(r.get('gate') or '')
    roll=dict(r.get('roll') or {}); roll_text=f"2d10 {int(roll.get('modifier',0)):+d} = **{int(roll.get('total',0))}** vs TN **{int(roll.get('tn',0))}**"
    if r.get('success'):
        lines=[f"📜 **{npc}** puts their name to you for the **{sect_name}**: **+{int(r.get('recommendation_bonus',0))} on both entrance-trial rolls**."]
        seat=str(r.get('seat') or '')
        if gate and seat:
            # The gate is a district of the sect's seat (v1.19.0): the city is
            # the route, and the gate a step inside it.
            lines.append(f"🗺️ **{seat}** is on your travel list — **/travel** there, **/world → City → Enter** the **{gate}**, then sit the trial with **/sect → Recruitment → Trial**.")
        elif gate:
            lines.append(f"🗺️ **{gate}** is on your travel list — **/travel** there, then sit the trial with **/sect → Recruitment → Trial**.")
    else:
        lines=[
            f"**{npc}** will not vouch for you yet. You may ask again after a world day.",
            f"Standing with the **{sect_name}** makes a sponsor likelier to agree: whoever keeps its gate has entry-level work open to those in no sect (**/npc → People → Talk** there).",
        ]
    progressed=[]
    if r.get('new_sect'):
        progressed=await record_quest_progress(interaction.user.id,"sect_discovery",amount=1,game_minute=(await current_world_time()).total_minutes)
    await interaction.response.send_message("\n".join([roll_text,*lines]),ephemeral=False)
    await announce_quest_progress(interaction,progressed)


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
        # The gate and the examiner are the engine's, read off the catalogue
        # (v1.1.0); this sends neither.
        e=await ENGINE.authoritative_action("sect.recruitment.trial",interaction.user.id,{"sect_name":sect_name,"trial_name":profile.trial_name,"primary_details":primary_notes,"secondary_details":secondary_notes},action_id=f"discord:{interaction.id}:sect.recruitment.trial"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    outcome=str(r.get('outcome','fail')); p=dict(r.get('primary') or {}); q=dict(r.get('secondary') or {})
    text=f"**{profile.trial_name}** — {outcome.replace('_',' ').title()}\nPrimary: **{p.get('total','?')}** vs TN **{p.get('tn','?')}**\nSecondary: **{q.get('total','?')}** vs TN **{q.get('tn','?')}**"
    granted=dict(r.get('granted_manual') or {})
    if granted:
        # v0.21.3: the engine bestows one entry manual on joining, in the same
        # transaction as the membership. Python only says so.
        tier=int(granted.get('min_realm_index',0) or 0)
        when="study it now" if tier<=int(c.get('realm_index',0)) else f"study it once you reach **{WORLD.realm_name(tier)}**"
        text+=(f"\n📕 **{sect_name}** bestows its entry inheritance: **{granted.get('name','a manual')}** is in your inventory — "
               f"{when} with **/cultivation → Arts → Study**.")
    # The "A Road Toward a Sect" quest's sect_trial objective was never
    # reported anywhere, so the quest could not complete (found while building
    # the Quest Forge, v0.20.6). Recorded before the reply, told after (v1.0.5).
    wt_trial = await current_world_time()
    progressed = await record_quest_progress(interaction.user.id, "sect_trial", amount=1, game_minute=wt_trial.total_minutes)
    await interaction.response.send_message(text,ephemeral=False)
    await announce_quest_progress(interaction, progressed)


@registered_group_command(sect_recruitment_group, name="ascend", description="Carry your sect's letter to the gate of the allied sect one world above and be taken in")
@serialized_user_action
async def sect_recruitment_ascend(interaction: discord.Interaction) -> None:
    """The way up into an allied sect (v1.18.0).

    A sect is for life and the trial refuses anybody already in one, so a
    cultivator who took the sect road at realm 1 carried a Mortal sect through
    three worlds. Each public sect names in content the sect one world above
    (`ascends_to`); a member standing at that sect's gate, at the world's own
    floor, is taken in as an Outer Disciple on their elders' letter. No roll,
    and the payload carries nothing: which sect, which gate and which floor
    are the engine's to read off the catalogue (v1.1.0).
    """
    c = await require_character(interaction)
    if not c:
        return
    # The letter is the first thing this handler does, so the interaction is
    # acknowledged before the engine is asked (`test_ack_before_mutation`): a
    # token that expired mid-call would otherwise retry a mutation that landed.
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True)
    try:
        e = await ENGINE.authoritative_action("sect.ascend", interaction.user.id, {}, action_id=f"discord:{interaction.id}:sect.ascend")
        r = dict(e.get("result") or {})
    except GameEngineError as exc:
        await respond(interaction, f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    text = (f"📜 **The way up.** The letter of the **{r.get('from')}** is read at **{r.get('gate')}**, and the "
            f"**{r.get('to')}** takes you in as **{r.get('rank_name')}**. Your standing begins again here: "
            f"rank, contribution and the disciple bond were your old sect's.")
    await respond(interaction, text, ephemeral=False)


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
        rung = WORLD.next_promotion_rung(int(membership.get("rank_level") or 0))
        if rung and int(membership.get("contribution_earned") or 0) >= rung[1]:
            lines.append(f"🎖️ Eligible for **{WORLD.sect_rank_name(rung[0])}** - ask your master or an Elder (**/sect → Sect → Promote**).")
        elif rung:
            lines.append(f"Next rank: **{WORLD.sect_rank_name(rung[0])}** at **{rung[1]}** contribution earned (you have earned {int(membership.get('contribution_earned') or 0)}).")
    else:
        lines.append("Sect: **Unaffiliated / not recorded**")
    if not master:
        try:
            npc_master = await DB.get_npc_master(target.id)
        except Exception:
            npc_master = None
        if npc_master:
            master = {"name": f"{npc_master['name']} ({npc_master.get('sect_rank') or 'of the sect'})"}
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
    if not master:
        try:
            npc_master = await DB.get_npc_master(interaction.user.id)
        except Exception:
            npc_master = None
        if npc_master:
            master = {"name": f"{npc_master['name']}, {npc_master.get('sect_rank') or 'of the sect'} (one of the sect's own)"}
    lines.append(f"Master: **{master['name']}**" if master else "Master: *none* - **Npcmaster** asks one of the sect's people here")
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
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
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
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
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
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
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
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send("🧵 Your discipleship bond has been ended.",ephemeral=False)


# The sect's own people as masters, and a rank granted by somebody (v1.24.0).
# The engine decides every one of these (`discipleship.npc_request`,
# `sect.master.teach`, `sect.promote`); the pickers below only offer the people
# it would hear - rc.46's rule, a surface must not offer what the engine will
# refuse - and read the bars off the same content the engine reads.

async def _sect_people_here(user_id: int, c: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The member's sect row and the sect's people standing where they stand,
    through the one resolver of who is here (`npcs_present`). A failed read is
    nobody here, never an exception in a picker."""
    try:
        membership = await DB.get_sect_membership(user_id)
        if not membership:
            return None, []
        here = str(c.get("location") or "")
        present = set(await npcs_present(here, (await current_world_time()).period))
        roster = await DB.get_sect_npc_roster(str(membership["sect_name"]))
    except Exception:
        log.exception("Could not read the sect's people here")
        return None, []
    return membership, [row for row in roster if str(row.get("name")) in present]


def _npc_choice(row: dict[str, Any], note: str = "") -> app_commands.Choice[str]:
    label = f"{row['name']} — {row.get('sect_rank') or 'member'}, {WORLD.realm_name(int(row.get('realm_index') or 0))}"
    return app_commands.Choice(name=(label + (f" · {note}" if note else ""))[:100], value=str(row["name"])[:100])


async def sect_npc_master_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    _membership, people = await _sect_people_here(interaction.user.id, c)
    rule = WORLD.npc_master_rule()
    power = int(c.get("realm_index") or 0) * 10 + int(c.get("phase") or 0)
    needle = current.casefold().strip()
    out = []
    for row in people:
        if WORLD.sect_rank_level(str(row.get("sect_rank") or "")) < rule["min_rank_level"]:
            continue
        if int(row.get("realm_index") or 0) * 10 + int(row.get("phase") or 0) <= power:
            continue
        if needle and needle not in str(row["name"]).casefold():
            continue
        out.append(_npc_choice(row))
    return out[:25]


async def sect_promoter_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    membership, people = await _sect_people_here(interaction.user.id, c)
    if not membership:
        return []
    rung = WORLD.next_promotion_rung(int(membership.get("rank_level") or 0))
    if not rung:
        return []
    try:
        master = await DB.get_npc_master(interaction.user.id)
    except Exception:
        master = None
    master_name = str((master or {}).get("name") or "")
    rule = WORLD.npc_master_rule()
    needle = current.casefold().strip()
    out = []
    for row in people:
        level = WORLD.sect_rank_level(str(row.get("sect_rank") or ""))
        is_master = str(row["name"]) == master_name
        if level <= rung[0] or (not is_master and level < rule["promoter_rank_level"]):
            continue
        if needle and needle not in str(row["name"]).casefold():
            continue
        out.append(_npc_choice(row, "your master" if is_master else ""))
    return out[:25]


@registered_group_command(sect_disciple_group, name="npcmaster", description="Ask one of your sect's own people, standing here, to take you as their disciple")
@app_commands.autocomplete(npc=sect_npc_master_autocomplete)
@serialized_user_action
async def sect_discipleship_npcmaster(interaction: discord.Interaction, npc: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    try:
        envelope=await ENGINE.authoritative_action("discipleship.npc_request",interaction.user.id,{"npc_name":npc},action_id=f"discord:{interaction.id}:discipleship.npc_request")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    lines=[f"🙇 **{result.get('master_npc_name')}** ({result.get('master_rank') or 'of the sect'}) takes you as their disciple."]
    gifts=[]
    if int(result.get("breakthrough_bonus") or 0):
        gifts.append(f"**{int(result['breakthrough_bonus']):+d}** on every breakthrough")
    if int(result.get("insight_on_realm") or 0):
        gifts.append(f"**{int(result['insight_on_realm'])}** insight each time you cross a realm")
    if float(result.get("cultivation_mult") or 1)!=1:
        gifts.append(f"cultivation **×{float(result['cultivation_mult']):g}**, behind a closed door too")
    if gifts:
        lines.append("While they live: " + ", ".join(gifts) + ".")
    if result.get("teach_rank"):
        lines.append(f"From **{result['teach_rank']}** they will teach you the sect's art (**/sect → Discipleship → Teach**), and they can raise your rank when you have earned it (**/sect → Sect → Promote**).")
    await interaction.followup.send("\n".join(lines),ephemeral=False)


@registered_group_command(sect_disciple_group, name="teach", description="Ask your master among the sect's people to teach you the sect's art")
@serialized_user_action
async def sect_discipleship_teach(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    try:
        envelope=await ENGINE.authoritative_action("sect.master.teach",interaction.user.id,{},action_id=f"discord:{interaction.id}:sect.master.teach")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    await interaction.followup.send(
        f"📕 **{result.get('master_npc_name')}** hands you **{result.get('name')}** and goes through its first forms with you. "
        f"It is in your bags; study it with **/cultivation → Arts → Study**.",ephemeral=False)


@registered_group_command(sect_group, name="promote", description="Ask your master, or an Elder of your sect standing here, to raise you a rank")
@app_commands.autocomplete(npc=sect_promoter_autocomplete)
@serialized_user_action
async def sect_promote(interaction: discord.Interaction, npc: str) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    try:
        envelope=await ENGINE.authoritative_action("sect.promote",interaction.user.id,{"npc_name":npc},action_id=f"discord:{interaction.id}:sect.promote")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    lines=[f"🎖️ **{result.get('granted_by')}** raises you to **{result.get('promoted_to')}** of the **{result.get('sect_name')}**."]
    if result.get("eligible_for"):
        lines.append(f"Your contribution already reaches **{result['eligible_for']}** - ask again.")
    await interaction.followup.send("\n".join(lines),ephemeral=False)


SECT_ABODE_ACTIONS = [
    app_commands.Choice(name="Status / Open Thread", value="status"),
    app_commands.Choice(name="Enter Sect Abode", value="enter"),
    app_commands.Choice(name="Leave Sect Abode", value="leave"),
    app_commands.Choice(name="Build or raise a facility", value="upgrade"),
]
# The residence a sect assigns grows the way a homestead does (v0.30.1),
# paid in contribution points and capped by rank and stage - the engine
# holds every one of those rules (sect.abode.upgrade). The content names
# which facilities a sect residence has.
SECT_ABODE_FACILITY_KEYS: tuple[str, ...] = tuple(
    str(key) for key in (WORLD.data.get("sect_abode_system") or {}).get("facilities", ())
) or ("cultivation", "alchemy", "forge", "formation", "storage", "herb_garden")
SECT_ABODE_FACILITIES = [
    app_commands.Choice(name=PLAYER_PROPERTY_FACILITY_LABELS.get(key, key.replace("_", " ").title()), value=key)
    for key in SECT_ABODE_FACILITY_KEYS
][:25]


async def _sect_residence_upgrade(interaction: discord.Interaction, abode: dict[str, Any], facility: app_commands.Choice[str]) -> None:
    """Build or raise one facility of the sect residence through the engine (v0.30.1)."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False)
    try:
        envelope = await ENGINE.authoritative_action(
            "sect.abode.upgrade", interaction.user.id, {"facility": facility.value},
            action_id=f"discord:{interaction.id}:sect.abode.upgrade",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await respond(interaction, f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    label = PLAYER_PROPERTY_FACILITY_LABELS.get(facility.value, facility.name)
    level = int(result.get("level", 0) or 0)
    spent = f"for **{int(result.get('cost', 0) or 0)}** contribution points (**{int(result.get('remaining_points', 0) or 0)}** left)"
    if result.get("built") or level <= 1:
        await respond(interaction, f"🏯 The sect's artisans build a **{label}** in **{abode['name']}** {spent}.", ephemeral=False)
    else:
        await respond(interaction, f"🏯 **{label}** in **{abode['name']}** raised to level **{level}** {spent}.", ephemeral=False)


@registered_group_command(sect_group, name="abode", description="Open, enter, leave or develop the private residence assigned by your public sect")
@app_commands.choices(action=SECT_ABODE_ACTIONS, facility=SECT_ABODE_FACILITIES)
@serialized_user_action
async def sect_abode(interaction: discord.Interaction, action: app_commands.Choice[str], facility: app_commands.Choice[str] | None = None) -> None:
    c = await require_character(interaction)
    if not c:
        return
    # Entering and leaving are authoritative moves now, so ack before any of
    # the branches below can reach one.
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False)
    membership = await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await respond(interaction, "You are not a public sect member, so no sect abode is assigned.", ephemeral=False)
        return
    abode = await ensure_sect_abode_record(interaction.user.id, c, membership)
    thread = await ensure_sect_abode_thread_for(interaction.guild, interaction.user, abode) if interaction.guild else None
    if action.value == "status":
        facilities = " • ".join(player_property_facility_lines(abode, SECT_ABODE_FACILITY_KEYS)) or "No developed facilities"
        unbuilt = player_property_unbuilt(abode, SECT_ABODE_FACILITY_KEYS)
        await respond(interaction, 
            f"🏯 **{abode['name']}**\nSect: **{abode['sect_name']}** • Rank: **{membership.get('rank_name', 'Disciple')}**\n"
            f"Sect gate: **{abode['base_location']}**\n"
            f"Current location: **{await character_location_display(c)}**\n"
            f"Facilities: {facilities}\n"
            + (f"Not yet built: {', '.join(unbuilt)}\n" if unbuilt else "")
            + f"Contribution points: **{int(membership.get('contribution_points', 0) or 0)}** - a facility is built or raised with "
            "**/sect → Holdings → Abode** and **Build or raise a facility**; the sect caps each level by your rank and stage.\n"
            + (f"Private scene: {thread.mention}" if thread else "⚠️ Private scene thread is unavailable; repair the base channels."),
            ephemeral=False,
        )
        return
    if action.value == "upgrade":
        if facility is None:
            await respond(interaction, "Choose which facility to build or raise: " + ", ".join(choice.name for choice in SECT_ABODE_FACILITIES) + ".", ephemeral=False)
            return
        await _sect_residence_upgrade(interaction, abode, facility)
        return
    if action.value == "leave":
        try:
            await ENGINE.authoritative_action(
                "sect.abode.leave", interaction.user.id, {},
                action_id=f"discord:{interaction.id}:sect.abode.leave",
            )
        except GameEngineError as exc:
            await respond(interaction, _explain_engine_error(exc), ephemeral=False)
            return
        if thread:
            try: await thread.send(f"🚪 **{c['name']}** leaves the sect abode and returns to **{abode['base_location']}**.")
            except discord.HTTPException: pass
        await respond(interaction, f"You leave your sect abode and return to **{abode['base_location']}**.", ephemeral=False)
        return
    try:
        await ENGINE.authoritative_action(
            "sect.abode.enter", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:sect.abode.enter",
        )
    except GameEngineError as exc:
        await respond(interaction, _explain_engine_error(exc), ephemeral=False)
        return
    if thread:
        try: await thread.send(f"🏯 **{c['name']}** enters **{abode['name']}**. This private thread is now the active residence scene.")
        except discord.HTTPException: pass
    await respond(interaction, 
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
    # The engine owns the karma gates, the hostile transition and the
    # initiation (v0.23.0). A status read is an action too, because opening
    # this screen is what settles a membership the player's karma has already
    # turned - and that settling is a write, not a render.
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False)
    try:
        envelope=await ENGINE.authoritative_action(
            "sect.shadow", interaction.user.id, {"mode":"status"},
            action_id=f"discord:{interaction.id}:sect.shadow:status",
        )
    except GameEngineError as exc:
        await respond(interaction, f"❌ {_explain_engine_error(exc)}", ephemeral=False); return
    shadow=dict(envelope.get("result") or {})
    karma=int(shadow.get("karma",0)); hidden=shadow.get("membership") or None
    branch=str(shadow.get("branch") or "Unknown Shadow Cell")
    righteous_enemy=int(shadow.get("righteous_enemy",50)); observe=int(shadow.get("karma_observation",-50)); initiate=int(shadow.get("karma_initiation",-200))
    if action.value=='status':
        if hidden:
            await respond(interaction, 
                f"🌑 **Heaven-Devouring Demon Sect**\nBranch: **{hidden['branch_name']}** • Rank **{hidden['rank_name']}** • Status **{hidden['status']}** • Standing **{hidden['standing']:+d}**\nKarma: **{karma:+d}**. Reaching righteous karma (**+{righteous_enemy}**) turns the hidden sect hostile.",ephemeral=False);return
        if karma<=observe:
            await respond(interaction, f"🌫️ Your karma **{karma:+d}** has attracted unseen observation from **{branch}**, but you are not initiated.",ephemeral=False);return
        await respond(interaction, "🌫️ You find rumors and contradictory signs, but no hidden-sect contact reveals itself to this incarnation.",ephemeral=False);return
    if action.value=='investigate':
        if karma>=righteous_enemy:
            await respond(interaction, f"☀️ Your righteous karma **{karma:+d}** marks you as a probable enemy. Shadow messengers avoid open contact; concealed hostility is more likely than recruitment.",ephemeral=False);return
        if karma<=initiate:
            await respond(interaction, f"🌑 **{branch}** stops merely observing you. Your karma **{karma:+d}** satisfies the initiation gate. You may use **/sect → Sect → Shadow** and choose **Accept initiation**.",ephemeral=False);return
        if karma<=observe:
            await respond(interaction, f"👁️ You detect a watcher from **{branch}**. Your karma **{karma:+d}** is dark enough for observation, but initiation requires **{initiate}** or lower.",ephemeral=False);return
        await respond(interaction, f"You uncover only dead drops and false trails. A karma stain of **{observe}** or lower is normally required before the sect takes interest.",ephemeral=False);return
    if hidden and str(hidden.get('status'))=='active':
        await respond(interaction, "You are already an active hidden-sect initiate.",ephemeral=False);return
    if karma>initiate:
        await respond(interaction, f"The initiation seal remains cold. Required karma: **{initiate} or lower**; yours is **{karma:+d}**.",ephemeral=False);return
    if karma>=righteous_enemy:
        await respond(interaction, "The hidden sect recognizes you as a righteous enemy, not a recruit.",ephemeral=False);return
    try:
        envelope=await ENGINE.authoritative_action(
            "sect.shadow", interaction.user.id, {"mode":"initiate"},
            action_id=f"discord:{interaction.id}:sect.shadow:initiate",
        )
    except GameEngineError as exc:
        await respond(interaction, f"❌ {_explain_engine_error(exc)}", ephemeral=False); return
    initiation=dict(envelope.get("result") or {})
    hidden=initiation.get("membership") or {}
    text=f"🌑 You accept the **Heaven-Devouring Demon Sect** initiation in **{initiation.get('branch',branch)}**. Hidden rank: **{hidden.get('rank_name','Shadow Initiate')}**. This affiliation is stored separately from your public sect lineage."
    if initiation.get("manual_name"): text+=f"\n📕 Initiation inheritance: **{initiation['manual_name']}** was placed in your inventory; study it with **/cultivation → Arts → Study**."
    elif initiation.get("manual_absent"): text+=f"\n📕 No initiation inheritance: {initiation['manual_absent']}. Return when you have cultivated further."
    await respond(interaction, text,ephemeral=False)


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
    # The sect's own people (v1.24.0): the hall the politics tick keeps full,
    # grouped by rank. A failed read costs this half of the page, not the page.
    try:
        people=await DB.get_sect_npc_roster(str(membership['sect_name']))
    except Exception:
        log.exception("Could not read the sect's NPC roster")
        people=[]
    if people:
        lines.append(f"\n\n👥 **The sect's people** ({len(people)})")
        by_rank:dict[str,list[str]]={}
        for person in people:
            by_rank.setdefault(str(person.get('sect_rank') or 'Member'),[]).append(
                f"{person['name']} ({WORLD.realm_name(int(person.get('realm_index') or 0))})")
        for rank,names in by_rank.items():
            lines.append(f"\n**{rank}** — " + ", ".join(names[:12]) + (f" and {len(names)-12} more" if len(names)>12 else ""))
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(sect_group, name="politics", description="Show current sect influence, master attention and resource pressure")
async def sect_politics(interaction: discord.Interaction) -> None:
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


async def _sect_exchange(user_id:int)->dict[str,Any]:
    """What this member may redeem, at the engine's price (v1.8.0)."""
    return dict(await ENGINE.action("sect.exchange",user_id,{}) or {})


@registered_group_command(sect_group, name="treasury", description="Your sect's stock: what it issues by rank and what members donated")
async def sect_treasury(interaction:discord.Interaction)->None:
    if not await require_character(interaction):return
    try:
        exchange=await _sect_exchange(interaction.user.id)
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
    if not exchange.get("member"):
        await interaction.response.send_message("You are not a sect member.",ephemeral=False);return
    # Every price, rank and threshold below is the engine's; this only prints.
    lines=[f"📦 **{exchange.get('sect_name')} — Exchange & Treasury**",
           f"You are a **{exchange.get('rank_name')}** • contribution points **{int(exchange.get('contribution_points') or 0)}**"]
    nxt=dict(exchange.get("next_rank") or {})
    if nxt and "contribution_earned" in exchange:
        lines.append(f"-# Earned in this sect: **{int(exchange['contribution_earned'])}** — "
                     f"**{nxt.get('rank_name')}** at {int(nxt.get('earned') or 0)}. Spending points never lowers it.")
    issued=list(exchange.get("issued") or [])
    if issued:
        lines.append("\n**Issued by the sect** — never out of stock")
        for row in issued:
            tag=" *(this sect only)*" if row.get("sect_only") else ""
            if row.get("eligible"):
                lines.append(f"• {row.get('name')} — **{int(row.get('points') or 0)} CP**{tag}")
            else:
                lines.append(f"• 🔒 {row.get('name')} — {int(row.get('points') or 0)} CP, needs **{row.get('min_rank_name')}**{tag}")
    treasury=list(exchange.get("treasury") or [])
    lines.append("\n**Donated by members**"+(f" — redemption ×{float(exchange.get('pressure_mult') or 1):.2f} while resources are short" if float(exchange.get('pressure_mult') or 1)>1 else ""))
    if not treasury: lines.append("*No contributed materials are currently stocked.*")
    for row in treasury:
        lines.append(f"• {row.get('name')} x{int(row.get('quantity') or 0)} — **{int(row.get('unit_cost') or 0)} CP each**")
    lines.append("-# Redeem with **/sect → Holdings → Redeem**. Earn points by donating, by your sect's commissions and by world events in its world.")
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
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    lines=[f"🏯 Contributed **{WORLD.item_name(item)} x{quantity}** to the sect treasury — **+{int(result.get('points') or 0)}** contribution."]
    if result.get("crafted_bonus"):
        lines.append(f"-# Your own trade's work: ×{float(result['crafted_bonus']):g}.")
    if result.get("capped_at_shelf"):
        lines.append(f"-# The sect credits no more than a shop asks: {int(result['capped_at_shelf'])} a unit.")
    if result.get("eligible_for"):
        lines.append(f"🎖️ You have earned enough to be raised to **{result['eligible_for']}** - ask your master or an Elder (**/sect → Sect → Promote**).")
    await interaction.followup.send("\n".join(lines),ephemeral=False)


async def sect_treasury_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    """The sect's issued stock and its donated treasury, both priced by the
    engine. An issued lot rides as `issued:<id>` so the redeem knows which
    to ask for; a lot above the member's rank is listed with the rank it
    needs, because a road nobody can see is a road nobody learns exists."""
    needle=current.casefold().strip();out=[]
    try:
        exchange=await _sect_exchange(interaction.user.id)
    except Exception:
        exchange={}
    if exchange:
        if not exchange.get("member"):return []
        for row in list(exchange.get("issued") or []):
            name=str(row.get("name") or row.get("item_id")); item_id=str(row.get("item_id"))
            if needle and needle not in name.casefold() and needle not in item_id.casefold():continue
            label=f"{name} — {int(row.get('points') or 0)} CP (sect-issued)" if row.get("eligible") else f"{name} — needs {row.get('min_rank_name')}"
            out.append(app_commands.Choice(name=label[:100],value=f"issued:{item_id}"[:100]))
        for row in list(exchange.get("treasury") or []):
            name=str(row.get("name") or row.get("item_id")); item_id=str(row.get("item_id"))
            if needle and needle not in name.casefold() and needle not in item_id.casefold():continue
            out.append(app_commands.Choice(name=f"{name} x{int(row.get('quantity') or 0)} — {int(row.get('unit_cost') or 0)} CP"[:100],value=item_id[:100]))
        return out[:25]
    # The engine did not answer: the treasury alone, so the picker is never
    # empty on a hiccup. The redeem itself is still the engine's.
    membership=await DB.get_sect_membership(interaction.user.id)
    if not membership:return []
    treasury=await DB.get_sect_treasury(str(membership['sect_name']))
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
        payload:dict[str,Any]={"item_id":item,"quantity":int(quantity)}
        if item.startswith("issued:"):
            payload={"item_id":item.removeprefix("issued:"),"quantity":int(quantity),"source":"issued"}
        envelope=await ENGINE.authoritative_action("sect.redeem",interaction.user.id,payload,action_id=f"discord:{interaction.id}:sect.redeem")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}",ephemeral=False); return
    where="issued by the sect" if result.get("source")=="issued" else "from the sect treasury"
    await interaction.followup.send(
        f"🏯 Redeemed **{WORLD.item_name(str(payload['item_id']))} x{quantity}** {where} for **{int(result.get('cost') or 0)} CP** "
        f"({int(result.get('remaining_points') or 0)} left).",ephemeral=False)


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
            # The rank is the content's (v1.17.1), the same key the engine refuses by.
            f"A {WORLD.sect_rank_name(int((WORLD.data.get('sect_abode_system') or {}).get('manor_founding_rank_level') or 0))} or higher can establish one at a normal world location once the shared treasury holds the foundation materials.",
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
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
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
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}",ephemeral=False);return
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
    result = await _address_context(interaction.user.id, member.id)
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

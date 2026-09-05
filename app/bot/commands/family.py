"""The /family hub: your NPC birth household, its people, and its fortunes.

Split stage 2. Stage 1 (v0.19.12-v0.19.14) moved the shared core into
``app/bot/runtime.py`` and cost two failed deploys, both of which this module is
shaped to avoid:

* **Definition order.** v0.19.12 built ``DB`` above the line assigning ``ROOT``.
  Everything here is a function or a literal, in the order it had in main.py.

* **Annotation resolution through ``functools.wraps``.** v0.19.13 died with
  ``NameError: name 'app_commands' is not defined``. Eight of the handlers below
  are wrapped by ``serialized_user_action``, which lives in runtime.py, and
  ``wraps`` cannot copy ``__globals__`` - so discord.py evaluates their
  annotation STRINGS against runtime.py's namespace, not this file's. Every
  annotation used here (``discord.Interaction``, ``app_commands.Choice[str]``,
  ``app_commands.Range[int, 1, 20]``, ``int``, ``str``) resolves there, and
  ``WrappingDecoratorAnnotationTests`` enforces that across the package.

Four names still live in main.py because the rest of main.py uses them far more
than /family does - ``SIM`` (31 other uses), ``_get_thread`` (5),
``ensure_birth_family_household_thread`` (3) and
``open_expedition_thread_after_exit`` (3). Each is imported inside the one
function that needs it. That is deliberate: a module-level ``from ..main import``
would be a genuine import cycle, while a call-time import runs long after main.py
has finished importing. There are exactly four, each used once.

Update, split phases 2-4 (v0.19.34-v0.19.39): all four of those names now live
below main.py (``SIM`` in services.py, ``_get_thread`` in channels.py, the two
thread helpers in threads.py) and are ordinary module-level imports. This
module no longer imports main.py at all; the paragraph above is kept as the
record of why the split was ordered the way it was.
"""

from __future__ import annotations

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ...rules.worldtime import MINUTES_PER_MONTH, MINUTES_PER_YEAR
from ...rules.birthfamily import family_tier_name
from ..registry import registered_group_command
from ..channels import _get_thread
from ..services import SIM
from ..threads import ensure_birth_family_household_thread, open_expedition_thread_after_exit
from ..runtime import (
    DB,
    ENGINE,
    GENDER_CHOICES,
    WORLD,
    authoritative_lifespan,
    current_world_time,
    log,
    reply_long,
    require_character,
    serialized_user_action,
)

# Used only by /family (the child-descendant listing), so it moves with it.
CHILD_CULTIVATION_AWAKENING_AGE = 12


family_group=app_commands.Group(name="family",description="Your NPC birth family, relatives, descendants, support and family fortunes")

async def _current_birth_family(user_id:int, *, simulate:bool=True)->dict|None:
    fam=await DB.get_birth_family(user_id)
    if not fam or not simulate:
        return fam
    c=await DB.get_character(user_id)
    wt=await current_world_time()
    if c and c.get("life_status")=="deceased":
        return fam
    try:
        await ENGINE.authoritative_action("family.simulate",int(user_id),{"family_id":int(fam['family_id']),"minutes_per_year":MINUTES_PER_YEAR},action_id=f"family:auto:{int(user_id)}:{wt.total_minutes}")
    except GameEngineError:
        log.exception("Go family simulation failed")
    return await DB.get_birth_family(user_id) or fam

@registered_group_command(family_group, name="view",description="View the NPC family you were born into")
async def birth_family_view(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded for this character.",ephemeral=False);return
    lines=[
        f"🏠 **{fam['family_name']} — {family_tier_name(int(fam.get('tier',1)))}**",
        f"Background: **{str(fam.get('archetype','family')).replace('_',' ').title()}**",
        f"Home: **{fam.get('location','Unknown')}**",
        f"Generation: **{fam.get('member_generation',1)}** • Your birth order: **#{fam.get('birth_order',1)}**",
        f"Family head: **{fam.get('head_title','Family Head')} {fam.get('head_name','Unknown')}** — {WORLD.realm_name(int(fam.get('head_realm_index',0)))} Stage {fam.get('head_phase',1)}",
        f"Wealth **{fam.get('wealth',0)}/100** • Influence **{fam.get('influence',0)}/100** • Stability **{fam.get('stability',0)}/100**",
        f"Bloodline status: **{str(fam.get('line_status','active')).title()}**",
        (f"Clan structure: **{str(fam.get('clan_structure','extended_household')).replace('_',' ').title()}**"),
        (f"Ancestral bloodline: **{fam.get('bloodline_name','None')}** • Purity **{fam.get('bloodline_purity',0)}%**" if int(fam.get('bloodline_purity',0)) > 0 else "Ancestral bloodline: **None awakened**"),
        "\n**Close relatives**",
    ]
    for npc in fam.get('npcs',[])[:12]:
        status_note = " ☠️" if npc.get("status")=="deceased" else ""
        lines.append(f"• **{npc['relation']}** — {npc['name']} • {WORLD.realm_name(int(npc.get('realm_index',0)))} Stage {npc.get('phase',1)}{status_note}")
    if c.get("life_status")=="deceased":
        state=await DB.get_reincarnation_state(interaction.user.id)
        lines.extend([
            "",
            "🕯️ **Past-Life Family**",
            "This household is **not** accelerated by your death. It continues only with normal shared world time.",
        ])
        if state:
            lines.append("Your soul is currently in **Samsara**; use **/character → Samsara** for the reincarnation clock.")
    household_key = f"birth_family:{int(fam.get('family_id') or 0)}"
    if str(c.get("location") or "") == household_key:
        lines.extend([
            "",
            "🏠 **You are inside this shared household right now.** Other player members who enter "
            "share the same scene, so you can roleplay together here.",
            "Exploring, hunting and travel need the open world — step outside with **/family → Leave** "
            f"to stand in **{fam.get('location') or 'your home region'}**.",
        ])
    else:
        lines.extend([
            "",
            "🏠 Use **/family → Enter** to visit the shared household. Players born into this same "
            "starter family meet in the same scene.",
        ])
    await reply_long(interaction,"\n".join(lines),ephemeral=False)

@registered_group_command(family_group, name="enter", description="Enter your shared birth-family household")
@serialized_user_action
async def birth_family_enter(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    fam = await DB.get_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.", ephemeral=False)
        return
    wt = await current_world_time()
    await interaction.response.defer(ephemeral=False)
    try:
        envelope = await ENGINE.authoritative_action(
            "family.household.enter",
            interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:family.household.enter",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ Could not enter the household: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    thread = await ensure_birth_family_household_thread(interaction, fam)
    players = [str(row.get("name") or "Cultivator") for row in (result.get("players_present") or [])]
    others = [name for name in players if name != str(c.get("name") or "")]
    presence = f"\nPresent with you: **{', '.join(others)}**" if others else "\nYou are currently the only player member inside."
    if thread is not None:
        await interaction.followup.send(
            f"🏠 Entered **{result.get('family_name') or fam.get('family_name')}**. Shared household scene: {thread.mention}{presence}",
            ephemeral=False,
        )
    else:
        await interaction.followup.send(
            f"🏠 Entered **{result.get('family_name') or fam.get('family_name')}**.{presence}\n"
            "The canonical shared location is active, but Discord could not create/recover its household thread.",
            ephemeral=False,
        )

@registered_group_command(family_group, name="leave", description="Leave your shared birth-family household")
@serialized_user_action
async def birth_family_leave(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    fam = await DB.get_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.", ephemeral=False)
        return
    wt = await current_world_time()
    household_row = None
    if interaction.guild is not None:
        household_row = await DB.get_birth_family_household_thread(interaction.guild.id, int(fam["family_id"]))
    await interaction.response.defer(ephemeral=False)
    try:
        envelope = await ENGINE.authoritative_action(
            "family.household.leave",
            interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:family.household.leave",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ Could not leave the household: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if interaction.guild is not None and household_row:
        thread = await _get_thread(interaction.guild, household_row.get("thread_id"))
        if thread is not None:
            try:
                await thread.remove_user(interaction.user)
            except (discord.Forbidden, discord.HTTPException):
                pass
    await interaction.followup.send(
        f"🚪 Left **{result.get('family_name') or fam.get('family_name')}** and returned to **{result.get('location') or fam.get('location')}**.",
        ephemeral=False,
    )
    await open_expedition_thread_after_exit(interaction)

@registered_group_command(family_group, name="clan",description="View bloodline, branches, retainers and martial-clan alliance ties")
async def birth_family_clan(interaction:discord.Interaction)->None:
    if not await require_character(interaction,allow_deceased=True):return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.",ephemeral=False);return
    purity=max(0,int(fam.get('bloodline_purity',0)))
    if purity <= 0:
        text=(f"🪶 **{fam['family_name']} — Extended Household**\n"
              "No awakened ancestral bloodline is currently recorded. A household can still rise into clan status through wealth, cultivation, marriage, inheritance, or major world events.")
    else:
        text=(f"🩸 **{fam['family_name']} — Clan Record**\n"
              f"Structure: **{str(fam.get('clan_structure','bloodline_clan')).replace('_',' ').title()}**\n"
              f"Bloodline: **{fam.get('bloodline_name','Unknown')}**\n"
              f"Affinity: **{fam.get('bloodline_affinity','Unknown')}**\n"
              f"Purity: **{purity}%**\n"
              f"Inherited tendency: {fam.get('bloodline_trait','Unknown')}\n\n"
              f"Branches: **{fam.get('branch_count',1)}** • Retainers/adopted household members: **{fam.get('retainer_count',0)}**\n"
              f"Martial alliance: **{fam.get('confederacy_name','Independent')}**\n\n"
              "Bloodline purity improves the chance that descendants inherit family cultivation potential, but it never guarantees a Spiritual Root or successful breakthrough.")
    clan = await SIM.clan_status(int(fam['family_id']))
    details=[]
    branches=list(clan.get('branches') or [])
    retainers=list(clan.get('retainers') or [])
    relations=list(clan.get('relations') or [])
    if branches:
        details.append("\n\n**Mechanical branches**")
        for branch in branches[:8]:
            details.append(f"• **{branch['branch_name']}** — {branch['status']} • strength {branch['martial_strength']} • loyalty {branch['loyalty']}/100 • ~{branch['members_estimate']} members")
    if retainers:
        details.append("\n**Retainer groups**")
        for group in retainers[:8]:
            details.append(f"• **{group['group_name']}** — {group['role']} • {group['members']} members • loyalty {group['loyalty']}/100 • {group['status']}")
    if relations:
        details.append("\n**Clan alliances / rivalries**")
        for relation in relations[:8]:
            details.append(f"• **{relation['partner_name']}** — {str(relation['relation_type']).replace('_',' ').title()} ({int(relation['relation_score']):+d})")
    await reply_long(interaction,text+"\n".join(details),ephemeral=False)


@registered_group_command(family_group, name="support",description="Ask your birth family for resources or emergency support")
@serialized_user_action
async def birth_family_support(interaction:discord.Interaction)->None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("family.support",interaction.user.id,{"cooldown_game_minutes":3*MINUTES_PER_MONTH},action_id=f"discord:{interaction.id}:family.support")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}",ephemeral=False); return
    await interaction.followup.send(f"🏠 **{result.get('family_name','Your family')} supports you.**\nReceived: **{int(result.get('stones',0))} Low-Grade Spirit Stones**",ephemeral=False)

@registered_group_command(family_group, name="history",description="View recent rises, setbacks and political changes in your family")
async def birth_family_history(interaction:discord.Interaction)->None:
    if not await require_character(interaction,allow_deceased=True):return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.",ephemeral=False);return
    history=list(fam.get('history',[]))[-15:]
    text=f"📜 **{fam['family_name']} — Recent History**\n"+("\n".join(f"• {x}" for x in history) if history else "No major family events have been recorded yet.")
    await reply_long(interaction,text,ephemeral=False)

@registered_group_command(family_group, name="ancestry", description="Trace persistent family history across your Samsara incarnations")
async def birth_family_ancestry(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 1, 20] = 10,
) -> None:
    if not await require_character(interaction, allow_deceased=True):
        return
    rows = await DB.get_samsara_dynasty_history(interaction.user.id, limit=int(limit))
    if not rows:
        await interaction.response.send_message(
            "☸️ No cross-incarnation dynasty transitions have been recorded yet. "
            "Your first Samsara rebirth will create the first persistent ancestry record.",
            ephemeral=False,
        )
        return

    state_names = {
        0: "Uninvestigated",
        1: "Clue Found",
        2: "Corroborated",
        3: "Confirmed",
    }
    lines = ["🧬 **Persistent Samsara Dynasty History**"]
    for row in reversed(rows):
        level = max(0, min(3, int(row.get("investigation_level", 0))))
        status = str(row.get("lineage_status") or "uncertain_lineage").replace("_", " ").title()
        history_id = int(row.get("history_id", 0))
        lines.extend([
            "",
            f"**Record #{history_id} — Incarnation {int(row.get('incarnation_number', 0))}**",
            f"{row.get('source_family_name', 'Unknown')} ({row.get('source_world', 'Unknown')}) → "
            f"{row.get('destination_family_name', 'Unknown')} ({row.get('destination_world', 'Unknown')})",
            f"Outcome: **{status}** • Investigation: **{state_names[level]}**",
            str(row.get("summary") or "No surviving summary."),
        ])
        if level >= 3:
            continuity = "confirmed" if int(row.get("blood_continuity", 0)) else "none — replacement/unrelated line"
            lines.append(f"Blood continuity: **{continuity}**")
        evidence = list(row.get("evidence") or [])[:level]
        if evidence:
            lines.append("Evidence: " + " | ".join(str(item) for item in evidence))
    lines.append("\nUse **/family → Investigate** with a record number to uncover and corroborate its surviving evidence.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(family_group, name="investigate", description="Investigate an extinct, replaced or surviving Samsara family connection")
@serialized_user_action
async def birth_family_investigate(interaction: discord.Interaction, history_id: int = 0) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "family.lineage.investigate",
            interaction.user.id,
            {"history_id": max(0, int(history_id))},
            action_id=f"discord:{interaction.id}:family.lineage.investigate",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return

    state = str(result.get("investigation_state") or "unknown").replace("_", " ").title()
    lines = [
        f"🔎 **Dynasty Investigation — Record #{int(result.get('history_id', 0))}**",
        f"{result.get('source_family', 'Unknown')} ({result.get('source_world', 'Unknown')}) → "
        f"{result.get('destination_family', 'Unknown')} ({result.get('destination_world', 'Unknown')})",
        f"Outcome: **{str(result.get('lineage_status') or 'unknown').replace('_', ' ').title()}**",
        f"Evidence state: **{state}**",
    ]
    evidence = list(result.get("evidence") or [])
    if evidence:
        lines.append("\n**Recovered evidence**")
        lines.extend(f"• {item}" for item in evidence)
    continuity = str(result.get("blood_continuity") or "not_yet_confirmed")
    if continuity == "confirmed_ancestral_continuity":
        lines.append("\n🩸 **Blood continuity confirmed.** The later house descends from the historical branch, but rank/resources remain independent.")
    elif continuity == "confirmed_no_blood_continuity":
        lines.append("\n✂️ **No blood continuity.** The later house is a replacement or unrelated family, not a descendant of the extinct line.")
    else:
        lines.append("\nBlood continuity remains **unconfirmed**; investigate this record again to strengthen the evidence.")

    leads = list(result.get("ancestral_leads") or [])
    if leads:
        lines.append("\n**Ancestral leads**")
        for lead in leads[:8]:
            label = str(lead.get("lead_kind") or "lead").replace("_", " ").title()
            lines.append(
                f"• **{label}: {lead.get('name', 'Unknown')}** — {lead.get('location', 'Unknown')} "
                f"({lead.get('world', 'Unknown')}) • danger {int(lead.get('danger', 0))}/100"
            )
            if lead.get("retainer_name"):
                lines.append(f"  Retainer: **{lead.get('retainer_name')}** — {lead.get('retainer_relation', 'ancestral witness')}")
    quests = [quest for quest in (result.get("investigation_quests") or []) if str(quest.get("status")) != "completed"]
    if quests:
        lines.append("\n**Available investigation quests**")
        for quest in quests[:8]:
            lines.append(
                f"• `#{int(quest.get('quest_id', 0))}` **{quest.get('title', 'Investigation')}** — "
                f"{int(quest.get('progress', 0))}/{int(quest.get('target', 1))}"
            )
        lines.append("Use **/family → Quest** to work an unlocked investigation quest.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(family_group, name="legacy", description="View ancestral sites, investigation quests, claims and dynasty conflicts")
async def birth_family_legacy(interaction: discord.Interaction, history_id: int = 0) -> None:
    if not await require_character(interaction, allow_deceased=True):
        return
    state = await DB.get_samsara_legacy_state(interaction.user.id, history_id=max(0, int(history_id)))
    leads = list(state.get("leads") or [])
    quests = list(state.get("quests") or [])
    claims = list(state.get("claims") or [])
    conflicts = list(state.get("conflicts") or [])
    if not any((leads, quests, claims, conflicts)):
        await interaction.response.send_message(
            "🏚️ No ancestral investigation content has been uncovered yet. Use **/family → Investigate** on a Samsara ancestry record first.",
            ephemeral=False,
        )
        return
    lines = ["🏛️ **Ancestral Legacy Ledger**"]
    if leads:
        lines.append("\n**Sites & surviving retainers**")
        for lead in leads[:15]:
            label = str(lead.get("lead_kind") or "lead").replace("_", " ").title()
            lines.append(
                f"• `#{int(lead.get('lead_id', 0))}` **{label}: {lead.get('name', 'Unknown')}** — "
                f"{lead.get('location', 'Unknown')} • {str(lead.get('status', 'unknown')).replace('_', ' ').title()} "
                f"• danger {int(lead.get('danger', 0))}/100"
            )
            if lead.get("retainer_name"):
                lines.append(f"  **{lead.get('retainer_name')}** — {lead.get('retainer_relation', 'ancestral witness')}")
    if quests:
        lines.append("\n**Investigation quests**")
        for quest in quests[:15]:
            hostile = " • ⚔️ hostile cause established" if int(quest.get("hostile_cause", 0)) else ""
            lines.append(
                f"• `#{int(quest.get('quest_id', 0))}` **{quest.get('title', 'Investigation')}** — "
                f"{quest.get('status', 'unknown')} {int(quest.get('progress', 0))}/{int(quest.get('target', 1))}{hostile}"
            )
    if claims:
        lines.append("\n**Dynasty claims**")
        for claim in claims[:12]:
            lines.append(
                f"• `#{int(claim.get('claim_id', 0))}` **{str(claim.get('claim_type', 'claim')).replace('_', ' ').title()}** "
                f"for {claim.get('dynasty_name', 'Unknown')} — {str(claim.get('status', 'unknown')).replace('_', ' ').title()} "
                f"• legitimacy {int(claim.get('legitimacy', 0))}/100"
            )
            if claim.get("resolution"):
                lines.append(f"  Resolution: **{str(claim.get('resolution')).replace('_', ' ').title()}**")
    if conflicts:
        lines.append("\n**Active / historical conflicts**")
        for conflict in conflicts[:12]:
            lines.append(
                f"• Claim `#{int(conflict.get('claim_id', 0))}` vs **{conflict.get('opponent_name', 'Unknown')}** — "
                f"{str(conflict.get('status', 'unknown')).title()} • "
                f"{int(conflict.get('player_progress', 0))} vs {int(conflict.get('opponent_progress', 0))}"
            )
            if conflict.get("outcome"):
                lines.append(f"  {conflict.get('outcome')}")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(family_group, name="quest", description="Work an unlocked ancestral archive, ruin, tomb or retainer investigation")
@serialized_user_action
async def birth_family_quest(interaction: discord.Interaction, history_id: int, quest_id: int = 0) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "family.lineage.quest",
            interaction.user.id,
            {"history_id": max(0, int(history_id)), "quest_id": max(0, int(quest_id))},
            action_id=f"discord:{interaction.id}:family.lineage.quest",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    lines = [
        f"🧭 **{result.get('title', 'Ancestral Investigation')}**",
        f"Progress: **{int(result.get('progress', 0))}/{int(result.get('target', 1))}**",
        f"State: **{str(result.get('status', 'unknown')).replace('_', ' ').title()}**",
    ]
    roll = dict(result.get("roll") or {})
    if roll:
        attribute = str(result.get("attribute") or "will").replace("_", " ").title()
        lines.append(
            f"Check: **{attribute} {int(roll.get('total', 0))} vs TN {int(result.get('difficulty_tn', roll.get('tn', 0)))}** "
            f"— {str(roll.get('degree') or 'Resolved')} • danger **{int(result.get('danger', 0))}/100**"
        )
    if not result.get("success", True):
        lines.append("❌ The attempt failed to advance the investigation.")
        if int(result.get("setback", 0)) > 0:
            lines.append(f"Setback: **-{int(result.get('setback', 0))} progress** from the existing investigation.")
        if int(result.get("vitality_loss", 0)) > 0:
            lines.append(f"🩸 The danger was real: **-{int(result.get('vitality_loss', 0))} vitality**.")
    if result.get("completed"):
        lines.append(f"Recovered evidence weight: **{int(result.get('reward_evidence', 0))}**")
        if result.get("hostile_cause"):
            lines.append(f"⚔️ Corroborated hostile culprit: **{result.get('culprit_name', 'Unknown')}**. A revenge claim is now legally/historically supportable.")
    await interaction.followup.send("\n".join(lines), ephemeral=False)


@registered_group_command(family_group, name="claim", description="Assert inheritance, restore a fallen dynasty, seek revenge, or challenge a replacement house")
@app_commands.choices(
    claim_type=[
        app_commands.Choice(name="Inheritance", value="inheritance"),
        app_commands.Choice(name="Dynasty Restoration", value="restoration"),
        app_commands.Choice(name="Ancestral Revenge", value="revenge"),
        app_commands.Choice(name="Challenge Replacement Family", value="replacement_challenge"),
    ]
)
@serialized_user_action
async def birth_family_claim(
    interaction: discord.Interaction,
    history_id: int,
    claim_type: app_commands.Choice[str],
) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "family.dynasty.claim",
            interaction.user.id,
            {"history_id": max(0, int(history_id)), "claim_type": claim_type.value},
            action_id=f"discord:{interaction.id}:family.dynasty.claim",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    lines = [
        f"⚖️ **{str(result.get('claim_type', 'claim')).replace('_', ' ').title()} — {result.get('dynasty_name', 'Unknown')}**",
        f"Status: **{str(result.get('status', 'unknown')).replace('_', ' ').title()}**",
        f"Legitimacy: **{int(result.get('legitimacy', 0))}/100** • Support: **{int(result.get('support', 0))}/100** • Opposition: **{int(result.get('opposition', 0))}/100**",
        f"Blood-based claim: **{'yes' if result.get('blood_based') else 'no'}**",
    ]
    if int(result.get("conflict_id", 0)) > 0:
        lines.append(f"Conflict opened. Use **/family → Conflict** with claim id **{int(result.get('claim_id', 0))}** to contest it.")
    elif result.get("resolution"):
        lines.append(f"Resolution: **{str(result.get('resolution')).replace('_', ' ').title()}**")
    await interaction.followup.send("\n".join(lines), ephemeral=False)


@registered_group_command(family_group, name="conflict", description="Advance an active inheritance, restoration, revenge or replacement-family conflict")
@app_commands.choices(
    tactic=[
        app_commands.Choice(name="Negotiate", value="negotiate"),
        app_commands.Choice(name="Expose Evidence", value="expose"),
        app_commands.Choice(name="Rally Supporters", value="rally"),
        app_commands.Choice(name="Investigate Weakness", value="investigate"),
        app_commands.Choice(name="Formal Duel", value="duel"),
    ]
)
@serialized_user_action
async def birth_family_conflict(
    interaction: discord.Interaction,
    claim_id: int,
    tactic: app_commands.Choice[str],
) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "family.dynasty.conflict",
            interaction.user.id,
            {"claim_id": max(0, int(claim_id)), "tactic": tactic.value},
            action_id=f"discord:{interaction.id}:family.dynasty.conflict",
        )
        result = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=False)
        return
    lines = [
        f"⚔️ **Dynasty Conflict vs {result.get('opponent', 'Unknown')}**",
        f"Tactic: **{str(result.get('tactic', '')).title()}**",
    ]
    player_roll = dict(result.get("player_roll") or {})
    opponent_roll = dict(result.get("opponent_roll") or {})
    if player_roll:
        attribute = str(result.get("attribute") or "will").replace("_", " ").title()
        lines.append(
            f"Your check: **{attribute} {int(player_roll.get('total', 0))} vs TN {int(result.get('player_tn', player_roll.get('tn', 0)))}** "
            f"— {str(player_roll.get('degree') or 'Resolved')}"
        )
    if opponent_roll:
        lines.append(
            f"Opposition check: **{int(opponent_roll.get('total', 0))} vs TN {int(result.get('opponent_tn', opponent_roll.get('tn', 0)))}** "
            f"— {str(opponent_roll.get('degree') or 'Resolved')}"
        )
    lines.extend([
        f"Round **{int(result.get('rounds', 0))}**: +{int(result.get('player_gain', 0))} claim pressure / +{int(result.get('opponent_gain', 0))} opposition",
        f"Progress: **{int(result.get('player_progress', 0))}** vs **{int(result.get('opponent_progress', 0))}**",
        f"State: **{str(result.get('status', 'active')).title()}**",
    ])
    if result.get("outcome"):
        lines.append(f"\n{result.get('outcome')}")
        if result.get("resolution"):
            lines.append(f"Resolution: **{str(result.get('resolution')).replace('_', ' ').title()}**")
    await interaction.followup.send("\n".join(lines), ephemeral=False)


@registered_group_command(family_group, name="child",description="Add a child to your family branch; descendants may awaken cultivation talent")
@app_commands.choices(gender=GENDER_CHOICES)
@serialized_user_action
async def birth_family_child(interaction:discord.Interaction,name:str,gender:app_commands.Choice[str])->None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time(); life=await authoritative_lifespan(interaction.user.id)
    if life.age_years<18:
        await interaction.response.send_message("Your character must be at least **18 years old** to have a recorded child.",ephemeral=False);return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.",ephemeral=False);return
    try:
        e=await ENGINE.authoritative_action("family.add_child",interaction.user.id,{"name":name,"gender":gender.value},action_id=f"discord:{interaction.id}:family.add_child"); result=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"👶 **{name.strip()}** is born as descendant `#{result.get('child_id')}`.",ephemeral=False)

@registered_group_command(family_group, name="descendants",description="View descendants in your family branch and whether they can cultivate")
async def birth_family_descendants(interaction:discord.Interaction)->None:
    if not await require_character(interaction,allow_deceased=True):return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.",ephemeral=False);return
    wt=await current_world_time(); rows=[n for n in fam.get('npcs',[]) if str(n.get('relation','')).startswith('Child of user')]
    if not rows:
        await interaction.response.send_message(f"🌿 **{fam['family_name']}** has no recorded descendants in your branch yet.",ephemeral=False);return
    lines=[f"🌿 **{fam['family_name']} — Your Descendants**"]
    for child in rows[:30]:
        age=max(0,wt.total_minutes-int(child.get('birth_game_minute',wt.total_minutes)))/MINUTES_PER_YEAR
        if age>=CHILD_CULTIVATION_AWAKENING_AGE:
            talent=(f"Awakened **{child['spiritual_root']}**" if child.get('spiritual_root')!='Mortal Root' else "No usable spiritual root awakened; currently walking a mortal path")
        else:
            talent=f"Cultivation talent unawakened until around age {CHILD_CULTIVATION_AWAKENING_AGE}"
        lines.append(f"• **{child['name']}** — age **{age:.1f}**\n  {talent}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)

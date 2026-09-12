"""Cultivation progression: cultivate, breakthrough, /seclusion, /body, /bodyperfect, /perfect and /tribulation.

Split phase 9a (v0.19.44, docs/history/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ...rules.progression_systems import ascension_gate
from ...simulation import MINUTES_PER_DAY
from ..character_state import current_effect_modifiers
from ..formatting import roll_line
from ..registry import registered_group_command, registered_root_command
from ..runtime import (
    _explain_engine_error,
    DB,
    ENGINE,
    SETTINGS,
    WORLD,
    character_location_display,
    current_world_time,
    log,
    player_property_label,
    reply_long,
    require_character,
    serialized_user_action,
    settle_seclusion_for_user,
)
from ..services import GUILD, NARRATOR, NARRATOR_CONTEXT

@registered_root_command(name="cultivate", description="Meditate and gather cultivation essence", guild=GUILD)
@serialized_user_action
async def cultivate(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.train",
            interaction.user.id,
            {"cooldown_seconds": SETTINGS.cultivate_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:cultivation.train",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    gain, total, cost = int(result.get("gain", 0)), int(result.get("total", 0)), int(result.get("cost", 0))
    extra = ""
    if int(result.get("resonance_bonus", 0)):
        extra += f"\n☯ Dual Cultivation Resonance added **+{int(result['resonance_bonus'])}** to the attempted gain before the stage cap."
    extra += f"\n🕰️ {result.get('period','World-time')} cultivation flow: **x{float(result.get('time_mult',1)):.2f}** Qi efficiency."
    if result.get("root_resonance"):
        extra += f"\n🌿 **{c.get('spiritual_root')}** resonates with **{result.get('season','the season')}**: +10% seasonal Qi efficiency."
    if float(result.get("effect_mult", 1)) != 1.0:
        extra += f"\n💊 Active effects modified cultivation efficiency to **x{float(result['effect_mult']):.2f}**."
    if float(result.get("soul_mult", 1)) > 1.0:
        extra += f"\n☸️ Soul Legacy talent echo: **x{float(result['soul_mult']):.2f}** cultivation efficiency."
    if float(result.get("world_mult", 1)) != 1.0:
        extra += f"\n🌏 The qi of the **{result.get('world_name') or 'world'}** is thick: **x{float(result['world_mult']):.2f}**."
    if float(result.get("era_mult", 1)) != 1.0:
        extra += f"\n🌌 **{result.get('era_name') or 'World Era'}** modifies cultivation to **x{float(result['era_mult']):.2f}**."
    if float(result.get("manor_mult", 1)) != 1.0:
        extra += f"\n🏯 **{result.get('manor_name') or 'Sect Manor'}** Qi Gathering Array: **x{float(result['manor_mult']):.2f}** cultivation efficiency."
    if int(result.get("storm_bonus", 0)):
        extra += f"\n⚡ Active Qi Storm added **+{int(result['storm_bonus'])}** before the stage cap."
    if int(result.get("perfection_gain", 0)):
        extra += f"\n★ Realm refinement deepens by **+{int(result['perfection_gain'])}%**."
    if float(result.get("manual_mult", 1)) != 1.0:
        chosen = "you practise" if result.get("manual_chosen") else "the best method you have learned"
        extra += f"\n📖 **{result.get('manual_name')}** ({result.get('manual_grade')} grade, {chosen}): **x{float(result['manual_mult']):.2f}**."
    if str(result.get("place_name") or ""):
        extra += f"\n🪨 **{result.get('place_name')}** — {result.get('place_quality') or 'ordinary'} ground: **x{float(result.get('place_mult', 1)):.2f}** cultivation efficiency."
    stance = str(result.get("stance") or "circulate")
    if stance != "circulate":
        extra += f"\n🧭 **{result.get('stance_label') or stance.title()}** stance: **x{float(result.get('stance_mult', 1)):.2f}** gain."
    if int(result.get("insight_xp_gain", 0)):
        extra += f"\n💡 Refining banks **+{int(result['insight_xp_gain'])} Insight XP** toward the realm gate."
    deviation = dict(result.get("deviation") or {})
    if deviation:
        extra += f"\n⚠️ The forced qi ran wild: **{deviation.get('name') or 'Qi Deviation'}** (severity {int(deviation.get('severity', 1))}). Treat it under **/character → Treatment**, or it drags every session down."
    if result.get("stage_full") and not gain:
        extra += "\n🪷 This stage is already full: the session gathered nothing, banked nothing and risked nothing. Break through before meditating again."
    ready = ""
    if result.get("ready"):
        ready = "\n✨ Stage 9 is full. Choose **/quest → Realm Perfection → Start** or **/quest → Main Progression → Breakthrough**." if int(c.get("phase", 1)) == 9 else "\n✨ You are ready to attempt **/quest → Main Progression → Breakthrough**."
    await interaction.followup.send(
        f"🧘 **{c['name']} cultivates.**\nYou circulate qi through your meridians and gain **+{gain} cultivation essence**.\nProgress: **{total}/{cost}**{extra}{ready}"
    )


STANCE_CHOICES = [
    app_commands.Choice(name="Circulate — the full gain, nothing risked", value="circulate"),
    app_commands.Choice(name="Refine — a fifth slower, banks Insight XP", value="refine"),
    app_commands.Choice(name="Force — a third faster, risks qi deviation", value="force"),
]


@registered_root_command(name="stance", description="Choose the meditation stance every cultivation session uses", guild=GUILD)
@app_commands.choices(stance=STANCE_CHOICES)
@serialized_user_action
async def stance_command(interaction: discord.Interaction, stance: app_commands.Choice[str]) -> None:
    """A better cultivation system (v1.0.0-rc.3): the stance is engine state
    - Go stores it and applies it to every session - so this only chooses."""
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.stance", interaction.user.id, {"stance": stance.value},
            action_id=f"discord:{interaction.id}:cultivation.stance",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    label = str(result.get("label") or stance.name.split("—")[0].strip())
    changed = bool(result.get("changed"))
    lines = [
        f"🧭 **{c['name']} settles into the {label} stance.**" if changed else f"🧭 **{c['name']} keeps the {label} stance.**",
        f"{result.get('description') or ''}".strip(),
        f"Every session under it gains **x{float(result.get('gain_mult', 1)):.2f}**. Meditate with **/cultivation → Cultivate**.",
    ]
    await interaction.followup.send("\n".join(line for line in lines if line), ephemeral=False)


@registered_root_command(name="insight", description="Spend Insight XP to bank the insight that opens the next realm gate", guild=GUILD)
@serialized_user_action
async def insight(interaction: discord.Interaction) -> None:
    """The realm gate (v1.0.0-rc.3): stage 9 into a new realm needs an insight
    banked here, or a completed Realm Perfection. Insight XP comes from
    exploration, quests, battles, a disciple's breakthrough and the Refine
    stance; the engine prices the gate and keeps the bank."""
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.insight", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:cultivation.insight",
        )
    except GameEngineError as exc:
        message = str(exc)
        if "already banked" in message:
            message = "An insight is already banked. It is spent when you cross the realm gate at **/quest → Main Progression → Breakthrough**."
        elif "costs" in message and "Insight XP" in message:
            message += " Insight XP comes from exploring, quests, battles and the Refine stance (**/cultivation → Cultivate → Stance**)."
        await interaction.followup.send(f"❌ {message}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    await interaction.followup.send(
        f"💡 **{c['name']} banks an insight into {result.get('next_realm') or 'the next realm'}.**\n"
        f"Spent **{int(result.get('cost', 0))} Insight XP** (**{int(result.get('insight_xp', 0))}** left). "
        f"The realm gate out of **{result.get('realm') or 'this realm'}** is open: fill Stage 9 and attempt **/quest → Main Progression → Breakthrough**.",
        ephemeral=False,
    )


seclusion_group = app_commands.Group(
    name="seclusion",
    description="Closed-door background cultivation that progresses with world time",
)


SECLUSION_MODE_CHOICES = [
    app_commands.Choice(name="Qi Cultivation", value="qi"),
    app_commands.Choice(name="Body Cultivation", value="body"),
]


@registered_group_command(seclusion_group, name="start", description="Enter closed-door cultivation for a number of world-days")
@app_commands.choices(mode=SECLUSION_MODE_CHOICES)
@serialized_user_action
async def seclusion_start(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    days: app_commands.Range[int, 1, 365] = 7,
) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    # The engine decides whether this location can hold a seclusion and what
    # the environment is worth (abode chamber, safe zone, sect manor array,
    # deployed formation): it holds every one of those facts. Since v0.30.0
    # this handler sends the intent and formats the answer.
    try:
        envelope = await ENGINE.authoritative_action(
            "seclusion.start", interaction.user.id,
            {"mode": mode.value, "duration_game_minutes": int(days) * MINUTES_PER_DAY,
             "location": str(c.get("location", ""))},
            action_id=f"discord:{interaction.id}:seclusion.start",
        )
        state = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    environment = dict(state.get("environment") or {})
    env_mult = float(state.get("environment_mult", 1.0))
    daily = int(state.get("projected_daily_gain", 0))
    if environment.get("site") == "abode":
        property_label = player_property_label({"property_type": environment.get("abode_property_type")})
        env_label = f"{property_label} cultivation chamber Lv.{int(environment.get('abode_level', 0))}"
    elif environment.get("site") == "sect_abode":
        env_label = f"{environment.get('abode_name') or 'sect residence'} cultivation chamber Lv.{int(environment.get('abode_level', 0))}"
    elif environment.get("manor_name"):
        env_label = f"{environment.get('manor_name')} • Qi Gathering Array Lv.{int(environment.get('manor_level', 0))}"
    else:
        env_label = "protected meditation site"
    array_mult = float(environment.get("array_mult", 1.0))
    if environment.get("array_name") and array_mult != 1.0:
        env_label += f" • {environment.get('array_name')} x{array_mult:.2f}"
    await interaction.followup.send(
        f"🔒 **Closed-Door Seclusion Begun**\n"
        f"Path: **{'Qi' if mode.value == 'qi' else 'Body'} Cultivation**\n"
        f"Duration: **{int(days)} world-days**\n"
        f"Location: **{await character_location_display(c)}** ({env_label})\n"
        f"Environment efficiency: **x{env_mult:.2f}**\n"
        f"Projected background gain: about **{daily} essence per completed world-day**.\n\n"
        "Progress is settled automatically while the bot is online and catches up after restarts. "
        "Seclusion never auto-breaks through a stage. Any state-changing command will remain locked until you use **/cultivation → Cultivate → End** or the planned seclusion completes."
    )


@registered_group_command(seclusion_group, name="status", description="Check closed-door cultivation progress")
async def seclusion_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    state = await settle_seclusion_for_user(interaction.user.id, wt.total_minutes)
    if not state:
        state = await DB.get_seclusion(interaction.user.id, active_only=False)
    if not state:
        await interaction.response.send_message("You have no recorded seclusion session.", ephemeral=False)
        return
    remaining = max(0, int(state["ends_game_minute"]) - wt.total_minutes) if str(state.get("status")) == "active" else 0
    elapsed = max(0, min(wt.total_minutes, int(state["ends_game_minute"])) - int(state["started_game_minute"]))
    start_location_display = await character_location_display(
        {"location": state.get("start_location"), "user_id": c.get("user_id")}
    )
    await interaction.response.send_message(
        f"🔒 **Seclusion Status**\n"
        f"State: **{str(state.get('status','unknown')).title()}**\n"
        f"Mode: **{str(state.get('mode','qi')).upper()}**\n"
        f"Location: **{start_location_display}**\n"
        f"Elapsed: **{elapsed / MINUTES_PER_DAY:.1f} world-days**\n"
        f"Remaining: **{remaining / MINUTES_PER_DAY:.1f} world-days**\n"
        f"Cultivation awarded: **{int(state.get('accumulated_gain',0))}**\n"
        f"Environment: **x{float(state.get('environment_mult',1.0)):.2f}**\n"
        + ("Use **/cultivation → Cultivate → End** to emerge early." if str(state.get("status")) == "active" else f"Ended: **{state.get('ended_reason') or 'completed'}**"),
        ephemeral=False,
    )


@registered_group_command(seclusion_group, name="end", description="Leave seclusion early and settle completed background cultivation")
@serialized_user_action
async def seclusion_end(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    state = await DB.get_seclusion(interaction.user.id)
    if not state:
        await interaction.response.send_message("You are not in active seclusion.", ephemeral=False)
        return
    try:
        await ENGINE.authoritative_action(
            "seclusion.settle", interaction.user.id,
            {"minutes_per_day": MINUTES_PER_DAY, "force_end": True, "end_reason": "emerged early"},
            action_id=f"discord:{interaction.id}:seclusion.end",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    final = await DB.get_seclusion(interaction.user.id, active_only=False) or state
    await interaction.response.send_message(
        f"🚪 **You emerge from seclusion.**\n"
        f"Total background cultivation gained: **{int(final.get('accumulated_gain',0))}**.\n"
        "Any incomplete fraction of the current world-day produced no background gain."
    )


@registered_root_command(name="breakthrough", description="Attempt to advance your cultivation stage", guild=GUILD)
@serialized_user_action
async def breakthrough(interaction: discord.Interaction, confirm: bool = False, reroll: bool = False) -> None:
    """`reroll` (v1.0.0-rc.4) seizes the moment after a failed attempt at this
    stage: one more roll, bought with Insight XP, once a stage. The engine
    holds whether there is a moment to seize and what it costs."""
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.breakthrough",
            interaction.user.id,
            {"confirm": bool(confirm), "reroll": bool(reroll)},
            action_id=f"discord:{interaction.id}:cultivation.breakthrough",
        )
    except GameEngineError as exc:
        message = str(exc)
        if "perfection choice requires explicit confirmation" in message:
            message = "⚠️ **Stage 9 choice**\nYou can pursue **/quest → Realm Perfection → Start** for a stronger long-term foundation, or explicitly confirm this breakthrough to skip it."
        elif "no moment to seize" in message or "seized once already" in message:
            message = "⚠️ **No moment to seize**\nA seized moment is one more roll after a failed attempt at this same stage, and only one. Fill the stage and attempt it again."
        elif "seizing the moment costs" in message:
            message += " Insight XP comes from exploring, quests, battles and the Refine stance (**/cultivation → Cultivate → Stance**)."
        elif "realm gate" in message:
            message = f"⚠️ **The realm gate is closed**\n{message}\nBank the insight under **/cultivation → Cultivate → Insight**, or complete **/quest → Realm Perfection**. The sheet at **/cultivation** shows your Insight XP and the odds."
        await interaction.followup.send(f"❌ {message}" if not message.startswith("⚠️") else message, ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    next_realm = str(result.get("to_realm") or "Unknown Realm")
    next_phase = int(result.get("to_stage", 1))
    success = bool(result.get("success"))
    breakthrough_context = await NARRATOR_CONTEXT.build(c, scene_type="cultivation breakthrough", query_text=f"breakthrough {next_realm} stage {next_phase}")
    try:
        narration = await NARRATOR.narrate_breakthrough(c, next_realm, next_phase, roll_line(roll), success, scene_context=breakthrough_context.text)
    except Exception:
        log.exception("Breakthrough narration failed")
        narration = "Qi surges through your meridians as the breakthrough reaches its fixed mechanical result."
    try:
        await DB.add_rag_memory(
            interaction.user.id, memory_kind="breakthrough", salience=88 if success else 66,
            location=str(c.get("location") or ""), source="breakthrough", game_minute=breakthrough_context.game_minute,
            summary=(f"{c.get('name','The cultivator')} attempted a breakthrough from {result.get('from_realm','Unknown')} Stage {result.get('from_stage',1)} to {next_realm} Stage {next_phase}. Outcome: {'success' if success else 'failure'}. Observed: {narration[:540]}"),
        )
    except Exception:
        log.exception("Could not persist breakthrough RAG memory")
    mechanical = roll_line(roll)
    if result.get("reroll"):
        mechanical += f"\n🎯 You seized the moment: **{int(result.get('reroll_cost', 0))} Insight XP** spent for this second roll, and the stage's essence was not asked for again."
    if result.get("insight_spent"):
        mechanical += "\n🔑 Your banked insight opened the realm gate and is spent."
    elif result.get("realm_gate") and result.get("gate_via_insight"):
        mechanical += "\n🔑 Your banked insight held the gate open; it is kept for the next attempt."
    if int(result.get("perfect_bonus", 0)):
        mechanical += "\n★ Perfect-foundation legacy bonus: **+2** to this breakthrough."
    if int(result.get("resonance_bonus", 0)):
        mechanical += "\n☯ Dual Cultivation Resonance: **+1** to this breakthrough."
    if int(result.get("innate_breakthrough_bonus", 0)):
        mechanical += f"\n🌿 Innate aptitude modifier: **{int(result['innate_breakthrough_bonus']):+d}** to this breakthrough."
    if success:
        mechanical += f"\n✨ Advanced to **{next_realm}, Stage {next_phase}**."
        gains = dict(result.get("attribute_gains") or {})
        if gains:
            grown = ", ".join(f"**+{int(v)} {k.replace('_', ' ')}**" for k, v in sorted(gains.items()))
            mechanical += f"\n💪 Crossing into a new realm remade your foundation: {grown}. Every session from here gathers more."
        if float(result.get("world_mult", 1)) != 1.0 and result.get("ascended"):
            mechanical += f"\n🌏 The qi of **{result.get('to_world')}** is **x{float(result['world_mult']):.2f}** what you knew."
        master = dict(result.get("master_reward") or {})
        if master:
            mechanical += f"\n🎓 Your breakthrough feeds the master-disciple bond: **{master.get('master_name','Your master')}** receives **+{int(master.get('insight_xp',0))} Insight XP** and the lineage gains **+{int(master.get('attention',0))} Master Attention**."
        legacy = dict(result.get("soul_legacy") or {})
        if int(legacy.get("awakened_memory", 0)):
            mechanical += f"\n🕯️ Past-life memory awakened: **{int(legacy.get('awakened_memory',0))}% / {int(legacy.get('memory_seed',0))}%**."
    else:
        mechanical += f"\n⚠️ Breakthrough failed; **{int(result.get('failure_loss',0))} cultivation essence** was lost."
        if result.get("reroll_available"):
            mechanical += (
                f"\n🎯 The moment has not passed: **/cultivation → Cultivate → Breakthrough** with **reroll** rolls again "
                f"for **{int(result.get('reroll_cost', 0))} Insight XP** (you hold **{int(result.get('insight_xp', 0))}**), once at this stage, "
                "and the essence is not asked for again."
            )
    await reply_long(interaction, f"{mechanical}\n\n{narration}")


# ---------------- Body Cultivation commands ----------------
body_group = app_commands.Group(name="body", description="Parallel body-cultivation progression")


@registered_group_command(body_group, name="sheet", description="View your body-cultivation realm and progress")
async def body_sheet(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    ri = int(c.get("body_realm_index", 0))
    ph = int(c.get("body_phase", 1))
    realm = WORLD.body_realm_name(ri, c.get("gender"))
    cost = WORLD.body_phase_cost(ri, ph)
    p = await DB.get_body_perfection(interaction.user.id, ri)
    lines = [
        f"💪 **{c['name']} — Body Cultivation**",
        f"Realm: **{realm} — Stage {ph}**",
        f"Body Essence: **{c.get('body_cultivation', 0)}/{cost}**",
        f"World: **{WORLD.body_realm_world(ri)}**",
    ]
    if WORLD.dual_resonance_active(c):
        lines.append("☯ **Dual Cultivation Resonance ACTIVE** — +10% training gains and +1 breakthrough/combat checks.")
    if ph == 9:
        if p and p["completed"]:
            lines.append("★ **Perfect Body Realm achieved.**")
        elif p and p["active"]:
            lines.append(
                f"★ Body Perfection: **{p['progress']}%** • Training {p['training_progress']}/{WORLD.body_perfection_training_cap()} • "
                f"Quests {p['completed_quests']}/{WORLD.body_perfection_quest_count()}"
            )
        else:
            lines.append("Stage 9 choice: **/quest → Body Perfection → Start** or **/cultivation → Body → Breakthrough**.")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_group_command(body_group, name="cultivate", description="Temper your body and gather body-cultivation essence")
@serialized_user_action
async def body_cultivate(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.body_train",
            interaction.user.id,
            {"cooldown_seconds": SETTINGS.cultivate_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:cultivation.body_train",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    gain, total, cost = int(result.get("gain", 0)), int(result.get("total", 0)), int(result.get("cost", 0))
    extra = ""
    if int(result.get("resonance_bonus", 0)):
        extra += f"\n☯ Resonance added **+{int(result['resonance_bonus'])}** to the attempted body gain before the stage cap."
    extra += f"\n🕰️ {result.get('period','World-time')} body-tempering flow: **x{float(result.get('time_mult',1)):.2f}** efficiency."
    if float(result.get("soul_mult", 1)) > 1.0:
        extra += f"\n☸️ Soul Legacy talent echo: **x{float(result['soul_mult']):.2f}** body-cultivation efficiency."
    if float(result.get("world_mult", 1)) != 1.0:
        extra += f"\n🌏 The qi of the **{result.get('world_name') or 'world'}** is thick: **x{float(result['world_mult']):.2f}**."
    if float(result.get("era_mult", 1)) != 1.0:
        extra += f"\n🌌 **{result.get('era_name') or 'World Era'}** modifies cultivation to **x{float(result['era_mult']):.2f}**."
    if int(result.get("perfection_gain", 0)):
        extra += f"\n★ Body refinement deepens by **+{int(result['perfection_gain'])}%**."
    if result.get("ready"):
        extra += "\n✨ Body Stage 9 is full. Choose **/quest → Body Perfection → Start** or **/cultivation → Body → Breakthrough**." if int(c.get("body_phase", 1)) == 9 else "\n✨ Your body is ready for **/cultivation → Body → Breakthrough**."
    await interaction.followup.send(f"💪 **{c['name']} tempers the body.**\nYou refine flesh, blood, bone, and meridians for **+{gain} body essence**.\nProgress: **{total}/{cost}**{extra}")


@registered_group_command(body_group, name="breakthrough", description="Attempt to advance your body-cultivation stage")
@serialized_user_action
async def body_breakthrough(interaction: discord.Interaction, confirm: bool = False) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.body_breakthrough",
            interaction.user.id,
            {"confirm": bool(confirm)},
            action_id=f"discord:{interaction.id}:cultivation.body_breakthrough",
        )
    except GameEngineError as exc:
        message = str(exc)
        if "perfection choice requires explicit confirmation" in message:
            message = "⚠️ **Body Stage 9 choice**\nPursue **/quest → Body Perfection → Start** for a stronger physical foundation, or explicitly confirm this breakthrough to skip it."
        await interaction.followup.send(f"❌ {message}" if not message.startswith("⚠️") else message, ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    success = bool(result.get("success"))
    text = f"💥 **Body Breakthrough**\n{roll_line(roll)}"
    if int(result.get("perfect_bonus", 0)):
        text += "\n★ Perfect-body legacy bonus: **+2**."
    if int(result.get("resonance_bonus", 0)):
        text += "\n☯ Dual Cultivation Resonance: **+1**."
    if int(result.get("innate_breakthrough_bonus", 0)):
        text += f"\n🌿 Innate aptitude modifier: **{int(result['innate_breakthrough_bonus']):+d}**."
    if success:
        text += f"\n✨ Advanced to **{result.get('to_realm','Unknown Realm')}, Stage {int(result.get('to_stage',1))}**."
        if int(result.get("vitality_gain", 0)):
            text += f"\n❤️ Physical advancement increases maximum Vitality by **+{int(result['vitality_gain'])}**."
        master = dict(result.get("master_reward") or {})
        if master:
            text += f"\n🎓 **{master.get('master_name','Your master')}** receives **+{int(master.get('insight_xp',0))} Insight XP** from your advancement; Master Attention rises by **+{int(master.get('attention',0))}**."
        legacy = dict(result.get("soul_legacy") or {})
        if int(legacy.get("awakened_memory", 0)):
            text += f"\n🕯️ Past-life memory awakened: **{int(legacy.get('awakened_memory',0))}% / {int(legacy.get('memory_seed',0))}%**."
    else:
        text += f"\n⚠️ The tempering fails; **{int(result.get('failure_loss',0))} body essence** is lost, with no permanent mutilation."
    await interaction.followup.send(text)


bodyperfect_group = app_commands.Group(name="bodyperfect", description="Long-form Stage 9 Body Realm Perfection")


@registered_group_command(bodyperfect_group, name="start", description="Begin the optional Perfect Body Path at Body Stage 9")
@serialized_user_action
async def bodyperfect_start(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        await ENGINE.authoritative_action(
            "perfection.body_start", interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:perfection.body_start",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Perfect Body Path could not begin: {exc}", ephemeral=False)
        return
    ri = int(c.get("body_realm_index", 0))
    q = WORLD.body_perfection_quest(ri, 0, c)
    await interaction.followup.send(
        f"★ **Perfect Body Path begun: {WORLD.body_realm_name(ri, c.get('gender'))}**\n"
        f"Training can contribute **{WORLD.body_perfection_training_cap()}%**; the remaining progress comes from seven physical trials.\n\n"
        f"First quest: **{q['title']}**\n{q['description']}\nUse **/quest → Body Perfection → Quest**."
    )


@registered_group_command(bodyperfect_group, name="info", description="View your Body Realm Perfection progress")
async def bodyperfect_info(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    ri = int(c.get("body_realm_index", 0))
    p = await DB.get_body_perfection(interaction.user.id, ri)
    if not p:
        await interaction.response.send_message("No Body Perfect Path is recorded for this realm.", ephemeral=False)
        return
    status = "Completed" if p["completed"] else "Active" if p["active"] else "Inactive"
    text = (
        f"★ **{WORLD.body_realm_name(ri, c.get('gender'))} Body Perfection — {status}**\n"
        f"Progress: **{p['progress']}%**\nTraining: **{p['training_progress']}/{WORLD.body_perfection_training_cap()}**\n"
        f"Quests: **{p['completed_quests']}/{WORLD.body_perfection_quest_count()}**"
    )
    if p["active"] and p["quest_index"] < WORLD.body_perfection_quest_count():
        q = WORLD.body_perfection_quest(ri, p["quest_index"], c)
        text += f"\nCurrent quest: **{q['title']}** — preparation **{p['quest_preparation']}/{q['preparation_required']}**"
    await interaction.response.send_message(text, ephemeral=False)


BODY_PERFECT_ACTIONS = [
    app_commands.Choice(name="Info", value="info"),
    app_commands.Choice(name="Prepare", value="prepare"),
    app_commands.Choice(name="Attempt", value="attempt"),
]


@registered_group_command(bodyperfect_group, name="quest", description="Inspect, prepare for, or attempt your current Body Perfection quest")
@app_commands.choices(action=BODY_PERFECT_ACTIONS)
@serialized_user_action
async def bodyperfect_quest(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    ri = int(c.get("body_realm_index", 0))
    p = await DB.get_body_perfection(interaction.user.id, ri)
    if action.value == "info":
        if not p or not p["active"]:
            await interaction.response.send_message("No active Perfect Body Path.", ephemeral=False)
            return
        if p["quest_index"] >= WORLD.body_perfection_quest_count():
            await interaction.response.send_message("All Body Perfection quests are complete. Use **/quest → Body Perfection → Trial**.", ephemeral=False)
            return
        q = WORLD.body_perfection_quest(ri, p["quest_index"], c)
        await interaction.response.send_message(
            f"📜 **Body Perfection Quest {p['quest_index']+1}/{WORLD.body_perfection_quest_count()} — {q['title']}**\n"
            f"{q['description']}\nPreparation: **{p['quest_preparation']}/{q['preparation_required']}**\n"
            f"Trial: **{q['attribute'].title()} — TN {q['tn']}**\nClue: *{q['clue']}*\n"
            f"Reward: **+{q['progress_reward']}% Body Perfection**", ephemeral=False,
        )
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.body_quest", interaction.user.id,
            {"mode": action.value, 
             "quest_cooldown_seconds": SETTINGS.perfect_quest_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:perfection.body_quest:{action.value}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Body Perfection quest could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if action.value == "prepare":
        prep = int(result.get("preparation", 0)); required = int(result.get("preparation_required", 0))
        await interaction.response.send_message(
            f"💪 **{result.get('title','Body Perfection')} — Preparation**\nProgress: **{min(prep, required)}/{required}**\n{result.get('clue','')}" +
            ("\n✨ The trial is available with **/quest → Body Perfection → Quest → Attempt**." if prep >= required else ""),
            ephemeral=False,
        )
        return
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    if bool(result.get("success")):
        text = f"{roll_line(roll)}\n✨ **{result.get('title','Body Perfection quest')} completed.** +{int(result.get('progress_reward',0))}% Body Perfection."
    else:
        text = f"{roll_line(roll)}\n⚠️ Your body cannot complete this tempering yet. Preparation is retained."
    await interaction.response.send_message(text, ephemeral=False)


@registered_group_command(bodyperfect_group, name="clues", description="Review Body Perfection clues you have discovered")
async def bodyperfect_clues(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    p = await DB.get_body_perfection(interaction.user.id, int(c.get("body_realm_index", 0)))
    clues = (p or {}).get("discovered", [])
    await interaction.response.send_message(
        "🔎 **Discovered Body Realm Clues**\n" + ("\n".join(f"• {x}" for x in clues) if clues else "None yet."),
        ephemeral=False,
    )


@registered_group_command(bodyperfect_group, name="trial", description="Attempt the final Perfect Body Realm trial")
@serialized_user_action
async def bodyperfect_trial(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.body_trial", interaction.user.id,
            {"trial_cooldown_seconds": SETTINGS.perfect_trial_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:perfection.body_trial",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Final Body Perfection trial could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    lines = ["💥 **FINAL BODY REALM PERFECTION TRIAL**"]
    for row in result.get("rolls", []):
        lines.append(f"{row.get('name','Trial')}: {roll_line(SimpleNamespace(**dict(row)))}")
    if bool(result.get("success")):
        lines.append(
            f"\n★ **PERFECT {WORLD.body_realm_name(int(c.get('body_realm_index',0)), c.get('gender')).upper()} ACHIEVED**\n"
            "Your physical foundation permanently improves: Max Vitality +10%, Max Qi +5%, and future body breakthroughs gain +2."
        )
    else:
        lines.append(
            f"\n⚠️ The final body tempering fails. **{int(result.get('training_loss',0))}% recoverable Body Perfection training** is lost; "
            "your body realm remains stable. Restore it with **/cultivation → Body → Cultivate** before trying again."
        )
    await reply_long(interaction, "\n".join(lines))


@registered_group_command(bodyperfect_group, name="abandon", description="Abandon the active Perfect Body Path")
@serialized_user_action
async def bodyperfect_abandon(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.body_abandon", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:perfection.body_abandon",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Perfect Body Path could not be abandoned: {exc}", ephemeral=False)
        return
    if not bool(dict(envelope.get("result") or {}).get("abandoned")):
        await interaction.followup.send("No active Perfect Body Path to abandon.", ephemeral=False)
        return
    await interaction.followup.send("The Perfect Body Path has been abandoned. You may now break through normally.", ephemeral=False)


# ---------------- Perfect Realm commands ----------------
perfect_group = app_commands.Group(name="perfect", description="Long-form Stage 9 Realm Perfection")


@registered_group_command(perfect_group, name="start", description="Begin the optional Perfect Path at Stage 9")
@serialized_user_action
async def perfect_start(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        await ENGINE.authoritative_action(
            "perfection.start", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:perfection.start",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Perfect Path could not begin: {exc}", ephemeral=False)
        return
    quest = WORLD.perfection_quest(int(c["realm_index"]), 0, c)
    await interaction.followup.send(
        f"★ **Perfect Path begun: {WORLD.realm_name(c['realm_index'], c.get('gender'))}**\n"
        f"Perfection starts at **0%**. Training contributes at most **{WORLD.perfection_training_cap()}%**; the rest comes from seven long quests.\n\n"
        f"**First Quest — {quest['title']}**\n{quest['description']}\nPreparation: 0/{quest['preparation_required']}\nUse **/quest → Realm Perfection → Quest**.",
        ephemeral=False,
    )


@registered_group_command(perfect_group, name="info", description="View your Realm Perfection progress")
async def perfect_info(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c: return
    p = await DB.get_perfection(interaction.user.id, c["realm_index"])
    if not p:
        await interaction.response.send_message("No Perfect Path is active. At Stage 9 use **/quest → Realm Perfection → Start**.", ephemeral=False); return
    status = "COMPLETED" if p["completed"] else ("ACTIVE" if p["active"] else "INACTIVE")
    text = (f"★ **{WORLD.realm_name(c['realm_index'], c.get("gender"))} Perfection — {status}**\n"
            f"Progress: **{p['progress']}%**\nTraining: **{p['training_progress']}/{WORLD.perfection_training_cap()}**\n"
            f"Quests: **{p['completed_quests']}/{WORLD.perfection_quest_count()}**")
    if p["active"] and p["quest_index"] < WORLD.perfection_quest_count():
        q = WORLD.perfection_quest(c["realm_index"], p["quest_index"], c)
        text += f"\n\nCurrent: **{q['title']}**\nPreparation: **{p['quest_preparation']}/{q['preparation_required']}**"
    await interaction.response.send_message(text, ephemeral=False)


PERFECT_ACTIONS=[app_commands.Choice(name="Info",value="info"),app_commands.Choice(name="Prepare",value="prepare"),app_commands.Choice(name="Attempt",value="attempt")]


@registered_group_command(perfect_group, name="quest", description="Inspect, prepare for, or attempt your current Perfection quest")
@app_commands.choices(action=PERFECT_ACTIONS)
@serialized_user_action
async def perfect_quest(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    p = await DB.get_perfection(interaction.user.id, c["realm_index"])
    if action.value == "info":
        if not p or not p["active"]:
            await interaction.response.send_message("Start the Perfect Path first with **/quest → Realm Perfection → Start**.", ephemeral=False)
            return
        if p["quest_index"] >= WORLD.perfection_quest_count():
            await interaction.response.send_message("All seven quests are complete. Reach 100% and use **/quest → Realm Perfection → Trial**.", ephemeral=False)
            return
        q = WORLD.perfection_quest(c["realm_index"], p["quest_index"], c)
        await interaction.response.send_message(
            f"📜 **Perfection Quest {p['quest_index']+1}/{WORLD.perfection_quest_count()} — {q['title']}**\n{q['description']}\n"
            f"Preparation: **{p['quest_preparation']}/{q['preparation_required']}**\nTrial: **{q['attribute'].title()} — TN {q['tn']}**\nClue: *{q['clue']}*\nReward: **+{q['progress_reward']}% Perfection**", ephemeral=False,
        )
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.quest", interaction.user.id,
            {"mode": action.value, 
             "quest_cooldown_seconds": SETTINGS.perfect_quest_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:perfection.quest:{action.value}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Perfection quest could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if action.value == "prepare":
        prep = int(result.get("preparation", 0)); required = int(result.get("preparation_required", 0))
        await interaction.response.send_message(
            f"🧭 **{result.get('title','Perfection')} — Preparation**\nProgress: **{min(prep, required)}/{required}**\n{result.get('clue','')}" +
            ("\n✨ The trial is now available with **/quest → Realm Perfection → Quest → Attempt**." if prep >= required else ""), ephemeral=False,
        )
        return
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    if bool(result.get("success")):
        text = f"{roll_line(roll)}\n✨ **{result.get('title','Perfection quest')} completed.** +{int(result.get('progress_reward',0))}% Perfection."
    else:
        text = f"{roll_line(roll)}\n⚠️ The trial rejects your current understanding. Your preparation remains; try again later."
    await interaction.response.send_message(text, ephemeral=False)


@registered_group_command(perfect_group, name="clues", description="Review Perfection clues you have discovered")
async def perfect_clues(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    p=await DB.get_perfection(interaction.user.id,c["realm_index"])
    clues=(p or {}).get("discovered",[])
    await interaction.response.send_message("🔎 **Discovered Realm Clues**\n"+("\n".join(f"• {x}" for x in clues) if clues else "None yet."),ephemeral=False)


@registered_group_command(perfect_group, name="trial", description="Attempt the final Realm Perfection trial")
@serialized_user_action
async def perfect_trial(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.trial", interaction.user.id,
            {"trial_cooldown_seconds": SETTINGS.perfect_trial_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:perfection.trial",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Final Perfection trial could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    lines = ["🌌 **FINAL REALM PERFECTION TRIAL**"]
    for row in result.get("rolls", []):
        lines.append(f"{row.get('name','Trial')}: {roll_line(SimpleNamespace(**dict(row)))}")
    if bool(result.get("success")):
        lines.append(f"\n★ **PERFECT {WORLD.realm_name(c['realm_index'], c.get('gender')).upper()} ACHIEVED**\nYour Max Qi and Vitality permanently increase, and future major breakthroughs gain a bonus.")
    else:
        lines.append(
            f"\n⚠️ The final compression fails. **{int(result.get('training_loss',0))}% recoverable Perfection training** is lost, but your realm remains stable. "
            "Restore it with **/cultivation → Cultivate** before trying again."
        )
    await reply_long(interaction, "\n".join(lines))


@registered_group_command(perfect_group, name="abandon", description="Abandon the active Perfect Path and lose its progress")
@serialized_user_action
async def perfect_abandon(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.abandon", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:perfection.abandon",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Perfect Path could not be abandoned: {exc}", ephemeral=False)
        return
    if not bool(dict(envelope.get("result") or {}).get("abandoned")):
        await interaction.followup.send("No active Perfect Path to abandon.", ephemeral=False)
        return
    await interaction.followup.send("The Perfect Path has been abandoned. You may now break through normally.", ephemeral=False)


# ---------- Explicit heavenly tribulations / ascension gates ----------
tribulation_group = app_commands.Group(name="tribulation", description="Prepare for and survive world-crossing heavenly tribulations")


def _eligible_ascension_gate(c: dict) -> dict | None:
    qi_index=int(c.get("realm_index",-1)); qi_phase=int(c.get("phase",1))
    body_index=int(c.get("body_realm_index",-1)); body_phase=int(c.get("body_phase",1))
    if qi_phase==9 and ascension_gate(qi_index):
        return {**ascension_gate(qi_index),"gate_realm_index":qi_index,"path":"Qi"}
    if body_phase==9 and ascension_gate(body_index):
        return {**ascension_gate(body_index),"gate_realm_index":body_index,"path":"Body"}
    return None


@registered_group_command(tribulation_group, name="status", description="Inspect your current ascension-gate tribulation state")
async def tribulation_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    gate = _eligible_ascension_gate(c)
    if not gate:
        await interaction.response.send_message(
            "⚡ No world-crossing tribulation is currently at your cultivation threshold. These gates occur at the peak of Ascension Realm, Transcendence Realm and Immortal Sovereign.",
            ephemeral=False,
        )
        return
    state = await DB.get_tribulation_state(interaction.user.id, int(gate["gate_realm_index"])) or {}
    cleared = bool(int(state.get("cleared", 0)))
    await interaction.response.send_message(
        f"⚡ **{gate['name']}**\n{gate['from_world']} → **{gate['to_world']}**\n"
        f"Path: **{gate['path']}** • Preparation: **{int(state.get('preparation',0))}/5** • Attempts: **{int(state.get('attempts',0))}**\n"
        f"Clearance: **{'CLEARED — the cross-world breakthrough is unlocked' if cleared else 'NOT CLEARED'}**",
        ephemeral=False,
    )


TRIBULATION_PATH_CHOICES = [
    app_commands.Choice(name="Qi cultivation", value="qi"),
    app_commands.Choice(name="Body cultivation", value="body"),
]


@registered_group_command(tribulation_group, name="prepare", description="Spend realm-appropriate currency to prepare a tribulation defense")
@app_commands.choices(path=TRIBULATION_PATH_CHOICES)
@serialized_user_action
async def tribulation_prepare(interaction: discord.Interaction, path: app_commands.Choice[str] | None = None) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "tribulation.prepare", interaction.user.id, ({"path": path.value} if path else {}),
            action_id=f"discord:{interaction.id}:tribulation.prepare",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Tribulation preparation could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    currency = str(result.get("currency", "low_spirit_stone"))
    await interaction.followup.send(
        f"⚡ Tribulation preparation rises to **{int(result.get('preparation',0))}/5**. Remaining {WORLD.currency_name(currency)}: **{int(result.get('balance',0))}**.",
        ephemeral=False,
    )


@registered_group_command(tribulation_group, name="attempt", description="Face the three-wave heavenly tribulation at your current world gate")
@app_commands.choices(path=TRIBULATION_PATH_CHOICES)
@serialized_user_action
async def tribulation_attempt(interaction: discord.Interaction, path: app_commands.Choice[str] | None = None) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "tribulation.attempt", interaction.user.id, ({"path": path.value} if path else {}),
            action_id=f"discord:{interaction.id}:tribulation.attempt",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Tribulation attempt could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    lines = [f"⚡ **{result.get('gate_name','Heavenly Tribulation')}** • Preparation **{int(result.get('preparation_used',0))}/5**"]
    for wave in result.get("waves", []):
        roll = SimpleNamespace(**dict(wave))
        lines.append(f"\n**{wave.get('name','Tribulation Wave')}:** {roll_line(roll)}")
        condition = wave.get("condition") or {}
        if condition:
            lines.append(f"↳ Persistent consequence: **{condition.get('name', condition.get('condition_key','Condition'))}**, severity **{int(condition.get('severity',1))}/5**.")
    if bool(result.get("success")):
        lines.append(
            f"\n🌌 **Tribulation cleared.** Heaven accepts your claim to enter **{result.get('to_world','the higher world')}**. "
            f"Your next world-crossing **{'/breakthrough' if result.get('path')=='Qi' else '/body breakthrough'}** may proceed."
        )
        if int(result.get("fate_after", -1)) >= 0:
            lines.append(f"🌠 Heaven leaves providence in your wake: **Fate {int(result.get('fate_after',0))}/9**.")
    else:
        lines.append("\n💥 **Tribulation failed.** Preparation is consumed. Heal persistent injuries and prepare before trying again.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)




# ---------------- The qi body (v1.0.0-rc.7) ----------------
# The channels qi runs through and the three dantian they feed. The engine
# owns every number here; these commands choose and report.
meridian_group = app_commands.Group(name="meridian", description="The channels qi runs through: open them, mend them")
dantian_group = app_commands.Group(name="dantian", description="The three dantian: what you hold, how clean it is, how far you feel")


def _dantian_state_word(state: str) -> str:
    return {"intact": "intact", "cracked": "**cracked**", "shattered": "**shattered**"}.get(str(state), str(state))


async def _qi_body(user_id: int) -> dict[str, Any]:
    return dict(await ENGINE.action("qi.status", user_id, {}) or {})


def _qi_body_lines(name: str, status: dict[str, Any]) -> list[str]:
    open_count, damaged = int(status.get("meridians_open", 0)), int(status.get("meridians_damaged", 0))
    ceiling = int(status.get("meridian_ceiling", 108))
    purity, ceiling_purity = int(status.get("purity", 0)), int(status.get("purity_ceiling", 0))
    lines = [
        f"🧬 **{name} — the qi body**",
        f"🫀 **Lower dantian:** **{int(status.get('qi', 0)):,} / {int(status.get('qi_max', 0)):,}** qi"
        f" • recovering **{float(status.get('regen_per_game_minute', 0)):.1f}** a game minute"
        f" • the vessel is {_dantian_state_word(status.get('dantian_state', 'intact'))}",
        f"⚗️ **Middle dantian:** purity **{purity}%** of a possible **{ceiling_purity}%**"
        f" • every technique costs **x{float(status.get('skill_cost_mult', 1)):.2f}**",
    ]
    if status.get("upper_open"):
        lines.append(f"👁️ **Upper dantian:** open • spiritual sense reaches **{int(status.get('sense_reach', 0)):,}**")
    else:
        lines.append("👁️ **Upper dantian:** sealed until the Nascent Soul realm.")
    lines.append(
        f"🩸 **Meridians:** **{open_count}/{ceiling}** open"
        + (f", **{damaged} ruptured**" if damaged else "")
        + f" • the next costs **{int(status.get('meridian_open_cost', 0))} Insight XP** (you hold **{int(status.get('insight_xp', 0))}**)"
    )
    if str(status.get("manual_name") or ""):
        lines.append(
            f"📖 **{status.get('manual_name')}** ({status.get('manual_grade')}) widens the dantian by "
            f"**x{float(status.get('manual_capacity_mult', 1)):.2f}**."
        )
    lines.append(f"☯️ A breakthrough attempt burns **{int(status.get('breakthrough_qi_cost', 0)):,}** qi.")
    return lines


@registered_group_command(dantian_group, name="status", description="Inspect your qi body: the three dantian and your meridians")
async def dantian_status(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        status = await _qi_body(interaction.user.id)
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    await reply_long(interaction, "\n".join(_qi_body_lines(str(c["name"]), status)), ephemeral=False)


@registered_group_command(dantian_group, name="refine", description="Refine what you hold: cleaner qi makes every technique cheaper")
@serialized_user_action
async def dantian_refine(interaction: discord.Interaction) -> None:
    """The middle dantian's work: a session spent cleaning rather than
    gathering. Purity sets what every technique costs."""
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "qi.refine", interaction.user.id, {"cooldown_seconds": 30 * 60},
            action_id=f"discord:{interaction.id}:qi.refine",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    await interaction.followup.send(
        f"⚗️ **{c['name']} circulates what they hold through the middle dantian.**\n"
        f"Purity **+{int(result.get('purity_gain', 0))}** → **{int(result.get('purity', 0))}%** "
        f"of a possible **{int(result.get('purity_ceiling', 0))}%**.\n"
        f"Every technique now costs **x{float(result.get('skill_cost_mult', 1)):.2f}**. "
        f"The refining spent **{int(result.get('qi_spent', 0)):,}** qi; **{int(result.get('qi', 0)):,} / {int(result.get('qi_max', 0)):,}** remains.",
        ephemeral=False,
    )


@registered_group_command(meridian_group, name="status", description="See which of your channels are open, and which are ruptured")
async def meridian_status(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        status = await _qi_body(interaction.user.id)
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    open_count, ceiling = int(status.get("meridians_open", 0)), int(status.get("meridian_ceiling", 108))
    damaged = int(status.get("meridians_damaged", 0))
    filled = max(0, min(20, round(open_count * 20 / max(1, ceiling))))
    lines = [
        f"🩸 **{c['name']} — the meridians**",
        f"`{'▰' * filled}{'▱' * (20 - filled)}` **{open_count}/{ceiling}** open"
        + (f" • **{damaged} ruptured**" if damaged else ""),
        f"Each open channel widens the dantian by **2%** and quickens its recovery by **1%**.",
        f"The next costs **{int(status.get('meridian_open_cost', 0))} Insight XP** and a quarter of your qi — "
        "**/cultivation → Qi Body → Meridian Open**.",
    ]
    if damaged:
        lines.append("A ruptured channel halves your recovery and doubles what techniques cost. Mend it with **/cultivation → Qi Body → Meridian Heal**.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(meridian_group, name="open", description="Force the next channel open: Insight XP, qi, and a roll that can go wrong")
@serialized_user_action
async def meridian_open(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "meridian.open", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:meridian.open",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    lines = [f"🩸 **{c['name']} drives qi against the next channel.**", roll_line(roll)]
    if result.get("success"):
        lines.append(f"✨ It opens. **{int(result.get('meridians_open', 0))}/{int(result.get('meridian_ceiling', 108))}** channels now carry your qi.")
    else:
        lines.append("⚠️ The channel holds, and the qi turns back on you.")
        deviation = dict(result.get("deviation") or {})
        if deviation:
            lines.append(
                f"⚠️ **{deviation.get('name') or 'Qi Deviation'}** (severity {int(deviation.get('severity', 1))}/5). "
                "Treat it under **/character → Treatment**."
            )
    lines.append(
        f"Spent **{int(result.get('insight_spent', 0))} Insight XP** and **{int(result.get('qi_spent', 0)):,}** qi; "
        f"**{int(result.get('qi', 0)):,} / {int(result.get('qi_max', 0)):,}** remains."
    )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(meridian_group, name="heal", description="Mend a ruptured channel, or a cracked dantian, with spirit stones and quiet")
@serialized_user_action
async def meridian_heal(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "meridian.heal", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:meridian.heal",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    mended = "channel" if str(result.get("mended")) == "meridian" else "dantian"
    await interaction.followup.send(
        f"🩹 **{c['name']} mends a ruptured {mended}.**\n"
        f"Spent **{int(result.get('stones_spent', 0)):,}** spirit stones. "
        f"**{int(result.get('meridians_damaged', 0))}** ruptures remain; the vessel is "
        f"{_dantian_state_word(result.get('dantian_state', 'intact'))}.\n"
        f"Qi: **{int(result.get('qi', 0)):,} / {int(result.get('qi_max', 0)):,}**.",
        ephemeral=False,
    )

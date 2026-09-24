"""Cultivation progression: cultivate, breakthrough, /seclusion, /body, /bodyperfect, /perfect and /tribulation.

Split phase 9a (v0.19.44, docs/history/MAIN_SPLIT_PLAN.md). Cut verbatim from
main.py in definition order; reads only modules below main.py.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import discord
from discord import app_commands

from ...ops.game_engine import GameEngineError
from ...rules.progression_systems import ascension_gate
from ..character_state import record_quest_progress, announce_quest_progress, current_effect_modifiers
from ..formatting import roll_line
from ..status_cards import _ELEMENT_MARKS
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
from ..services import GUILD, NARRATOR, NARRATOR_CONTEXT, QUESTS

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
            {},
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
    # v1.0.0-rc.55: what the root itself is worth. Until rc.55 the grade was
    # folded into the element multiplier below, which is hidden when the
    # relation is indifferent - so for most cultivators the one thing their
    # root did was applied and never shown. It has its own line now.
    if float(result.get("root_mult", 1)) != 1.0:
        extra += (f"\n🌿 **{result.get('root_grade') or 'Common'}** spiritual root: "
                  f"**x{float(result['root_mult']):.2f}** cultivation efficiency.")
    if float(result.get("manual_mult", 1)) != 1.0:
        chosen = "you practise" if result.get("manual_chosen") else "the best method you have learned"
        extra += f"\n📖 **{result.get('manual_name')}** ({result.get('manual_grade')} grade, {chosen}): **x{float(result['manual_mult']):.2f}**."
    # v1.0.0-rc.9: the kind of qi the method draws, said only when the root
    # makes something of it - an indifferent element is not worth a line.
    # Since rc.55 element_mult is the relation alone, so an indifferent
    # element really is exactly x1.00 and this suppression hides nothing.
    element_relation = str(result.get("element_relation") or "neutral")
    if str(result.get("element") or "") and element_relation != "neutral":
        extra += (f"\n{_ELEMENT_MARKS.get(str(result.get('element')), '☯️')} **{result.get('element')} qi** is "
                  f"**{result.get('element_label') or element_relation}** with your root: "
                  f"**x{float(result.get('element_mult', 1)):.2f}**."
                  + (f" {result.get('element_note')}" if str(result.get("element_note") or "") else ""))
    if result.get("element_clash"):
        extra += "\n⚠️ The qi turned going in — a method your root cannot stomach is its own danger."
    if str(result.get("place_name") or ""):
        extra += f"\n🪨 **{result.get('place_name')}** — {result.get('place_quality') or 'ordinary'} ground: **x{float(result.get('place_mult', 1)):.2f}** cultivation efficiency."
    stance = str(result.get("stance") or "circulate")
    if stance != "circulate":
        extra += f"\n🧭 **{result.get('stance_label') or stance.title()}** stance: **x{float(result.get('stance_mult', 1)):.2f}** gain."
    if int(result.get("insight_xp_gain", 0)):
        extra += f"\n💡 Refining banks **+{int(result['insight_xp_gain'])} Insight XP** toward the realm gate."
    deviation = dict(result.get("deviation") or {})
    if deviation:
        extra += f"\n⚠️ The forced qi ran wild: **{deviation.get('name') or 'Qi Deviation'}** (severity {int(deviation.get('severity', 1))}). Treat it under **/character → Afflictions**, or it drags every session down."
    if result.get("stage_full") and not gain:
        extra += "\n🪷 This stage is already full: the session gathered nothing, banked nothing and risked nothing. Break through before meditating again."
    ready = ""
    if result.get("ready"):
        ready = "\n✨ Stage 9 is full. Choose **/ascend → Perfection → Start** or **/ascend → Main Progression → Breakthrough**." if int(c.get("phase", 1)) == 9 else "\n✨ You are ready to attempt **/ascend → Main Progression → Breakthrough**."
    try:
        progressed = await record_quest_progress(
            interaction.user.id, "cultivate", amount=1, game_minute=wt.total_minutes)
        await announce_quest_progress(interaction, progressed)
    except Exception:
        log.exception("Quest progress update failed after cultivation")
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
            message = "An insight is already banked. It is spent when you cross the realm gate at **/ascend → Main Progression → Breakthrough**."
        elif "costs" in message and "Insight XP" in message:
            message += " Insight XP comes from exploring, quests, battles and the Refine stance (**/cultivation → Cultivate → Stance**)."
        await interaction.followup.send(f"❌ {message}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    await interaction.followup.send(
        f"💡 **{c['name']} banks an insight into {result.get('next_realm') or 'the next realm'}.**\n"
        f"Spent **{int(result.get('cost', 0))} Insight XP** (**{int(result.get('insight_xp', 0))}** left). "
        f"The realm gate out of **{result.get('realm') or 'this realm'}** is open: fill Stage 9 and attempt **/ascend → Main Progression → Breakthrough**.",
        ephemeral=False,
    )


seclusion_group = app_commands.Group(
    name="seclusion",
    description="Closed-door background cultivation that progresses with world time",
)


# How long a retreat may last, in real minutes. The engine owns the bound
# (`seclusionMaxRealMinutes`) and refuses anything over it with a sentence
# naming the cap - this is the picker's shape, not the rule. Until
# v1.0.0-rc.56 the only limit in the game was `days: Range[int, 1, 365]` on
# this very command, which is presentation and which no other caller had.
SECLUSION_MAX_REAL_MINUTES = 120


def _real_minutes_label(minutes: int) -> str:
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60} real hour{'s' if minutes >= 120 else ''}"
    if minutes > 60:
        return f"{minutes // 60}h {minutes % 60}m real time"
    return f"{minutes} real minutes"


def _seclusion_real_remaining(state: dict) -> str:
    """The wall-clock the retreat actually runs on (schema 57).

    `ends_game_minute` is what the world clock will read when the doors open,
    and it moves if a GM changes the world's rate; `ends_real_ts` is the
    deadline itself and does not. A retreat started before that column existed
    has none, and says nothing rather than guessing.
    """
    ends = state.get("ends_real_ts")
    if not ends or str(state.get("status")) != "active":
        return ""
    left = float(ends) - time.time()
    if left <= 0:
        return " • the doors are due to open"
    return f" • **{_real_minutes_label(max(1, int(left // 60)))}** of real time"


SECLUSION_MODE_CHOICES = [
    app_commands.Choice(name="Qi Cultivation", value="qi"),
    app_commands.Choice(name="Body Cultivation", value="body"),
]


@registered_group_command(seclusion_group, name="start", description="Enter closed-door cultivation, for up to two real hours")
@app_commands.choices(mode=SECLUSION_MODE_CHOICES)
@serialized_user_action
async def seclusion_start(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    # The literal is deliberate and cannot be the constant: `@serialized_user_action`
    # wraps the handler, so discord.py resolves this annotation against
    # `runtime.py`'s globals rather than this module's, and a name here is a
    # NameError at import. `test_seclusion_cap.py` holds the two equal to the
    # engine's own `seclusionMaxRealMinutes`, which is the real bound - this is
    # only the picker's shape.
    minutes: app_commands.Range[int, 10, 120] = SECLUSION_MAX_REAL_MINUTES,
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
            {"mode": mode.value, "duration_real_minutes": int(minutes),
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
        f"Duration: **{_real_minutes_label(int(minutes))}** (about **{int(state.get('ends_game_minute', 0)) - int(state.get('started_game_minute', 0))} world-minutes** at the world's current pace)\n"
        f"Location: **{await character_location_display(c)}** ({env_label})\n"
        f"Environment efficiency: **x{env_mult:.2f}**\n"
        f"Projected gain: about **{int(state.get('projected_total_gain', 0))} essence** over the whole retreat"
        f" (a rate of **{daily} per completed world-day**, settled every completed world-hour).\n\n"
        "Progress is settled automatically while the bot is online and catches up after restarts. "
        "Seclusion never auto-breaks through a stage. Any state-changing command will remain locked until you use **/cultivation → Cultivate → Seclusion End** or the planned seclusion completes."
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
        f"Elapsed: **{elapsed / 60:.1f} world-hours**\n"
        f"Remaining: **{remaining / 60:.1f} world-hours**{_seclusion_real_remaining(state)}\n"
        f"Cultivation awarded: **{int(state.get('accumulated_gain',0))}**\n"
        f"Environment: **x{float(state.get('environment_mult',1.0)):.2f}**\n"
        + ("Use **/cultivation → Cultivate → Seclusion End** to emerge early." if str(state.get("status")) == "active" else f"Ended: **{state.get('ended_reason') or 'completed'}**"),
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
            {"force_end": True, "end_reason": "emerged early"},
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
            message = "⚠️ **Stage 9 choice**\nYou can pursue **/ascend → Perfection → Start** for a stronger long-term foundation, or explicitly confirm this breakthrough to skip it."
        elif "no moment to seize" in message or "seized once already" in message:
            message = "⚠️ **No moment to seize**\nA seized moment is one more roll after a failed attempt at this same stage, and only one. Fill the stage and attempt it again."
        elif "seizing the moment costs" in message:
            message += " Insight XP comes from exploring, quests, battles and the Refine stance (**/cultivation → Cultivate → Stance**)."
        elif "realm gate" in message:
            message = f"⚠️ **The realm gate is closed**\n{message}\nBank the insight under **/cultivation → Cultivate → Insight**, or complete **/ascend → Perfection**. The sheet at **/cultivation** shows your Insight XP and the odds."
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
        # Ascension carries you (v1.0.0-rc.15). Until now the crossing was
        # announced and then left you standing exactly where you had been.
        if result.get("ascended_to_location"):
            mechanical += (
                f"\n🕊️ The heavens take you. You rise from **{result.get('ascended_from_location')}** and come down in "
                f"**{result.get('ascended_to_location')}**, the capital of {result.get('to_world')} — it is on your map now, "
                "and the road out of it begins with **/travel**."
            )
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
    # A gate crossed (v1.2.0): the tutorial's last stage asks for the first
    # breakthrough, and this is its one reporter - the qi ladder only, since
    # the label names this command. Recorded after the engine decided it
    # (rc.28) and before the reply; told after (v1.0.5).
    progressed = []
    if success:
        progressed = await record_quest_progress(
            interaction.user.id, "breakthrough", amount=1, game_minute=wt.total_minutes)
    await reply_long(interaction, f"{mechanical}\n\n{narration}")
    await announce_quest_progress(interaction, progressed)


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
            lines.append("Stage 9 choice: **/ascend → Perfection → Start** or **/cultivation → Body → Breakthrough**.")
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
            {},
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
        extra += "\n✨ Body Stage 9 is full. Choose **/ascend → Perfection → Start** or **/cultivation → Body → Breakthrough**." if int(c.get("body_phase", 1)) == 9 else "\n✨ Your body is ready for **/cultivation → Body → Breakthrough**."
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
            message = "⚠️ **Body Stage 9 choice**\nPursue **/ascend → Perfection → Start** for a stronger physical foundation, or explicitly confirm this breakthrough to skip it."
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


# ---------------- Realm and Body Perfection ----------------
# One group, not two (v1.0.0-rc.13). `/perfect *` and `/bodyperfect *` were the
# same six verbs twelve times over - start, info, quest, clues, trial, abandon,
# once for the cultivation realm and once for the body - differing only in
# which engine action, which table and which nouns. The mechanic was always
# one; only the spelling was two. It is now a `path` on each command, and the
# two spellings that can drift apart are a single table below.
@dataclass(frozen=True)
class _PerfectionPath:
    """Everything that differs between the Realm and Body Perfection paths."""
    value: str
    noun: str                 # "Perfection" / "Body Perfection"
    path_name: str            # "Perfect Path" / "Perfect Body Path"
    realm_key: str            # the character column holding the realm index
    action_prefix: str        # "perfection." / "perfection.body_"
    clue_heading: str
    trial_heading: str
    restore_hint: str
    reward_line: str

    def realm_index(self, character: dict) -> int:
        return int(character.get(self.realm_key, 0) or 0)

    def realm_name(self, character: dict) -> str:
        name = WORLD.body_realm_name if self.value == "body" else WORLD.realm_name
        return name(self.realm_index(character), character.get("gender"))

    def training_cap(self) -> int:
        return int(WORLD.body_perfection_training_cap() if self.value == "body"
                   else WORLD.perfection_training_cap())

    def quest_count(self) -> int:
        return int(WORLD.body_perfection_quest_count() if self.value == "body"
                   else WORLD.perfection_quest_count())

    def quest(self, realm_index: int, index: int, character: dict) -> dict:
        source = WORLD.body_perfection_quest if self.value == "body" else WORLD.perfection_quest
        return source(realm_index, index, character)

    async def record(self, user_id: int, realm_index: int):
        read = DB.get_body_perfection if self.value == "body" else DB.get_perfection
        return await read(user_id, realm_index)

    def action(self, name: str) -> str:
        return f"{self.action_prefix}{name}"


_PERFECTION_PATHS = {
    "realm": _PerfectionPath(
        value="realm", noun="Perfection", path_name="Perfect Path",
        realm_key="realm_index", action_prefix="perfection.",
        clue_heading="Discovered Realm Clues",
        trial_heading="🌌 **FINAL REALM PERFECTION TRIAL**",
        restore_hint="**/cultivation → Cultivate**",
        reward_line="Your Max Qi and Vitality permanently increase, and future major breakthroughs gain a bonus.",
    ),
    "body": _PerfectionPath(
        value="body", noun="Body Perfection", path_name="Perfect Body Path",
        realm_key="body_realm_index", action_prefix="perfection.body_",
        clue_heading="Discovered Body Realm Clues",
        trial_heading="💥 **FINAL BODY REALM PERFECTION TRIAL**",
        restore_hint="**/cultivation → Body → Cultivate**",
        reward_line="Your physical foundation permanently improves: Max Vitality +10%, Max Qi +5%, and future body breakthroughs gain +2.",
    ),
}

PERFECT_PATHS = [
    app_commands.Choice(name="Realm — the cultivation realm", value="realm"),
    app_commands.Choice(name="Body — the body realm", value="body"),
]
PERFECT_ACTIONS = [
    app_commands.Choice(name="Info", value="info"),
    app_commands.Choice(name="Prepare", value="prepare"),
    app_commands.Choice(name="Attempt", value="attempt"),
]

perfect_group = app_commands.Group(name="perfect", description="Long-form Stage 9 Realm and Body Perfection")


def _perfection_path(choice: app_commands.Choice[str]) -> _PerfectionPath:
    return _PERFECTION_PATHS[str(getattr(choice, "value", choice))]


@registered_group_command(perfect_group, name="start", description="Begin the optional Perfect Path at Stage 9, for the realm or the body")
@app_commands.choices(path=PERFECT_PATHS)
@serialized_user_action
async def perfect_start(interaction: discord.Interaction, path: app_commands.Choice[str]) -> None:
    spec = _perfection_path(path)
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    await current_world_time()
    try:
        await ENGINE.authoritative_action(
            spec.action("start"), interaction.user.id, {},
            action_id=f"discord:{interaction.id}:{spec.action('start')}",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"{spec.path_name} could not begin: {exc}", ephemeral=False)
        return
    realm_index = spec.realm_index(c)
    quest = spec.quest(realm_index, 0, c)
    await interaction.followup.send(
        f"★ **{spec.path_name} begun: {spec.realm_name(c)}**\n"
        f"{spec.noun} starts at **0%**. Training contributes at most **{spec.training_cap()}%**; "
        f"the rest comes from {spec.quest_count()} long quests.\n\n"
        f"**First Quest — {quest['title']}**\n{quest['description']}\n"
        f"Preparation: 0/{quest['preparation_required']}\nUse **/ascend → Perfection → Quest**.",
        ephemeral=False,
    )


@registered_group_command(perfect_group, name="info", description="View your Realm or Body Perfection progress")
@app_commands.choices(path=PERFECT_PATHS)
async def perfect_info(interaction: discord.Interaction, path: app_commands.Choice[str]) -> None:
    spec = _perfection_path(path)
    c = await require_character(interaction)
    if not c:
        return
    realm_index = spec.realm_index(c)
    p = await spec.record(interaction.user.id, realm_index)
    if not p:
        await interaction.response.send_message(
            f"No {spec.path_name} is recorded for this realm. At Stage 9 use **/ascend → Perfection → Start**.",
            ephemeral=False,
        )
        return
    status = "COMPLETED" if p["completed"] else ("ACTIVE" if p["active"] else "INACTIVE")
    text = (
        f"★ **{spec.realm_name(c)} {spec.noun} — {status}**\n"
        f"Progress: **{p['progress']}%**\nTraining: **{p['training_progress']}/{spec.training_cap()}**\n"
        f"Quests: **{p['completed_quests']}/{spec.quest_count()}**"
    )
    if p["active"] and p["quest_index"] < spec.quest_count():
        q = spec.quest(realm_index, p["quest_index"], c)
        text += f"\n\nCurrent: **{q['title']}**\nPreparation: **{p['quest_preparation']}/{q['preparation_required']}**"
    await interaction.response.send_message(text, ephemeral=False)


@registered_group_command(perfect_group, name="quest", description="Inspect, prepare for, or attempt your current Perfection quest")
@app_commands.choices(path=PERFECT_PATHS, action=PERFECT_ACTIONS)
@serialized_user_action
async def perfect_quest(interaction: discord.Interaction, path: app_commands.Choice[str], action: app_commands.Choice[str]) -> None:
    spec = _perfection_path(path)
    c = await require_character(interaction)
    if not c:
        return
    realm_index = spec.realm_index(c)
    p = await spec.record(interaction.user.id, realm_index)
    if action.value == "info":
        if not p or not p["active"]:
            await interaction.response.send_message(
                f"No active {spec.path_name}. Start it with **/ascend → Perfection → Start**.", ephemeral=False,
            )
            return
        if p["quest_index"] >= spec.quest_count():
            await interaction.response.send_message(
                f"All {spec.noun} quests are complete. Reach 100% and use **/ascend → Perfection → Trial**.",
                ephemeral=False,
            )
            return
        q = spec.quest(realm_index, p["quest_index"], c)
        await interaction.response.send_message(
            f"📜 **{spec.noun} Quest {p['quest_index']+1}/{spec.quest_count()} — {q['title']}**\n{q['description']}\n"
            f"Preparation: **{p['quest_preparation']}/{q['preparation_required']}**\n"
            f"Trial: **{q['attribute'].title()} — TN {q['tn']}**\nClue: *{q['clue']}*\n"
            f"Reward: **+{q['progress_reward']}% {spec.noun}**",
            ephemeral=False,
        )
        return
    await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            spec.action("quest"), interaction.user.id,
            {"mode": action.value,
},
            action_id=f"discord:{interaction.id}:{spec.action('quest')}:{action.value}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"{spec.noun} quest could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if action.value == "prepare":
        prep = int(result.get("preparation", 0))
        required = int(result.get("preparation_required", 0))
        await interaction.response.send_message(
            f"🧭 **{result.get('title', spec.noun)} — Preparation**\n"
            f"Progress: **{min(prep, required)}/{required}**\n{result.get('clue','')}" +
            ("\n✨ The trial is now available with **/ascend → Perfection → Quest**, Attempt."
             if prep >= required else ""),
            ephemeral=False,
        )
        return
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    if bool(result.get("success")):
        text = (f"{roll_line(roll)}\n✨ **{result.get('title', spec.noun + ' quest')} completed.** "
                f"+{int(result.get('progress_reward',0))}% {spec.noun}.")
    else:
        text = f"{roll_line(roll)}\n⚠️ The trial rejects your current understanding. Your preparation remains; try again later."
    await interaction.response.send_message(text, ephemeral=False)


@registered_group_command(perfect_group, name="clues", description="Review the Perfection clues you have discovered")
@app_commands.choices(path=PERFECT_PATHS)
async def perfect_clues(interaction: discord.Interaction, path: app_commands.Choice[str]) -> None:
    spec = _perfection_path(path)
    c = await require_character(interaction)
    if not c:
        return
    p = await spec.record(interaction.user.id, spec.realm_index(c))
    clues = (p or {}).get("discovered", [])
    await interaction.response.send_message(
        f"🔎 **{spec.clue_heading}**\n" + ("\n".join(f"• {x}" for x in clues) if clues else "None yet."),
        ephemeral=False,
    )


@registered_group_command(perfect_group, name="trial", description="Attempt the final Realm or Body Perfection trial")
@app_commands.choices(path=PERFECT_PATHS)
@serialized_user_action
async def perfect_trial(interaction: discord.Interaction, path: app_commands.Choice[str]) -> None:
    spec = _perfection_path(path)
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            spec.action("trial"), interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:{spec.action('trial')}",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Final {spec.noun} trial could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    lines = [spec.trial_heading]
    for row in result.get("rolls", []):
        lines.append(f"{row.get('name','Trial')}: {roll_line(SimpleNamespace(**dict(row)))}")
    if bool(result.get("success")):
        lines.append(f"\n★ **PERFECT {spec.realm_name(c).upper()} ACHIEVED**\n{spec.reward_line}")
    else:
        lines.append(
            f"\n⚠️ The final compression fails. **{int(result.get('training_loss',0))}% recoverable "
            f"{spec.noun} training** is lost, but your realm remains stable. "
            f"Restore it with {spec.restore_hint} before trying again."
        )
    await reply_long(interaction, "\n".join(lines))


@registered_group_command(perfect_group, name="abandon", description="Abandon the active Perfect Path and lose its progress")
@app_commands.choices(path=PERFECT_PATHS)
@serialized_user_action
async def perfect_abandon(interaction: discord.Interaction, path: app_commands.Choice[str]) -> None:
    spec = _perfection_path(path)
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            spec.action("abandon"), interaction.user.id, {},
            action_id=f"discord:{interaction.id}:{spec.action('abandon')}",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"{spec.path_name} could not be abandoned: {exc}", ephemeral=False)
        return
    if not bool(dict(envelope.get("result") or {}).get("abandoned")):
        await interaction.followup.send(f"No active {spec.path_name} to abandon.", ephemeral=False)
        return
    await interaction.followup.send(
        f"The {spec.path_name} has been abandoned. You may now break through normally.", ephemeral=False,
    )



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
    # The Heart Calming Pill's own number, finally visible (v1.0.0-rc.58). It
    # rode the Heart Tribulation wave's modifier and was read by nothing until
    # this release; a player who drank one before the storm should be able to
    # see that it counted.
    if int(result.get("heart_demon_resistance", 0)) > 0:
        lines.append(
            f"🫖 A settled mind steadies the Heart Tribulation: **{int(result.get('heart_demon_resistance', 0))}** "
            "heart-demon resistance."
        )
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
        if result.get("quest_granted"):
            lines.append(
                "🌀 The sky where the lightning stood is thin, and it will not stay that way: anchor the seam with "
                "**/ascend → Tribulation / Ascension → Gate** and the crossing is yours — see **/quests**."
            )
    else:
        lines.append("\n💥 **Tribulation failed.** Preparation is consumed. Heal persistent injuries and prepare before trying again.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(tribulation_group, name="gate", description="Anchor the seam a survived tribulation left into a permanent crossing here")
@serialized_user_action
async def tribulation_gate(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    if not await require_character(interaction):
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "ascension.gate", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:ascension.gate",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    currency = str(result.get("currency") or "")
    # The stage's objective is the anchored gate: recorded before the reply,
    # told after (v1.0.5). The seam exists in the engine by here, so the record
    # is already earned and drawing the reply must not be able to lose it.
    progressed = await record_quest_progress(
        interaction.user.id, "ascension_gate", game_minute=wt.total_minutes)
    await interaction.followup.send(
        f"🌀 The seam over **{result.get('location','here')}** holds. The **{result.get('name','Ascension Gate')}** stands there now: "
        f"a public crossing out of {result.get('from_world','this world')} into **{result.get('to_world','the world above')}**, "
        f"setting travellers down at **{result.get('destination','the far side')}**.\n"
        f"Anchoring it cost **{int(result.get('cost',0))} {WORLD.currency_name(currency)}** "
        f"(remaining: **{int(result.get('balance',0))}**); the transit itself asks **{int(result.get('fare',0))}** of anyone who uses it. "
        f"Step through with **/travel → Teleportation Arrays → Use**.",
        ephemeral=False,
    )
    await announce_quest_progress(interaction, progressed)




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
            "qi.refine", interaction.user.id, {},
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
                "Treat it under **/character → Afflictions**."
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


# ---------------- The ghost road (v1.0.0-rc.8) ----------------
# Death qi: the same three dantian, filled from what a place keeps after
# something died in it. Only a cultivator born to one of the two ghost
# households walks it; the engine refuses every one of these to anyone else.
ghost_group = app_commands.Group(name="ghost", description="The ghost road: death qi, the residue it leaves, and the rites that lift it")


def _ghost_lines(name: str, status: dict[str, Any]) -> list[str]:
    corruption, cap = int(status.get("corruption", 0)), int(status.get("corruption_cap", 100))
    filled = max(0, min(10, round(corruption * 10 / max(1, cap))))
    lines = [
        f"👻 **{name} — the ghost road**",
        f"🕯️ **{status.get('ghost_form_name')}** • `{'▰' * filled}{'▱' * (10 - filled)}` corruption **{corruption}/{cap}**",
    ]
    if str(status.get("ghost_form_note") or ""):
        lines.append(f"*{status.get('ghost_form_note')}*")
    lines.append(
        f"🫀 The form widens the dantian by **x{float(status.get('capacity_mult', 1)):.2f}**"
        f" and costs **{int(round(float(status.get('daylight_penalty', 0)) * 100))}%** of what you gather under the sun."
    )
    nxt = dict(status.get("next_form") or {})
    if nxt:
        lines.append(
            f"⬆️ Next: **{nxt.get('name')}** at **{int(nxt.get('corruption', 0))}** corruption"
            f" and realm **{int(nxt.get('min_realm_index', 0))}** — *{nxt.get('note')}*"
        )
    else:
        lines.append("⬆️ There is nothing further down this road.")
    ground = str(status.get("ground_name") or "open ground")
    lines.append(
        f"📍 Here: {ground} **x{float(status.get('ground_mult', 1)):.2f}** • **{status.get('period')}** **x{float(status.get('hour_mult', 1)):.2f}**"
    )
    penalty = int(status.get("purity_ceiling_penalty", 0))
    if penalty:
        lines.append(f"⚗️ The residue has taken **{penalty}%** off how clean your qi can ever be.")
    if corruption >= int(status.get("rupture_threshold", 60) or 60):
        lines.append("🩸 Past this depth every session can tear a channel.")
    todo = []
    if status.get("can_harvest_here"):
        todo.append("**/cultivation → Ghost → Harvest**")
    if status.get("can_appease_here"):
        todo.append("**/cultivation → Ghost → Appease**")
    lines.append("➡️ " + (" • ".join(todo) if todo else "Neither the harvest nor the rites belong on this ground."))
    return lines


@registered_group_command(ghost_group, name="status", description="The ghost road: what you have become and what this ground is worth")
async def ghost_status(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        status = dict(await ENGINE.action("ghost.status", interaction.user.id, {}) or {})
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    if not status.get("walking_the_road"):
        await interaction.followup.send(
            f"🕯️ **{c['name']}** holds spirit qi, not death qi. The **{status.get('ghost_path')}** road belongs to "
            "those born into a ghost household — it is chosen at birth, and never after.",
            ephemeral=False,
        )
        return
    await reply_long(interaction, "\n".join(_ghost_lines(str(c["name"]), status)), ephemeral=False)


@registered_group_command(ghost_group, name="harvest", description="Take the death qi this place is holding — fast, and it stains")
async def ghost_harvest(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "ghost.harvest", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:ghost.harvest",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    risen = str(result.get("form_risen") or "")
    await interaction.followup.send(
        f"👻 **{c['name']} draws on {result.get('ground_name') or 'the ground'}.**\n"
        f"The residue comes up cold and fast: **+{int(result.get('qi_gained', 0)):,}** qi "
        f"(**{int(result.get('qi', 0)):,} / {int(result.get('qi_max', 0)):,}**) at **x{float(result.get('ground_mult', 1)):.2f}**.\n"
        f"🕯️ Corruption **+{int(result.get('corruption_gain', 0))}** → **{int(result.get('corruption', 0))}**"
        f" • karma **{int(result.get('karma_delta', 0))}**."
        + (f"\n⬆️ Something in you settles differently. You are **{risen}** now." if risen else ""),
        ephemeral=False,
    )


@registered_group_command(ghost_group, name="appease", description="Burn incense where the living keep their dead, and shed some of what clings")
async def ghost_appease(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False)
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "ghost.appease", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:ghost.appease",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ {_explain_engine_error(exc)}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    await interaction.followup.send(
        f"🕯️ **{c['name']} burns incense at {result.get('ground_name') or 'the shrine'}.**\n"
        f"Some of it lifts: corruption **-{int(result.get('corruption_shed', 0))}** → **{int(result.get('corruption', 0))}** "
        f"for **{int(result.get('stones_spent', 0)):,}** spirit stones • karma **+{int(result.get('karma_delta', 0))}**.\n"
        f"What your body has become does not lift with it: you are still **{result.get('ghost_form_name')}**.",
        ephemeral=False,
    )

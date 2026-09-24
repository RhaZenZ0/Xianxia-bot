"""The live cards the surface shows (v1.0.0-rc.3): the cultivation sheet
that heads the cultivation hub, and the facts line above the menu's rows.
Both read the engine, which surface.py - the wiring - does not; the
surface imports these and registers them."""
from __future__ import annotations

import time
from typing import Any

import discord

from ..ops.game_engine import GameEngineError
from ..rules import feature_unlocks as unlocks
from .hubs import HubStatusField
from .locations import here_summary, npcs_present
from . import seclusion
from .runtime import DB, ENGINE, WORLD, character_location_display, log


def _stage_bar(current: object, cost: object) -> str:
    current_value = max(0, int(current or 0))
    cost_value = max(1, int(cost or 1))
    filled = max(0, min(10, round(min(current_value, cost_value) * 10 / cost_value)))
    return f"`{'▰' * filled}{'▱' * (10 - filled)}` {current_value:,} / {cost_value:,}"


def _relative_time(seconds: object) -> str:
    remaining = max(0, int(seconds or 0))
    if remaining <= 0:
        return "ready now"
    return f"ready <t:{int(time.time()) + remaining}:R>"


# The mark each kind of qi is written with, so a glance at the sheet says which
# one the method draws (v1.0.0-rc.9).
_ELEMENT_MARKS = {
    "Fire": "🔥", "Water": "💧", "Wood": "🌿", "Metal": "⚙️", "Earth": "🪨",
    "Lightning": "⚡", "Wind": "🌬️", "Ice": "❄️", "Yin": "🌑", "Yang": "☀️",
    "Void": "🕳️", "Chaos": "🌀",
}


def _qi_body_value(status: dict) -> str:
    """The qi body on the sheet (v1.0.0-rc.7): what the lower dantian holds and
    how fast it fills, how clean the middle dantian keeps it and what that
    costs, and how many channels carry it."""
    capacity = max(1, int(status.get("qi_max", 1) or 1))
    qi = max(0, int(status.get("qi", 0) or 0))
    filled = max(0, min(10, round(qi * 10 / capacity)))
    # v1.0.0-rc.8: a ghost cultivator's dantian holds death qi, and the sheet
    # names it rather than pretending the two are the same thing.
    death = str(status.get("qi_type") or "spirit") == "death"
    line = f"`{'▰' * filled}{'▱' * (10 - filled)}` **{qi:,} / {capacity:,}** {'death qi' if death else 'qi'}"
    if float(status.get("qi_regen", 0) or 0):
        line += f" • +{float(status.get('qi_regen', 0)):.1f}/game minute"
    line += f"\n⚗️ purity **{int(status.get('purity', 0))}%** of **{int(status.get('purity_ceiling', 0))}%** — techniques **x{float(status.get('skill_cost_mult', 1)):.2f}**"
    damaged = int(status.get("meridians_damaged", 0) or 0)
    line += f"\n🩸 **{int(status.get('meridians_open', 0))}/{int(status.get('meridian_ceiling', 108))}** meridians"
    if damaged:
        line += f" • **{damaged} ruptured**"
    if str(status.get("dantian_state") or "intact") != "intact":
        line += f" • the vessel is **{status.get('dantian_state')}**"
    if death:
        line += f"\n👻 **{status.get('ghost_form_name') or 'Living Flesh'}** • corruption **{int(status.get('corruption', 0))}/100**"
    return line


def _qi_body_field(status: dict, realm_index: int) -> str:
    """The qi body's line, or where it opens (v1.2.0).

    The Qi Body page waits for the Spiritual World, and the card used to name
    the meridians every session anyway - "20/108 meridians" - which is
    v1.0.13's own finding turned round: a card advertising a number whose
    lever the curriculum hides. So the line waits with its page, read off the
    same roster the panel reads (no literal realm here), and says where it
    opens. A roster that names no Qi Body lever prints the line as it always
    did.
    """
    roster = WORLD.data.get("feature_unlocks") or {}
    locked = unlocks.locked_leaves(roster, realm_index)
    waits = [realm for path, realm in locked.items()
             if path.split(" ")[0] in ("dantian", "meridian") and not unlocks.is_status_read(path)]
    if not waits:
        return _qi_body_value(status)
    return f"-# the channels and the dantian open at **{WORLD.realm_name(min(waits))}** — the Spiritual World's game"


async def cultivation_status_fields(interaction: discord.Interaction, *, fallback: Any) -> list[HubStatusField]:
    """The cultivation sheet (v1.0.0-rc.3), the hub's first page: realm and
    stage with the essence bar, the stance and when the next session is
    ready, the odds of the next breakthrough and what moves them, the realm
    gate, today's multipliers, the body path and the Insight XP. One engine
    query (`cultivation.status`) computes it from the same numbers the roll
    uses, so the sheet and the dice cannot disagree."""
    character = await DB.get_character(interaction.user.id)
    if character is None:
        return [
            HubStatusField("🌱 Character", "Not created — use **/begin**", inline=False),
        ]
    try:
        status = dict(await ENGINE.action("cultivation.status", interaction.user.id, {}) or {})
    except (GameEngineError, Exception):
        log.exception("cultivation.status unavailable; showing the player card")
        return await fallback(interaction)
    realm = WORLD.realm_name(int(status.get("realm_index", 0)), character.get("gender"))
    stage = int(status.get("stage", 1))
    realm_line = f"**{realm}** • Stage **{stage}**/9\n{_stage_bar(status.get('cultivation'), status.get('cost'))}"
    if status.get("ready"):
        realm_line += " ✨ full"
    pace = int(status.get("pace", 0) or 0)
    if pace:
        realm_line += f"\n-# about **{int(status.get('sessions_per_stage', 12) or 12)}** sessions a stage at **{pace:,}** each"
    odds = dict(status.get("odds") or {})
    movers = " • ".join(
        f"{str(m.get('label'))} {int(m.get('value', 0)):+d}" for m in list(odds.get("movers") or [])[:4]
    )
    odds_line = f"**{int(odds.get('probability', 0))}%** (2d10 {int(odds.get('modifier', 0)):+d} vs TN {int(odds.get('tn', 0))})"
    if movers:
        odds_line += f"\n-# {movers}"
    if status.get("reroll_available"):
        odds_line += f"\n🎯 A moment to seize: one more roll for **{int(status.get('reroll_cost', 0))} Insight XP**"
    odds_line += f"\n-# 💡 **{int(status.get('insight_xp', 0)):,}** Insight XP"
    if int(status.get("breakthrough_qi_cost", 0)):
        odds_line += f" • burns **{int(status.get('breakthrough_qi_cost', 0)):,}** qi"
    if status.get("ceiling"):
        odds_line = "At the ceiling of this path."
    elif status.get("realm_gate"):
        if status.get("gate_open"):
            odds_line += "\n🔑 Realm gate open" + (" (insight banked)" if status.get("insight_banked") else " (Perfection complete)")
        else:
            odds_line += f"\n🔒 Realm gate: bank an insight (**{int(status.get('insight_cost', 0))} XP**) or complete Perfection"
    today = f"{status.get('period') or 'Day'} **x{float(status.get('time_mult', 1)):.2f}** • {status.get('season') or ''}"
    if status.get("root_resonance"):
        today += " 🌿"
    if str(status.get("place_name") or ""):
        today += f"\n🪨 {status.get('place_name')} — {status.get('place_quality') or 'ordinary'} ground **x{float(status.get('place_mult', 1)):.2f}**"
    if float(status.get("world_mult", 1)) != 1.0:
        today += f"\n🌏 world qi **x{float(status.get('world_mult', 1)):.2f}**"
    if float(status.get("root_mult", 1)) != 1.0:
        today += f"\n🌿 {status.get('root_grade') or 'Common'} spiritual root **x{float(status.get('root_mult', 1)):.2f}**"
    if str(status.get("manual_name") or ""):
        today += f"\n📖 {status.get('manual_name')} ({status.get('manual_grade')}) **x{float(status.get('manual_mult', 1)):.2f}**"
        # v1.0.0-rc.9: what kind of qi it draws, and what this root makes of it.
        if str(status.get("element") or ""):
            today += (f" • {_ELEMENT_MARKS.get(str(status.get('element')), '☯️')} **{status.get('element')}**"
                      f" {status.get('element_label') or 'indifferent'} **x{float(status.get('element_mult', 1)):.2f}**")
    extras = []
    if float(status.get("effect_mult", 1)) != 1.0:
        extras.append(f"effects x{float(status.get('effect_mult', 1)):.2f}")
    if float(status.get("era_mult", 1)) != 1.0:
        extras.append(f"era x{float(status.get('era_mult', 1)):.2f}")
    if float(status.get("manor_mult", 1)) != 1.0:
        extras.append(f"manor x{float(status.get('manor_mult', 1)):.2f}")
    if float(status.get("soul_mult", 1)) > 1.0:
        extras.append(f"soul x{float(status.get('soul_mult', 1)):.2f}")
    if int(status.get("storm_bonus", 0)):
        extras.append(f"qi storm +{int(status.get('storm_bonus', 0))}")
    if extras:
        today += "\n-# " + " • ".join(extras)
    body_odds = dict(status.get("body_odds") or {})
    body_line = (
        f"**{status.get('body_realm') or 'Untempered'}** • Stage **{int(status.get('body_stage', 1))}**/9\n"
        f"{_stage_bar(status.get('body_cultivation'), status.get('body_cost'))}"
    )
    if body_odds:
        body_line += f" • {int(body_odds.get('probability', 0))}%"
    if status.get("dual_resonance"):
        body_line += " ☯"
    stance_line = f"**{status.get('stance_label') or 'Circulate'}** x{float(status.get('stance_mult', 1)):.2f} • {_relative_time(status.get('cooldown_remaining'))}"
    severity = int(status.get("deviation_severity", 0) or 0)
    if severity:
        stance_line += f"\n⚠️ Qi deviation **{severity}/5** — treat it under **/character → Afflictions**"
    fields = [
        HubStatusField("☯️ Realm", realm_line),
        HubStatusField("🧭 Stance", stance_line),
        HubStatusField("🎲 Breakthrough", odds_line, inline=False),
        HubStatusField("🌤️ Today", today),
        HubStatusField("💪 Body", body_line),
        HubStatusField("🫀 Qi Body", _qi_body_field(status, int(status.get("realm_index", character.get("realm_index", 0)) or 0)), inline=False),
    ]
    # The doors, if they are shut (v1.0.0-rc.56). The card rendered nothing
    # about seclusion at all, so a secluded cultivator saw the ordinary sheet
    # and no sign that every other command was about to refuse them - which
    # was survivable while the lockout was half a gate and is not now.
    retreat = await _seclusion_line(interaction)
    if retreat:
        fields.insert(0, HubStatusField("🚪 Seclusion", retreat, inline=False))
    return fields


async def _seclusion_line(interaction: discord.Interaction) -> str:
    """The open retreat, in the clock it actually runs on, or "" for none.

    A failed read says nothing rather than taking the card down with it: the
    sheet is what a player checks when something is wrong.
    """
    try:
        state = await seclusion.active_session(DB, interaction.user.id)
    except Exception:
        log.exception("seclusion line unavailable")
        return ""
    if not state or str(state.get("status")) != "active":
        return ""
    mode = str(state.get("mode") or "qi").upper()
    line = f"**{mode}** • closed-door, every other command is locked"
    ends = state.get("ends_real_ts")
    if ends:
        left = max(0, int((float(ends) - time.time()) // 60))
        line += (f"\n-# doors open in about **{left // 60}h {left % 60}m**" if left >= 60
                 else f"\n-# doors open in about **{max(1, left)} minutes**")
    gained = int(state.get("accumulated_gain", 0) or 0)
    line += f" • **{gained:,}** essence settled so far"
    line += "\n-# **/cultivation → Cultivate → Seclusion End** to emerge early"
    return line


async def _who_is_here(character: dict) -> list[str]:
    """`npcs_present` for a character's own location, and never a failure.

    The Here line is a header, drawn beside everything else a panel shows, so a
    lookup that raised would cost the whole card rather than one line of it. An
    empty answer names nobody - the same thing the line says when no caller can
    resolve anybody at all.
    """
    where = str(character.get("location") or "")
    if not where:
        return []
    try:
        return list(await npcs_present(where))
    except Exception:
        log.exception("Could not read who is standing at %s", where)
        return []


async def menu_facts_line(interaction: discord.Interaction) -> str:
    """The lines above the rows: no character yet, or the Here line, the
    realm and stage with the essence, and what is waiting (trade offers)."""
    character = await DB.get_character(interaction.user.id)
    if character is None:
        return "🌱 No cultivator yet. **Begin** creates one: a family, a path, a name."
    lines = []
    # Who is actually here, not who content says lives here (v1.0.10): this
    # header and `/talk`'s picker disagreed, because only one of them asked.
    here = here_summary(str(character.get("location") or ""), present=await _who_is_here(character))
    where = (await character_location_display(character))[:120]
    lines.append(f"📍 **{where}**" + (f" — {here[:200]}" if here else ""))
    realm_index = int(character.get("realm_index", 0))
    phase = int(character.get("phase", 1))
    realm = WORLD.realm_name(realm_index, character.get("gender"))
    try:
        cost = int(WORLD.phase_cost(realm_index, phase))
    except Exception:
        cost = 0
    stage = f"☯️ **{realm}** • Stage **{phase}**/9"
    if cost:
        stage += f" • essence {int(character.get('cultivation', 0) or 0):,}/{cost:,}"
    lines.append(stage)
    try:
        trade = dict(await ENGINE.action("trade.status", interaction.user.id, {}) or {})
        waiting = len(list(trade.get("offers_received") or []))
        if waiting:
            lines.append(f"📨 **{waiting}** trade offer{'s' if waiting != 1 else ''} waiting at the inn — **/economy → Trade**")
    except Exception:
        log.debug("trade.status unavailable for the menu facts", exc_info=True)
    return "\n".join(lines)

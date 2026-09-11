"""The live cards the surface shows (v1.0.0-rc.3): the cultivation sheet
that heads the cultivation hub, and the facts line above the menu's rows.
Both read the engine, which surface.py - the wiring - does not; the
surface imports these and registers them."""
from __future__ import annotations

import time
from typing import Any

import discord

from ..ops.game_engine import GameEngineError
from .hubs import HubStatusField
from .locations import here_summary
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
    odds = dict(status.get("odds") or {})
    movers = " • ".join(
        f"{str(m.get('label'))} {int(m.get('value', 0)):+d}" for m in list(odds.get("movers") or [])[:4]
    )
    odds_line = f"**{int(odds.get('probability', 0))}%** (2d10 {int(odds.get('modifier', 0)):+d} vs TN {int(odds.get('tn', 0))})"
    if movers:
        odds_line += f"\n-# {movers}"
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
    return [
        HubStatusField("☯️ Realm", realm_line),
        HubStatusField(
            "🧭 Stance",
            f"**{status.get('stance_label') or 'Circulate'}** x{float(status.get('stance_mult', 1)):.2f} • {_relative_time(status.get('cooldown_remaining'))}",
        ),
        HubStatusField("🎲 Breakthrough", odds_line, inline=False),
        HubStatusField("🌤️ Today", today),
        HubStatusField("💪 Body", body_line),
        HubStatusField(
            "💡 Insight XP",
            f"**{int(status.get('insight_xp', 0)):,}**" + (" • banked 🔑" if status.get("insight_banked") else f" • +{int(status.get('insight_xp_per_refine', 2))}/Refine session"),
        ),
    ]


async def menu_facts_line(interaction: discord.Interaction) -> str:
    """The lines above the rows: no character yet, or the Here line, the
    realm and stage with the essence, and what is waiting (trade offers)."""
    character = await DB.get_character(interaction.user.id)
    if character is None:
        return "🌱 No cultivator yet. **Begin** creates one: a family, a path, a name."
    lines = []
    here = here_summary(str(character.get("location") or ""))
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

"""Small pure formatters shared across command modules.

Phase 2 of the main.py split (v0.19.34, docs/history/MAIN_SPLIT_PLAN.md). roll_line has
21 call sites and human_duration 12, spread over most of the command blocks;
keeping them in main.py forced every module split out to either duplicate
them or import main.py at call time. Nothing here touches Discord or the
database. Definition order is the order these had in main.py.
"""
from __future__ import annotations

from typing import Any

from .runtime import player_property_definition
from .services import PLAYER_PROPERTY_FACILITY_KEYS, PLAYER_PROPERTY_FACILITY_LABELS

def player_property_emoji(abode: dict[str, Any]) -> str:
    definition = player_property_definition(str(abode.get("property_type") or "homestead"))
    return str(definition.get("emoji") or "🏡")

def player_property_facility_lines(abode: dict[str, Any], keys: tuple[str, ...] | None = None) -> list[str]:
    lines: list[str] = []
    for key in (keys if keys is not None else PLAYER_PROPERTY_FACILITY_KEYS):
        value = int(abode.get(f"{key}_level", 0) or 0)
        if value > 0 or key in {"cultivation", "storage"}:
            lines.append(f"{PLAYER_PROPERTY_FACILITY_LABELS.get(key, key.replace('_', ' ').title())} **Lv.{value}**")
    return lines

def player_property_unbuilt(abode: dict[str, Any], keys: tuple[str, ...] | None = None) -> list[str]:
    """Facilities a home does not have yet - what an upgrade can build (v0.30.1).

    `keys` narrows the set: a homestead has all nine, a sect residence the six
    the content's sect_abode_system names.
    """
    return [
        PLAYER_PROPERTY_FACILITY_LABELS.get(key, key.replace("_", " ").title())
        for key in (keys if keys is not None else PLAYER_PROPERTY_FACILITY_KEYS)
        if int(abode.get(f"{key}_level", 0) or 0) <= 0
    ]

def facility_does_text(does: dict[str, Any] | None) -> str:
    """What one facility does at one level, in words, off the engine's numbers.

    `property.overview` (v1.30.0) answers each facility with the numbers the
    rules themselves use; this only says them. A key the engine does not send
    says nothing, so nothing here can promise a number no rule reads.
    """
    d = dict(does or {})
    parts: list[str] = []
    if "cultivation_mult" in d:
        parts.append(f"cultivation here ×{float(d['cultivation_mult']):.2f}")
    if "array_mult" in d:
        parts.append(f"gathering array ×{float(d['array_mult']):.2f} on the qi path")
    if "craft_bonus" in d:
        trades = " and ".join(str(t) for t in (d.get("trades") or [])) or "craft"
        parts.append(f"+{int(d['craft_bonus'])} to {trades} rolls here")
    if "forage_bonus" in d:
        parts.append(f"+{int(d['forage_bonus'])} to forage here")
    if "beast_training_bonus" in d:
        parts.append(f"+{int(d['beast_training_bonus'])} to beast training here")
    if "storage_slots" in d:
        parts.append(f"+{int(d['storage_slots'])} stacks of spatial storage")
    if "capture_slowed_percent" in d:
        parts.append(f"a bounty hunter's capture slowed {int(d['capture_slowed_percent'])}% while you are inside")
    if "stall_slots" in d:
        parts.append(f"market stall of {int(d['stall_slots'])} slots at a {int(d.get('stall_fee_percent') or 0)}% cut")
    if d.get("focus_effect"):
        parts.append(f"Focus grants {d['focus_effect']}")
    return "; ".join(parts)


def property_overview_lines(home: dict[str, Any], *, currency_name: Any = None) -> list[str]:
    """One line per facility of one home, from `property.overview`.

    Each line is the facility, its level, what it does, and - when it can rise -
    what the next level adds, what it costs and anything it still asks. An
    unbuilt facility says what building it would give, which is the question a
    player standing in front of the list is asking.
    """
    namer = currency_name or (lambda cid: str(cid).replace("_", " ").title())
    lines: list[str] = []
    for row in home.get("facilities") or []:
        key = str(row.get("key") or "")
        label = PLAYER_PROPERTY_FACILITY_LABELS.get(key, key.replace("_", " ").title())
        level, top = int(row.get("level") or 0), int(row.get("max_level") or 0)
        does = facility_does_text(row.get("does"))
        head = f"**{label}** Lv.{level}/{top}" if level > 0 else f"**{label}** — not built"
        line = head + (f" — {does}" if level > 0 and does else "")
        if row.get("next") is not None and row.get("next_cost") is not None:
            cost = int(row.get("next_cost") or 0)
            currency = str(row.get("next_currency") or "")
            price = f"{cost:,} contribution points" if currency == "contribution" else f"{cost:,} {namer(currency)}"
            gain = facility_does_text(row.get("next"))
            verb = "build" if level <= 0 else f"Lv.{level + 1}"
            line += f"\n  ↳ {verb}: {gain or 'the next level'} for {price}"
            if row.get("next_needs"):
                line += f" (needs {row['next_needs']})"
        elif level > 0:
            line += " · at its highest level"
        lines.append(line)
    return lines


def human_duration(seconds: int) -> str:
    minutes, sec = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"

def roll_line(result) -> str:
    """One 2d10 check as a line. Since v1.0.0-rc.4 it ends with the chance
    the roll had, which the engine puts in every roll map (`rollOdds`): the
    dice are the same dice, but the player can see whether a failure was
    unlucky or hopeless."""
    sign = "+" if result.modifier >= 0 else ""
    odds = getattr(result, "probability", None)
    chance = f" · **{int(odds)}%** chance" if odds is not None else ""
    return (
        f"2d10 ({result.die1}+{result.die2}) {sign}{result.modifier} = "
        f"**{result.total}** vs TN **{result.tn}** — **{result.degree}**{chance}"
    )


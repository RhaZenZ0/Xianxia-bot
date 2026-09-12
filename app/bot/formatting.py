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


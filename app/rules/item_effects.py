"""What an item does, in one line (v1.21.0).

Asked for from play: "what the crafted items does is needed". The craft picker
named a recipe and nothing else, and the craft reply said "Created Qi
Nourishing Pill" - a player choosing between six pills had to buy one, use it
and read the reply to learn what any of them was for. Every number below was
already in the content file, on the item's own ``use`` block; nothing printed it
where the choice is made.

This is a display twin and decides nothing. The grade scaling restates the
engine's three rules, each named where it lives: ``gradedAmount`` for a
whole-number use (a restore, a lifespan, a duration, a marrow tempering), and
``gradedEffectPayload`` for a modifier - an additive value multiplied, a
multiplier's distance from 1 multiplied. ``tests/python/unit/test_what_a_crafted_item_does.py``
reads those Go bodies so the two cannot part company silently.

Gear is described by ``advanced_runtime.describe_equipment``, handed in rather
than imported, because the equipment table is that module's and a second
describer here would be a second answer to "what does this sword give". A
deployable array's effect lives in the engine's own table
(``deployedArrayDefs``), which Python cannot read, so an array is described by
the item's own words rather than by numbers this module would have to copy.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.rules.item_grades import graded_amount

# What each modifier stat is called where a player reads it. A stat this map
# does not carry is spelled from its key, so a new stat is never dropped - only
# named less prettily until somebody adds it here.
STAT_LABELS: dict[str, str] = {
    "cultivation_gain": "cultivation speed",
    "insight_gain": "insight gained",
    "breakthrough_bonus": "breakthrough rolls",
    "combat_bonus": "combat rolls",
    "heart_demon_resistance": "heart-demon resistance",
    "body": "body",
    "agility": "agility",
    "spirit": "spirit",
    "insight": "insight",
    "will": "will",
    "presence": "presence",
}


def _stat_label(stat: str) -> str:
    return STAT_LABELS.get(stat, stat.replace("_", " "))


def _number(value: float) -> str:
    rounded = round(value, 1)
    return f"{rounded:g}"


def graded_modifier(operation: str, value: float, mult: float) -> float:
    """A modifier at a grade: the twin of ``gradedEffectPayload``."""
    if mult == 1:
        return value
    if operation == "add":
        return value * mult
    if operation == "mul":
        return 1 + (value - 1) * mult
    return value


def describe_modifier(modifier: Mapping[str, Any], mult: float = 1.0) -> str:
    """``+20% cultivation speed``, ``+2 will``; ``""`` for a shape nobody reads."""
    stat = str(modifier.get("stat") or "")
    operation = str(modifier.get("operation") or "")
    try:
        value = float(modifier.get("value") or 0)
    except (TypeError, ValueError):
        return ""
    if not stat:
        return ""
    value = graded_modifier(operation, value, mult)
    if operation == "mul":
        percent = (value - 1) * 100
        return f"{'+' if percent >= 0 else ''}{_number(percent)}% {_stat_label(stat)}"
    if operation == "add":
        return f"{'+' if value >= 0 else ''}{_number(value)} {_stat_label(stat)}"
    return ""


def _duration(minutes: int) -> str:
    """World time, the clock every effect expires on."""
    if minutes <= 0:
        return "for good"
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"for {hours}h {rest}m"
    if hours:
        return f"for {hours}h"
    return f"for {rest}m"


def describe_item_use(definition: Mapping[str, Any], *, mult: float = 1.0, equipment: str = "") -> str:
    """One line saying what an item does when it is used, or ``""``.

    ``definition`` is the item's catalogue entry (its base entry, for a graded
    id), ``mult`` the grade's effect multiplier, and ``equipment`` the gear line
    ``describe_equipment`` already draws for this id, if any.
    """
    if equipment:
        return equipment
    use = dict(definition.get("use") or {})
    parts: list[str] = []
    instant = dict(use.get("instant") or {})
    vitality = int(instant.get("vitality_restore") or 0)
    if vitality > 0:
        parts.append(f"❤️ restores {graded_amount(vitality, mult)} vitality")
    qi = int(instant.get("qi_restore") or 0)
    if qi > 0:
        parts.append(f"🌀 restores {graded_amount(qi, mult)} qi")
    years = int(use.get("lifespan_years") or 0)
    if years > 0:
        parts.append(f"⏳ +{graded_amount(years, mult)} years of lifespan")
    percent = int(use.get("vitality_max_percent") or 0)
    if percent > 0 or int(use.get("vitality_max_min") or 0) > 0:
        floor = int(use.get("vitality_max_min") or 0)
        line = f"🦴 +{percent}% max vitality for good"
        if floor:
            line += f" (at least +{floor})"
        if mult != 1:
            line += f", ×{_number(mult)} at this grade"
        parts.append(line)
    effect = dict(use.get("effect") or {})
    modifiers = [
        text for text in (describe_modifier(m, mult) for m in list(effect.get("modifiers") or []) if isinstance(m, Mapping))
        if text
    ]
    if modifiers:
        duration = int(use.get("duration_game_minutes") or 0)
        parts.append(f"✨ {', '.join(modifiers)} {_duration(graded_amount(duration, mult) if duration > 0 else 0)}")
    if use.get("homeward"):
        parts.append("🏠 carries you home to your birth household from anywhere")
    if use.get("waymark"):
        parts.append("🧭 carries you from your household back to where the Hearth-Return found you")
    if not parts and definition.get("array_deploy"):
        description = str(definition.get("description") or "").strip()
        parts.append(f"🧿 deploys an array where you stand{': ' + description if description else ''}")
    return " · ".join(parts)


def craft_readiness(
    cost: Mapping[str, Any], carried: Mapping[str, Any], level: int, min_level: int
) -> tuple[str, int]:
    """The status page's mark (✅ ❌ 🔴) and how many the bags pay for now.

    The count is what a batch can be (``craft.resolve`` takes the whole batch's
    materials before it rolls), so the picker can say "you can make 3" and
    mean a batch of 3 will not be refused for materials.
    """
    counts = [
        int(carried.get(item, 0) or 0) // int(qty)
        for item, qty in cost.items()
        if int(qty or 0) > 0
    ]
    makeable = min(counts) if counts else 0
    if int(level) < int(min_level):
        return "🔴", makeable
    return ("✅" if makeable > 0 else "❌"), makeable

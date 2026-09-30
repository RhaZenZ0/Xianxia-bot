"""Item grades (v1.7.0), the presentation half.

A crafted item carries a grade, stored as a suffix on its id: ``qi_pill@high``.
The bare id is the first grade (Low), so everything written before grades
existed is Low. The engine owns what a grade does (``go_core/internal/game/
item_grade.go``); this module only reads an id and names it.

The separator is part of how a row is stored, not a tuning knob, so it is a
constant here and in the engine - changing it would orphan every graded row.
The ladder itself (labels, multipliers, the rank each needs) is content and is
passed in, because ``rules`` imports nothing above it.
"""

from __future__ import annotations

from typing import Any, Mapping

SEPARATOR = "@"

# The first rung no keeper deals in: Low and Mid are shelf goods, the rest is
# priced by players. The engine's keeperGradeCeiling is the same number.
KEEPER_GRADE_CEILING = 2


def split_item_grade(item_id: str) -> tuple[str, str]:
    """``"qi_pill@high"`` -> ``("qi_pill", "high")``; a bare id has grade ``""``."""
    text = str(item_id or "")
    head, sep, tail = text.rpartition(SEPARATOR)
    if sep and head:
        return head, tail
    return text, ""


def base_item_id(item_id: str) -> str:
    return split_item_grade(item_id)[0]


def grade_rung(ladder: Mapping[str, Any] | None, grade: str) -> tuple[int, Mapping[str, Any]]:
    """The rung a grade key stands on, and its entry; ``""`` is the first rung.
    An unknown key answers rung 0 and an empty entry."""
    grades = list((ladder or {}).get("grades") or [])
    if not grade:
        return 0, (grades[0] if grades else {})
    for index, entry in enumerate(grades):
        if entry.get("key") == grade:
            return index, entry
    return 0, {}


def grade_label(ladder: Mapping[str, Any] | None, item_id: str) -> str:
    """The label a graded id shows (``"High"``), or ``""`` for the first rung."""
    _, grade = split_item_grade(item_id)
    if not grade:
        return ""
    _, entry = grade_rung(ladder, grade)
    return str(entry.get("label") or grade.title())


def graded_name(base_name: str, ladder: Mapping[str, Any] | None, item_id: str) -> str:
    """``"Qi Pill"`` at High is ``"Qi Pill (High)"``; Low is the bare name."""
    label = grade_label(ladder, item_id)
    return f"{base_name} ({label})" if label else base_name


def effect_mult(ladder: Mapping[str, Any] | None, item_id: str) -> float:
    """What a grade multiplies an item's use by: the display twin of the
    engine's ``itemEffectMult``. A bare id is the first rung, and an id whose
    grade the ladder does not carry is worth 1 (never some other rung's
    multiplier), as the engine answers."""
    _, grade = split_item_grade(item_id)
    index, entry = grade_rung(ladder, grade)
    if grade and not entry:
        return 1.0
    try:
        mult = float(entry.get("effect_mult") or 1)
    except (TypeError, ValueError):
        return 1.0
    return mult if mult > 0 else 1.0


def graded_amount(base: int, mult: float) -> int:
    """A whole-number use scaled by a grade: the twin of the engine's
    ``gradedAmount`` - rounded half away from zero (``round`` would round half
    to even and disagree at x1.25 of an amount ending in 2), and never below
    the base, because a grade only ever adds."""
    base = int(base)
    if base <= 0:
        return max(0, base)
    return max(base, int(base * float(mult) + 0.5))


def grade_cap_note(reached: str, grade: str, rank_label: str = "", opener: str = "") -> str:
    """The line under a craft whose rank held its grade back: what the roll had
    reached, what the rank made of it, and what making the higher one really
    takes - the engine's ``grade_reached_rank`` and ``grade_reached_opener``.
    The top grade asks a rank no trade reaches on rank alone, so the road the
    engine reports is the lower rank *with* an opener (a fully refined flame, a
    fully built spirit sense); printing the rank by itself named a rank nobody
    could have (v1.12.3). ``rank_label`` is already the trade's own name for the
    rank, because ``rules`` names trades in ``progression_systems``."""
    note = f"The roll reached **{reached}**; your rank caps it at {grade}."
    if rank_label:
        note += f" {reached} needs **{rank_label}**"
        note += f" and **{opener}**." if opener else "."
    return note

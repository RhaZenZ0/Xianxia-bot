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

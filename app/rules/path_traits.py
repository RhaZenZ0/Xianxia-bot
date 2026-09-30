"""What each cultivation path does (v1.13.0).

The engine's rules live in ``go_core/internal/game/path_traits.go`` and read
their numbers off ``paths.<name>.trait`` in the content file. This module is
the presentation's half: it fills a trait's summary from those same numbers,
so the prose on the sheet can never restate a number the engine does not use,
and it says which stances a path may take, so the /stance picker offers what
the engine accepts (rc.46). ``tests/python/unit/test_path_traits.py`` holds
the constants below equal to the Go.

The path table is passed in rather than imported: ``WORLD`` is built in the
bot, and ``rules`` sits below it (``test_app_layout.py``).
"""

from __future__ import annotations

import string
from typing import Any, Mapping

SWORD_CULTIVATOR = "Sword Cultivator"
QI_REFINER = "Qi Refiner"
REFINED_STANCE_KEY = "refined"

# The three stances every path may take. Their numbers are code in the engine
# (``cultivationStances``), so the labels here are only what the picker says.
BASE_STANCES: tuple[tuple[str, str], ...] = (
    ("circulate", "Circulate — the full gain, nothing risked"),
    ("refine", "Refine — a fifth slower, banks Insight XP"),
    ("force", "Force — a third faster, risks qi deviation"),
)


def path_trait(paths: Mapping[str, Mapping[str, Any]], path: str | None) -> dict[str, Any]:
    """The trait of a path, or an empty dict for a path the content lacks."""
    definition = paths.get(str(path or "").strip()) or {}
    trait = definition.get("trait") if isinstance(definition, Mapping) else None
    return dict(trait) if isinstance(trait, Mapping) else {}


def _number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def trait_summary(paths: Mapping[str, Mapping[str, Any]], path: str | None) -> str:
    """The trait's summary with its ``{field}`` placeholders filled from the
    trait's own numbers. A placeholder the trait does not carry is left out
    rather than raising: a sheet line must never cost the sheet."""
    trait = path_trait(paths, path)
    summary = str(trait.get("summary") or "").strip()
    if not summary:
        return ""
    values = {key: _number(value) for key, value in trait.items() if key not in {"name", "summary"}}
    formatter = string.Formatter()
    out = []
    for literal, field, _spec, _conv in formatter.parse(summary):
        out.append(literal)
        if field is not None:
            out.append(values.get(field, "?"))
    return "".join(out)


def trait_line(paths: Mapping[str, Mapping[str, Any]], path: str | None) -> str:
    """``**Name** — summary``, or empty when the path has no trait."""
    trait = path_trait(paths, path)
    name = str(trait.get("name") or "").strip()
    summary = trait_summary(paths, path)
    if not name:
        return ""
    return f"**{name}** — {summary}" if summary else f"**{name}**"


def sword_intent_cap(paths: Mapping[str, Mapping[str, Any]], path: str | None) -> int:
    """How much intent a path may hold; 0 for anybody but a Sword Cultivator,
    which is ``swordIntentCap`` in the engine."""
    if str(path or "").strip() != SWORD_CULTIVATOR:
        return 0
    try:
        return max(0, int(path_trait(paths, path).get("intent_cap") or 0))
    except (TypeError, ValueError):
        return 0


def stances_for(paths: Mapping[str, Mapping[str, Any]], path: str | None) -> list[tuple[str, str]]:
    """Every stance this path may take, as ``(key, label)``: the three anybody
    takes, and the refined circulation for a Qi Refiner whose trait carries a
    stance multiplier - ``stanceForPath`` in the engine."""
    out = list(BASE_STANCES)
    if str(path or "").strip() == QI_REFINER:
        trait = path_trait(paths, path)
        try:
            mult = float(trait.get("stance_gain_mult") or 0)
        except (TypeError, ValueError):
            mult = 0.0
        if mult > 0:
            name = str(trait.get("name") or "Refined Circulation")
            out.append((REFINED_STANCE_KEY, f"{name} — x{mult:g} gain, no deviation (Qi Refiner)"))
    return out

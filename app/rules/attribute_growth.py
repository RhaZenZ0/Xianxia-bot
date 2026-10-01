"""What a cultivator's attributes read on the sheet (v1.14.0).

The engine computes growth from the stage (``go_core/internal/game/
attribute_growth.go``); ``attributes_json`` holds only the base. This is the
display twin of ``characterSheetAttributes`` - +``per_stage`` a qi stage on all
six, +``path_per_stage`` on the path's pair - and it decides nothing: no rule
reads it. ``tests/python/unit/test_attribute_growth.py`` holds it equal to the
Go.

The content is passed in: ``WORLD`` is built in the bot, and ``rules`` sits
below it (``test_app_layout.py``).
"""

from __future__ import annotations

from typing import Any, Mapping

ATTRIBUTES = ("body", "agility", "spirit", "insight", "will", "presence")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def growth_rules(content: Mapping[str, Any]) -> dict[str, int]:
    g = content.get("attribute_growth") or {}
    return {
        "per_stage": max(0, _int(g.get("per_stage"))),
        "path_per_stage": max(0, _int(g.get("path_per_stage"))),
        "path_edge_cap": max(0, _int(g.get("path_edge_cap"))),
        "stages_per_realm": _int(g.get("stages_per_realm"), 9) or 9,
    }


def qi_stages_crossed(content: Mapping[str, Any], realm_index: Any, phase: Any) -> int:
    """``qiStagesCrossed``: 0 at realm 0 stage 1."""
    per_realm = growth_rules(content)["stages_per_realm"]
    return max(0, _int(realm_index) * per_realm + _int(phase, 1) - 1)


def path_pair(content: Mapping[str, Any], path: Any) -> tuple[str, ...]:
    """``pathGrowthAttributes``: every attribute tied for the path's highest
    starting value. A path the content does not carry has no pair."""
    definition = (content.get("paths") or {}).get(str(path or "").strip())
    if not isinstance(definition, Mapping):
        return ()
    values = {name: _int(definition.get(name)) for name in ATTRIBUTES}
    best = max(values.values())
    return tuple(name for name in ATTRIBUTES if values[name] == best)


def sheet_attributes(content: Mapping[str, Any], character: Mapping[str, Any]) -> dict[str, int]:
    """The grown attributes a sheet shows, off the stored base."""
    base = dict(character.get("attributes") or {})
    rules = growth_rules(content)
    stages = qi_stages_crossed(content, character.get("realm_index"), character.get("phase"))
    pair = set(path_pair(content, character.get("path")))
    out = {name: _int(value) for name, value in base.items()}
    for name in ATTRIBUTES:
        per = rules["per_stage"]
        if name in pair:
            per = max(per, rules["path_per_stage"])
        out[name] = out.get(name, 0) + per * stages
    return out


def growth_line(content: Mapping[str, Any], character: Mapping[str, Any]) -> str:
    """One line under the sheet's attributes, or empty before the first stage."""
    rules = growth_rules(content)
    stages = qi_stages_crossed(content, character.get("realm_index"), character.get("phase"))
    if stages <= 0 or rules["per_stage"] <= 0:
        return ""
    pair = path_pair(content, character.get("path"))
    line = f"+{rules['per_stage'] * stages} to each from {stages} qi stage{'s' if stages != 1 else ''}"
    if pair and rules["path_per_stage"] > rules["per_stage"]:
        names = " and ".join(name.title() for name in pair)
        line += (f"; {names} grow {rules['path_per_stage']} a stage, and on a roll that lead counts "
                 f"for up to +{rules['path_edge_cap']}")
    return line

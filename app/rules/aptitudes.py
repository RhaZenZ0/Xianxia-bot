from __future__ import annotations

from typing import Any, Callable, Iterable


RandBelow = Callable[[int], int]


def clamp(value: int | float, low: int | float, high: int | float):
    return max(low, min(high, value))


def grade_index(system: dict[str, Any], grade: str) -> int:
    grades = list(system.get("grades", []))
    for index, entry in enumerate(grades):
        if str(entry.get("name", "")).casefold() == str(grade).casefold():
            return index
    return 0


def grade_definition(system: dict[str, Any], grade: str) -> dict[str, Any]:
    grades = list(system.get("grades", []))
    if not grades:
        return {"name": "Common", "cultivation_mult": 1.0, "min_realm_to_evolve": 0}
    return dict(grades[min(len(grades) - 1, grade_index(system, grade))])


def root_cultivation_mult(system: dict[str, Any], grade: str, purity: int) -> float:
    """What a spiritual root is worth to a cultivation session.

    The display twin of the engine's `rootWorthMultiplier` (v1.0.0-rc.55): the
    grade's own authored multiplier, deepened by purity. Both read the same two
    numbers out of `spiritual_root_system`, which is the only way a Python copy
    of a Go rule can be honest - it cannot call the engine, so it must at least
    not invent. A grade the ladder does not carry is worth 1, never the bottom
    rung: `admin.player.set_spiritual_root` writes the column unvalidated.
    """
    grades = list(system.get("grades", []))
    match = next((g for g in grades if str(g.get("name", "")).casefold() == str(grade).casefold()), None)
    if not match:
        return 1.0
    mult = float(match.get("cultivation_mult", 1.0))
    if mult <= 0:
        return 1.0
    bonus = float(system.get("purity_bonus_at_full", 0.0))
    return round(mult * (1 + bonus * clamp(int(purity), 0, 100) / 100.0), 4)


def root_compatibility(
    elements: Iterable[str],
    path: str,
    system: dict[str, Any],
    mutation: str = "",
) -> int:
    elements = [str(value) for value in elements if str(value).strip()]
    affinities = set(str(value) for value in system.get("path_affinities", {}).get(path, []))
    if not elements or elements == ["Mortal Root"]:
        score = 30
    else:
        matches = sum(1 for element in elements if element in affinities)
        score = 45 + matches * 20 - max(0, len(elements) - 1) * 4
        if matches == len(elements):
            score += 5
    mutation_def = system.get("mutations", {}).get(mutation, {})
    if path in mutation_def.get("favored_paths", []):
        score += 10
    return int(clamp(score, 10, 100))


def _definition_by_name(definitions: dict[str, dict[str, Any]], name: str) -> tuple[str, dict[str, Any]] | None:
    needle = str(name).strip().casefold()
    for key, definition in definitions.items():
        if str(definition.get("name", "")).casefold() == needle:
            return str(key), dict(definition)
    return None


def bloodline_definition(
    bloodline: dict[str, Any] | None,
    definitions: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    bloodline = bloodline or {}
    bloodline_id = str(bloodline.get("bloodline_id", "legacy_family_bloodline"))
    if bloodline_id in definitions:
        return bloodline_id, dict(definitions[bloodline_id])
    matched = _definition_by_name(definitions, str(bloodline.get("name", "")))
    return matched if matched else (bloodline_id, {})


def aptitude_effects(
    bundle: dict[str, Any],
    *,
    root_system: dict[str, Any],
    bloodline_definitions: dict[str, dict[str, Any]],
    physique_definitions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Render innate traits into the same shape as the generic effects engine."""
    effects: list[dict[str, Any]] = []
    root = bundle.get("root") or {}
    if root:
        # v1.0.0-rc.55: the two numbers the content authors, and nothing else.
        # This used to multiply the grade by invented purity, mixed-element,
        # compatibility and stability factors and publish the product - and
        # because every caller of current_effect_modifiers discards the
        # aggregate, that arithmetic was the only statement of the rule in the
        # tree and it reached no mechanic. The engine reads the grade now
        # (`rootWorthMultiplier`), so this is the display twin of a rule Go
        # owns and it must read what Go reads: one authored number, two
        # readers that agree. `root_cultivation_mult` is that formula, stated
        # once here and held to the engine's by test_root_grade_is_worth_something.
        gdef = grade_definition(root_system, str(root.get("grade", "Mortal")))
        modifiers: list[dict[str, Any]] = [
            {"stat": "cultivation_gain", "operation": "mul",
             "value": root_cultivation_mult(root_system, str(root.get("grade", "Mortal")), int(root.get("purity", 50)))},
            {"stat": "breakthrough_bonus", "operation": "add", "value": int(gdef.get("breakthrough_bonus", 0))},
        ]
        mutation = str(root.get("mutation", ""))
        modifiers.extend(root_system.get("mutations", {}).get(mutation, {}).get("modifiers", []))
        effects.append({
            "effect_key": "innate_spiritual_root",
            "name": f"{root.get('grade', 'Mortal')} Spiritual Root",
            "category": "Spiritual Root",
            "description": "The grade of the root you were born with, deepened by its purity.",
            "modifiers": modifiers,
            "tags": ["innate", "spiritual-root"],
            "stacks": 1,
            "source_type": "spiritual_root",
            "source_id": mutation or "natural_root",
            "ends_game_minute": None,
        })

    bloodline = bundle.get("bloodline")
    if bloodline:
        _, definition = bloodline_definition(bloodline, bloodline_definitions)
        state = str(bloodline.get("state", "dormant"))
        modifiers: list[dict[str, Any]] = []
        if state in {"awakened", "evolved", "mutated"}:
            stage = max(1, int(bloodline.get("evolution_stage", 1)))
            evolutions = list(definition.get("evolutions", []))
            if evolutions:
                modifiers.extend(evolutions[min(len(evolutions), stage) - 1].get("modifiers", []))
        rejection = int(bloodline.get("rejection", 0))
        if state == "rejected" or rejection >= 60:
            modifiers.extend([
                {"stat": "cultivation_gain", "operation": "mul", "value": 0.85 if state == "rejected" else 0.94},
                {"stat": "will", "operation": "add", "value": -2 if state == "rejected" else -1},
            ])
        if modifiers:
            effects.append({
                "effect_key": "innate_bloodline",
                "name": str(bloodline.get("name", "Ancestral Bloodline")),
                "category": "Bloodline",
                "description": str(definition.get("trait", "An inherited ancestral lineage.")),
                "modifiers": modifiers,
                "tags": ["innate", "bloodline", state],
                "stacks": 1,
                "source_type": "bloodline",
                "source_id": str(bloodline.get("bloodline_id", "unknown")),
                "ends_game_minute": None,
            })

    physique = bundle.get("physique") or {}
    physique_id = str(physique.get("physique_id", "ordinary_mortal_body"))
    state = str(physique.get("state", "ordinary"))
    if physique_id != "ordinary_mortal_body" and state in {"awakened", "evolved"}:
        definition = physique_definitions.get(physique_id, {})
        stage = max(1, int(physique.get("evolution_stage", 1)))
        evolutions = list(definition.get("evolutions", []))
        modifiers = list(evolutions[min(len(evolutions), stage) - 1].get("modifiers", [])) if evolutions else []
        modifiers.extend(definition.get("drawback_modifiers", []))
        if int(physique.get("instability", 0)) >= 60:
            modifiers.append({"stat": "will", "operation": "add", "value": -1})
        effects.append({
            "effect_key": "innate_physique",
            "name": str(physique.get("name", "Special Physique")),
            "category": "Physique",
            "description": str(definition.get("advantage", "A special cultivated body.")),
            "modifiers": modifiers,
            "tags": ["innate", "physique", state],
            "stacks": 1,
            "source_type": "physique",
            "source_id": physique_id,
            "ends_game_minute": None,
        })
    return effects

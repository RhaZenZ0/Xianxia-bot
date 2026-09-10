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
    path: str,
    root_system: dict[str, Any],
    bloodline_definitions: dict[str, dict[str, Any]],
    physique_definitions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Render innate traits into the same shape as the generic effects engine."""
    effects: list[dict[str, Any]] = []
    root = bundle.get("root") or {}
    if root:
        gdef = grade_definition(root_system, str(root.get("grade", "Mortal")))
        purity_factor = 0.90 + int(root.get("purity", 50)) / 1000.0
        mixed_factor = 1.0 - max(0, len(root.get("elements", [])) - 1) * 0.03
        compatibility_factor = 0.95 + root_compatibility(
            root.get("elements", []), path, root_system, str(root.get("mutation", ""))
        ) / 1000.0
        stability_factor = 1.0 if int(root.get("stability", 100)) >= 50 else 0.94
        cultivation_mult = float(gdef.get("cultivation_mult", 1.0)) * purity_factor * mixed_factor * compatibility_factor * stability_factor
        modifiers: list[dict[str, Any]] = [
            {"stat": "cultivation_gain", "operation": "mul", "value": round(cultivation_mult, 4)},
            {"stat": "breakthrough_bonus", "operation": "add", "value": int(gdef.get("breakthrough_bonus", 0))},
        ]
        mutation = str(root.get("mutation", ""))
        modifiers.extend(root_system.get("mutations", {}).get(mutation, {}).get("modifiers", []))
        effects.append({
            "effect_key": "innate_spiritual_root",
            "name": f"{root.get('grade', 'Mortal')} Spiritual Root",
            "category": "Spiritual Root",
            "description": "Innate root quality, purity, stability and path compatibility.",
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

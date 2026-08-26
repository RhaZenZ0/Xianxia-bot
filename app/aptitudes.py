from __future__ import annotations

import secrets
from typing import Any, Callable, Iterable


RandBelow = Callable[[int], int]


def clamp(value: int | float, low: int | float, high: int | float):
    return max(low, min(high, value))


def _pick(values: list[Any], randbelow: RandBelow) -> Any:
    if not values:
        raise ValueError("Cannot choose from an empty aptitude list")
    return values[randbelow(len(values))]


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


def roll_root_grade(
    system: dict[str, Any],
    *,
    family_tier: int = 1,
    talent_echo: int = 0,
    randbelow: RandBelow = secrets.randbelow,
) -> str:
    """Roll quality separately from elemental affinity.

    Family background and a reincarnated soul's talent echo nudge the roll, but
    neither can guarantee a rare grade.  The grade thresholds are content data.
    """
    grades = list(system.get("grades", []))
    if not grades:
        return "Common"
    luck = max(0, int(family_tier) - 1) * 24 + max(0, int(talent_echo)) * 2
    roll = min(999, randbelow(1000) + luck)
    selected = str(grades[0].get("name", "Mortal"))
    for entry in grades:
        if roll >= int(entry.get("min_roll", 0)):
            selected = str(entry.get("name", selected))
    return selected


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


def generate_root_profile(
    *,
    base_root: str,
    path: str,
    system: dict[str, Any],
    family_tier: int = 1,
    talent_echo: int = 0,
    randbelow: RandBelow = secrets.randbelow,
) -> dict[str, Any]:
    grade = roll_root_grade(
        system,
        family_tier=family_tier,
        talent_echo=talent_echo,
        randbelow=randbelow,
    )
    gdef = grade_definition(system, grade)
    purity = int(clamp(38 + grade_index(system, grade) * 10 + randbelow(24) + talent_echo // 8, 20, 100))
    elements = [str(base_root)]
    candidates = [
        str(value)
        for value in system.get("elements", [])
        if str(value) not in {str(base_root), "Mortal Root"}
    ]
    secondary_chance = int(gdef.get("secondary_chance", 0))
    if base_root != "Mortal Root" and candidates and randbelow(100) < secondary_chance:
        elements.append(str(_pick(candidates, randbelow)))
    tertiary_chance = int(gdef.get("tertiary_chance", 0))
    remaining = [value for value in candidates if value not in elements]
    if remaining and len(elements) > 1 and randbelow(100) < tertiary_chance:
        elements.append(str(_pick(remaining, randbelow)))

    mutation = ""
    mutation_chance = int(gdef.get("mutation_chance", 0)) + max(0, talent_echo // 20)
    eligible_mutations = [
        key
        for key, definition in system.get("mutations", {}).items()
        if not definition.get("requires_any")
        or any(element in definition.get("requires_any", []) for element in elements)
    ]
    if eligible_mutations and randbelow(100) < mutation_chance:
        mutation = str(_pick(eligible_mutations, randbelow))

    stability = int(clamp(88 + randbelow(13) - max(0, len(elements) - 1) * 6, 35, 100))
    return {
        "grade": grade,
        "purity": purity,
        "elements": elements,
        "mutation": mutation,
        "stability": stability,
        "refinement_progress": 0,
        "compatibility": root_compatibility(elements, path, system, mutation),
    }


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


def generate_bloodline_profile(
    *,
    family: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
    randbelow: RandBelow = secrets.randbelow,
) -> dict[str, Any] | None:
    name = str(family.get("bloodline_name") or "None")
    if name == "None" or int(family.get("bloodline_purity", 0)) <= 0:
        return None
    matched = _definition_by_name(definitions, name)
    bloodline_id, definition = matched if matched else ("legacy_family_bloodline", {})
    family_purity = int(clamp(int(family.get("bloodline_purity", 0)), 1, 100))
    # Inheritance is imperfect: a child can be purer or more diluted than the
    # current clan average, but large changes require later gameplay.
    purity = int(clamp(family_purity - 12 + randbelow(25), 5, 100))
    return {
        "bloodline_id": bloodline_id,
        "name": str(definition.get("name", name)),
        "affinity": str(definition.get("affinity", family.get("bloodline_affinity", "None"))),
        "purity": purity,
        "state": "dormant",
        "evolution_stage": 0,
        "progress": 0,
        "rejection": 0,
        "mutation": "",
        "primary_lineage": 1,
        "unlocked_techniques": [],
    }


def generate_physique_profile(
    *,
    path: str,
    root_elements: Iterable[str],
    family: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
    talent_echo: int = 0,
    randbelow: RandBelow = secrets.randbelow,
) -> dict[str, Any]:
    chance = int(clamp(18 + int(family.get("tier", 1)) * 4 + talent_echo // 5, 18, 60))
    if randbelow(100) >= chance:
        return {
            "physique_id": "ordinary_mortal_body",
            "name": "Ordinary Mortal Body",
            "state": "ordinary",
            "evolution_stage": 0,
            "progress": 0,
            "stability": 100,
            "instability": 0,
        }
    roots = set(str(value) for value in root_elements)
    weighted: list[tuple[str, dict[str, Any]]] = []
    for key, definition in definitions.items():
        if key == "ordinary_mortal_body":
            continue
        weight = 1
        if path in definition.get("favored_paths", []):
            weight += 4
        if roots.intersection(str(value) for value in definition.get("favored_roots", [])):
            weight += 4
        weighted.extend([(str(key), dict(definition))] * weight)
    if not weighted:
        return {
            "physique_id": "ordinary_mortal_body",
            "name": "Ordinary Mortal Body",
            "state": "ordinary",
            "evolution_stage": 0,
            "progress": 0,
            "stability": 100,
            "instability": 0,
        }
    physique_id, definition = _pick(weighted, randbelow)
    return {
        "physique_id": physique_id,
        "name": str(definition.get("name", physique_id.replace("_", " ").title())),
        "state": "dormant",
        "evolution_stage": 0,
        "progress": 0,
        "stability": 82 + randbelow(19),
        "instability": 0,
    }


def generate_aptitude_bundle(
    *,
    base_root: str,
    path: str,
    family: dict[str, Any],
    root_system: dict[str, Any],
    bloodline_definitions: dict[str, dict[str, Any]],
    physique_definitions: dict[str, dict[str, Any]],
    talent_echo: int = 0,
    randbelow: RandBelow = secrets.randbelow,
) -> dict[str, Any]:
    root = generate_root_profile(
        base_root=base_root,
        path=path,
        system=root_system,
        family_tier=int(family.get("tier", 1)),
        talent_echo=talent_echo,
        randbelow=randbelow,
    )
    return {
        "root": root,
        "bloodline": generate_bloodline_profile(
            family=family,
            definitions=bloodline_definitions,
            randbelow=randbelow,
        ),
        "physique": generate_physique_profile(
            path=path,
            root_elements=root["elements"],
            family=family,
            definitions=physique_definitions,
            talent_echo=talent_echo,
            randbelow=randbelow,
        ),
    }


def unlocked_ancestral_techniques(
    bloodline: dict[str, Any],
    definition: dict[str, Any],
) -> list[str]:
    if str(bloodline.get("state", "dormant")) not in {"awakened", "evolved", "mutated"}:
        return []
    stage = int(bloodline.get("evolution_stage", 0))
    purity = int(bloodline.get("purity", 0))
    return [
        str(entry["name"])
        for entry in definition.get("ancestral_techniques", [])
        if stage >= int(entry.get("stage", 1)) and purity >= int(entry.get("min_purity", 0))
    ]


def progression_requirements(
    target: str,
    bundle: dict[str, Any],
    character: dict[str, Any],
    *,
    root_system: dict[str, Any],
    bloodline_definitions: dict[str, dict[str, Any]],
    physique_definitions: dict[str, dict[str, Any]],
    action: str,
) -> list[str]:
    """Return unmet requirements for awaken/evolve actions."""
    problems: list[str] = []
    target = str(target).lower()
    if target == "root":
        root = bundle.get("root") or {}
        if action != "evolve":
            return ["Spiritual roots are awakened at birth; refine or evolve the root instead."]
        grades = list(root_system.get("grades", []))
        index = grade_index(root_system, str(root.get("grade", "Mortal")))
        if index >= len(grades) - 1:
            problems.append("Your spiritual root is already at the highest configured grade.")
        else:
            next_grade = grades[index + 1]
            if int(root.get("refinement_progress", 0)) < 100:
                problems.append("Root refinement must reach 100%.")
            if int(character.get("realm_index", 0)) < int(next_grade.get("min_realm_to_evolve", 0)):
                problems.append(f"Qi realm {int(next_grade.get('min_realm_to_evolve', 0))} is required.")
            if int(root.get("stability", 0)) < 35:
                problems.append("Root stability must be at least 35%.")
        return problems

    if target == "bloodline":
        bloodline = bundle.get("bloodline")
        if not bloodline:
            return ["You do not currently carry a recognized ancestral bloodline."]
        _, definition = bloodline_definition(bloodline, bloodline_definitions)
        state = str(bloodline.get("state", "dormant"))
        if action == "awaken":
            if state not in {"dormant", "rejected"}:
                problems.append("This bloodline is already awakened.")
            if int(bloodline.get("progress", 0)) < 100:
                problems.append("Bloodline tempering must reach 100%.")
            if int(bloodline.get("purity", 0)) < int(definition.get("awakening_min_purity", 15)):
                problems.append("Bloodline purity is too diluted for awakening.")
            if int(character.get("realm_index", 0)) < int(definition.get("awakening_min_realm", 0)):
                problems.append("Your Qi realm is too low for this awakening.")
            if int(bloodline.get("rejection", 0)) >= 100:
                problems.append("Bloodline rejection must be harmonized below 100%.")
        elif action == "evolve":
            if state not in {"awakened", "evolved", "mutated"}:
                problems.append("Awaken the bloodline before evolving it.")
            stage = int(bloodline.get("evolution_stage", 0))
            evolutions = list(definition.get("evolutions", []))
            if stage >= len(evolutions):
                problems.append("This bloodline has reached its final evolution.")
            else:
                next_stage = evolutions[stage]
                if int(bloodline.get("progress", 0)) < 100:
                    problems.append("Bloodline evolution progress must reach 100%.")
                if int(bloodline.get("purity", 0)) < int(next_stage.get("min_purity", 0)):
                    problems.append(f"Purity {int(next_stage.get('min_purity', 0))}% is required.")
                if int(character.get("realm_index", 0)) < int(next_stage.get("min_realm", 0)):
                    problems.append(f"Qi realm {int(next_stage.get('min_realm', 0))} is required.")
        return problems

    if target == "physique":
        physique = bundle.get("physique") or {}
        if str(physique.get("physique_id", "ordinary_mortal_body")) == "ordinary_mortal_body":
            return ["You do not currently possess a dormant special physique."]
        definition = physique_definitions.get(str(physique.get("physique_id")), {})
        state = str(physique.get("state", "ordinary"))
        if action == "awaken":
            if state != "dormant":
                problems.append("This physique is already awakened.")
            if int(physique.get("progress", 0)) < 100:
                problems.append("Physique tempering must reach 100%.")
            if int(character.get("body_realm_index", 0)) < int(definition.get("awakening_min_body_realm", 0)):
                problems.append("Your Body realm is too low for this awakening.")
            if int(physique.get("stability", 0)) < 35:
                problems.append("Physique stability must be at least 35%.")
        elif action == "evolve":
            if state not in {"awakened", "evolved"}:
                problems.append("Awaken the physique before evolving it.")
            stage = int(physique.get("evolution_stage", 0))
            evolutions = list(definition.get("evolutions", []))
            if stage >= len(evolutions):
                problems.append("This physique has reached its final evolution.")
            else:
                next_stage = evolutions[stage]
                if int(physique.get("progress", 0)) < 100:
                    problems.append("Physique evolution progress must reach 100%.")
                if int(character.get("body_realm_index", 0)) < int(next_stage.get("min_body_realm", 0)):
                    problems.append(f"Body realm {int(next_stage.get('min_body_realm', 0))} is required.")
                if int(physique.get("stability", 0)) < int(next_stage.get("min_stability", 35)):
                    problems.append(f"Stability {int(next_stage.get('min_stability', 35))}% is required.")
        return problems
    return ["Unknown aptitude target."]


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

from __future__ import annotations

from typing import Any


CONDITIONS: dict[str, dict[str, Any]] = {
    "flesh_wound": {
        "name": "Flesh Wound", "category": "Injury", "treatment_item": "recovery_pill",
        "description": "Torn flesh and bruised organs reduce physical reliability until treated.",
    },
    "bone_fracture": {
        "name": "Bone Fracture", "category": "Injury", "treatment_item": "recovery_pill",
        "description": "Fractured bones make movement and body techniques unreliable.",
    },
    "meridian_damage": {
        "name": "Meridian Damage", "category": "Cultivation Injury", "treatment_item": "jade_life_herb",
        "description": "Damaged meridians restrict qi circulation and slow cultivation.",
    },
    "dantian_damage": {
        "name": "Dantian Damage", "category": "Cultivation Injury", "treatment_item": "jade_life_herb",
        "description": "The dantian cannot safely hold or release its full power.",
    },
    "foundation_crack": {
        "name": "Foundation Crack", "category": "Cultivation Injury", "treatment_item": "jade_life_herb",
        "description": "Cracks in the cultivation foundation make breakthroughs significantly more dangerous.",
    },
    "soul_wound": {
        "name": "Soul Wound", "category": "Soul Injury", "treatment_item": "heart_calming_pill",
        "description": "Damage to the soul disrupts insight, divine sense, and mental control.",
    },
    "poison": {
        "name": "Spiritual Poison", "category": "Poison", "treatment_item": "purging_phoenix_pill",
        "description": "Foreign toxic qi circulates through the body and weakens action reliability.",
    },
    "qi_deviation": {
        "name": "Qi Deviation", "category": "Deviation", "treatment_item": "heart_calming_pill",
        "description": "Cultivation energy is circulating incorrectly and resists deliberate control.",
    },
    "heart_demon": {
        "name": "Heart Demon", "category": "Heart Demon", "treatment_item": "heart_calming_pill",
        "description": "An unresolved obsession interferes with will, comprehension, and breakthroughs.",
    },
}


def condition_definition(key: str) -> dict[str, Any]:
    return dict(CONDITIONS.get(str(key), {}))


def condition_effect(key: str, severity: int) -> dict[str, Any]:
    severity = max(1, min(5, int(severity)))
    base = condition_definition(key)
    name = str(base.get("name", key.replace("_", " ").title()))
    description = str(base.get("description", "Persistent cultivation damage."))
    category = str(base.get("category", "Condition"))
    mods: list[dict[str, Any]] = []

    if key == "flesh_wound":
        mods = [
            {"stat": "body", "operation": "add", "value": -severity},
            {"stat": "combat_bonus", "operation": "add", "value": -max(1, (severity + 1) // 2)},
        ]
    elif key == "bone_fracture":
        mods = [
            {"stat": "body", "operation": "add", "value": -severity},
            {"stat": "agility", "operation": "add", "value": -severity},
        ]
    elif key == "meridian_damage":
        mods = [
            {"stat": "spirit", "operation": "add", "value": -severity},
            {"stat": "cultivation_gain", "operation": "mul", "value": max(0.55, 1.0 - severity * 0.07)},
            {"stat": "combat_bonus", "operation": "add", "value": -max(1, severity // 2)},
        ]
    elif key == "dantian_damage":
        mods = [
            {"stat": "spirit", "operation": "add", "value": -severity},
            {"stat": "cultivation_gain", "operation": "mul", "value": max(0.45, 1.0 - severity * 0.10)},
            {"stat": "breakthrough_bonus", "operation": "add", "value": -severity},
        ]
    elif key == "foundation_crack":
        mods = [
            {"stat": "breakthrough_bonus", "operation": "add", "value": -(severity * 2)},
            {"stat": "cultivation_gain", "operation": "mul", "value": max(0.50, 1.0 - severity * 0.08)},
        ]
    elif key == "soul_wound":
        mods = [
            {"stat": "insight", "operation": "add", "value": -severity},
            {"stat": "spirit", "operation": "add", "value": -severity},
            {"stat": "sense_precision_bonus", "operation": "add", "value": -(severity * 2)},
        ]
    elif key == "poison":
        mods = [
            {"stat": "body", "operation": "add", "value": -severity},
            {"stat": "agility", "operation": "add", "value": -max(1, severity // 2)},
        ]
    elif key == "qi_deviation":
        mods = [
            {"stat": "spirit", "operation": "add", "value": -severity},
            {"stat": "will", "operation": "add", "value": -severity},
            {"stat": "cultivation_gain", "operation": "mul", "value": max(0.40, 1.0 - severity * 0.11)},
        ]
    elif key == "heart_demon":
        mods = [
            {"stat": "will", "operation": "add", "value": -severity},
            {"stat": "insight", "operation": "add", "value": -max(1, severity // 2)},
            {"stat": "breakthrough_bonus", "operation": "add", "value": -(severity * 2)},
        ]

    return {
        "effect_key": f"condition:{key}",
        "name": name,
        "description": description,
        "category": category,
        "severity": severity,
        "special": True,
        "modifiers": mods,
        "tags": ["persistent", "condition", key],
    }


PROFESSIONS = (
    "Alchemy", "Forging", "Formation", "Inscription", "Foraging",
    "Beast Taming", "Artifact Refining", "Appraisal",
)




def craft_quality(margin: int, *, success: bool) -> dict[str, Any]:
    """Shared craftsmanship quality for non-alchemy professions.

    Alchemy keeps its specialized pill-quality/output rules. Forging, formation
    crafting and inscription still gain a visible quality grade and profession
    XP without duplicating finished equipment simply because a roll was high.
    """
    if not success:
        return {"key": "failed", "label": "Failed", "xp_bonus": 0, "quality_points": 0}
    margin = int(margin)
    if margin >= 9:
        return {"key": "masterwork", "label": "Masterwork", "xp_bonus": 8, "quality_points": 9}
    if margin >= 6:
        return {"key": "superior", "label": "Superior", "xp_bonus": 5, "quality_points": 6}
    if margin >= 3:
        return {"key": "fine", "label": "Fine", "xp_bonus": 3, "quality_points": 3}
    return {"key": "ordinary", "label": "Ordinary", "xp_bonus": 0, "quality_points": max(0, margin)}

def profession_xp_needed(level: int) -> int:
    level = max(0, int(level))
    return 60 + level * 40


def profession_rank(level: int) -> str:
    names = ("Novice", "Apprentice", "Journeyman", "Expert", "Master", "Grandmaster", "Saint")
    return names[min(len(names) - 1, max(0, int(level)))]


ASCENSION_GATES: dict[int, dict[str, Any]] = {
    7: {"from_world": "Mortal World", "to_world": "Spiritual World", "name": "Mortal Ascension Tribulation"},
    15: {"from_world": "Spiritual World", "to_world": "Immortal World", "name": "Transcendence Tribulation"},
    23: {"from_world": "Immortal World", "to_world": "Celestial World", "name": "Celestial Ascension Tribulation"},
}


def ascension_gate(realm_index: int) -> dict[str, Any] | None:
    gate = ASCENSION_GATES.get(int(realm_index))
    return dict(gate) if gate else None


def tribulation_tns(realm_index: int) -> tuple[int, int, int]:
    base = 14 + max(0, int(realm_index)) // 2
    return base, base + 1, base + 2

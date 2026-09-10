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


PROFESSIONS = (
    "Alchemy", "Forging", "Formation", "Inscription", "Foraging",
    "Beast Taming", "Artifact Refining", "Appraisal",
)




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



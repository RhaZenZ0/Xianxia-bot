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
    "Alchemy", "Forging", "Formation", "Inscription", "Foraging", "Mining",
    "Beast Taming", "Artifact Refining", "Appraisal",
)

# The rank ladder, stated once. "Journeyman sounds medieval" (v1.2.0), and the
# owner's call in v1.2.2 is the genre's Nine-Tier ladder: Unranked before the
# first examination, then Tier 1 to Tier 9, each tier a title, each trade its
# own word in front of it - Pill Apprentice, Forge Sovereign. A rank is a level
# and nothing stored changes; the tiers are how a level is read. The content
# file's `profession_exams[].rank_name` quotes these and a gate holds them equal.
PROFESSION_TIERS = (
    "Apprentice", "Adept", "Artisan", "Expert", "Master",
    "Grandmaster", "Sage", "Emperor", "Sovereign",
)
# The word a trade puts in front of its tier. A trade this does not name gets
# the bare tier, never a wrong word.
PROFESSION_TIER_WORDS = {
    "Alchemy": "Pill", "Forging": "Forge", "Inscription": "Talisman", "Formation": "Array",
    "Foraging": "Herb", "Mining": "Ore", "Beast Taming": "Beast",
    "Artifact Refining": "Artifact", "Appraisal": "Treasure",
}
PROFESSION_RANKS = ("Unranked", *(f"Tier {n} {title}" for n, title in enumerate(PROFESSION_TIERS, start=1)))




def profession_xp_needed(level: int) -> int:
    level = max(0, int(level))
    return 60 + level * 40


def profession_rank(level: int, trade: str = "") -> str:
    """What a level in a trade is called: "Unranked", or "Tier N <word> <title>".

    The trade's word is optional so a caller that knows only the level still
    reads the ladder; the examinations and every trade-aware surface pass it.
    """
    tier = min(len(PROFESSION_TIERS), max(0, int(level)))
    if tier == 0:
        return PROFESSION_RANKS[0]
    word = PROFESSION_TIER_WORDS.get(str(trade or "").strip(), "")
    title = PROFESSION_TIERS[tier - 1]
    return f"Tier {tier} {word} {title}" if word else f"Tier {tier} {title}"


ASCENSION_GATES: dict[int, dict[str, Any]] = {
    7: {"from_world": "Mortal World", "to_world": "Spiritual World", "name": "Mortal Ascension Tribulation"},
    15: {"from_world": "Spiritual World", "to_world": "Immortal World", "name": "Transcendence Tribulation"},
    23: {"from_world": "Immortal World", "to_world": "Celestial World", "name": "Celestial Ascension Tribulation"},
}


def ascension_gate(realm_index: int) -> dict[str, Any] | None:
    gate = ASCENSION_GATES.get(int(realm_index))
    return dict(gate) if gate else None


